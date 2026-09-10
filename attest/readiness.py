"""Decide whether the files can be closed at all — before the engine sees them.

`ingest.py` used to parse exactly one schema: the one `generate.py` emits. A real
Razorpay or bank export differs from it in every boring way — renamed columns,
`DD/MM/YYYY`, a BOM on the header, `₹1,23,456.78` as text, a half-month, refunds
in a separate file with a different key. Against the old loader each of those
either killed the whole close with a traceback that named nothing, or was
silently coerced into a valid-looking record. There was no third state.

This module is the third state. Every source is read through `read_table`, which
records what it saw and rejects — never coerces — a row it cannot parse. The
result is a `Readiness` with a verdict:

  READY    every row of every supplied source parsed; the covered range spans
           the declared month.
  PARTIAL  the close can be produced but must be marked visibly incomplete: a
           source is absent, some rows were rejected, a column was not
           recognised, or the data does not cover the whole month.
  REFUSED  the settlement report — the one authoritative source — could not be
           read in full, or the period volume denominator cannot be computed.
           No close is produced.

Two rules are load-bearing:

  * **Column aliasing is an explicit map (`ALIASES`), never inference.** A header
    not in the map is reported as unrecognised. Nothing is matched by position,
    edit distance or case-folded similarity. A guessed column in a finance tool
    is worse than a refusal.

  * **A blank cell is `MISSING`, not zero.** Invariant 8 lives at this boundary
    too: ₹0 is a real value and a blank is the absence of one, and the loader
    must be able to tell them apart.
"""
from __future__ import annotations

import calendar
import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date

# ==========================================================================
# Sentinels and verdict constants
# ==========================================================================
class _Missing:
    """A cell that was empty. Distinct from a parsed 0 — invariant 8."""
    __slots__ = ()

    def __repr__(self) -> str:            # pragma: no cover - debugging aid
        return "MISSING"

    def __bool__(self) -> bool:
        return False


MISSING = _Missing()

READY = "READY"
PARTIAL = "PARTIAL"
REFUSED = "REFUSED"

# Emitted only while nothing is wrong. `corroborate` retracts it the moment a
# completeness check downgrades, because leading a PARTIAL close with a line
# about everything having been read is the same overstatement this whole stage
# exists to remove.
_READY_NOTE = ("every row supplied was parsed, and the covered range spans the "
               "declared month; completeness against the other sources is "
               "checked separately once records are resolved")


# ==========================================================================
# What a read produced
# ==========================================================================
@dataclass
class Rejection:
    """One row (or one whole column) the reader would not accept.

    `line_no` is the physical line in the file, header and preamble counted, so
    a person can open the file and go straight to it. 0 means the problem is the
    column as a whole, not a single row.
    """
    source: str
    line_no: int
    column: str
    raw: str
    reason: str

    def describe(self) -> str:
        where = f"{self.source}"
        if self.line_no:
            where += f" line {self.line_no}"
        if self.column:
            where += f", column {self.column}"
        tail = f" ({self.raw!r})" if self.raw not in ("", None) else ""
        return f"{where}: {self.reason}{tail}"


@dataclass
class SourceReadiness:
    name: str
    required: bool = False
    present: bool = False
    encoding: str | None = None
    bom: bool = False
    delimiter: str | None = None
    preamble_skipped: int = 0
    rows_in: int = 0                       # data rows physically present
    rows_read: int = 0                     # data rows that became records
    rejections: list[Rejection] = field(default_factory=list)
    columns_recognised: dict[str, str] = field(default_factory=dict)   # canonical -> header
    columns_unrecognised: list[str] = field(default_factory=list)
    columns_missing: list[str] = field(default_factory=list)
    conventions: list[str] = field(default_factory=list)
    date_min: date | None = None
    date_max: date | None = None

    @property
    def clean(self) -> bool:
        return (
            self.present
            and not self.rejections
            and not self.columns_unrecognised
            and not self.columns_missing
        )

    def note(self, text: str) -> None:
        if text not in self.conventions:
            self.conventions.append(text)


