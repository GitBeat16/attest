"""Turn exceptions into recoverable money with a clock on it.

This is the module that separates a reconciliation report from a product.

Finding a variance is not the same as recovering it. Almost every finding here is
claimable against a counterparty -- Razorpay for a fee applied off-contract, the
courier for a short remittance, the bank for a credit that never landed -- and
almost every one of those claims has a window that closes. Courier disputes
typically die at 7-14 days. A month-end close surfaces a day-8 finding on day 31,
by which time the money is gone and the report is an obituary.

So each exception is assigned a counterparty, a claim window measured from the
event that started the clock, and a state. What the operator sees is not a list of
problems; it is a list of money, sorted by how soon it stops being recoverable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

# Claim windows in days, measured from the event that starts the clock.
# Courier windows are the tight ones and the reason this module exists.
WINDOWS = {
    "ADJUSTMENT":               ("courier",   14),   # COD short remittance
    "COD_FEE":                  ("courier",   14),
    "FREIGHT":                  ("courier",   14),
    "COD_VALUE":                ("courier",   14),
    "DUPLICATE_AWB":            ("courier",   14),
    "MDR":                      ("razorpay",  60),   # fee applied off-contract
    "GST":                      ("razorpay",  60),
    "SELF_REFERENTIAL_TIE":     ("razorpay",  60),
    "OFFSETTING_PAIR":          ("razorpay",  60),
    "TOLERANCE_ABUSE":          ("razorpay",  60),
    "REFUND_MISMATCH":          ("razorpay",  60),
    "DUPLICATE_SETTLEMENT_LINE":("razorpay",  60),
    "CREDIT":                   ("bank",      90),   # settlement never landed
    "MISSING_CREDIT":           ("bank",      90),
    "ORPHAN_BANK_CREDIT":       ("bank",      90),
    "CHARGEBACK_ORPHAN":        ("razorpay",  45),
    "UNREFERENCED_ADJ":         ("razorpay",  45),
}

# Classes where the money is genuinely gone or was never ours -- reporting them
# as "recoverable" would be a lie the operator would discover the hard way.
NOT_RECOVERABLE = {"ORDER", "NET", "SHIPMENT", "OUT_OF_PERIOD_SETTLEMENT"}


@dataclass
class Claim:
    exception_class: str
    counterparty: str
    exposure: int
    count: int
    opened_on: date
    deadline: date
    evidence_required: str
    state: str = "open"          # open | evidence_ready | filed | recovered | lapsed
    sample: list = field(default_factory=list)

    def days_left(self, today: date) -> int:
        return (self.deadline - today).days

    def urgency(self, today: date) -> str:
        d = self.days_left(today)
        if d < 0:
            return "lapsed"
        if d <= 3:
            return "critical"
        if d <= 7:
            return "urgent"
        return "open"


def build_claims(exceptions: list[dict], period_end: date, today: date) -> list[Claim]:
    """Attach a counterparty, a window and a clock to every exception."""
    claims: list[Claim] = []
    for e in exceptions:
        cls = e["class"]
        if cls in NOT_RECOVERABLE:
            continue
        counterparty, window = WINDOWS.get(cls, ("razorpay", 60))

        # The clock starts at the event, not at the close. This is the whole
        # point: a courier claim found on day 31 of a 14-day window is already
        # dead, and the system must say so rather than list it hopefully.
        opened = e.get("occurred_on") or period_end
        if isinstance(opened, str):
            opened = date.fromisoformat(opened)

        claims.append(Claim(
            exception_class=cls,
            counterparty=counterparty,
            exposure=e["exposure"],
            count=e["count"],
            opened_on=opened,
            deadline=opened + timedelta(days=window),
            evidence_required=e["evidence_required"],
            sample=e.get("sample", []),
        ))

    # Sorted by how soon the money stops being recoverable, then by size.
    claims.sort(key=lambda c: (c.deadline, -c.exposure))
    return claims


def summarise(claims: list[Claim], today: date) -> dict:
    live = [c for c in claims if c.days_left(today) >= 0]
    lapsed = [c for c in claims if c.days_left(today) < 0]
    critical = [c for c in live if c.urgency(today) in ("critical", "urgent")]

    by_party: dict[str, int] = {}
    for c in live:
        by_party[c.counterparty] = by_party.get(c.counterparty, 0) + c.exposure

    return {
        "recoverable": sum(c.exposure for c in live),
        "recoverable_count": sum(c.count for c in live),
        "expiring_soon": sum(c.exposure for c in critical),
        "expiring_count": sum(c.count for c in critical),
        "lapsed": sum(c.exposure for c in lapsed),
        "lapsed_count": sum(c.count for c in lapsed),
        "by_counterparty": by_party,
        "next_deadline": min((c.deadline for c in live), default=None),
    }
