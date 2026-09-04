"""Close a month of a merchant's own data.

The difference between this and `attest.run` is not the pipeline -- it is the
same engine, the same tolerances, the same adversarial pass. The difference is
what may honestly be claimed afterwards.

`attest.run` closes the benchmark corpus, whose truth was constructed before the
system existed, so it can report recall against an answer key. This module closes
a real merchant-month, where no answer key exists and none can. It therefore
reports evidence and refuses to report accuracy. A tool that quotes you a recall
figure on your own data is quoting a number it cannot have measured.

The second difference is that evidence here is *optional*. A merchant closing
their own month has whatever exports they have. Missing a file does not stop the
close; it lowers the proof rate, which is the correct and honest response --
fewer lines can be traced end to end because there is less to trace them
through.
"""
from __future__ import annotations

import json
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from . import audit as audit_mod
from . import engine as engine_mod
from .ingest import load, resolve, resolve_naive
from .money import fmt
from .recovery import build_claims, summarise
from .run import build_exceptions, _payload

# Every source the pipeline can use, what it contributes, and whether the close
# can proceed without it. Order is the order the UI offers them in.
SOURCES: list[tuple[str, str, bool]] = [
    ("razorpay_settlements.csv", "Razorpay settlement report", True),
    ("bank_statement.csv", "Bank statement", False),
    ("orders.csv", "Order export", False),
    ("refunds.csv", "Refund export", False),
    ("cod_remittances.csv", "Courier COD remittances", False),
    ("shipments.csv", "Shipment / AWB export", False),
    ("disputes.csv", "Chargebacks and disputes", False),
    ("razorpay_mdr_invoice.json", "Razorpay MDR tax invoice", False),
]

HEADERS: dict[str, str] = {
    "razorpay_settlements.csv":
        "settlement_id,settled_on,payment_id,order_id,gross_amount,mdr,"
        "gst_on_mdr,net_amount,row_type",
    "bank_statement.csv": "value_date,narration,utr,credit_amount,debit_amount",
    "orders.csv": "order_id,placed_on,gross_amount,channel,payment_mode",
    "refunds.csv": "refund_id,payment_id,order_id,initiated_on,amount",
    "cod_remittances.csv":
        "remittance_id,remitted_on,awb,cod_value,cod_fee,rto_freight,"
        "adjustment,net_remitted",
    "shipments.csv": "awb,order_id,shipped_on,delivered_on,cod_value,status",
    "disputes.csv": "dispute_id,payment_id,order_id,raised_on,amount",
}

# What each missing source costs, in the merchant's language rather than ours.
COST: dict[str, str] = {
    "bank_statement.csv":
        "no line can be proven to have reached your bank — the credit stage of "
        "every evidence chain stays open",
    "orders.csv":
        "settled amounts cannot be checked against what the customer was "
        "actually charged",
    "refunds.csv":
        "refund deductions cannot be independently rebuilt, so an offsetting "
        "pair can hide inside a batch that ties",
    "cod_remittances.csv": "COD short-remittance goes unchecked",
    "shipments.csv": "RTO freight and duplicate AWBs go unchecked",
    "disputes.csv": "chargebacks cannot be tied to a dispute",
    "razorpay_mdr_invoice.json":
        "the GST you claim as input credit is not checked against the GST "
        "actually deducted",
}

DEFAULT_TERMS = {
    "gateway": "Razorpay",
    "contracted_mdr_rate_pct": "2.00",
    "gst_on_mdr_rate_pct": "18.00",
    "settlement_cycle": "T+2 business days",
    "refund_netting": ("Refunds are deducted from a later settlement batch, "
                       "typically 5 business days after initiation."),
    "courier_cod_fee_pct": "1.50",
    "courier_rto_freight_inr": "85.00",
    "cod_remittance_cycle": "Weekly, covering deliveries 7+ days prior",
    "match_key_note": ("settlement_id is authoritative. The bank UTR is issued "
                       "by the correspondent bank and is NOT a Razorpay key."),
}


class CloseError(Exception):
    """Something about the submitted evidence makes a close impossible."""


def stage(files: dict[str, str], merchant: str, period: str,
          terms: dict | None, dest: Path) -> tuple[list[str], list[str]]:
    """Write the supplied sources to `dest`, stubbing the ones that are absent.

    Returns (supplied, missing). A stub is a header row and nothing else, which
    the loader reads as "this system reported no rows" -- true, and materially
    different from pretending the rows exist.
    """
    dest.mkdir(parents=True, exist_ok=True)
    supplied, missing = [], []

    for name, _label, required in SOURCES:
        body = (files.get(name) or "").strip()
        if body:
            supplied.append(name)
            (dest / name).write_text(body + "\n", encoding="utf-8")
            continue
        if required:
            raise CloseError(
                f"{name} is required — without the settlement report there is "
                "nothing to reconcile against.")
        missing.append(name)
        if name.endswith(".csv"):
            (dest / name).write_text(HEADERS[name] + "\n", encoding="utf-8")

    # The declared period anchors the month being closed. It is taken from the
    # merchant, never inferred from max(settled_on): a single stray out-of-period
    # row must not be able to redefine which month this is.
    if "razorpay_mdr_invoice.json" in missing:
        (dest / "razorpay_mdr_invoice.json").write_text(json.dumps({
            "period": period,
            "supplier": "Razorpay Software Private Limited",
            "recipient": merchant,
            "total_tax": "",            # empty: the ITC check is skipped, not faked
            "note": "not supplied by the merchant; input-credit check skipped",
        }), encoding="utf-8")
    else:
        inv = json.loads((dest / "razorpay_mdr_invoice.json").read_text())
        inv["period"] = period          # the declared month always wins
        (dest / "razorpay_mdr_invoice.json").write_text(json.dumps(inv),
                                                        encoding="utf-8")

    t = dict(DEFAULT_TERMS)
    t.update({k: str(v) for k, v in (terms or {}).items() if str(v).strip()})
    t["merchant"] = merchant
    t["period"] = period
    (dest / "contract_terms.json").write_text(json.dumps(t), encoding="utf-8")

    return supplied, missing