@dataclass
class Readiness:
    declared_period: str = ""
    sources: dict[str, SourceReadiness] = field(default_factory=dict)
    verdict: str = READY
    reasons: list[str] = field(default_factory=list)
    period_first: date | None = None
    period_last: date | None = None
    activity_first: date | None = None
    activity_last: date | None = None
    activity_basis: str = ""
    days_in_month: int = 0
    rows_outside_period: int = 0

    # --- verdict helpers ------------------------------------------------
    @property
    def ready(self) -> bool:
        return self.verdict == READY

    @property
    def partial(self) -> bool:
        return self.verdict == PARTIAL

    @property
    def refused(self) -> bool:
        return self.verdict == REFUSED

    def refuse(self, reason: str) -> None:
        self.verdict = REFUSED
        # A refusal is decisive — surface it above any downgrade notes already
        # collected, so the reason a close was not produced reads first.
        if reason not in self.reasons:
            self.reasons.insert(0, reason)

    def downgrade(self, reason: str) -> None:
        """Drop to PARTIAL unless already REFUSED. Never silently upgrades."""
        if self.verdict != REFUSED:
            self.verdict = PARTIAL
        if reason and reason not in self.reasons:
            self.reasons.append(reason)

    # --- throughput, with a denominator at last -----------------------
    def rows_in(self) -> int:
        return sum(s.rows_in for s in self.sources.values())

    def rows_read(self) -> int:
        return sum(s.rows_read for s in self.sources.values())

    def rows_rejected(self) -> int:
        return sum(len(s.rejections) for s in self.sources.values())

    def all_rejections(self) -> list[Rejection]:
        out: list[Rejection] = []
        for s in self.sources.values():
            out.extend(s.rejections)
        return out

    def sources_supplied(self) -> list[str]:
        return [n for n, s in self.sources.items() if s.present]

    def sources_absent(self) -> list[str]:
        return [n for n, s in self.sources.items() if not s.present]

    def unrecognised_columns(self) -> list[str]:
        out: list[str] = []
        for n, s in self.sources.items():
            out.extend(f"{n}:{c}" for c in s.columns_unrecognised)
        return out

    # --- the one line a readiness report exists to print --------------
    def coverage_line(self) -> str:
        if not (self.activity_first and self.period_first):
            return "coverage: not assessed"
        return (
            f"covers {self.activity_first.isoformat()} – "
            f"{self.activity_last.isoformat()} of "
            f"{self.period_first.isoformat()} – {self.period_last.isoformat()} "
            f"(by {self.activity_basis})"
        )

    def as_dict(self) -> dict:
        """A JSON-safe view for the API summary, the pack and the seal."""
        return {
            "verdict": self.verdict,
            "declared_period": self.declared_period,
            "reasons": list(self.reasons),
            "rows_in": self.rows_in(),
            "rows_read": self.rows_read(),
            "rows_rejected": self.rows_rejected(),
            "rows_outside_period": self.rows_outside_period,
            "coverage": self.coverage_line(),
            "activity_first": self.activity_first.isoformat() if self.activity_first else None,
            "activity_last": self.activity_last.isoformat() if self.activity_last else None,
            "period_first": self.period_first.isoformat() if self.period_first else None,
            "period_last": self.period_last.isoformat() if self.period_last else None,
            "days_in_month": self.days_in_month,
            "sources": {
                n: {
                    "present": s.present,
                    "required": s.required,
                    "encoding": s.encoding,
                    "bom": s.bom,
                    "delimiter": s.delimiter,
                    "preamble_skipped": s.preamble_skipped,
                    "rows_in": s.rows_in,
                    "rows_read": s.rows_read,
                    "rows_rejected": len(s.rejections),
                    "rejections": [r.describe() for r in s.rejections[:25]],
                    "columns_recognised": dict(s.columns_recognised),
                    "columns_unrecognised": list(s.columns_unrecognised),
                    "columns_missing": list(s.columns_missing),
                    "conventions": list(s.conventions),
                    "date_min": s.date_min.isoformat() if s.date_min else None,
                    "date_max": s.date_max.isoformat() if s.date_max else None,
                }
                for n, s in self.sources.items()
            },
        }

    def coverage_digest(self) -> dict:
        """Only the facts the seal records — small integers and short strings."""
        return {
            "verdict": self.verdict,
            "rows_in": self.rows_in(),
            "rows_read": self.rows_read(),
            "rows_rejected": self.rows_rejected(),
            "rows_outside_period": self.rows_outside_period,
            "sources_supplied": sorted(self.sources_supplied()),
            "activity_first": self.activity_first.isoformat() if self.activity_first else "",
            "activity_last": self.activity_last.isoformat() if self.activity_last else "",
        }


# ==========================================================================
# The explicit column map. Never inference.
# ==========================================================================
@dataclass(frozen=True)
class Col:
    canonical: str
    aliases: frozenset
    kind: str                      # money | date | enum | id | text
    required: bool = True
    enum_allowed: frozenset = frozenset()
    enum_map: tuple = ()           # ((from, to), ...) applied before enum_allowed


@dataclass(frozen=True)
class TableSpec:
    name: str
    required_source: bool
    cols: tuple
    ignore: frozenset = frozenset()      # recognised-but-unused headers
    unique_key: str | None = None        # canonical field that must not repeat

    def col(self, canonical: str) -> Col | None:
        return next((c for c in self.cols if c.canonical == canonical), None)


def _c(canonical, aliases, kind, required=True, enum_allowed=frozenset(), enum_map=()):
    return Col(canonical, frozenset(aliases), kind, required,
               frozenset(enum_allowed), tuple(enum_map))


_ROW_TYPE = _c(
    "row_type", {"row_type", "type", "txn_type", "transaction_type"}, "enum",
    required=True,
    enum_allowed={"captured", "refund_adjustment", "chargeback", "hold_release"},
    enum_map=(
        ("payment", "captured"),
        ("capture", "captured"),
        ("sale", "captured"),
        ("refund", "refund_adjustment"),
        ("refund_adjustment", "refund_adjustment"),
        ("adjustment", "hold_release"),
        ("transfer", "hold_release"),
        ("hold_release", "hold_release"),
        ("chargeback", "chargeback"),
        ("dispute", "chargeback"),
    ),
)

