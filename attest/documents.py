"""Render a World into the source documents a merchant actually receives.

Amounts are written as decimal rupee strings, because that is how every real
export looks. The ingestion layer has to parse them back to integer paise, which
is exactly the boundary where naive pipelines start manufacturing drift.
"""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from .money import to_rupees
from .world import World, MDR_RATE, GST_RATE, COD_FEE_RATE, PERIOD


def _r(paise: int) -> str:
    return f"{to_rupees(paise):.2f}"


def _write(path: Path, header: list[str], rows: list[list]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return len(rows)


# ==========================================================================
# The five sources the agent is allowed to see
# ==========================================================================
def write_sources(doc: World, out: Path) -> dict[str, int]:
    counts: dict[str, int] = {}

    # 1. Order export (from the merchant's storefront / OMS)
    counts["orders.csv"] = _write(
        out / "orders.csv",
        ["order_id", "placed_on", "gross_amount", "channel", "payment_mode"],
        [
            [o.order_id, o.placed_on.isoformat(), _r(o.gross), o.channel, o.payment_mode]
            for o in doc.orders
        ],
    )

    # 2. Razorpay settlement report, exported at transaction level
    rows = []
    for s in doc.settlements:
        for l in s.lines:
            rows.append([
                s.settlement_id, s.settled_on.isoformat(), l.payment_id, l.order_id,
                _r(l.gross), _r(l.mdr), _r(l.gst_on_mdr), _r(l.net), "captured",
            ])
        # Batch-level adjustments appear as their own rows with no payment_id --
        # exactly as they do in a real export.
        if s.refund_deductions:
            rows.append([s.settlement_id, s.settled_on.isoformat(), "", "",
                         "", "", "", _r(-s.refund_deductions), "refund_adjustment"])
        if s.chargeback_deductions:
            rows.append([s.settlement_id, s.settled_on.isoformat(), "", "",
                         "", "", "", _r(-s.chargeback_deductions), "chargeback"])
        if s.hold_release:
            rows.append([s.settlement_id, s.settled_on.isoformat(), "", "",
                         "", "", "", _r(s.hold_release), "hold_release"])
    counts["razorpay_settlements.csv"] = _write(
        out / "razorpay_settlements.csv",
        ["settlement_id", "settled_on", "payment_id", "order_id",
         "gross_amount", "mdr", "gst_on_mdr", "net_amount", "row_type"],
        rows,
    )

    # 3. Bank statement. Note there is no settlement_id column -- the bank does
    #    not know Razorpay's identifiers. Everything must come out of narration.
    counts["bank_statement.csv"] = _write(
        out / "bank_statement.csv",
        ["value_date", "narration", "utr", "credit_amount", "debit_amount"],
        [
            [bc.credited_on.isoformat(), bc.narration, bc.utr, _r(bc.amount), ""]
            for bc in sorted(doc.bank_credits, key=lambda b: b.credited_on)
        ],
    )

    # 4. Courier COD remittance statement
    counts["cod_remittances.csv"] = _write(
        out / "cod_remittances.csv",
        ["remittance_id", "remitted_on", "awb", "cod_value", "cod_fee",
         "rto_freight", "adjustment", "net_remitted"],
        [
            [r.remitted_on and r.remittance_id, r.remitted_on.isoformat(), l.awb,
             _r(l.cod_value), _r(l.cod_fee), _r(l.rto_freight),
             _r(l.adjustment), _r(l.net)]
            for r in doc.cod_remittances for l in r.lines
        ],
    )

    # 5. Shipment manifest -- the merchant's own record of what was sent
    counts["shipments.csv"] = _write(
        out / "shipments.csv",
        ["awb", "order_id", "shipped_on", "delivered_on", "cod_value", "status"],
        [
            [s.awb, s.order_id, s.shipped_on.isoformat(),
             s.delivered_on.isoformat() if s.delivered_on else "",
             _r(s.cod_value), s.status]
            for s in doc.shipments
        ],
    )

    # 6. Refund and dispute exports from the merchant side
    counts["refunds.csv"] = _write(
        out / "refunds.csv",
        ["refund_id", "payment_id", "order_id", "initiated_on", "amount"],
        [
            [r.refund_id, r.payment_id, r.order_id, r.initiated_on.isoformat(),
             _r(r.amount)]
            for r in doc.refunds
        ],
    )
    counts["disputes.csv"] = _write(
        out / "disputes.csv",
        ["dispute_id", "payment_id", "order_id", "raised_on", "amount"],
        [
            [c.dispute_id, c.payment_id, c.order_id, c.raised_on.isoformat(),
             _r(c.amount)]
            for c in doc.chargebacks
        ],
    )

    # 7. Razorpay's monthly GST invoice for MDR. The merchant claims input credit
    #    on this, so it must tie to the GST actually deducted across settlements.
    inv_mdr = sum(l.mdr for s in doc.settlements for l in s.lines)
    inv_gst = sum(l.gst_on_mdr for s in doc.settlements for l in s.lines)
    invoice = {
        "invoice_number": f"RZP/{PERIOD.strftime('%Y%m')}/ACME/0001",
        "period": PERIOD.strftime("%Y-%m"),
        "supplier": "Razorpay Software Private Limited",
        "supplier_gstin": "29AAGCR4375J1ZU",
        "recipient": "Acme Athleisure Private Limited",
        "recipient_gstin": "27AABCA1234K1Z9",
        "taxable_value": _r(inv_mdr),
        "cgst": _r(inv_gst // 2),
        "sgst": _r(inv_gst - inv_gst // 2),
        "total_tax": _r(inv_gst),
        "invoice_total": _r(inv_mdr + inv_gst),
        "contracted_mdr_rate_pct": str(MDR_RATE),
        "gst_rate_pct": str(GST_RATE),
    }
    (out / "razorpay_mdr_invoice.json").write_text(
        json.dumps(invoice, indent=2), encoding="utf-8"
    )
    counts["razorpay_mdr_invoice.json"] = 1

    # Contract terms the agent is entitled to know.
    (out / "contract_terms.json").write_text(json.dumps({
        "merchant": "Acme Athleisure Private Limited",
        "period": PERIOD.strftime("%Y-%m"),
        "gateway": "Razorpay",
        "contracted_mdr_rate_pct": str(MDR_RATE),
        "gst_on_mdr_rate_pct": str(GST_RATE),
        "settlement_cycle": "T+2 business days",
        "refund_netting": "Refunds are deducted from a later settlement batch, "
                          "typically 5 business days after initiation.",
        "courier_cod_fee_pct": str(COD_FEE_RATE),
        "courier_rto_freight_inr": "85.00",
        "cod_remittance_cycle": "Weekly, covering deliveries 7+ days prior",
        "match_key_note": "settlement_id is authoritative. The bank UTR is issued "
                          "by the correspondent bank and is NOT a Razorpay key.",
    }, indent=2), encoding="utf-8")
    counts["contract_terms.json"] = 1

    return counts


# ==========================================================================
# Ground truth -- held out, never read by the agent
# ==========================================================================
def write_truth(truth: World, ledger, out: Path) -> dict[str, int]:
    out.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}

    (out / "world_summary.json").write_text(
        json.dumps(truth.summary(), indent=2), encoding="utf-8"
    )

    (out / "defect_ledger.json").write_text(json.dumps({
        "total_planted": len(ledger.entries),
        "by_class": ledger.counts(),
        "total_exposure_paise": ledger.exposure(),
        "entries": [e.as_dict() for e in ledger.entries],
    }, indent=2), encoding="utf-8")
    counts["defect_ledger.json"] = len(ledger.entries)

    # The true per-settlement-line ledger. This is what the documents SHOULD say.
    counts["true_settlement_lines.csv"] = _write(
        out / "true_settlement_lines.csv",
        ["settlement_id", "payment_id", "order_id", "gross", "mdr", "gst_on_mdr", "net"],
        [
            [l.settlement_id, l.payment_id, l.order_id,
             _r(l.gross), _r(l.mdr), _r(l.gst_on_mdr), _r(l.net)]
            for s in truth.settlements for l in s.lines
        ],
    )

    counts["true_bank_credits.csv"] = _write(
        out / "true_bank_credits.csv",
        ["settlement_id", "utr", "credited_on", "amount"],
        [
            [bc.settlement_id, bc.utr, bc.credited_on.isoformat(), _r(bc.amount)]
            for bc in truth.bank_credits
        ],
    )

    counts["true_cod_remittance_lines.csv"] = _write(
        out / "true_cod_remittance_lines.csv",
        ["remittance_id", "awb", "cod_value", "cod_fee", "rto_freight", "net"],
        [
            [l.remittance_id, l.awb, _r(l.cod_value), _r(l.cod_fee),
             _r(l.rto_freight), _r(l.net)]
            for r in truth.cod_remittances for l in r.lines
        ],
    )

    return counts
