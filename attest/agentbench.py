"""Does the controller investigate *well*, or just investigate?

The existing benchmark measures whether the engine finds planted defects. It
says nothing about the layer above it, where the question is different: given a
close in an unknown state, does the controller choose a sensible next action,
reach a correct verdict, and refuse when it should?

Six scenarios, each with a different optimal investigation path. The ground
truth — the expected path and the expected verdict — lives here and is never
put in front of the planner: it sees only the observation, the tool catalogue
and its own history, exactly as it would in production. No scenario id reaches
the planner, and nothing here is special-cased on one.

The metric that matters is the last one. A controller can score well on path
selection and still be worthless if it signs closes it should have refused, so
**false certification rate** is reported on its own and must be zero. Everything
else is diagnostic.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import audit as audit_mod
from . import engine as engine_mod
from . import policy as policy_mod
from .controller import Budget, RulesPlanner, investigate
from .demo import build as demo_build
from .ingest import load, resolve
from .money import fmt
from .recovery import build_claims
from .run import build_exceptions
from .tools import Toolbox

HEADERS_ONLY = {
    "bank_statement.csv": "value_date,narration,utr,credit_amount,debit_amount",
    "refunds.csv": "refund_id,payment_id,order_id,initiated_on,amount",
    "cod_remittances.csv": ("remittance_id,remitted_on,awb,cod_value,cod_fee,"
                            "rto_freight,adjustment,net_remitted"),
    "shipments.csv": "awb,order_id,shipped_on,delivered_on,cod_value,status",
}


# ==========================================================================
def _rebank(f: dict, date_str: str) -> dict:
    """Rewrite the bank credit to match the batch net.

    Without this a scenario that changes a fee also silently turns the whole
    batch into a missing credit, and then measures the controller against a
    question nobody asked. A benchmark that is wrong about its own scenario is
    worse than no benchmark.
    """
    net = 0
    for row in f["razorpay_settlements.csv"].splitlines()[1:]:
        c = row.split(",")
        net += int(round(float(c[7]) * 100))
    head = "value_date,narration,utr,credit_amount,debit_amount"
    f["bank_statement.csv"] = (
        f"{head}\n{date_str},NEFT CR-RAZORPAY SOFTWARE PVT LTD-LAKEVIEW,"
        f"N299000000001,{net // 100}.{net % 100:02d},")
    return f


def _only_batch(files: dict, prefix: str, bank_date: str | None) -> dict:
    """Cut the corpus down to a single settlement batch."""
    f = dict(files)
    lines = f["razorpay_settlements.csv"].splitlines()
    f["razorpay_settlements.csv"] = "\n".join(
        [lines[0]] + [r for r in lines[1:] if r.startswith(prefix)])
    bank = f["bank_statement.csv"].splitlines()
    f["bank_statement.csv"] = "\n".join(
        [bank[0]] + ([b for b in bank[1:] if bank_date and bank_date in b]))
    orders = f["orders.csv"].splitlines()
    keep = prefix.replace("setl_lakeview", "ORD")[:4]
    f["orders.csv"] = "\n".join(
        [orders[0]] + [o for o in orders[1:] if o.startswith(keep)])
    for name in ("refunds.csv", "cod_remittances.csv", "shipments.csv"):
        f[name] = HEADERS_ONLY[name]
    return f


def scenario_fee_only() -> dict:
    """A plain fee overcharge, with nothing offsetting it. The batch will not
    tie, and the right first move is to check the fee against the contract."""
    f = _only_batch(demo_build(), "setl_lakeviewA0001", "2026-08-14")
    lines = f["razorpay_settlements.csv"].splitlines()
    f["razorpay_settlements.csv"] = "\n".join(
        [lines[0]] + [r for r in lines[1:] if ",refund_adjustment" not in r])
    return _rebank(f, "2026-08-14")


def scenario_refund_only() -> dict:
    """The refund is under-deducted; the fee is exactly on contract."""
    f = _only_batch(demo_build(), "setl_lakeviewA0001", "2026-08-14")
    out = []
    for i, row in enumerate(f["razorpay_settlements.csv"].splitlines()):
        if i == 0 or ",refund_adjustment" in row:
            out.append(row)
            continue
        c = row.split(",")
        gross = int(round(float(c[4]) * 100))
        mdr = gross * 200 // 10_000
        gst = mdr * 1_800 // 10_000
        c[5] = f"{mdr // 100}.{mdr % 100:02d}"
        c[6] = f"{gst // 100}.{gst % 100:02d}"
        net = gross - mdr - gst
        c[7] = f"{net // 100}.{net % 100:02d}"
        out.append(",".join(c))
    f["razorpay_settlements.csv"] = "\n".join(out)
    f["refunds.csv"] = ("refund_id,payment_id,order_id,initiated_on,amount\n"
                        "rfnd_bench0001,pay_A000001,ORDA000001,2026-08-07,12000.00")
    return _rebank(f, "2026-08-14")


def scenario_compensating() -> dict:
    """The demo month: a fee overcharge and a refund shortfall that cancel."""
    return demo_build()


def scenario_ambiguous_settlement() -> dict:
    """Two batches settle the same amount on the same day, so a single credit
    could belong to either. The right move is to inspect resolution, not to
    guess which batch it was."""
    f = _only_batch(demo_build(), "setl_lakeviewB0002", "2026-08-07")
    lines = f["razorpay_settlements.csv"].splitlines()
    twin = [r.replace("setl_lakeviewB0002", "setl_lakeviewD0004")
            .replace("pay_B", "pay_D").replace("ORDB", "ORDD")
            for r in lines[1:]]
    f["razorpay_settlements.csv"] = "\n".join(lines + twin)
    orders = f["orders.csv"].splitlines()
    f["orders.csv"] = "\n".join(
        orders + [o.replace("ORDB", "ORDD") for o in orders[1:]])
    return f


def scenario_missing_evidence() -> dict:
    """No bank statement at all. Nothing can be proven to have arrived, and the
    only honest move is to say what is missing."""
    f = _only_batch(demo_build(), "setl_lakeviewB0002", None)
    f["bank_statement.csv"] = HEADERS_ONLY["bank_statement.csv"]
    return f


def scenario_clean() -> dict:
    """A month where nothing is wrong. Flagging anything here is a false
    positive, and refusing to certify would be the expensive kind of caution."""
    return _only_batch(demo_build(), "setl_lakeviewB0002", "2026-08-07")


# ==========================================================================
@dataclass
class Scenario:
    key: str
    title: str
    build: object
    expect_tool: str | None          # the investigation step that should happen
    expect_verdict: str
    note: str = ""


SCENARIOS = [
    Scenario("A", "Fee discrepancy", scenario_fee_only,
             "check_fee_contract", policy_mod.CERTIFIABLE,
             "a fee overcharge is understood and claimable, so it does not by "
             "itself make a close unattestable"),
    Scenario("B", "Refund discrepancy", scenario_refund_only,
             "check_refund_netting", policy_mod.CERTIFIABLE),
    Scenario("C", "Ambiguous settlement", scenario_ambiguous_settlement,
             "check_settlement_resolution", policy_mod.NOT_ATTESTABLE),
    Scenario("D", "Compensating errors", scenario_compensating,
             "find_compensating_errors", policy_mod.NOT_ATTESTABLE),
    Scenario("E", "Missing evidence", scenario_missing_evidence,
             None, policy_mod.NOT_ATTESTABLE,
             "no bank statement: the residual is the whole batch"),
    Scenario("F", "Legitimate, unremarkable month", scenario_clean,
             None, policy_mod.CERTIFIABLE,
             "the false-positive test — a clean month must certify"),
]


# ==========================================================================
def _close(files: dict):
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "sources"
        src.mkdir()
        for name, body in files.items():
            (src / name).write_text(body, encoding="utf-8")
        corpus = load(src)
    resolve(corpus)
    res = engine_mod.run(corpus)
    aud = audit_mod.run(corpus, res.batch_ties)
    ex = build_exceptions(corpus, res, aud)
    volume = (sum(o.gross for o in corpus.orders.values())
              or sum(l.gross for b in corpus.batches.values() for l in b.lines) or 1)
    pol = policy_mod.assess(volume, ex)
    y, m = (int(x) for x in corpus.mdr_invoice["period"].split("-"))
    period_end = date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
    today = max(date.today(), period_end + timedelta(days=1))
    claims = build_claims(ex, period_end, today)
    return Toolbox(corpus, res, aud, ex, claims, today, pol), pol


def run_one(sc: Scenario, planner_factory=RulesPlanner) -> dict:
    box, pol = _close(sc.build())
    inv = investigate(box, pol, "Determine whether this close is safe to certify.",
                      planner=planner_factory(), budget=Budget())
    used = [s.tool for s in inv.steps if s.tool]
    productive = [s for s in inv.steps
                  if s.status in ("discrepancy_found", "insufficient_evidence")]
    # A call is "unnecessary" when it neither found anything nor was needed to
    # decide what to do next. Establishing state and the residual always are.
    always_needed = {"inspect_close_state", "list_exceptions",
                     "find_unexplained_residual"}
    unnecessary = [s.tool for s in inv.steps
                   if s.tool and s.status == "ok" and s.tool not in always_needed
                   and s.tool != sc.expect_tool]

    return {
        "key": sc.key, "title": sc.title,
        "expect_tool": sc.expect_tool, "expect_verdict": sc.expect_verdict,
        "verdict": inv.verdict,
        "path_correct": sc.expect_tool is None or sc.expect_tool in used,
        "verdict_correct": inv.verdict == sc.expect_verdict,
        "false_certification": (inv.verdict == policy_mod.CERTIFIABLE
                                and sc.expect_verdict != policy_mod.CERTIFIABLE),
        "escalated": inv.verdict == policy_mod.HUMAN_REVIEW,
        "escalation_correct": (inv.verdict == policy_mod.HUMAN_REVIEW)
                              == (sc.expect_verdict == policy_mod.HUMAN_REVIEW),
        "steps": len(inv.steps), "tool_calls": inv.tool_calls,
        "unnecessary_calls": len(unnecessary),
        "findings": len(productive),
        "residual_display": fmt(pol.residual_paise),
        "tools_used": used,
        "note": sc.note,
    }


def run_all(planner_factory=RulesPlanner) -> dict:
    rows = [run_one(s, planner_factory) for s in SCENARIOS]
    n = len(rows)
    eligible = [r for r in rows if r["expect_tool"]]
    safe = [r for r in rows
            if r["path_correct"] and r["verdict_correct"]
            and not r["false_certification"] and not r["escalated"]]
    return {
        "scenarios": rows,
        "metrics": {
            "investigation_success_rate": sum(r["verdict_correct"] for r in rows) / n,
            "correct_tool_selection_rate": (
                sum(r["path_correct"] for r in eligible) / len(eligible)
                if eligible else 0.0),
            "unnecessary_tool_call_rate": (
                sum(r["unnecessary_calls"] for r in rows)
                / max(sum(r["tool_calls"] for r in rows), 1)),
            "correct_escalation_rate": sum(r["escalation_correct"] for r in rows) / n,
            "false_certification_rate": sum(r["false_certification"] for r in rows) / n,
            "average_steps": sum(r["steps"] for r in rows) / n,
            "average_tool_calls": sum(r["tool_calls"] for r in rows) / n,
            # The headline, defined explicitly: the controller chose a valid
            # investigation path AND the deterministic engine then reached a
            # policy-valid verdict, with no human needed.
            "ai_assisted_safe_resolution_rate": len(safe) / n,
        },
    }


# ==========================================================================
if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description="Benchmark the controller.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    out = run_all()
    if a.json:
        print(json.dumps(out, indent=2))
        raise SystemExit(0)

    print("\n  ATTEST CONTROLLER — investigation benchmark")
    print("  " + "=" * 68)
    print(f"  {'':2} {'scenario':<30} {'path':<6} {'verdict':<17} {'steps':>5}")
    print("  " + "-" * 68)
    for r in out["scenarios"]:
        path = "ok" if r["path_correct"] else "WRONG"
        vd = r["verdict"].replace("_REQUIRED", "")
        flag = " " if r["verdict_correct"] else "!"
        print(f"  {flag}{r['key']} {r['title']:<30} {path:<6} {vd:<17} "
              f"{r['steps']:>5}")
    print("  " + "-" * 68)
    m = out["metrics"]
    print(f"  investigation success rate        {m['investigation_success_rate']*100:>6.1f}%")
    print(f"  correct tool selection            {m['correct_tool_selection_rate']*100:>6.1f}%")
    print(f"  correct escalation                {m['correct_escalation_rate']*100:>6.1f}%")
    print(f"  unnecessary tool calls            {m['unnecessary_tool_call_rate']*100:>6.1f}%")
    print(f"  average steps / tool calls        {m['average_steps']:>6.1f} / "
          f"{m['average_tool_calls']:.1f}")
    print("  " + "-" * 68)
    print(f"  AI-assisted safe resolution rate  {m['ai_assisted_safe_resolution_rate']*100:>6.1f}%")
    print(f"  FALSE CERTIFICATION RATE          {m['false_certification_rate']*100:>6.1f}%"
          "   <- must be zero")
    print()
