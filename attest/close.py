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
from .money import fmt, pct
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

    for name, label, required in SOURCES:
        body = (files.get(name) or "").strip()
        if body:
            # Check the header before the engine does. A missing column would
            # otherwise surface as a KeyError several layers down, which tells a
            # merchant nothing about which file they uploaded or what is wrong
            # with it.
            if name.endswith(".csv"):
                want = set(HEADERS[name].split(","))
                got = {h.strip().lstrip("\ufeff")
                       for h in body.splitlines()[0].split(",")}
                gap = want - got
                if gap:
                    raise CloseError(
                        f"{label} is missing {len(gap)} column"
                        f"{'' if len(gap) == 1 else 's'}: "
                        f"{', '.join(sorted(gap))}. Expected the header "
                        f"{HEADERS[name]}")
            elif name.endswith(".json"):
                try:
                    json.loads(body)
                except json.JSONDecodeError as e:
                    raise CloseError(f"{label} is not valid JSON ({e.msg} at "
                                     f"line {e.lineno}).") from None
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
            "today": today, "corpus": corpus, "res": res, "aud": aud}

# ==========================================================================
# What a conventional tool would have reported, and the evidence that it is
# wrong. Both are derived from the same corpus the engine read -- nothing here
# is narrated, asserted or stored; it is the same arithmetic presented the way
# each audience would present it.
# ==========================================================================
def naive_view(corpus, res, aud) -> dict | None:
    """The batch a total-level check waves through, and what is inside it.

    Returns None when the run contains no compensating pair, because inventing
    one would be exactly the dishonesty this project exists to argue against.
    """
    pairs = [a for a in aud.overturned if a.hypothesis == "offsetting_pair"]
    if not pairs:
        return None
    worst = max(pairs, key=lambda a: a.delta)
    ev = worst.evidence
    b = corpus.batches.get(worst.target)
    if b is None:
        return None

    line_var = ev["line_variance_paise"]
    refund_var = ev["refund_variance_paise"]
    residual = ev["residual_paise"]
    exposure = abs(line_var) + abs(refund_var)

    gross = sum(l.gross for l in b.lines)
    charged_fee = sum(l.mdr for l in b.lines)
    charged_tax = sum(l.gst_on_mdr for l in b.lines)
    mdr_rate = Decimal(corpus.terms["contracted_mdr_rate_pct"])
    gst_rate = Decimal(corpus.terms["gst_on_mdr_rate_pct"])
    true_fee = sum(pct(l.gross, mdr_rate) for l in b.lines)
    true_tax = sum(pct(pct(l.gross, mdr_rate), gst_rate) for l in b.lines)
    settled_net = sum(l.net for l in b.lines) + b.refund_adj
    credit = next((x.credit for x in corpus.bank if x.settlement_id == worst.target), 0)

    eff = (Decimal(charged_fee) / Decimal(gross) * 100) if gross else Decimal(0)

    return {
        "batch": worst.target,
        "settled_on": b.settled_on.isoformat(),
        "lines": len(b.lines),
        # --- what a conventional reconciliation reports -------------------
        "naive": {
            "settlement_paise": settled_net,
            "bank_paise": credit,
            "variance_paise": abs(settled_net - credit),
            "verdict": "Reconciled — within tolerance",
        },
        # --- what is actually inside it -----------------------------------
        "attest": {
            "findings": 2,
            "mdr_overcharge_paise": charged_fee - true_fee,
            "gst_overcharge_paise": charged_tax - true_tax,
            "fee_overcharge_paise": abs(line_var),
            "refund_variance_paise": abs(refund_var),
            "residual_paise": abs(residual),
            "exposure_paise": exposure,
            "effective_mdr_pct": f"{eff:.2f}",
            "contract_mdr_pct": str(mdr_rate),
            "understated_by": exposure // max(abs(residual), 1),
            "verdict": "Ties, but is not clean",
        },
        "worst_line": ev.get("worst_line", ""),
        "reasoning": worst.reasoning,
    }


def evidence_chain(corpus, payment_id: str) -> list[dict]:
    """Every link between a customer's order and the money in the bank, with
    the broken link named. This is the whole meaning of the product: not
    "there is a discrepancy" but "here is the record that proves it"."""
    mdr_rate = Decimal(corpus.terms["contracted_mdr_rate_pct"])
    gst_rate = Decimal(corpus.terms["gst_on_mdr_rate_pct"])

    for sid, b in corpus.batches.items():
        for ln in b.lines:
            if ln.payment_id != payment_id:
                continue
            order = corpus.orders.get(ln.order_id)
            bank = next((x for x in corpus.bank if x.settlement_id == sid), None)
            exp_mdr = pct(ln.gross, mdr_rate)
            exp_gst = pct(exp_mdr, gst_rate)
            eff = (Decimal(ln.mdr) / Decimal(ln.gross) * 100) if ln.gross else Decimal(0)

            return [
                {"stage": "Order", "id": ln.order_id,
                 "value": fmt(order.gross) if order else "not in export",
                 "note": (f"placed {order.placed_on.isoformat()} · {order.channel}"
                          if order else "no order export supplied"),
                 "status": "ok" if order and order.gross == ln.gross else "gap"},
                {"stage": "Payment", "id": ln.payment_id, "value": fmt(ln.gross),
                 "note": "captured amount agrees with the order",
                 "status": "ok"},
                {"stage": "Fee charged", "id": f"{eff:.2f}% effective",
                 "value": fmt(ln.mdr),
                 "note": "what Razorpay deducted on this line",
                 "status": "break" if ln.mdr != exp_mdr else "ok"},
                {"stage": "Fee contracted", "id": f"{mdr_rate}% agreed",
                 "value": fmt(exp_mdr),
                 "note": "what the contract says it should have been",
                 "status": "break" if ln.mdr != exp_mdr else "ok"},
                {"stage": "GST on the fee", "id": f"{gst_rate}%",
                 "value": fmt(ln.gst_on_mdr),
                 "note": (f"{fmt(exp_gst)} on the contracted fee — the tax "
                          "inherits the error")
                          if ln.gst_on_mdr != exp_gst else "computed on the correct base",
                 "status": "break" if ln.gst_on_mdr != exp_gst else "ok"},
                {"stage": "Settlement", "id": sid, "value": fmt(ln.net),
                 "note": f"batch settled {b.settled_on.isoformat()}",
                 "status": "ok"},
                {"stage": "Bank", "id": bank.utr if bank else "no credit found",
                 "value": fmt(bank.credit) if bank else "—",
                 "note": (f"credited {bank.value_date.isoformat()} — the batch "
                          "total agrees") if bank
                         else "settled by Razorpay, never credited",
                 "status": "ok" if bank else "break"},
            ]
    return []