SPECS: dict[str, TableSpec] = {
    "razorpay_settlements.csv": TableSpec(
        "razorpay_settlements.csv", True,
        (
            _c("settlement_id", {"settlement_id", "settlement id", "setl_id"}, "id"),
            _c("settled_on",
               {"settled_on", "settled_at", "settlement_date", "settlement_on", "date"},
               "date"),
            _c("payment_id", {"payment_id", "payment id", "entity_id"}, "id",
               required=False),
            _c("order_id", {"order_id", "order id", "order_receipt", "receipt"}, "id",
               required=False),
            _c("gross", {"gross_amount", "amount", "gross", "gross_value"}, "money",
               required=False),
            _c("mdr", {"mdr", "fee", "mdr_amount", "commission"}, "money",
               required=False),
            _c("gst_on_mdr", {"gst_on_mdr", "tax", "gst", "gst_amount", "tax_on_fee"},
               "money", required=False),
            _c("net", {"net_amount", "net", "credit", "amount_settled", "net_settled"},
               "money", required=False),
            _ROW_TYPE,
        ),
        ignore=frozenset({
            "settlement_utr", "utr", "currency", "method", "description",
            "on_hold", "arn", "notes", "created_at", "narration", "vpa",
            "card_network", "international",
        }),
    ),
    "bank_statement.csv": TableSpec(
        "bank_statement.csv", False,
        (
            _c("value_date",
               {"value_date", "value dt", "txn_date", "transaction_date", "date",
                "posting_date", "post_date"},
               "date"),
            _c("narration",
               {"narration", "description", "particulars", "remarks", "details"},
               "text", required=False),
            _c("utr",
               {"utr", "ref_no", "reference", "reference_no", "cheque_no", "utr_no",
                "rrn", "transaction_id"},
               "id", required=False),
            _c("credit",
               {"credit_amount", "credit", "deposit", "deposit_amt", "cr", "cr_amount"},
               "money", required=False),
            _c("debit",
               {"debit_amount", "debit", "withdrawal", "withdrawal_amt", "dr",
                "dr_amount"},
               "money", required=False),
        ),
        ignore=frozenset({
            "balance", "closing_balance", "running_balance", "chq_no", "branch",
            "value dt", "type",
        }),
    ),
    "orders.csv": TableSpec(
        "orders.csv", False,
        (
            _c("order_id",
               {"order_id", "order id", "order_number", "order_no", "receipt", "name"},
               "id"),
            _c("placed_on",
               {"placed_on", "order_date", "created_at", "created_on", "date",
                "placed_at"},
               "date"),
            _c("gross",
               {"gross_amount", "amount", "total", "order_value", "grand_total",
                "total_price"},
               "money"),
            _c("channel", {"channel", "source", "sales_channel", "medium"}, "text",
               required=False),
            _c("payment_mode",
               {"payment_mode", "payment_method", "method", "mode", "gateway"},
               "text", required=False),
        ),
        ignore=frozenset({
            "status", "customer", "customer_name", "email", "phone", "currency",
            "tax", "discount", "shipping", "financial_status", "fulfillment_status",
        }),
        unique_key="order_id",
    ),
    "cod_remittances.csv": TableSpec(
        "cod_remittances.csv", False,
        (
            _c("remittance_id",
               {"remittance_id", "remittance id", "payout_id", "utr", "settlement_id"},
               "id"),
            _c("remitted_on",
               {"remitted_on", "remittance_date", "payout_date", "date", "credited_on"},
               "date"),
            _c("awb", {"awb", "awb_number", "awb_no", "tracking_id", "waybill"}, "id"),
            _c("cod_value",
               {"cod_value", "cod_amount", "collectible_amount", "collectable_amount"},
               "money"),
            _c("cod_fee", {"cod_fee", "cod_charges", "fee", "cod_charge"}, "money",
               required=False),
            _c("rto_freight",
               {"rto_freight", "rto_charges", "return_freight", "rto_freight_charges"},
               "money", required=False),
            _c("adjustment",
               {"adjustment", "adjustments", "other_deductions", "misc_deduction"},
               "money", required=False),
            _c("net", {"net_remitted", "net_amount", "net", "payout_amount", "amount"},
               "money"),
        ),
        ignore=frozenset({"status", "courier", "zone", "weight", "order_id"}),
    ),
    "shipments.csv": TableSpec(
        "shipments.csv", False,
        (
            _c("awb",
               {"awb", "awb_number", "awb_no", "tracking_id", "waybill", "lr_no"},
               "id"),
            _c("order_id", {"order_id", "order id", "order_number", "order_no"}, "id",
               required=False),
            _c("shipped_on",
               {"shipped_on", "ship_date", "manifest_date", "date", "dispatch_date"},
               "date"),
            _c("delivered_on",
               {"delivered_on", "delivery_date", "pod_date", "rto_date"},
               "date", required=False),
            _c("cod_value", {"cod_value", "cod_amount", "collectible_amount"}, "money",
               required=False),
            _c("status",
               {"status", "shipment_status", "current_status", "delivery_status"},
               "text", required=False),
        ),
        ignore=frozenset({"courier", "weight", "zone", "pincode", "city", "channel"}),
        unique_key="awb",
    ),
    "refunds.csv": TableSpec(
        "refunds.csv", False,
        (
            _c("refund_id", {"refund_id", "refund id", "rfnd_id"}, "id"),
            _c("payment_id", {"payment_id", "payment id", "entity_id"}, "id",
               required=False),
            _c("order_id", {"order_id", "order id", "order_receipt", "receipt"}, "id",
               required=False),
            _c("initiated_on",
               {"initiated_on", "refund_date", "created_at", "created_on", "date"},
               "date"),
            _c("amount", {"amount", "refund_amount", "value", "refund_value"}, "money"),
        ),
        ignore=frozenset({"status", "speed", "notes", "currency", "utr", "arn"}),
    ),
    "disputes.csv": TableSpec(
        "disputes.csv", False,
        (
            _c("dispute_id", {"dispute_id", "dispute id", "disp_id", "case_id"}, "id"),
            _c("payment_id", {"payment_id", "payment id", "entity_id"}, "id",
               required=False),
            _c("order_id", {"order_id", "order id", "order_receipt", "receipt"}, "id",
               required=False),
            _c("raised_on",
               {"raised_on", "dispute_date", "created_at", "created_on", "date",
                "reported_on"},
               "date"),
            _c("amount", {"amount", "disputed_amount", "value", "dispute_amount"},
               "money"),
        ),
        ignore=frozenset({
            "status", "reason_code", "reason", "phase", "currency", "respond_by",
            "type",
        }),
    ),
}


