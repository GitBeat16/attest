"""Close the month and produce the attestation.

    python -m attest.run --data data

Reports throughput, the three rates, an exception register ranked by rupee
exposure, accuracy against held-out ground truth, and the unexplained residual.

The residual is the number to read last and trust most: rupees the system could
not attribute to any classified cause. Attest declines to sign a close where it
is material, because refusing to certify is the whole point of an attestation.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from . import audit as audit_mod
from . import engine as engine_mod
from .ingest import load, resolve, resolve_naive
from .money import fmt
from .recovery import build_claims, summarise
from .score import render, score

RESIDUAL_LIMIT_BPS = 25          # 0.25% of period volume


def build_exceptions(corpus, res, aud) -> list[dict]:
    """Type every unresolved item, rank by rupee exposure, and say what would fix it."""
    ex: list[dict] = []

    # Group by (class, event date), not by class alone. Fourteen short
    # remittances spread across four weekly statements are fourteen different
    # clocks, and collapsing them into one row hides the ones already dead.
    cod_dates = {r.awb: r.remitted_on for r in corpus.cod}
    batch_dates = {sid: b.settled_on for sid, b in corpus.batches.items()}

    grouped: dict[tuple, list] = defaultdict(list)
    for c in res.chains:
        if c.complete:
            continue
        when = cod_dates.get(c.subject_id) or batch_dates.get(c.stages.get("_batch_id"))
        grouped[(c.broke_at, when)].append(c)

    remedy = {
        "mdr": "Razorpay rate-card confirmation for the period; recoverable if the "
               "contracted rate applies",
        "gst": "corrected GST computation on MDR; also revise the ITC claimed",
        "credit": "bank advice or Razorpay settlement trace for the missing credit",
        "adjustment": "AWB-level justification from the courier for the adjustment line",
        "order": "the order record this settlement line refers to",
        "net": "recomputation of the line; the stated net does not follow from its parts",
        "cod_value": "manifest reconciliation for the AWB",
        "cod_fee": "courier rate-card confirmation",
        "freight": "RTO status confirmation for the shipment",
        "shipment": "the shipment manifest entry for this AWB",
    }

    for (stage, when), chains in grouped.items():
        ex.append({
            "class": stage.upper(),
            "count": len(chains),
            "exposure": sum(abs(c.delta) for c in chains),
            "evidence_required": remedy.get(stage, "manual investigation"),
            "sample": [c.subject_id for c in chains[:3]],
            "occurred_on": when.isoformat() if when else None,
        })

    for f in res.batch_findings:
        if f["class"] == "TIMING_NOT_ERROR":
            continue
        _when = batch_dates.get(f["container"])
        ex.append({
            "class": f["class"], "count": 1, "exposure": abs(f["delta"]),
            "evidence_required": "counterparty confirmation",
            "sample": [f["container"]],
            "occurred_on": _when.isoformat() if _when else None,
        })

    for a in aud.overturned:
        _when = batch_dates.get(a.target)
        ex.append({
            "class": a.hypothesis.upper(), "count": 1, "exposure": abs(a.delta),
            "evidence_required": "review the overturned match before posting",
            "sample": [a.target],
            "occurred_on": _when.isoformat() if _when else None,
        })

    ex.sort(key=lambda e: -e["exposure"])
    return ex


def main() -> None:
    ap = argparse.ArgumentParser(description="Close the month and attest to it.")
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--json", type=Path, help="write the attestation to this path")
    ap.add_argument("--html", type=Path, help="write a self-contained close pack")
    args = ap.parse_args()

    t0 = time.time()
    corpus = load(args.data / "sources")
    naive = resolve_naive(corpus)
    keys = resolve(corpus)
    res = engine_mod.run(corpus)
    aud = audit_mod.run(corpus, res.batch_ties)
    elapsed = time.time() - t0

    tied = sum(1 for v in res.batch_ties.values() if v)
    proven = sum(1 for c in res.chains if c.complete)
    total = len(res.chains)
    volume = sum(o.gross for o in corpus.orders.values())
    exceptions = build_exceptions(corpus, res, aud)
    residual = sum(e["exposure"] for e in exceptions if e["class"] in
                   ("CREDIT", "CHARGEBACK_ORPHAN", "UNREFERENCED_ADJ"))
    residual_bps = (residual / volume * 10000) if volume else 0

    P = print
    P(f"\n  ATTEST  ·  period {corpus.mdr_invoice['period']}  ·  "
      f"{corpus.terms['merchant']}")
    P("  " + "=" * 68)
    P(f"  {corpus.record_count():,} records from 7 sources closed in {elapsed:.2f}s")
    P("")
    P("  KEY RESOLUTION")
    P(f"    naive  (narration/UTR key)   {naive['resolved']:>4}/{naive['bank_rows']} resolved")
    P(f"    attest (settlement_id)       {keys['exact']:>4}/{keys['bank_rows']} exact, "
      f"{keys['ambiguous']} ambiguous, {keys['unresolved']} unresolved")
    P("")
    P("  THE THREE RATES")
    P(f"    match rate   (batches tied)      {tied}/{len(res.batch_ties)}"
      f"   = {tied/len(res.batch_ties)*100:>5.1f}%   <- what everyone reports")
    P(f"    proof rate   (evidence chains)   {proven}/{total}"
      f" = {proven/total*100:>5.1f}%   <- what we report")
    P(f"    false-match  (overturned)        {len(aud.overturned)}/{aud.tested}"
      f"    = {aud.false_match_rate()*100:>5.1f}%   <- what nobody reports")
    P("")
    P("  EXCEPTION REGISTER (ranked by exposure)")
    P("  " + "-" * 68)
    for e in exceptions[:10]:
        P(f"    {e['class']:<22}{e['count']:>4}  {fmt(e['exposure']):>14}   "
          f"{e['evidence_required'][:34]}")
    P("  " + "-" * 68)
    P(f"    {'TOTAL EXPOSURE':<22}{sum(e['count'] for e in exceptions):>4}  "
      f"{fmt(sum(e['exposure'] for e in exceptions)):>14}")
    P("")
    # --- recovery: what is claimable, from whom, and by when ---------------
    from datetime import date as _date
    y, m = (int(x) for x in corpus.mdr_invoice["period"].split("-"))
    period_end = _date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
    today = period_end + timedelta(days=3)      # a realistic close date
    claims = build_claims(exceptions, period_end, today)
    rec = summarise(claims, today)

    P("  RECOVERY  (as at " + today.isoformat() + ")")
    P("  " + "-" * 68)
    P(f"    {'recoverable':<24}{fmt(rec['recoverable']):>16}"
      f"   across {rec['recoverable_count']} items")
    P(f"    {'expiring within 7 days':<24}{fmt(rec['expiring_soon']):>16}"
      f"   {rec['expiring_count']} items")
    P(f"    {'already lapsed':<24}{fmt(rec['lapsed']):>16}"
      f"   {rec['lapsed_count']} items")
    P("")

    # The argument for running daily rather than monthly, computed rather than
    # asserted: the same findings, scored against a close that lands 30 days later.
    late = today + timedelta(days=30)
    rec_late = summarise(claims, late)
    cost = rec_late["lapsed"] - rec["lapsed"]
    P("    CADENCE")
    P(f"      close on {today.isoformat()} (daily)     "
      f"{fmt(rec['recoverable']):>13} recoverable, {fmt(rec['lapsed']):>11} lapsed")
    P(f"      close on {late.isoformat()} (monthly)   "
      f"{fmt(rec_late['recoverable']):>13} recoverable, {fmt(rec_late['lapsed']):>11} lapsed")
    P(f"      cost of waiting a month                 {fmt(cost):>13}"
      f"   ({rec_late['lapsed_count'] - rec['lapsed_count']} claims expire unfiled)")
    P("")
    P(f"    {'counterparty':<16}{'claimable':>16}")
    for party, amt in sorted(rec["by_counterparty"].items(), key=lambda x: -x[1]):
        P(f"    {party:<16}{fmt(amt):>16}")
    P("")
    P("    next 5 deadlines")
    for c in claims[:5]:
        d = c.days_left(today)
        mark = "LAPSED" if d < 0 else f"{d}d left"
        P(f"      {c.deadline.isoformat()}  {mark:>9}  {c.exception_class:<22}"
          f"{fmt(c.exposure):>13}  -> {c.counterparty}")
    P("")

    card = score(corpus, res, aud, args.data / "truth")
    P(render(card))
    P("")
    P("  ATTESTATION")
    P("  " + "-" * 68)
    P(f"    period volume            {fmt(volume):>16}")
    P(f"    unexplained residual     {fmt(residual):>16}   "
      f"({residual_bps:.1f} bps of volume)")
    signed = residual_bps <= RESIDUAL_LIMIT_BPS and not card.false_positives
    P(f"    close status             {'SIGNED' if signed else 'NOT ATTESTABLE':>16}")
    if not signed:
        P(f"    reason                   residual exceeds the "
          f"{RESIDUAL_LIMIT_BPS} bps limit; the close is")
        P(f"                             not certified until it is investigated.")
    P("")

    payload = _payload(corpus, res, aud, card, exceptions, elapsed, volume,
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
        "recoverable": rec["recoverable"], "recoverable_count": rec["recoverable_count"],
        "expiring_soon": rec["expiring_soon"], "expiring_count": rec["expiring_count"],
        "by_counterparty": rec["by_counterparty"],
        "monthly_lapsed": rec_late["lapsed"],
        "monthly_lapsed_count": rec_late["lapsed_count"] - rec["lapsed_count"],
        "late_date": late.isoformat(),
        "deadlines": [
            {"deadline": c.deadline.isoformat(), "days": c.days_left(today),
             "cls": c.exception_class, "exposure": c.exposure,
             "party": c.counterparty, "urgency": c.urgency(today)}
            for c in claims[:6]
        ],
    }

    if args.html:
        from .report import render as render_html
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(render_html(payload), encoding="utf-8")
        P(f"  close pack written to {args.html}")

    if args.json:
        args.json.write_text(json.dumps({
            "period": corpus.mdr_invoice["period"],
            "records": corpus.record_count(),
            "seconds": round(elapsed, 3),
            "match_rate": tied / len(res.batch_ties),
            "proof_rate": proven / total,
            "false_match_rate": aud.false_match_rate(),
            "overall_recall": card.overall_recall(),
            "false_positives": card.false_positives,
            "residual_paise": residual,
            "residual_bps": round(residual_bps, 2),
            "signed": signed,
            "exceptions": exceptions,
        }, indent=2), encoding="utf-8")
        P(f"  attestation written to {args.json}\n")


def _payload(corpus, res, aud, card, exceptions, elapsed, volume, residual,
             residual_bps, signed, tied, proven, total):
    from datetime import datetime
    broken = next((c for c in res.chains if not c.complete and c.broke_at == "mdr"),
                  None) or next((c for c in res.chains if not c.complete), None)
    stage_order = ["order", "mdr", "gst", "net", "credit"]
    return {
        "merchant": corpus.terms["merchant"], "period": corpus.mdr_invoice["period"],
        "records": corpus.record_count(), "seconds": round(elapsed, 3),
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "match_rate": tied / len(res.batch_ties), "tied": tied,
        "batches": len(res.batch_ties),
        "proof_rate": proven / total, "proven": proven, "lines": total,
        "false_match_rate": aud.false_match_rate(),
        "overturned": len(aud.overturned), "tested": aud.tested,
        "exceptions": exceptions,
        "scorecard": {
            k: {"held_out": v.held_out, "planted": v.planted,
                "detected": v.detected, "recall": v.recall}
            for k, v in card.by_class.items()
        },
        "designed_recall": card.designed_recall(),
        "holdout_recall": card.holdout_recall(),
        "notes": card.notes,
        "volume": volume, "residual_paise": residual,
        "residual_bps": round(residual_bps, 1), "signed": signed,
        "chain_nodes": stage_order,
        "chain_break": broken.broke_at if broken else None,
        "chain_subject": broken.subject_id if broken else "",
        "chain_detail": broken.detail if broken else "",
    }


if __name__ == "__main__":
    main()
