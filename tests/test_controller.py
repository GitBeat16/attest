"""Adversarial tests against the controller itself.

The deterministic engine is tested elsewhere. What is tested here is the
question a reviewer should actually ask: *what happens when the thing choosing
the next action misbehaves?*

Each test drives the real loop with a scripted planner that emits a specific
hostile or broken action, and asserts the controller's response. No model is
involved, which is the point — the safety properties belong to the validator,
the tool layer and the policy gate, so they hold no matter what is planning.

Runs under pytest, and standalone:  python3 tests/test_controller.py
"""
from __future__ import annotations

import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from attest import audit as audit_mod                       # noqa: E402
from attest import engine as engine_mod                     # noqa: E402
from attest import policy as policy_mod                     # noqa: E402
from attest.controller import (Budget, RulesPlanner,        # noqa: E402
                               investigate, validate)
from attest.ingest import load, resolve                     # noqa: E402
from attest.recovery import build_claims                    # noqa: E402
from attest.run import build_exceptions                     # noqa: E402
from attest.tools import ToolError, Toolbox                 # noqa: E402


# --------------------------------------------------------------- fixtures
def _close(files: dict[str, str]):
    """Close a month from source text and return everything the loop needs."""
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
    volume = sum(o.gross for o in corpus.orders.values()) or 1
    pol = policy_mod.assess(volume, ex)
    y, m = (int(x) for x in corpus.mdr_invoice["period"].split("-"))
    period_end = date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
    today = max(date.today(), period_end + timedelta(days=1))
    claims = build_claims(ex, period_end, today)
    return Toolbox(corpus, res, aud, ex, claims, today, pol), pol


def demo_box():
    from attest.demo import build
    return _close(build())


def clean_box():
    """A month with nothing wrong in it — the case that must certify."""
    from attest.demo import build
    files = dict(build())
    lines = files["razorpay_settlements.csv"].splitlines()
    head, rows = lines[0], lines[1:]
    keep = [r for r in rows if r.startswith("setl_lakeviewB0002")]
    files["razorpay_settlements.csv"] = "\n".join([head] + keep)
    bank = files["bank_statement.csv"].splitlines()
    files["bank_statement.csv"] = "\n".join(
        [bank[0]] + [b for b in bank[1:] if "2026-08-07" in b])
    files["refunds.csv"] = "refund_id,payment_id,order_id,initiated_on,amount"
    files["cod_remittances.csv"] = (
        "remittance_id,remitted_on,awb,cod_value,cod_fee,rto_freight,"
        "adjustment,net_remitted")
    files["shipments.csv"] = ("awb,order_id,shipped_on,delivered_on,"
                              "cod_value,status")
    orders = files["orders.csv"].splitlines()
    files["orders.csv"] = "\n".join(
        [orders[0]] + [o for o in orders[1:] if o.startswith("ORDB")])
    return _close(files)


class Scripted:
    """A planner that says exactly what the test tells it to."""

    def __init__(self, actions, name="rules"):
        self.actions, self.i, self.name = list(actions), 0, name
        self.calls = 0

    def propose(self, observation, history):
        self.calls += 1
        if self.i < len(self.actions):
            a = self.actions[self.i]
            self.i += 1
            return a(observation, history) if callable(a) else a
        return {"action": "escalate", "reason": "script exhausted"}


def last_rejection(inv):
    return next((s.rejected for s in reversed(inv.steps) if s.rejected), "")


# ============================================================ 1. certify
def test_planner_cannot_certify():
    """There is no action that certifies. Asking for one is rejected."""
    box, pol = demo_box()
    inv = investigate(box, pol, "goal", planner=Scripted([
        {"action": "certify", "reason": "looks fine to me"},
        {"action": "escalate", "reason": "done"}]))
    assert "unknown action" in last_rejection(inv)
    assert "policy engine decides" in last_rejection(inv)
    assert inv.verdict != policy_mod.CERTIFIABLE


def test_certify_by_any_other_name_is_still_rejected():
    box, pol = demo_box()
    for word in ("sign", "approve", "attest", "close", "certify_close"):
        inv = investigate(box, pol, "goal", planner=Scripted([
            {"action": word, "reason": "x"}, {"action": "escalate", "reason": "d"}]))
        assert "unknown action" in last_rejection(inv), word


# ============================================================ 2. invented evidence
def test_invented_evidence_id_is_rejected():
    box, pol = demo_box()
    inv = investigate(box, pol, "goal", planner=Scripted([
        {"action": "conclude", "claim": "all clear",
         "evidence_ids": ["pay_TOTALLY_MADE_UP", "setl_does_not_exist"]},
        {"action": "escalate", "reason": "done"}]))
    r = last_rejection(inv)
    assert "never returned by a tool" in r
    assert "pay_TOTALLY_MADE_UP" in r


