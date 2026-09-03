"""Generate the TRUE world for one merchant-month.

The construction principle for the whole project: build the truth first, derive
every document from it, and only then inject defects into the documents. Because
the truth exists before the system does, every accuracy number Attest reports is
measured rather than asserted -- and the agent provably never sees the labels.

Nothing in this module is defective. Defects live in defects.py.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from decimal import Decimal

from .money import rupees, pct

# --------------------------------------------------------------------------
# Contracted commercial terms. The agent is told these; the documents may
# silently disagree with them, which is the point.
# --------------------------------------------------------------------------
MDR_RATE = Decimal("2.00")        # % of gross, per Razorpay contract
GST_RATE = Decimal("18.00")       # % of MDR
COD_FEE_RATE = Decimal("1.50")    # % of COD value, per courier contract
RTO_FREIGHT = rupees(85)          # per RTO'd shipment
PERIOD = date(2026, 8, 1)         # the month being closed
DAYS_IN_PERIOD = 31

CHANNELS = ["web", "instagram", "marketplace"]
METHODS = ["upi", "card", "netbanking", "wallet"]


def _business_days_after(d: date, n: int) -> date:
    """Add n business days (Mon-Fri). Settlement cycles run on business days."""
    cur = d
    added = 0
    while added < n:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur


# --------------------------------------------------------------------------
# Entities
# --------------------------------------------------------------------------
@dataclass
class Order:
    order_id: str
    placed_on: date
    gross: int                 # paise
    channel: str
    payment_mode: str          # "prepaid" | "cod"


@dataclass
class Payment:
    payment_id: str
    order_id: str
    captured_on: date
    gross: int
    method: str
    mdr: int
    gst_on_mdr: int
    net: int


@dataclass
class Refund:
    refund_id: str
    payment_id: str
    order_id: str
    initiated_on: date
    amount: int
    deducted_in_settlement: str | None = None   # set during settlement build


@dataclass
class SettlementLine:
    settlement_id: str
    payment_id: str
    order_id: str
    gross: int
    mdr: int
    gst_on_mdr: int
    refund_adj: int            # negative when a refund is netted against this batch
    net: int


@dataclass
class Settlement:
    settlement_id: str
    settled_on: date
    lines: list[SettlementLine] = field(default_factory=list)
    refund_deductions: int = 0
    chargeback_deductions: int = 0
    hold_release: int = 0

    @property
    def net_credit(self) -> int:
        return (
            sum(l.net for l in self.lines)
            - self.refund_deductions
            - self.chargeback_deductions
            + self.hold_release
        )


@dataclass
class BankCredit:
    utr: str
    narration: str
    amount: int
    credited_on: date
    settlement_id: str | None   # the true link; documents may obscure it


@dataclass
class Shipment:
    awb: str
    order_id: str
    shipped_on: date
    delivered_on: date | None
    cod_value: int
    status: str                # "delivered" | "rto"


@dataclass
class CodRemittanceLine:
    remittance_id: str
    awb: str
    cod_value: int
    cod_fee: int
    rto_freight: int
    adjustment: int
    net: int


@dataclass
class CodRemittance:
    remittance_id: str
    remitted_on: date
    lines: list[CodRemittanceLine] = field(default_factory=list)

    @property
    def net_credit(self) -> int:
        return sum(l.net for l in self.lines)


@dataclass
class Chargeback:
    dispute_id: str
    payment_id: str
    order_id: str
    raised_on: date
    amount: int


@dataclass
class World:
    orders: list[Order]
    payments: list[Payment]
    refunds: list[Refund]
    settlements: list[Settlement]
    bank_credits: list[BankCredit]
    shipments: list[Shipment]
    cod_remittances: list[CodRemittance]
    chargebacks: list[Chargeback]

    # ---- truth aggregates the agent must independently arrive at ----
    @property
    def total_mdr(self) -> int:
        return sum(p.mdr for p in self.payments)

    @property
    def total_gst_on_mdr(self) -> int:
        return sum(p.gst_on_mdr for p in self.payments)

    @property
    def gateway_net(self) -> int:
        return sum(s.net_credit for s in self.settlements)

    @property
    def cod_net(self) -> int:
        return sum(r.net_credit for r in self.cod_remittances)

    def summary(self) -> dict:
        return {
            "period": PERIOD.strftime("%Y-%m"),
            "orders": len(self.orders),
            "prepaid_orders": sum(1 for o in self.orders if o.payment_mode == "prepaid"),
            "cod_orders": sum(1 for o in self.orders if o.payment_mode == "cod"),
            "payments": len(self.payments),
            "refunds": len(self.refunds),
            "settlements": len(self.settlements),
            "settlement_lines": sum(len(s.lines) for s in self.settlements),
            "bank_credits": len(self.bank_credits),
            "shipments": len(self.shipments),
            "cod_remittances": len(self.cod_remittances),
            "cod_remittance_lines": sum(len(r.lines) for r in self.cod_remittances),
            "chargebacks": len(self.chargebacks),
            "gross_order_value_paise": sum(o.gross for o in self.orders),
            "total_mdr_paise": self.total_mdr,
            "total_gst_on_mdr_paise": self.total_gst_on_mdr,
            "gateway_net_paise": self.gateway_net,
            "cod_net_paise": self.cod_net,
        }


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------
def _aov(rng: random.Random) -> int:
    """Realistic Indian D2C order value: lognormal-ish, floor ~₹399, long tail."""
    v = rng.lognormvariate(7.15, 0.55)      # centred around ~₹1,270
    return rupees(round(max(399.0, min(v, 14999.0)), 2))


def generate_world(seed: int = 20260801, n_orders: int = 620) -> World:
    rng = random.Random(seed)

    orders: list[Order] = []
    payments: list[Payment] = []
    refunds: list[Refund] = []
    shipments: list[Shipment] = []
    chargebacks: list[Chargeback] = []

    # ---- orders -----------------------------------------------------------
    for i in range(n_orders):
        day = PERIOD + timedelta(days=rng.randrange(DAYS_IN_PERIOD))
        mode = "cod" if rng.random() < 0.64 else "prepaid"   # India D2C reality
        orders.append(
            Order(
                order_id=f"ORD{20260800 + i:08d}",
                placed_on=day,
                gross=_aov(rng),
                channel=rng.choices(CHANNELS, weights=[62, 18, 20])[0],
                payment_mode=mode,
            )
        )
    orders.sort(key=lambda o: (o.placed_on, o.order_id))

    # ---- prepaid leg: payments -------------------------------------------
    for o in (x for x in orders if x.payment_mode == "prepaid"):
        mdr = pct(o.gross, MDR_RATE)
        gst = pct(mdr, GST_RATE)
        payments.append(
            Payment(
                payment_id=f"pay_{rng.getrandbits(56):014x}",
                order_id=o.order_id,
                captured_on=o.placed_on,
                gross=o.gross,
                method=rng.choices(METHODS, weights=[58, 26, 10, 6])[0],
                mdr=mdr,
                gst_on_mdr=gst,
                net=o.gross - mdr - gst,
            )
        )

    # ---- refunds: ~6% of prepaid, initiated a few days after capture ------
    for p in payments:
        if rng.random() < 0.06:
            init = p.captured_on + timedelta(days=rng.randrange(1, 9))
            if init < PERIOD + timedelta(days=DAYS_IN_PERIOD):
                refunds.append(
                    Refund(
                        refund_id=f"rfnd_{rng.getrandbits(56):014x}",
                        payment_id=p.payment_id,
                        order_id=p.order_id,
                        initiated_on=init,
                        amount=p.gross,
                    )
                )

    # ---- chargebacks: rare ------------------------------------------------
    for p in payments:
        if rng.random() < 0.025:
            chargebacks.append(
                Chargeback(
                    dispute_id=f"disp_{rng.getrandbits(48):012x}",
                    payment_id=p.payment_id,
                    order_id=p.order_id,
                    raised_on=p.captured_on + timedelta(days=rng.randrange(6, 20)),
                    amount=p.gross,
                )
            )

    # ---- settlements: T+2 business days, one batch per settlement date ----
    by_date: dict[date, list[Payment]] = {}
    for p in payments:
        by_date.setdefault(_business_days_after(p.captured_on, 2), []).append(p)

    settlements: list[Settlement] = []
    for idx, sd in enumerate(sorted(by_date)):
        s = Settlement(settlement_id=f"setl_{rng.getrandbits(56):014x}", settled_on=sd)
        for p in by_date[sd]:
            s.lines.append(
                SettlementLine(
                    settlement_id=s.settlement_id,
                    payment_id=p.payment_id,
                    order_id=p.order_id,
                    gross=p.gross,
                    mdr=p.mdr,
                    gst_on_mdr=p.gst_on_mdr,
                    refund_adj=0,
                    net=p.net,
                )
            )
        settlements.append(s)

    # Refunds are NOT paid out separately -- they reduce a LATER batch,
    # typically 5 business days on. This timing gap is the single most common
    # source of false exceptions in real reconciliation.
    for r in refunds:
        target_date = _business_days_after(r.initiated_on, 5)
        batch = next((s for s in settlements if s.settled_on >= target_date), None)
        if batch:
            batch.refund_deductions += r.amount
            r.deducted_in_settlement = batch.settlement_id

    for cb in chargebacks:
        batch = next((s for s in settlements if s.settled_on >= cb.raised_on), None)
        if batch:
            batch.chargeback_deductions += cb.amount

    # ---- bank credits: one NEFT per settlement ---------------------------
    bank_credits = [
        BankCredit(
            utr=f"N{rng.randrange(10**11, 10**12)}",
            narration=(
                f"NEFT CR-HDFC0000060-RAZORPAY SOFTWARE PVT LTD-"
                f"ACME ATHLEISURE-N{rng.randrange(10**11, 10**12)}"
            ),
            amount=s.net_credit,
            credited_on=s.settled_on,
            settlement_id=s.settlement_id,
        )
        for s in settlements
        if s.net_credit > 0
    ]

    # ---- COD leg: shipments ----------------------------------------------
    for o in (x for x in orders if x.payment_mode == "cod"):
        shipped = o.placed_on + timedelta(days=rng.randrange(0, 3))
        is_rto = rng.random() < 0.27              # ~27% RTO, realistic for D2C
        delivered = None if is_rto else shipped + timedelta(days=rng.randrange(2, 7))
        shipments.append(
            Shipment(
                awb=f"AWB{rng.randrange(10**11, 10**12)}",
                order_id=o.order_id,
                shipped_on=shipped,
                delivered_on=delivered,
                cod_value=0 if is_rto else o.gross,
                status="rto" if is_rto else "delivered",
            )
        )

    # ---- COD remittances: weekly, covering deliveries 7+ days prior -------
    cod_remittances: list[CodRemittance] = []
    cursor = PERIOD + timedelta(days=7)
    end = PERIOD + timedelta(days=DAYS_IN_PERIOD + 10)
    while cursor <= end:
        window_hi = cursor - timedelta(days=7)
        window_lo = window_hi - timedelta(days=7)
        rem = CodRemittance(
            remittance_id=f"REM{cursor.strftime('%Y%m%d')}", remitted_on=cursor
        )
        for sh in shipments:
            settled_ref = sh.delivered_on if sh.status == "delivered" else sh.shipped_on
            if settled_ref and window_lo <= settled_ref < window_hi:
                fee = pct(sh.cod_value, COD_FEE_RATE) if sh.cod_value else 0
                freight = RTO_FREIGHT if sh.status == "rto" else 0
                rem.lines.append(
                    CodRemittanceLine(
                        remittance_id=rem.remittance_id,
                        awb=sh.awb,
                        cod_value=sh.cod_value,
                        cod_fee=fee,
                        rto_freight=freight,
                        adjustment=0,
                        net=sh.cod_value - fee - freight,
                    )
                )
        if rem.lines:
            cod_remittances.append(rem)
        cursor += timedelta(days=7)

    return World(
        orders=orders,
        payments=payments,
        refunds=refunds,
        settlements=settlements,
        bank_credits=bank_credits,
        shipments=shipments,
        cod_remittances=cod_remittances,
        chargebacks=chargebacks,
    )
