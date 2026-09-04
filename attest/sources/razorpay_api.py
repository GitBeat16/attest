"""Real Razorpay ingestion. No mock data.

Talks to the live Settlement Recon API:

    GET https://api.razorpay.com/v1/settlements/recon/combined?year=YYYY&month=MM

authenticated with HTTP Basic (key_id as username, key_secret as password). Works
against test-mode keys (`rzp_test_...`) and live keys (`rzp_live_...`) without any
code change — the base URL is identical; only the key decides which data you get.

Uses only the standard library, so the zero-dependency guarantee survives.

Two details from the real API that matter, and that a naive integration gets wrong:

  * Amounts are integers in currency subunits — paise. Attest is integer-paise
    throughout, so nothing is converted and no float ever touches the money.

  * Every row carries BOTH `settlement_id` and `settlement_utr`. The UTR is
    issued by the correspondent bank and is not a Razorpay key. Grouping on it
    produces confident wrong batches. This module groups on `settlement_id` and
    keeps the UTR for the audit trail only.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

API_ROOT = "https://api.razorpay.com/v1"
PAGE = 1000                       # max the recon endpoint accepts


class RazorpayError(RuntimeError):
    pass


@dataclass
class ReconRow:
    """One row of the combined recon report, as the API returns it."""
    entity_id: str
    type: str                     # payment | refund | transfer | adjustment
    debit: int
    credit: int | None            # None means the API did not report one; 0 is a value
    amount: int
    currency: str
    fee: int                      # MDR, in paise
    tax: int                      # GST on MDR, in paise
    settled: bool
    on_hold: bool
    created_at: int | None
    settled_at: int | None
    settlement_id: str | None
    settlement_utr: str | None
    payment_id: str | None
    order_id: str | None
    order_receipt: str | None
    method: str | None
    dispute_id: str | None
    description: str | None
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def settled_on(self) -> date | None:
        if not self.settled_at:
            return None
        return datetime.fromtimestamp(self.settled_at, tz=timezone.utc).date()

    @property
    def created_on(self) -> date | None:
        if not self.created_at:
            return None
        return datetime.fromtimestamp(self.created_at, tz=timezone.utc).date()

    @property
    def net(self) -> int:
        """What actually reached the merchant for this row.

        The API exposes `credit` directly, but we recompute from amount minus fee
        minus tax and compare. A disagreement is not something to smooth over --
        it is exactly the class of finding this whole system exists to surface, so
        it is reported rather than silently trusted.

        The `is None` test is load-bearing. A credit of exactly zero is a real
        and common value -- a fully refunded payment, an adjustment that nets
        out -- and treating it as "missing" would silently substitute a computed
        figure for a reported one, which is the precise failure this module
        exists to detect. Falsiness is not absence.
        """
        if self.credit is None:
            return self.amount - self.fee - self.tax
        return self.credit

    @property
    def net_disagrees_by(self) -> int:
        """Zero when Razorpay reported no credit -- there is nothing to disagree
        with. A reported zero that differs from amount - fee - tax is a genuine
        disagreement and is returned as one."""
        if self.credit is None:
            return 0
        return self.credit - (self.amount - self.fee - self.tax)


@dataclass
class ReconBatch:
    settlement_id: str
    settled_on: date | None
    utr: str | None
    payments: list[ReconRow] = field(default_factory=list)
    refunds: list[ReconRow] = field(default_factory=list)
    adjustments: list[ReconRow] = field(default_factory=list)

    @property
    def expected_credit(self) -> int:
        return (
            sum(r.net for r in self.payments)
            - sum(r.amount for r in self.refunds)
            # r.net falls back to amount - fee - tax only when the API reported
            # no credit at all; a reported zero stays a zero.
            + sum(r.net - r.debit for r in self.adjustments)
        )

    @property
    def total_fee(self) -> int:
        return sum(r.fee for r in self.payments)

    @property
    def total_tax(self) -> int:
        return sum(r.tax for r in self.payments)


class RazorpayClient:
    """Minimal, dependency-free Razorpay REST client."""

    def __init__(self, key_id: str, key_secret: str, timeout: int = 30):
        if not key_id or not key_secret:
            raise RazorpayError(
                "Missing credentials. Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET "
                "in .env — get test keys free at dashboard.razorpay.com "
                "(Settings > API Keys, with the dashboard in Test Mode)."
            )
        self.key_id = key_id
        self.timeout = timeout
        token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        self._auth = f"Basic {token}"

    @property
    def mode(self) -> str:
        return "test" if self.key_id.startswith("rzp_test") else "live"

    def _get(self, path: str, params: dict) -> dict:
        url = f"{API_ROOT}{path}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={
            "Authorization": self._auth,
            "Accept": "application/json",
            "User-Agent": "attest/1.0",
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:400]
            if e.code == 401:
                raise RazorpayError(
                    "Razorpay rejected the credentials (401). Check the key id and "
                    "secret, and that both come from the same mode."
                ) from e
            raise RazorpayError(f"Razorpay API {e.code} on {path}: {body}") from e
        except urllib.error.URLError as e:
            raise RazorpayError(f"Could not reach Razorpay: {e.reason}") from e

    def fetch_recon(self, year: int, month: int) -> list[ReconRow]:
        """Every recon row for a month, paginated to exhaustion."""
        rows: list[ReconRow] = []
        skip = 0
        while True:
            payload = self._get("/settlements/recon/combined", {
                "year": year, "month": f"{month:02d}", "count": PAGE, "skip": skip,
            })
            items = payload.get("items", [])
            rows.extend(_row(i) for i in items)
            if len(items) < PAGE:
                break
            skip += PAGE
        return rows

    def ping(self) -> dict:
        """Cheap credential check before a long pull."""
        today = date.today()
        self._get("/settlements/recon/combined", {
            "year": today.year, "month": f"{today.month:02d}", "count": 1,
        })
        return {"ok": True, "mode": self.mode, "key_id": self.key_id}


def _row(i: dict) -> ReconRow:
    return ReconRow(
        entity_id=i.get("entity_id", ""),
        type=i.get("type", ""),
        debit=int(i.get("debit") or 0),
        credit=(int(i["credit"]) if i.get("credit") is not None else None),
        amount=int(i.get("amount") or 0),
        currency=i.get("currency", "INR"),
        fee=int(i.get("fee") or 0),
        tax=int(i.get("tax") or 0),
        settled=bool(i.get("settled")),
        on_hold=bool(i.get("on_hold")),
        created_at=i.get("created_at"),
        settled_at=i.get("settled_at"),
        settlement_id=i.get("settlement_id"),
        settlement_utr=i.get("settlement_utr"),
        payment_id=i.get("payment_id"),
        order_id=i.get("order_id"),
        order_receipt=i.get("order_receipt"),
        method=i.get("method"),
        dispute_id=i.get("dispute_id"),
        description=i.get("description"),
        raw=i,
    )


# --------------------------------------------------------------------------
def group_batches(rows: list[ReconRow]) -> tuple[dict[str, ReconBatch], list[ReconRow]]:
    """Group on settlement_id. Never on settlement_utr.

    Returns (batches, unsettled) — rows with no settlement_id have not been paid
    out yet and belong to a future period, not to an exception.
    """
    batches: dict[str, ReconBatch] = {}
    unsettled: list[ReconRow] = []

    for r in rows:
        if not r.settlement_id:
            unsettled.append(r)
            continue
        b = batches.get(r.settlement_id)
        if b is None:
            b = ReconBatch(r.settlement_id, r.settled_on, r.settlement_utr)
            batches[r.settlement_id] = b
        if b.utr is None:
            b.utr = r.settlement_utr
        if r.type == "payment":
            b.payments.append(r)
        elif r.type == "refund":
            b.refunds.append(r)
        else:
            b.adjustments.append(r)
    return batches, unsettled


def integrity_report(rows: list[ReconRow], batches: dict[str, ReconBatch]) -> dict:
    """What the live data itself says, before any reconciliation runs.

    Deliberately reported rather than smoothed over: if Razorpay's own `credit`
    disagrees with amount - fee - tax, that is a finding, not a rounding artefact.
    """
    disagreements = [r for r in rows if r.net_disagrees_by]
    utrs = {b.utr for b in batches.values() if b.utr}
    return {
        "rows": len(rows),
        "batches": len(batches),
        "distinct_utrs": len(utrs),
        "utr_collisions": len(batches) - len(utrs),
        "rows_where_credit_disagrees": len(disagreements),
        "disagreement_paise": sum(abs(r.net_disagrees_by) for r in disagreements),
        "on_hold_rows": sum(1 for r in rows if r.on_hold),
        "unsettled_rows": sum(1 for r in rows if not r.settlement_id),
        "total_fee_paise": sum(r.fee for r in rows),
        "total_tax_paise": sum(r.tax for r in rows),
    }