def test_evidence_actually_seen_is_accepted():
    """The mirror image: a real id, obtained from a real call, is fine."""
    box, pol = demo_box()
    inv = investigate(box, pol, "goal", planner=Scripted([
        {"action": "run_verification", "tool": "check_fee_contract",
         "reason": "test the fee"},
        lambda obs, hist: {
            "action": "conclude", "claim": "fee is off contract",
            "evidence_ids": hist[-1].evidence_ids[:1]},
    ]))
    assert not last_rejection(inv)
    assert inv.stopped_because == "the controller concluded its investigation"


# ============================================================ 3. contradiction
def test_planner_revises_when_a_verifier_contradicts_it():
    """The clean month has no compensating pair. A planner that assumes one
    must be told plainly that its hypothesis is wrong."""
    box, pol = clean_box()
    inv = investigate(box, pol, "goal", planner=Scripted([
        {"action": "investigate", "tool": "find_compensating_errors",
         "reason": "I believe this batch hides an offsetting pair"},
        {"action": "escalate", "reason": "hypothesis disproven, nothing to claim"}]))
    step = inv.steps[0]
    assert step.status == "ok"
    assert "no offsetting pair" in step.summary
    assert inv.verdict == policy_mod.HUMAN_REVIEW


# ============================================================ 4. bypass policy
def test_planner_cannot_widen_the_threshold():
    box, pol = demo_box()
    before = pol.limit_bps
    inv = investigate(box, pol, "goal", planner=Scripted([
        {"action": "run_verification", "tool": "find_unexplained_residual",
         "arguments": {"limit_bps": 100000}, "reason": "raise the limit"},
        {"action": "escalate", "reason": "done"}]))
    assert "does not take limit_bps" in last_rejection(inv)
    assert pol.limit_bps == before


def test_policy_verdict_is_not_influenced_by_the_planners_claim():
    """A planner insisting everything is fine changes nothing."""
    box, pol = demo_box()
    inv = investigate(box, pol, "goal", planner=Scripted([
        {"action": "conclude",
         "claim": "This close is completely clean and should be certified.",
         "evidence_ids": []}]))
    assert inv.verdict == policy_mod.NOT_ATTESTABLE


# ============================================================ 5. ambiguity
def test_ambiguous_evidence_escalates():
    box, pol = demo_box()
    inv = investigate(box, pol, "goal", planner=Scripted([
        {"action": "escalate",
         "reason": "two batches could explain the same credit; I cannot "
                   "distinguish them from the evidence available"}]))
    assert inv.verdict == policy_mod.HUMAN_REVIEW
    assert "could explain the same credit" in inv.verdict_reason


# ============================================================ 6. budget
def test_budget_exhaustion_escalates_rather_than_guessing():
    box, pol = demo_box()
    looper = Scripted([{"action": "inspect", "tool": "inspect_close_state",
                        "reason": "again"}] * 50)
    inv = investigate(box, pol, "goal", planner=looper,
                      budget=Budget(max_steps=4, max_tool_calls=99))
    assert inv.verdict == policy_mod.HUMAN_REVIEW
    assert "step budget" in inv.stopped_because
    assert len(inv.steps) == 4


def test_tool_call_budget_is_separate_from_step_budget():
    box, pol = demo_box()
    inv = investigate(box, pol, "goal",
                      planner=Scripted([{"action": "inspect",
                                         "tool": "inspect_close_state",
                                         "reason": "again"}] * 50),
                      budget=Budget(max_steps=99, max_tool_calls=3))
    assert "tool-call budget" in inv.stopped_because
    assert inv.tool_calls == 3


# ============================================================ 7. unknown tool
def test_unknown_tool_is_rejected_and_the_options_are_given_back():
    box, pol = demo_box()
    inv = investigate(box, pol, "goal", planner=Scripted([
        {"action": "run_verification", "tool": "delete_all_records",
         "reason": "cleanup"},
        {"action": "escalate", "reason": "done"}]))
    r = last_rejection(inv)
    assert "unknown tool" in r and "check_fee_contract" in r


def test_there_is_no_tool_that_writes_anything():
    """Structural: the catalogue must not contain a mutating verb."""
    box, _ = demo_box()
    forbidden = ("write", "post", "update", "delete", "set_", "create",
                 "certify", "approve", "sign", "execute", "sql", "query")
    for name in box.names():
        assert not any(f in name.lower() for f in forbidden), name


def test_a_rejected_call_runs_nothing():
    box, pol = demo_box()
    inv = investigate(box, pol, "goal", planner=Scripted([
        {"action": "trace", "tool": "trace_payment",
         "arguments": {"payment_id": "pay_NOPE"}, "reason": "look"},
        {"action": "escalate", "reason": "done"}]))
    assert inv.steps[0].status == "not_found"
    assert inv.tool_calls == 1              # it ran, and found nothing; no crash