def close(src: Path, supplied: list[str], missing: list[str]) -> dict:
    """Run the pipeline and return everything the app and the pack both need."""
    t0 = time.time()
    corpus = load(src)
    naive = resolve_naive(corpus)
    keys = resolve(corpus)
    res = engine_mod.run(corpus)
    aud = audit_mod.run(corpus, res.batch_ties)
    elapsed = time.time() - t0

    batches = len(res.batch_ties)
    if not batches:
        raise CloseError("the settlement report contained no batches to close.")

    tied = sum(1 for v in res.batch_ties.values() if v)
    proven = sum(1 for c in res.chains if c.complete)
    total = len(res.chains) or 1

    # Volume prefers the order export, because that is what the customer was
    # actually charged. Without it, fall back to settled gross and say so.
    volume = sum(o.gross for o in corpus.orders.values())
    volume_basis = "orders"
    if not volume:
        volume = sum(l.gross for b in corpus.batches.values() for l in b.lines)
        volume_basis = "settled gross"

    exceptions = build_exceptions(corpus, res, aud)
    residual = sum(e["exposure"] for e in exceptions
                   if e["class"] in ("CREDIT", "CHARGEBACK_ORPHAN",
                                     "UNREFERENCED_ADJ"))
    residual_bps = (residual / volume * 10000) if volume else 0
    signed = residual_bps <= 25

    y, m = (int(x) for x in corpus.mdr_invoice["period"].split("-"))
    period_end = date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
    today = max(date.today(), period_end + timedelta(days=1))
    claims = build_claims(exceptions, period_end, today)
    rec = summarise(claims, today)

    # The cadence argument, computed the same way as on the benchmark: the same
    # findings, scored against a close that lands 30 days later.
    late = today + timedelta(days=30)
    rec_late = summarise(claims, late)

    payload = _payload(corpus, res, aud, None, exceptions, elapsed, volume,
                       residual, residual_bps, signed, tied, proven, total)

    tied_ids = {sid for sid, ok in res.batch_ties.items() if ok}
    lines_in_tied = sum(
        1 for c in res.gateway_chains if c.stages.get("_batch_id") in tied_ids
    ) + len(res.cod_chains)
    payload["lines_in_tied"] = lines_in_tied
    payload["line_match_rate"] = lines_in_tied / total if total else 0
    payload["compensating"] = [
        {"target": a.target, "reasoning": a.reasoning, **a.evidence}
        for a in aud.overturned if a.hypothesis == "offsetting_pair"
    ]
    payload["recovery"] = {
        "as_at": today.isoformat(),
        "recoverable": rec["recoverable"],
        "recoverable_count": rec["recoverable_count"],
        "expiring_soon": rec["expiring_soon"],
        "expiring_count": rec["expiring_count"],
        "by_counterparty": rec["by_counterparty"],
        "monthly_lapsed": rec_late["lapsed"] - rec["lapsed"],
        "monthly_lapsed_count": rec_late["lapsed_count"] - rec["lapsed_count"],
        "late_date": late.isoformat(),
        "deadlines": [
            {"deadline": c.deadline.isoformat(), "days": c.days_left(today),
             "cls": c.exception_class, "exposure": c.exposure,
             "party": c.counterparty, "urgency": c.urgency(today)}
            for c in claims[:6]
        ],
    }

    summary = {
        "merchant": corpus.terms["merchant"],
        "period": corpus.mdr_invoice["period"],
        "records": corpus.record_count(),
        "seconds": round(elapsed, 3),
        "batches_total": batches,
        "batches_tied": tied,
        "lines_total": total,
        "lines_proven": proven,
        "claims_tested": aud.tested,
        "claims_overturned": len(aud.overturned),
        "match_rate": tied / batches,
        "proof_rate": proven / total,
        "false_match_rate": aud.false_match_rate(),
        "volume_paise": volume,
        "volume_basis": volume_basis,
        "volume_display": fmt(volume),
        "residual_paise": residual,
        "residual_bps": round(residual_bps, 2),
        "recoverable_paise": rec.get("recoverable", 0),
        "recoverable_display": fmt(rec.get("recoverable", 0)),
        "attestable": signed,
        "bank_resolution": keys,
        "naive_resolution": naive,
        "evidence_supplied": supplied,
        "evidence_missing": missing,
        "evidence_cost": [
            {"source": n, "cost": COST[n]} for n in missing if n in COST
        ],
        "exceptions": exceptions,
    }
    return {"summary": summary, "payload": payload, "claims": claims,
            "today": today}
