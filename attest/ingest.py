"""Load the source documents and resolve keys.

Three jobs now, and the first one is new.

READINESS is the gate. `readiness.py` reads every source through an explicit
column-alias map, rejects — never coerces — a row it cannot parse, and compares
the range actually covered with the declared month. The close is REFUSED if the
settlement report could not be read in full, marked PARTIAL if anything else is
incomplete, and READY only when every supplied row parsed. See `readiness.py`.

LOADING is mechanical: parse decimal rupee strings back into integer paise the
moment they cross the boundary, and never let a float touch the arithmetic. That
parsing now lives in `readiness.parse_money`, which handles the shapes a real
export actually uses (`₹1,23,456.78`, `1,234.00 Dr`, an accounting negative).

KEY RESOLUTION is the interesting part. A bank statement does not contain
Razorpay's settlement_id -- it contains a UTR issued by the correspondent bank.
The UTR looks like a key. It is not one. A matcher that trusts it produces
confident wrong answers, which is worse than producing none. So resolution runs
against the authoritative identifier and treats narration as a hint at best.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import readiness as rdy
from .readiness import MISSING, Readiness, assess_coverage, finalise, read_table


def _p(v) -> int:
    """A parsed money value from readiness -> integer paise. A blank cell is 0
    here: on an adjustment/hold row the amount columns are genuinely absent, and
    that has always meant zero. A *malformed* value never reaches this point --
    read_table rejected the row."""
    return 0 if v is MISSING or v is None else int(v)


def _dt(v) -> date | None:
    return v if isinstance(v, date) else None


def _s(v) -> str:
    return "" if v is MISSING or v is None else str(v)


# ==========================================================================
# Typed rows, one per source
# ==========================================================================
@dataclass
class SettlementRow:
    settlement_id: str
    settled_on: date
    payment_id: str
    order_id: str
    gross: int
    mdr: int
    gst_on_mdr: int
    net: int
    row_type: str          # captured | refund_adjustment | chargeback | hold_release


@dataclass
class BankRow:
    row_id: str
    value_date: date
    narration: str
    utr: str
    credit: int
    # filled in by resolution -- never present in the source
    settlement_id: str | None = None
    resolution: str = "unresolved"


@dataclass
class OrderRow:
    order_id: str
    placed_on: date
    gross: int
    channel: str
    payment_mode: str


@dataclass
class CodRow:
    remittance_id: str
    remitted_on: date
    awb: str
    cod_value: int
    cod_fee: int
    rto_freight: int
    adjustment: int
    net: int


@dataclass
class ShipmentRow:
    awb: str
    order_id: str
    shipped_on: date
    delivered_on: date | None
    cod_value: int
    status: str


@dataclass
class RefundRow:
    refund_id: str
    payment_id: str
    order_id: str
    initiated_on: date
    amount: int


@dataclass
class DisputeRow:
    dispute_id: str
    payment_id: str
    order_id: str
    raised_on: date
    amount: int


@dataclass
class Batch:
    """A settlement batch reassembled from the transaction-level export."""
    settlement_id: str
    settled_on: date
    lines: list[SettlementRow] = field(default_factory=list)
    refund_adj: int = 0
    chargeback_adj: int = 0
    hold_release: int = 0

    @property
    def expected_credit(self) -> int:
        """What the bank should have credited, per the settlement report itself."""
        return (
            sum(l.net for l in self.lines)
            + self.refund_adj          # already negative in the export
            + self.chargeback_adj
            + self.hold_release
        )


@dataclass
class Corpus:
    settlements: list[SettlementRow]
    batches: dict[str, Batch]
    bank: list[BankRow]
    orders: dict[str, OrderRow]
    cod: list[CodRow]
    shipments: dict[str, ShipmentRow]
    refunds: list[RefundRow]
    disputes: list[DisputeRow]
    mdr_invoice: dict
    terms: dict
    readiness: Readiness = field(default_factory=Readiness)

    def record_count(self) -> int:
        """Rows that became records. See `rows_in()` for the denominator."""
        return (
            len(self.settlements) + len(self.bank) + len(self.orders)
            + len(self.cod) + len(self.shipments) + len(self.refunds)
            + len(self.disputes)
        )

    def rows_in(self) -> int:
        """Data rows physically present across the CSV sources, rejected ones
        included. `record_count()` over `rows_in()` is the honest throughput."""
        return self.readiness.rows_in()


# ==========================================================================
# Loading
# ==========================================================================
def _read_json(path: Path, ready: Readiness, label: str, required: bool):
    if not path.exists():
        if required:
            ready.refuse(f"{path.name} was not supplied — {label}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, UnicodeError) as e:
        msg = f"{path.name} is not valid JSON ({e})"
        ready.refuse(msg) if required else ready.downgrade(msg)
        return {}


def load(src: Path) -> Corpus:
    src = Path(src)
    ready = Readiness()

    terms = _read_json(src / "contract_terms.json", ready, "the contracted rates "
                       "and tolerances live here", required=True)
    mdr_invoice = _read_json(src / "razorpay_mdr_invoice.json", ready,
                             "the declared invoice month is the period anchor",
                             required=True)
    ready.declared_period = str(mdr_invoice.get("period", ""))
    declared_date_format = terms.get("date_format")

    def table(name: str):
        spec = rdy.SPECS[name]
        rows, sr = read_table(src / name, spec, declared_date_format)
        ready.sources[name] = sr
        return rows

    # ---- settlements -> batches ----------------------------------------
    settlements: list[SettlementRow] = []
    for r in table("razorpay_settlements.csv"):
        settlements.append(SettlementRow(
            settlement_id=_s(r["settlement_id"]),
            settled_on=_dt(r["settled_on"]),
            payment_id=_s(r["payment_id"]),
            order_id=_s(r["order_id"]),
            gross=_p(r["gross"]),
            mdr=_p(r["mdr"]),
            gst_on_mdr=_p(r["gst_on_mdr"]),
            net=_p(r["net"]),
            row_type=_s(r["row_type"]) or "captured",
        ))

    batches: dict[str, Batch] = {}
    for r in settlements:
        b = batches.setdefault(r.settlement_id, Batch(r.settlement_id, r.settled_on))
        if r.row_type == "captured":
            b.lines.append(r)
        elif r.row_type == "refund_adjustment":
            b.refund_adj += r.net
        elif r.row_type == "chargeback":
            b.chargeback_adj += r.net
        elif r.row_type == "hold_release":
            b.hold_release += r.net

    # ---- bank statement ----------------------------------------------
    # `debit_amount` is now read. A debit row (money out: a fee, a transfer,
    # a reversal) is NOT a settlement credit, and treating its blank credit
    # column as ₹0 used to turn every one of them into a phantom
    # ORPHAN_BANK_CREDIT. Debit-only rows are counted and set aside.
    bank: list[BankRow] = []
    debit_rows = 0
    for i, r in enumerate(table("bank_statement.csv")):
        credit_val = r["credit"]
        debit_p = _p(r["debit"])
        if (credit_val is MISSING or _p(credit_val) == 0) and debit_p > 0:
            debit_rows += 1
            continue
        bank.append(BankRow(
            row_id=f"BNK{i:05d}",
            value_date=_dt(r["value_date"]),
            narration=_s(r["narration"]),
            utr=_s(r["utr"]),
            credit=_p(credit_val),
        ))
    if debit_rows:
        ready.sources["bank_statement.csv"].note(
            f"{debit_rows} debit row(s) set aside — money out is not a settlement credit")

    # ---- orders (unique on order_id, enforced in read_table) ---------
    orders: dict[str, OrderRow] = {}
    for r in table("orders.csv"):
        orders[_s(r["order_id"])] = OrderRow(
            order_id=_s(r["order_id"]), placed_on=_dt(r["placed_on"]),
            gross=_p(r["gross"]), channel=_s(r["channel"]),
            payment_mode=_s(r["payment_mode"]),
        )

    # ---- COD remittances -------------------------------------------
    cod = [
        CodRow(
            remittance_id=_s(r["remittance_id"]), remitted_on=_dt(r["remitted_on"]),
            awb=_s(r["awb"]), cod_value=_p(r["cod_value"]), cod_fee=_p(r["cod_fee"]),
            rto_freight=_p(r["rto_freight"]), adjustment=_p(r["adjustment"]),
            net=_p(r["net"]),
        )
        for r in table("cod_remittances.csv")
    ]

    # ---- shipments (unique on awb) --------------------------------
    shipments: dict[str, ShipmentRow] = {}
    for r in table("shipments.csv"):
        shipments[_s(r["awb"])] = ShipmentRow(
            awb=_s(r["awb"]), order_id=_s(r["order_id"]),
            shipped_on=_dt(r["shipped_on"]), delivered_on=_dt(r["delivered_on"]),
            cod_value=_p(r["cod_value"]), status=_s(r["status"]),
        )

    refunds = [
        RefundRow(
            refund_id=_s(r["refund_id"]), payment_id=_s(r["payment_id"]),
            order_id=_s(r["order_id"]), initiated_on=_dt(r["initiated_on"]),
            amount=_p(r["amount"]),
        )
        for r in table("refunds.csv")
    ]

    disputes = [
        DisputeRow(
            dispute_id=_s(r["dispute_id"]), payment_id=_s(r["payment_id"]),
            order_id=_s(r["order_id"]), raised_on=_dt(r["raised_on"]),
            amount=_p(r["amount"]),
        )
        for r in table("disputes.csv")
    ]

    corpus = Corpus(
        settlements=settlements, batches=batches, bank=bank, orders=orders,
        cod=cod, shipments=shipments, refunds=refunds, disputes=disputes,
        mdr_invoice=mdr_invoice, terms=terms, readiness=ready,
    )

    # ---- coverage, then the verdict --------------------------------
    _have_dates = any(o.placed_on for o in orders.values()) or \
        any(b.settled_on for b in batches.values())
    if ready.declared_period and _have_dates:
        assess_coverage(
            ready,
            order_dates=[o.placed_on for o in orders.values() if o.placed_on],
            settlement_dates=[b.settled_on for b in batches.values() if b.settled_on],
            other_dates=(
                [r.initiated_on for r in refunds if r.initiated_on]
                + [d.raised_on for d in disputes if d.raised_on]
            ),
        )
    finalise(ready)
    return corpus


# ==========================================================================
# Key resolution
# ==========================================================================
UTR_RE = re.compile(r"\bN\d{11,12}\b")
SETL_RE = re.compile(r"\bsetl_[0-9a-f]{10,20}\b")


def resolve_naive(corpus: Corpus) -> dict:
    """The wrong way, implemented on purpose.

    Key the bank credit on whatever identifier appears in the narration. This is
    the intuitive approach and it is what a first-pass reconciliation does. We
    run it so the report can show what it costs, rather than merely asserting it.
    """
    matched = 0
    for b in corpus.bank:
        m = SETL_RE.search(b.narration)
        if m and m.group(0) in corpus.batches:
            matched += 1
    return {
        "strategy": "narration/UTR key",
        "bank_rows": len(corpus.bank),
        "resolved": matched,
        "unresolved": len(corpus.bank) - matched,
    }


def resolve(corpus: Corpus, tolerance: int = 100) -> dict:
    """The authoritative way.

    settlement_id is the only real key, and it lives on the Razorpay side. So we
    resolve a bank credit to a batch by (value date, amount) against the
    settlement report, then carry settlement_id forward as the identity for
    everything downstream. The UTR is retained for the audit trail and used for
    nothing else.

    Ambiguity is never broken by guessing. Two batches that could both explain a
    credit produce an escalation, not a coin flip.
    """
    by_id = corpus.batches
    stats = {
        "strategy": "settlement_id via (value_date, amount)",
        "bank_rows": len(corpus.bank),
        "exact": 0, "near": 0, "ambiguous": 0, "unresolved": 0,
    }

    claimed: set[str] = set()
    for b in corpus.bank:
        window = [
            s for s in by_id.values()
            if abs((s.settled_on - b.value_date).days) <= 1
            and s.settlement_id not in claimed
        ]
        exact = [s for s in window if s.expected_credit == b.credit]
        near = [
            s for s in window
            if s not in exact and abs(s.expected_credit - b.credit) <= tolerance
        ]

        if len(exact) == 1:
            b.settlement_id, b.resolution = exact[0].settlement_id, "exact"
            claimed.add(b.settlement_id); stats["exact"] += 1
        elif len(exact) > 1:
            b.resolution = "ambiguous"; stats["ambiguous"] += 1
        elif len(near) == 1:
            b.settlement_id, b.resolution = near[0].settlement_id, "near"
            claimed.add(b.settlement_id); stats["near"] += 1
        elif len(near) > 1:
            b.resolution = "ambiguous"; stats["ambiguous"] += 1
        else:
            b.resolution = "unresolved"; stats["unresolved"] += 1

    stats["settlements_without_credit"] = [
        sid for sid in by_id if sid not in claimed
    ]
    return stats
