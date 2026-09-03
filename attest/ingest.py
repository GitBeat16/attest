"""Load the source documents and resolve keys.

Two jobs, and the second one is where most reconciliations quietly go wrong.

LOADING is mechanical: parse decimal rupee strings back into integer paise the
moment they cross the boundary, and never let a float touch the arithmetic.

KEY RESOLUTION is the interesting part. A bank statement does not contain
Razorpay's settlement_id -- it contains a UTR issued by the correspondent bank.
The UTR looks like a key. It is not one. A matcher that trusts it produces
confident wrong answers, which is worse than producing none. So resolution runs
against the authoritative identifier and treats narration as a hint at best.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path


def _paise(s: str | None) -> int:
    if s is None or s == "":
        return 0
    return int((Decimal(s) * 100).to_integral_value())


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


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

    def record_count(self) -> int:
        return (
            len(self.settlements) + len(self.bank) + len(self.orders)
            + len(self.cod) + len(self.shipments) + len(self.refunds)
            + len(self.disputes)
        )


# ==========================================================================
# Loading
# ==========================================================================
def load(src: Path) -> Corpus:
    def rows(name):
        with (src / name).open(encoding="utf-8") as f:
            return list(csv.DictReader(f))

    settlements = [
        SettlementRow(
            settlement_id=r["settlement_id"],
            settled_on=_d(r["settled_on"]),
            payment_id=r["payment_id"],
            order_id=r["order_id"],
            gross=_paise(r["gross_amount"]),
            mdr=_paise(r["mdr"]),
            gst_on_mdr=_paise(r["gst_on_mdr"]),
            net=_paise(r["net_amount"]),
            row_type=r["row_type"],
        )
        for r in rows("razorpay_settlements.csv")
    ]

    batches: dict[str, Batch] = {}
    for r in settlements:
        b = batches.setdefault(
            r.settlement_id, Batch(r.settlement_id, r.settled_on)
        )
        if r.row_type == "captured":
            b.lines.append(r)
        elif r.row_type == "refund_adjustment":
            b.refund_adj += r.net
        elif r.row_type == "chargeback":
            b.chargeback_adj += r.net
        elif r.row_type == "hold_release":
            b.hold_release += r.net

    bank = [
        BankRow(
            row_id=f"BNK{i:05d}",
            value_date=_d(r["value_date"]),
            narration=r["narration"],
            utr=r["utr"],
            credit=_paise(r["credit_amount"]),
        )
        for i, r in enumerate(rows("bank_statement.csv"))
    ]

    orders = {
        r["order_id"]: OrderRow(
            order_id=r["order_id"], placed_on=_d(r["placed_on"]),
            gross=_paise(r["gross_amount"]), channel=r["channel"],
            payment_mode=r["payment_mode"],
        )
        for r in rows("orders.csv")
    }

    cod = [
        CodRow(
            remittance_id=r["remittance_id"], remitted_on=_d(r["remitted_on"]),
            awb=r["awb"], cod_value=_paise(r["cod_value"]),
            cod_fee=_paise(r["cod_fee"]), rto_freight=_paise(r["rto_freight"]),
            adjustment=_paise(r["adjustment"]), net=_paise(r["net_remitted"]),
        )
        for r in rows("cod_remittances.csv")
    ]

    shipments = {
        r["awb"]: ShipmentRow(
            awb=r["awb"], order_id=r["order_id"], shipped_on=_d(r["shipped_on"]),
            delivered_on=_d(r["delivered_on"]) if r["delivered_on"] else None,
            cod_value=_paise(r["cod_value"]), status=r["status"],
        )
        for r in rows("shipments.csv")
    }

    refunds = [
        RefundRow(
            refund_id=r["refund_id"], payment_id=r["payment_id"],
            order_id=r["order_id"], initiated_on=_d(r["initiated_on"]),
            amount=_paise(r["amount"]),
        )
        for r in rows("refunds.csv")
    ]

    disputes = [
        DisputeRow(
            dispute_id=r["dispute_id"], payment_id=r["payment_id"],
            order_id=r["order_id"], raised_on=_d(r["raised_on"]),
            amount=_paise(r["amount"]),
        )
        for r in rows("disputes.csv")
    ]

    return Corpus(
        settlements=settlements,
        batches=batches,
        bank=bank,
        orders=orders,
        cod=cod,
        shipments=shipments,
        refunds=refunds,
        disputes=disputes,
        mdr_invoice=json.loads((src / "razorpay_mdr_invoice.json").read_text()),
        terms=json.loads((src / "contract_terms.json").read_text()),
    )


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