# ==========================================================================
# Decoding and dialect
# ==========================================================================
_ENCODINGS = ("utf-8-sig", "utf-16", "cp1252")


def _decode(raw: bytes) -> tuple[str, str, bool]:
    """Return (text, encoding_used, had_bom). Fixes A3/A2 at the read."""
    bom = raw[:3] == b"\xef\xbb\xbf" or raw[:2] in (b"\xff\xfe", b"\xfe\xff")
    for enc in _ENCODINGS:
        try:
            text = raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        used = "utf-8" if (enc == "utf-8-sig" and raw[:3] != b"\xef\xbb\xbf") else enc
        return text, used, bom
    return raw.decode("utf-8", errors="replace"), "unknown", bom


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        # Fall back to whichever candidate appears most on the busiest line.
        best, hits = ",", -1
        for line in sample.splitlines()[:20]:
            for d in ",;\t|":
                n = line.count(d)
                if n > hits:
                    best, hits = d, n
        return best


def _norm(header: str) -> str:
    return header.strip().lstrip("﻿").strip().lower().replace("  ", " ")


# ==========================================================================
# Cell parsers. Each returns (value | MISSING, reason | None). Reject, never coerce.
# ==========================================================================
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

_GROUPING_RE = re.compile(r"\d{1,3}(?:,\d{2,3})+")
_PLAIN_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def parse_money(s):
    """A rupee amount as it appears in a real export → integer paise.

    Accepts ``₹1,23,456.78`` (Indian grouping), ``1,234.00`` (Western),
    ``(1,234.00)`` and ``1,234.00 Dr`` (both negative), a leading ``INR``/``Rs``.
    Rejects — rather than rounds or guesses — scientific notation, more than two
    decimal places, and a lone comma that might be a decimal separator.

    Returns (paise:int, None), (MISSING, None) for a blank cell, or (None, reason).
    """
    if s is None:
        return MISSING, None
    t = str(s).strip()
    if t == "":
        return MISSING, None
    t = t.replace("₹", "").replace("\xa0", " ").replace(" ", " ")
    low = t.lower().strip()
    for token in ("inr ", "inr", "rs.", "rs "):
        if low.startswith(token):
            t = t[len(token):].strip()
            low = t.lower().strip()
            break

    neg = False
    if t.startswith("(") and t.endswith(")"):
        neg, t = True, t[1:-1].strip()
        low = t.lower().strip()
    for suf in ("cr", "dr"):
        if low.endswith(suf) and (len(low) == len(suf) or not low[-len(suf) - 1].isalnum()):
            neg = neg or (suf == "dr")
            t = t[:len(t) - len(suf)].rstrip(" .").strip()
            break
    if t.startswith("-"):
        neg, t = True, t[1:].strip()
    if t.startswith("+"):
        t = t[1:].strip()

    if t == "":
        return None, "no digits in the amount"
    if "e" in t.lower():
        return None, "scientific notation is not accepted; write the number in full"

    if "," in t and "." not in t:
        if _GROUPING_RE.fullmatch(t.replace(" ", "")):
            core = t.replace(",", "").replace(" ", "")
        else:
            return None, ("comma is ambiguous here — is it a decimal separator? "
                          "write the amount with a period and paise")
    else:
        core = t.replace(",", "").replace(" ", "").replace("_", "")

    if not _PLAIN_NUM_RE.fullmatch(core):
        return None, "not a decimal amount"
    whole, _, frac = core.partition(".")
    if len(frac) > 2:
        return None, (f"{len(frac)} decimal places — money is integer paise, "
                      "at most two")
    paise = int(whole or 0) * 100 + int((frac + "00")[:2])
    return (-paise if neg else paise), None


def _parse_dmon(v: str) -> date:
    parts = re.split(r"[-/ ]", v.strip())
    d, mon, y = int(parts[0]), _MONTHS[parts[1][:3].lower()], int(parts[2])
    if y < 100:
        y += 2000
    return date(y, mon, d)