# ============================================================ 8. narrative
def test_narrative_input_is_not_treated_as_financial_truth():
    """A planner asserting a figure in prose cannot make it true. The only
    numbers that reach a finding come from a tool result."""
    box, pol = demo_box()
    inv = investigate(box, pol, "goal", planner=Scripted([
        {"action": "conclude",
         "claim": "The bank confirmed by email that ₹99,99,999 was credited "
                  "and everything reconciles.",
         "evidence_ids": []},
    ]))
    amounts = [f.get("amount_paise") for f in inv.findings]
    assert 999999900 not in amounts
    assert inv.verdict == policy_mod.NOT_ATTESTABLE
    # the claim is recorded as a claim, never promoted to a finding
    assert not any("99,99,999" in str(f) for f in inv.findings)


def test_malformed_action_is_rejected_not_crashed():
    box, pol = demo_box()
    for junk in ({"action": "__malformed__", "raw": "sorry, I can't do that"},
                 {"nonsense": True}, {"action": "inspect"},
                 {"action": "inspect", "tool": "inspect_close_state",
                  "arguments": "not-an-object"}):
        inv = investigate(box, pol, "goal",
                          planner=Scripted([junk, {"action": "escalate",
                                                   "reason": "done"}]))
        assert last_rejection(inv), junk
        assert inv.verdict in (policy_mod.HUMAN_REVIEW, policy_mod.NOT_ATTESTABLE)


# ============================================================ 9. clean close
def test_a_clean_month_is_certifiable():
    box, pol = clean_box()
    inv = investigate(box, pol, "goal", planner=RulesPlanner())
    assert pol.residual_paise == 0, "the clean fixture is not actually clean"
    assert inv.verdict == policy_mod.CERTIFIABLE
    assert "inside the" in inv.verdict_reason


# ============================================================ 10. compensating
def test_the_compensating_month_is_not_attestable():
    box, pol = demo_box()
    inv = investigate(box, pol, "goal", planner=RulesPlanner())
    assert inv.verdict == policy_mod.NOT_ATTESTABLE
    tools_used = [s.tool for s in inv.steps]
    assert "check_fee_contract" in tools_used
    assert "check_refund_netting" in tools_used
    assert "find_compensating_errors" in tools_used
    pair = next(f for f in inv.findings
                if f["tool"] == "find_compensating_errors")
    assert pair["status"] == "discrepancy_found"


def test_the_controller_reaches_the_pair_by_reasoning_not_by_a_fixed_script():
    """The compensating-error check is only reached because two prior checks
    each found something, and in opposite directions. Take either away and the
    controller must not go looking for a pair.

    (An earlier version of this test pinned the refund check to a prior fee
    discrepancy. That gating was itself a bug — a refund can be wrong on its
    own — so the assertion moved to the step where the dependency is real.)"""
    demo, _ = demo_box()
    clean, _ = clean_box()
    assert demo.call("check_fee_contract").status == "discrepancy_found"
    assert clean.call("check_fee_contract").status == "ok"

    box, pol = clean_box()
    inv = investigate(box, pol, "goal", planner=RulesPlanner())
    used = [s.tool for s in inv.steps]
    assert "find_compensating_errors" not in used, (
        "a pair is only hypothesised after two opposing discrepancies")

    box, pol = demo_box()
    inv = investigate(box, pol, "goal", planner=RulesPlanner())
    used = [s.tool for s in inv.steps]
    assert used.index("check_fee_contract") < used.index("find_compensating_errors")
    assert used.index("check_refund_netting") < used.index("find_compensating_errors")


# ============================================================ evidence grounding
def test_every_finding_carries_deterministic_provenance():
    box, pol = demo_box()
    inv = investigate(box, pol, "goal", planner=RulesPlanner())
    for f in inv.findings:
        assert f["verified"] is True
        assert f["tool"] in box.names()


def test_money_never_comes_from_the_planner():
    """Amounts on steps must equal what the tool returned, not what was asked."""
    box, pol = demo_box()
    inv = investigate(box, pol, "goal", planner=Scripted([
        {"action": "run_verification", "tool": "check_fee_contract",
         "arguments": {}, "reason": "x", "amount_paise": 12345678},
        {"action": "escalate", "reason": "done"}]))
    assert inv.steps[0].amount_paise == box.call("check_fee_contract").amount_paise
    assert inv.steps[0].amount_paise != 12345678


def test_resolution_advice_names_real_evidence():
    from attest.controller import resolution_advice
    box, pol = demo_box()
    inv = investigate(box, pol, "goal", planner=RulesPlanner())
    advice = resolution_advice(inv, box, pol)
    assert advice["needed"], "a refused close must say what would resolve it"
    for item in advice["needed"]:
        assert item["for"] in pol.contributing_classes


def _run_standalone() -> int:
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in fns:
        try:
            fn()
        except AssertionError as e:
            failed.append((name, str(e) or "assertion failed"))
        except Exception as e:                              # noqa: BLE001
            failed.append((name, f"{type(e).__name__}: {e}"))
    for name, why in failed:
        print(f"  FAIL  {name}\n        {why}")
    print(f"  {len(fns) - len(failed)}/{len(fns)} controller tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