def _parse_numeric_date(v: str, day_first: bool) -> date:
    a, b, y = (int(x) for x in re.split(r"[-/]", v.strip()))
    if y < 100:
        y += 2000
    d, mth = (a, b) if day_first else (b, a)
    return date(y, mth, d)


def _wrap(fn):
    def parse(s):
        if s is None:
            return MISSING, None
        t = str(s).strip()
        if t == "":
            return MISSING, None
        try:
            return fn(t), None
        except (ValueError, KeyError, IndexError):
            return None, "not a valid date"
    return parse


_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}([ T].*)?$")
_DMON_RE = re.compile(r"\d{1,2}[-/ ][A-Za-z]{3,}[-/ ]\d{2,4}$")
_NUMDATE_RE = re.compile(r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")


def resolve_date_parser(values, declared_format=None):
    """Look at the whole column, decide ONE parser, then parse every row with it.

    A column of ``NN/NN/YYYY`` where no row has a field above 12 is genuinely
    ambiguous — DD/MM and MM/DD both parse. That refuses, unless
    ``contract_terms.json`` declares ``"date_format"``. Inferring it from locale,
    file name or position is exactly the guessed-column failure, at its worst:
    it moves rows across the period boundary invariant 6 exists to protect.

    Returns (parser, note) or (None, reason).
    """
    vals = [str(v).strip() for v in values if v is not None and str(v).strip()]
    if not vals:
        return _wrap(date.fromisoformat), "no dates"

    def _shape(v):
        if _ISO_RE.match(v):
            return "iso"
        if _DMON_RE.match(v):
            return "dmon"
        if _NUMDATE_RE.match(v):
            return "num"
        if v.isdigit():
            return "serial"
        return "other"

    shapes = {}
    for v in vals:
        shapes.setdefault(_shape(v), []).append(v)
    datelike = [k for k in ("iso", "dmon", "num") if k in shapes]

    if not datelike:
        if "serial" in shapes:
            return None, ("dates look like Excel serial numbers — re-export with "
                          "real calendar dates")
        return None, ("date format not recognised (expected ISO-8601, DD/MM/YYYY "
                      "or DD-Mon-YYYY)")
    if len(datelike) > 1:
        return None, ("the column mixes date formats ("
                      + " and ".join(datelike) + ") — it cannot be read as one")
    kind = datelike[0]

    # A handful of non-date values (a trailer line, a stray label) become
    # per-row rejections via _wrap; they do not decide the column's format.
    if kind == "iso":
        return _wrap(lambda v: date.fromisoformat(v[:10])), "ISO-8601 (YYYY-MM-DD)"
    if kind == "dmon":
        return _wrap(_parse_dmon), "DD-Mon-YYYY"

    parts = [re.split(r"[/-]", v) for v in shapes["num"]]
    firsts = [int(p[0]) for p in parts]
    seconds = [int(p[1]) for p in parts]
    day_first = None
    if any(x > 12 for x in firsts):
        day_first = True
    if any(x > 12 for x in seconds):
        if day_first:
            return None, ("the column mixes DD/MM and MM/DD rows — it cannot "
                          "be read as one format")
        day_first = False
    if declared_format:
        d = declared_format.strip().upper().replace("-", "/")
        if d.startswith("DD/MM"):
            day_first = True
        elif d.startswith("MM/DD"):
            day_first = False
    if day_first is None:
        return None, ('dates are NN/NN/YYYY and nothing in the column proves '
                      'DD/MM from MM/DD — add "date_format": "DD/MM/YYYY" to '
                      "contract_terms.json")
    note = "DD/MM/YYYY" if day_first else "MM/DD/YYYY"
    return _wrap(lambda v, _df=day_first: _parse_numeric_date(v, _df)), note


def make_enum_parser(col: Col):
    mapping = {a.lower(): b for a, b in col.enum_map}

    def parse(s):
        if s is None:
            return MISSING, None
        t = str(s).strip().lower()
        if t == "":
            return MISSING, None
        if t in mapping:
            return mapping[t], None
        if t in col.enum_allowed:
            return t, None
        return None, (f"unrecognised {col.canonical} '{s}' — expected one of "
                      + ", ".join(sorted(col.enum_allowed)))
    return parse


# ==========================================================================
# Header location — explicit mapping only
# ==========================================================================
def _map_headers(headers, spec):
    """(mapping, unrecognised, missing, ambiguous). mapping: canonical -> header."""
    norm = [_norm(h) for h in headers]
    mapping: dict[str, str] = {}
    ambiguous: list[str] = []
    for col in spec.cols:
        hits = [headers[i] for i, n in enumerate(norm) if n in col.aliases]
        if len(hits) == 1:
            mapping[col.canonical] = hits[0]
        elif len(hits) > 1:
            ambiguous.append(f"{col.canonical} (matched {', '.join(hits)})")
    claimed = {_norm(h) for h in mapping.values()}
    unrecognised = [
        headers[i] for i, n in enumerate(norm)
        if n and n not in claimed and n not in spec.ignore
    ]
    missing = [c.canonical for c in spec.cols if c.required and c.canonical not in mapping]
    return mapping, unrecognised, missing, ambiguous


def _locate_header(lines, spec):
    """Skip a bank-statement preamble: the header is the first line that maps to
    every required column. Returns (index, mapping, unrecognised, missing,
    ambiguous) or (None, ...)."""
    required = {c.canonical for c in spec.cols if c.required}
    for i, line in enumerate(lines[:40]):
        if not line.strip():
            continue
        try:
            cells = next(csv.reader([line], delimiter=_sniff_delimiter(line + "\n")))
        except csv.Error:
            continue
        mapping, unrec, missing, amb = _map_headers(cells, spec)
        if required.issubset(mapping):
            return i, mapping, unrec, missing, amb
    return None, {}, [], list(required), []


# ==========================================================================
# read_table
# ==========================================================================
def read_table(path, spec: TableSpec, declared_date_format=None):
    """Read one source into a list of canonical dict-rows plus a SourceReadiness.

    A dict-row maps canonical field -> parsed value or MISSING. A row with any
    unparseable required cell is rejected (recorded, not raised) and omitted.
    """
    sr = SourceReadiness(name=spec.name, required=spec.required_source)
    rows: list[dict] = []

    if path is None or not path.exists():
        return rows, sr
    sr.present = True

    raw = path.read_bytes()
    text, enc, bom = _decode(raw)
    sr.encoding, sr.bom = enc, bom
    if enc == "unknown":
        sr.rejections.append(Rejection(spec.name, 0, "", "",
                                       "could not decode the file as UTF-8, UTF-16 "
                                       "or Windows-1252"))
        return rows, sr

    all_lines = text.splitlines()
    if not any(ln.strip() for ln in all_lines):
        sr.rejections.append(Rejection(spec.name, 0, "", "", "the file is empty"))
        return rows, sr

    sr.delimiter = _sniff_delimiter("\n".join(all_lines[:20]) + "\n")
    hdr_idx, mapping, unrec, missing, ambiguous = _locate_header(all_lines, spec)
    if hdr_idx is None:
        sr.columns_missing = missing
        sr.rejections.append(Rejection(
            spec.name, 0, "", "",
            "no header row found for the columns "
            + ", ".join(c.canonical for c in spec.cols if c.required)))
        return rows, sr

    sr.preamble_skipped = hdr_idx
    sr.columns_recognised = mapping
    sr.columns_unrecognised = unrec
    sr.columns_missing = missing
    for a in ambiguous:
        sr.rejections.append(Rejection(spec.name, hdr_idx + 1, "", "",
                                       f"two columns both map to {a}"))
    for m in missing:
        sr.rejections.append(Rejection(spec.name, hdr_idx + 1, m, "",
                                       "required column not found in the header"))
    if hdr_idx > 0:
        sr.note(f"{hdr_idx} preamble line(s) skipped above the header")

    body = "\n".join(all_lines[hdr_idx:])
    reader = csv.reader(io.StringIO(body), delimiter=sr.delimiter or ",")
    try:
        header_cells = next(reader)
    except StopIteration:
        return rows, sr
    n_fields = len(header_cells)
    col_index = {canon: header_cells.index(h) for canon, h in mapping.items()}

    # First pass: collect raw rows (rejecting ragged ones), and column strings
    # for the two-pass date resolution.
    raw_rows: list[tuple[int, list]] = []
    for cells in reader:
        file_line = hdr_idx + reader.line_num
        if not cells or all(c.strip() == "" for c in cells):
            continue
        sr.rows_in += 1
        if len(cells) != n_fields:
            sr.rejections.append(Rejection(
                spec.name, file_line, "", sr.delimiter.join(cells)[:120],
                f"{len(cells)} fields, header has {n_fields} — row is ragged"))
            continue
        raw_rows.append((file_line, cells))

    if missing or ambiguous:
        # The header itself is unusable; every data row would fail the same way.
        return rows, sr

    # Resolve one date parser per date column, over the whole column.
    date_parsers: dict[str, object] = {}
    for col in spec.cols:
        if col.kind != "date" or col.canonical not in col_index:
            continue
        j = col_index[col.canonical]
        parser, note = resolve_date_parser((c[j] for _, c in raw_rows),
                                           declared_date_format)
        if parser is None:
            sr.rejections.append(Rejection(spec.name, 0, mapping[col.canonical], "",
                                           note))
            date_parsers[col.canonical] = None
        else:
            date_parsers[col.canonical] = parser
            sr.note(f"{mapping[col.canonical]}: dates read as {note}")

    if any(p is None for p in date_parsers.values()):
        return rows, sr

    enum_parsers = {c.canonical: make_enum_parser(c)
                    for c in spec.cols if c.kind == "enum"}

    # Second pass: build canonical rows.
    seen_keys: set = set()
    dmin = dmax = None
    for file_line, cells in raw_rows:
        rec: dict = {}
        bad = False
        for col in spec.cols:
            if col.canonical not in col_index:
                rec[col.canonical] = MISSING
                continue
            raw_val = cells[col_index[col.canonical]]
            if col.kind == "money":
                val, why = parse_money(raw_val)
            elif col.kind == "date":
                val, why = date_parsers[col.canonical](raw_val)
            elif col.kind == "enum":
                val, why = enum_parsers[col.canonical](raw_val)
            else:
                v = (raw_val or "").strip()
                val, why = (v if v != "" else MISSING), None
            if why:
                sr.rejections.append(Rejection(spec.name, file_line,
                                               col.canonical, str(raw_val), why))
                bad = True
                break
            if val is MISSING and col.required:
                sr.rejections.append(Rejection(spec.name, file_line, col.canonical,
                                               str(raw_val),
                                               "required value is blank"))
                bad = True
                break
            rec[col.canonical] = val
            if col.kind == "date" and isinstance(val, date):
                dmin = val if dmin is None or val < dmin else dmin
                dmax = val if dmax is None or val > dmax else dmax
        if bad:
            continue
        if spec.unique_key:
            k = rec.get(spec.unique_key)
            if k in seen_keys:
                sr.rejections.append(Rejection(
                    spec.name, file_line, spec.unique_key, str(k),
                    "duplicate key — the row would silently overwrite an earlier one"))
                continue
            seen_keys.add(k)
        rows.append(rec)

    sr.rows_read = len(rows)
    sr.date_min, sr.date_max = dmin, dmax
    return rows, sr


# ==========================================================================
# Coverage — the declared month vs the range actually present
# ==========================================================================
def assess_coverage(readiness: Readiness, *, order_dates, settlement_dates,
                    other_dates):
    """Compare the range of activity in the files with the declared month.

    Prefers order dates (a month's orders span it exactly). Falls back to
    settlement dates, which legitimately run a settlement cycle past month-end,
    so the tail is judged generously there.
    """
    period = readiness.declared_period
    try:
        y, m = int(period[:4]), int(period[5:7])
        days_in = calendar.monthrange(y, m)[1]
    except (ValueError, IndexError):
        readiness.refuse(f"declared period {period!r} is not YYYY-MM")
        return
    first_day, last_day = date(y, m, 1), date(y, m, days_in)
    readiness.period_first, readiness.period_last = first_day, last_day
    readiness.days_in_month = days_in

    if order_dates:
        dates, basis, lead_tol, tail_tol = sorted(order_dates), "orders placed", 3, 3
    elif settlement_dates:
        dates, basis, lead_tol, tail_tol = sorted(settlement_dates), "settlement dates", 6, 3
    else:
        readiness.downgrade("no dated rows to check the covered range against")
        return

    lo, hi = dates[0], dates[-1]
    readiness.activity_first, readiness.activity_last = lo, hi
    readiness.activity_basis = basis
    span_days = (hi - lo).days

    # Egregious over-coverage: a quarter's data filed as one month.
    if span_days > 2 * days_in + 7:
        readiness.refuse(
            f"the files span {span_days} days — a monthly close needs one month; "
            "split the export by month and close each")
        return

    lead = (lo - first_day).days           # >0: data starts late
    if lead > lead_tol:
        readiness.downgrade(
            f"first activity is {lo.isoformat()}, {lead} days into the declared "
            f"month — the earlier part of {period} is not in these files")

    if basis == "orders placed":
        tail = (last_day - hi).days        # >0: data ends early
        if tail > tail_tol:
            readiness.downgrade(
                f"last activity is {hi.isoformat()}, {tail} days before "
                f"{last_day.isoformat()} — the close would cover a partial month")
        past = (hi - last_day).days
        if past > 10:
            readiness.downgrade(
                f"activity runs {past} days past {last_day.isoformat()} — "
                "these files cover more than the declared month")
    else:
        if hi < last_day:
            readiness.downgrade(
                f"settlements stop on {hi.isoformat()}, before the month ends — "
                f"settlements for late-{period} orders are not in these files")

    outside = [d for d in dates if d.year != y or d.month != m]
    readiness.rows_outside_period = len(outside)

    # A bank statement that BEGINS after the settlement report does turns every
    # early batch into a phantom "settled but never credited" finding — which
    # feeds the residual that decides whether the close can be signed. A missing
    # batch at the tail is not this: a batch settled on the 30th and not yet
    # credited is a legitimate exception, not a coverage gap, so only the start
    # is checked.
    bank = readiness.sources.get("bank_statement.csv")
    setl = readiness.sources.get("razorpay_settlements.csv")
    if (bank and bank.present and bank.date_min and setl and setl.date_min
            and bank.date_min > setl.date_min + _days(5)):
        readiness.downgrade(
            f"the bank statement begins {bank.date_min.isoformat()}, after the "
            f"settlement report does ({setl.date_min.isoformat()}) — batches "
            "settled before the statement starts will look uncredited")


def _days(n):
    from datetime import timedelta
    return timedelta(days=n)


# ==========================================================================
# Final verdict from the per-source picture
# ==========================================================================
def finalise(readiness: Readiness) -> None:
    """Apply the POLICY once every source has been read and coverage assessed."""
    setl = readiness.sources.get("razorpay_settlements.csv")

    if setl is None or not setl.present:
        readiness.refuse("the Razorpay settlement report was not supplied — there "
                         "is nothing authoritative to reconcile against")
    else:
        if setl.columns_missing:
            readiness.refuse(
                "the settlement report is missing required column(s): "
                + ", ".join(setl.columns_missing))
        if setl.columns_unrecognised:
            readiness.refuse(
                "the settlement report has unrecognised column(s): "
                + ", ".join(setl.columns_unrecognised)
                + " — add them to the alias map or remove them; a guessed "
                "column is not acceptable in a finance tool")
        if setl.rejections:
            n = len(setl.rejections)
            readiness.refuse(
                f"{n} row(s) of the settlement report could not be read — "
                "the first is: " + setl.rejections[0].describe())
        if setl.present and setl.rows_read == 0 and not setl.rejections:
            readiness.refuse("the settlement report contains no data rows")

    for name, s in readiness.sources.items():
        if name == "razorpay_settlements.csv" or not s.present:
            if not s.present and s.name != "razorpay_settlements.csv":
                readiness.downgrade(f"{name} was not supplied")
            continue
        if s.rejections:
            readiness.downgrade(
                f"{len(s.rejections)} row(s) of {name} were rejected — first: "
                + s.rejections[0].describe())
        if s.columns_unrecognised:
            readiness.downgrade(
                f"{name} has unrecognised column(s): "
                + ", ".join(s.columns_unrecognised))
        if s.columns_missing:
            readiness.downgrade(
                f"{name} is missing column(s): " + ", ".join(s.columns_missing))

    if readiness.verdict == READY and not readiness.reasons:
        # Say what was actually verified. "Read in full" would mean "every row I
        # was handed, I parsed" -- which is not the same as "I received every row
        # that exists", and stating the stronger thing was a real defect.
        readiness.reasons.append(_READY_NOTE)


# ==========================================================================
# Completeness -- what was owed, not merely what was handed over
# ==========================================================================
def corroborate(readiness: Readiness, corpus) -> None:
    """Cross-check each source against the records that point into it.

    Everything above this line asks "could I read what I was given?". That
    question cannot detect rows that were never supplied: truncating 57% of the
    settlement report parsed cleanly, reported READY, and *raised* the proof
    rate, because the missing rows took their unproven lines with them.

    The signal is independence. The bank statement is produced by a different
    party than the settlement report, so a credit the settlement report cannot
    explain is evidence about that report -- and whether the cause is missing
    rows or a genuine orphan credit, the user must be told before trusting the
    close, and the required action is the same either way. So this does not try
    to distinguish them. It states the fact.

    No thresholds. A world with no defects planted produces exactly zero on
    every count below, so "any at all" is a sound rule and there is no invented
    cut-off to argue about later.

    Must run AFTER `ingest.resolve()`, which is what writes `settlement_id` onto
    each bank row. `corpus` is duck-typed rather than imported: `ingest` imports
    this module, so a type import here would be circular.

    Downgrades only, never refuses. A short file is a reason to distrust a
    close, not always a reason to refuse to produce one -- and the caller
    decides which.
    """
    def _flag(reason: str) -> None:
        """Downgrade, retracting the all-clear note the first time."""
        if _READY_NOTE in readiness.reasons:
            readiness.reasons.remove(_READY_NOTE)
        readiness.downgrade(reason)

    # 1. Money reached the bank that the settlement report does not account for.
    unexplained = [b for b in corpus.bank if not getattr(b, "settlement_id", None)]
    if unexplained:
        amt = sum(b.credit for b in unexplained)
        _flag(
            f"{len(unexplained)} bank credit(s) totalling {_rupees(amt)} have no "
            "matching entry in the settlement report -- either settlement rows "
            "are missing from what was supplied, or these credits are orphans; "
            "both need answering before this close can be relied on")

    # 2. The mirror: settlements the bank never confirms. Catches a truncated
    #    bank statement, which check 1 cannot see -- nothing references a bank row.
    credited = {b.settlement_id for b in corpus.bank if getattr(b, "settlement_id", None)}
    uncredited = [sid for sid in corpus.batches if sid not in credited]
    if uncredited:
        _flag(
            f"{len(uncredited)} settlement batch(es) have no corresponding bank "
            "credit -- either the bank statement is incomplete, or the money "
            "never arrived")

    # 3. Settlement lines pointing at orders that were not supplied.
    missing_orders = {
        ln.order_id for b in corpus.batches.values() for ln in b.lines
        if ln.order_id not in corpus.orders
    }
    if missing_orders:
        _flag(
            f"{len(missing_orders)} order(s) referenced by the settlement report "
            "are absent from the orders export -- the orders file does not cover "
            "everything that was settled")

    # 4. COD remittances pointing at shipments that were not supplied.
    missing_awbs = {r.awb for r in corpus.cod if r.awb not in corpus.shipments}
    if missing_awbs:
        _flag(
            f"{len(missing_awbs)} AWB(s) in the COD remittance file are absent "
            "from the shipment manifest -- the manifest does not cover "
            "everything that was remitted")

    # HONEST LIMITATION, deliberately recorded rather than hidden: this census
    # only sees a source that something else points INTO. Truncating
    # cod_remittances.csv, refunds.csv or disputes.csv is invisible here,
    # because nothing in the corpus holds a reference to those rows. Refunds and
    # disputes do surface indirectly as REFUND_MISMATCH and CHARGEBACK_ORPHAN,
    # but those are ambiguous with real defects. A short COD remittance file is
    # currently undetectable. Do not describe this function as complete.


def _rupees(paise: int) -> str:
    """Local formatter -- `money` must not be imported here (circular)."""
    neg = paise < 0
    whole, frac = divmod(abs(int(paise)), 100)
    s = str(whole)
    if len(s) > 3:                      # Indian grouping: 12,34,567
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:]); head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return f"{'-' if neg else ''}\u20b9{s}.{frac:02d}"
