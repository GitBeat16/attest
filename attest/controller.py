"""The Attest Finance Controller — an investigation loop, not a chatbot.

The controller is handed a goal ("determine whether this close is safe to
certify"), an observation of the current state, and a catalogue of deterministic
tools. It then decides *what to look at next*, one step at a time, revising as
results come back. It stops when the evidence is sufficient, when it is
provably insufficient, or when it runs out of budget.

The division of labour is the whole design, and it is enforced structurally
rather than by instruction:

    AI investigates      — chooses the next action. That is all it does.
    Attest proves        — tools.py returns deterministic, evidence-backed facts.
    Policy decides       — policy.py computes the verdict; nothing else may.

A planner emits an **action**, which is validated before anything happens.
Unknown tool, malformed arguments, an evidence id that no tool returned, an
attempt to certify — all rejected, with the rejection fed back so the planner
can revise. The planner never touches money, records, thresholds or the verdict.

Two planners implement the same interface:

  · `RulesPlanner`  — deterministic, priority-driven. Always available, needs no
    key, no network. This is what the demo runs on, and it means every safety
    property is testable without a model in the loop.
  · `ModelPlanner`  — the same action schema, chosen by an LLM. It sees the
    observation and the tool catalogue; it never sees a raw record.

Because both go through the same validator, the same tools and the same policy
gate, the safety envelope does not depend on which one is driving. That is the
point: the model is given the wheel, never the money.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field

from .money import fmt
from .policy import CERTIFIABLE, HUMAN_REVIEW, NOT_ATTESTABLE
from .tools import ToolError, Toolbox

# ==========================================================================
# Budgets. A controller that cannot run out is a controller that can hang.
# ==========================================================================
@dataclass
class Budget:
    max_steps: int = 12
    max_tool_calls: int = 20
    max_model_calls: int = 14
    timeout_seconds: float = 30.0
    # A model call over the network costs seconds; a deterministic step costs
    # microseconds. Inside one serverless request a model cannot finish a full
    # investigation, so it gets a slice of the clock and the rules planner
    # finishes the rest. Stopping early with nothing established would be the
    # worse answer — and the handover is the architecture's own claim, that the
    # planner is the replaceable part.
    model_seconds: float = 13.0

    def exhausted(self, steps, tool_calls, model_calls, started) -> str:
        if steps >= self.max_steps:
            return f"step budget of {self.max_steps} exhausted"
        if tool_calls >= self.max_tool_calls:
            return f"tool-call budget of {self.max_tool_calls} exhausted"
        if model_calls >= self.max_model_calls:
            return f"model-call budget of {self.max_model_calls} exhausted"
        if time.time() - started > self.timeout_seconds:
            return f"timed out after {self.timeout_seconds:.0f}s"
        return ""


# ==========================================================================
ACTIONS = ("inspect", "trace", "run_verification", "investigate",
           "request_evidence", "escalate", "conclude")


@dataclass
class Step:
    n: int
    action: str
    tool: str = ""
    arguments: dict = field(default_factory=dict)
    reason: str = ""
    status: str = ""
    summary: str = ""
    evidence_ids: list[str] = field(default_factory=list)
    amount_paise: int | None = None
    rejected: str = ""
    source: str = "rules"          # which planner chose this step

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.amount_paise is not None:
            d["amount_display"] = fmt(self.amount_paise)
        return d


@dataclass
class Investigation:
    goal: str
    steps: list[Step] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    verdict: str = ""
    verdict_reason: str = ""
    evidence_seen: list[str] = field(default_factory=list)
    stopped_because: str = ""
    planner: str = "rules"
    model_calls: int = 0
    tool_calls: int = 0
    seconds: float = 0.0
    handover: str = ""
    missing_evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "goal": self.goal,
            "planner": self.planner,
            "steps": [s.to_dict() for s in self.steps],
            "findings": self.findings,
            "verdict": self.verdict,
            "verdict_reason": self.verdict_reason,
            "stopped_because": self.stopped_because,
            "handover": self.handover,
            "evidence_seen": self.evidence_seen,
            "missing_evidence": self.missing_evidence,
            "tool_calls": self.tool_calls,
            "model_calls": self.model_calls,
            "seconds": round(self.seconds, 3),
        }


# ==========================================================================
# Action validation. Every rejection path a planner can hit is here.
# ==========================================================================
def validate(action: dict, box: Toolbox, evidence_seen: set[str]) -> str:
    """Return an empty string if the action is allowed, else why it is not."""
    if not isinstance(action, dict):
        return "an action must be an object"
    a = action.get("action")
    if a not in ACTIONS:
        return (f"unknown action {a!r}. Allowed: {', '.join(ACTIONS)}. "
                "Certification is not an action available to you — the policy "
                "engine decides that, from the evidence you gather.")

    # The planner may never certify, at any point, under any argument.
    if a == "conclude":
        claim = action.get("claim", "")
        if not isinstance(claim, str) or not claim.strip():
            return "conclude requires a claim describing what you established"
        cited = action.get("evidence_ids") or []
        if not isinstance(cited, list):
            return "evidence_ids must be a list"
        # An id the planner did not receive from a tool is an invented id.
        invented = [e for e in cited if e not in evidence_seen]
        if invented:
            return (f"evidence {', '.join(map(str, invented[:3]))} was never "
                    "returned by a tool. Cite only evidence you have actually "
                    "seen; you may not introduce record ids of your own.")
        return ""

    if a in ("escalate", "request_evidence"):
        if not str(action.get("reason", "")).strip():
            return f"{a} requires a reason"
        return ""

    tool = action.get("tool")
    if not tool:
        return f"{a} requires a tool"
    if tool not in box.names():
        return (f"unknown tool {tool!r}. Available: {', '.join(sorted(box.names()))}")
    args = action.get("arguments")
    if args is not None and not isinstance(args, dict):
        return "arguments must be an object"
    return ""


# ==========================================================================
# Planners
# ==========================================================================
class RulesPlanner:
    """A deterministic investigator.

    Not a fallback in the apologetic sense — it is the reference implementation
    of the investigation policy, and the reason every safety property here can
    be tested without a model. It follows the same evidence-driven order a
    finance controller would: establish the state, find where proof is missing,
    test the specific hypotheses that state suggests, then stop.

    It is honest about what it is: `source` on every step it produces says
    `rules`, and the UI labels it accordingly. Nothing here pretends to be a
    model.
    """

    name = "rules"

    def __init__(self):
        self._plan: list[dict] = []
        self._seeded = False

    def propose(self, observation: dict, history: list[Step]) -> dict:
        if not self._seeded:
            self._seeded = True
            self._plan = [{
                "action": "inspect", "tool": "inspect_close_state",
                "reason": "Establish what is proven before deciding what to test."}]

        if self._plan:
            return self._plan.pop(0)

        state = observation.get("state") or {}
        done = {s.tool for s in history}

        # The order below is the investigation policy, written as branching on
        # what the evidence has actually shown so far.
        if "list_exceptions" not in done:
            return {"action": "inspect", "tool": "list_exceptions",
                    "reason": "Rank the open exceptions by exposure so the "
                              "largest money is investigated first."}

        # An aggregate match with unproven lines underneath is the signature
        # worth attacking: agreement at the total says nothing about the parts.
        tied = state.get("batches_tied", 0)
        total = state.get("batches_total", 0)
        unproven = state.get("unproven_by_stage") or {}

        # Fees are checked whenever fees exist. An earlier version only tested
        # them when a batch tied, on the theory that a tie is what hides an
        # error -- which missed the simpler case of a fee that is wrong in a
        # batch that does not tie at all. Follow the unproven stages instead of
        # a single heuristic.
        if "check_fee_contract" not in done:
            return {"action": "run_verification", "tool": "check_fee_contract",
                    "reason": (f"{tied} of {total} batches tie to the bank"
                               + (", but a tie is between two documents Razorpay "
                                  "produced" if tied else "")
                               + (f"; {unproven['mdr']} lines are unproven at the "
                                  "fee stage" if "mdr" in unproven else "")
                               + ". Test the fee component against the contract.")}

        fee_found = any(s.tool == "check_fee_contract" and
                        s.status == "discrepancy_found" for s in history)
        # The refund deduction is rebuilt whenever a batch carries one, not only
        # after a fee discrepancy: a refund can be wrong on its own, and gating
        # this behind the fee check made that case invisible.
        if "check_refund_netting" not in done:
            return {"action": "run_verification", "tool": "check_refund_netting",
                    "reason": ("A fee discrepancy inside a batch that still ties "
                               "implies something absorbed it — rebuild the "
                               "expected refund independently and compare."
                               if fee_found else
                               "Rebuild the expected refund deduction from the "
                               "merchant's own export, independently of the "
                               "report under audit.")}

        refund_found = any(s.tool == "check_refund_netting" and
                           s.status == "discrepancy_found" for s in history)
        if fee_found and refund_found and "find_compensating_errors" not in done:
            return {"action": "investigate", "tool": "find_compensating_errors",
                    "reason": "Two discrepancies of opposite sign in one batch "
                              "is the compensating-error signature. Confirm it."}

        if "gst" in unproven and "check_tax_calculation" not in done:
            return {"action": "run_verification", "tool": "check_tax_calculation",
                    "reason": "Tax stages are unproven; GST inherits any error "
                              "in the fee it is charged on."}
        if "credit" in unproven and "check_settlement_resolution" not in done:
            return {"action": "run_verification", "tool": "check_settlement_resolution",
                    "reason": "Credit stages are unproven — check how bank "
                              "credits resolved to batches before assuming loss."}
        if "run_adversarial_check" not in done:
            return {"action": "run_verification", "tool": "run_adversarial_check",
                    "reason": "Put every match that passed under falsification "
                              "before treating any of them as evidence."}
        if "find_unexplained_residual" not in done:
            return {"action": "inspect", "tool": "find_unexplained_residual",
                    "reason": "Quantify what remains unattributed, which is what "
                              "the certification rule is measured against."}

        # Deadlines matter only once there is something to claim.
        big = observation.get("top_claimable")
        if big and "get_claim_deadline" not in done:
            return {"action": "inspect", "tool": "get_claim_deadline",
                    "arguments": {"exception_class": big},
                    "reason": f"{big} carries the largest claimable exposure; "
                              "establish whether the window is still open."}

        cited = [s for s in history if s.evidence_ids]
        return {"action": "conclude",
                "claim": "The evidence gathered is sufficient to put this close "
                         "to the certification rule.",
                "evidence_ids": [e for s in cited for e in s.evidence_ids][:8],
                "reason": "Every stage that could be tested has been tested."}


class ModelPlanner:
    """The same interface, with a language model choosing the next action.

    It is given the goal, the observation, the tool catalogue and the history so
    far, and must reply with one action object. It is not given records, keys,
    file contents or the ability to run anything. If it returns malformed JSON,
    an unknown tool or an invented evidence id, the action is rejected and the
    rejection is handed back as the next observation — which is how a planner
    learns mid-investigation without any of it touching the ledger.
    """

    name = "model"

    SYSTEM = """You are the Attest Finance Controller.

Your goal is to determine whether a financial close has sufficient evidence to
be safely certified. You are an investigator and an orchestrator. You are not
the financial authority.

You may: inspect state, form hypotheses, choose verification tools, trace
records, prioritise by exposure and deadline, request evidence, and escalate.

You may not: invent financial values, invent record ids, alter evidence,
override a verifier, change a threshold, post to a ledger, or certify. The
policy engine decides certification from the evidence you gather. There is no
action that certifies.

Every material claim must rest on evidence returned by an approved tool. When a
verifier contradicts your hypothesis, revise the hypothesis — do not restate it.
When the evidence is insufficient or conflicting, escalate.

Never optimise for the number of closes certified. A refusal supported by
evidence is a correct outcome. A certification that is not is the only real
failure.

Reply with exactly one JSON object and nothing else:
{"action": "<inspect|trace|run_verification|investigate|request_evidence|escalate|conclude>",
 "tool": "<tool name, if the action needs one>",
 "arguments": {},
 "reason": "<one sentence: what you are testing and why>"}
For "conclude", include "claim" and "evidence_ids" citing only ids a tool returned."""

    def __init__(self, engine):
        self.engine = engine
        self.calls = 0
        self.last_error = ""
        # If the model drops out mid-investigation — rate limit, timeout, a
        # network blip on stage — the loop must keep going on the deterministic
        # planner rather than taking the whole investigation down with it. The
        # planner is the replaceable part; that is the entire architecture.
        self.degraded = False
        self._rules = RulesPlanner()

    def propose(self, observation: dict, history: list[Step]) -> dict:
        prompt = json.dumps({
            "goal": observation.get("goal"),
            "state": observation.get("state"),
            "tools": observation.get("tools"),
            "history": [{"tool": s.tool, "status": s.status,
                         "summary": s.summary, "rejected": s.rejected}
                        for s in history[-6:]],
            "budget_left": observation.get("budget_left"),
            "last_rejection": observation.get("last_rejection", ""),
        }, default=str)[:6000]

        self.calls += 1
        try:
            raw = self.engine.plan(self.SYSTEM, prompt)
        except Exception as e:                               # noqa: BLE001
            self.degraded = True
            self.last_error = f"model unavailable: {type(e).__name__}"
            return self._rules.propose(observation, history)
        try:
            start, end = raw.index("{"), raw.rindex("}") + 1
            return json.loads(raw[start:end])
        except (ValueError, json.JSONDecodeError) as e:
            self.last_error = f"unparseable action: {e}"
            return {"action": "__malformed__", "raw": raw[:200]}


# ==========================================================================
def investigate(box: Toolbox, policy, goal: str, *, planner=None,
                budget: Budget | None = None) -> Investigation:
    """Run the loop. This function is the only thing that calls a tool."""
    budget = budget or Budget()
    planner = planner or RulesPlanner()
    inv = Investigation(goal=goal, planner=planner.name)

    started = time.time()
    evidence_seen: set[str] = set()
    last_rejection = ""
    top_claimable = next(
        (e["class"] for e in box._ex if e.get("kind") != "verdict"), "")

    while True:
        why = budget.exhausted(len(inv.steps), inv.tool_calls,
                               inv.model_calls, started)
        if why:
            inv.stopped_because = why
            inv.verdict, inv.verdict_reason = policy.decide(
                evidence_sufficient=False,
                reason=f"The investigation stopped early: {why}. What was "
                       "established is recorded above; the rest is unexamined.")
            break

        state = {}
        for f in inv.findings:
            if f["tool"] == "inspect_close_state":
                state = f.get("detail", {})
        observation = {
            "goal": goal,
            "state": state,
            "tools": box.catalogue(),
            "budget_left": {"steps": budget.max_steps - len(inv.steps),
                            "tool_calls": budget.max_tool_calls - inv.tool_calls},
            "last_rejection": last_rejection,
            "top_claimable": top_claimable,
        }

        # the model's slice of the clock is up: finish deterministically
        # rather than abandoning the close half-examined
        if (getattr(planner, "name", "") == "model"
                and time.time() - started > budget.model_seconds
                and len(inv.steps) < budget.max_steps):
            done_by_model = len(inv.steps)
            planner = RulesPlanner()
            inv.handover = (
                f"the model planned the first {done_by_model} step"
                f"{'' if done_by_model == 1 else 's'} and used its "
                f"{budget.model_seconds:.0f}s slice; the deterministic planner "
                "finished the investigation")

        action = planner.propose(observation, inv.steps)
        if getattr(planner, "name", "") == "model":
            inv.model_calls = getattr(planner, "calls", inv.model_calls + 1)

        n = len(inv.steps) + 1
        problem = validate(action, box, evidence_seen)
        if problem:
            last_rejection = problem
            inv.steps.append(Step(n=n, action=str(action.get("action"))[:40],
                                  tool=str(action.get("tool", ""))[:60],
                                  reason=str(action.get("reason", ""))[:300],
                                  status="rejected", rejected=problem,
                                  source=planner.name))
            continue
        last_rejection = ""

        act = action["action"]

        if act == "escalate":
            inv.steps.append(Step(n=n, action=act, reason=action["reason"][:300],
                                  status="escalated", source=planner.name))
            inv.stopped_because = "the controller escalated"
            inv.verdict, inv.verdict_reason = policy.decide(
                evidence_sufficient=False, reason=action["reason"][:300])
            break

        if act == "request_evidence":
            want = action.get("arguments", {}) or {}
            asked = [str(v) for v in want.values()] or [action["reason"][:120]]
            inv.missing_evidence.extend(asked)
            inv.steps.append(Step(n=n, action=act, reason=action["reason"][:300],
                                  status="evidence_requested",
                                  summary="; ".join(asked)[:200],
                                  source=planner.name))
            continue

        if act == "conclude":
            inv.steps.append(Step(
                n=n, action=act, reason=str(action.get("reason", ""))[:300],
                status="concluded", summary=action["claim"][:300],
                evidence_ids=action.get("evidence_ids", [])[:8],
                source=planner.name))
            inv.stopped_because = "the controller concluded its investigation"
            inv.verdict, inv.verdict_reason = policy.decide(evidence_sufficient=True)
            break

        # Everything else is a tool call.
        try:
            result = box.call(action["tool"], action.get("arguments"))
        except ToolError as e:
            last_rejection = str(e)
            inv.steps.append(Step(n=n, action=act, tool=str(action["tool"])[:60],
                                  arguments=action.get("arguments") or {},
                                  reason=str(action.get("reason", ""))[:300],
                                  status="rejected", rejected=str(e),
                                  source=planner.name))
            continue

        inv.tool_calls += 1
        evidence_seen.update(result.evidence_ids)
        inv.steps.append(Step(
            n=n, action=act, tool=result.tool,
            arguments=action.get("arguments") or {},
            reason=str(action.get("reason", ""))[:300],
            status=result.status, summary=result.summary,
            evidence_ids=result.evidence_ids[:6],
            amount_paise=result.amount_paise, source=planner.name))
        inv.findings.append(result.to_dict())

    inv.evidence_seen = sorted(evidence_seen)[:40]
    inv.seconds = time.time() - started
    return inv


# ==========================================================================
def resolution_advice(inv: Investigation, box: Toolbox, policy) -> dict:
    """Why the close cannot be signed, and what evidence would change that.

    The controller proposes; the policy engine still decides whether the
    proposed evidence would actually be sufficient. Both halves are here so the
    UI can show the question and the answer together.
    """
    if policy.within_limit and inv.verdict == CERTIFIABLE:
        return {"why": policy.decide(evidence_sufficient=True)[1], "needed": []}

    needed, seen = [], set()
    for e in box._ex:
        if e["class"] not in policy.contributing_classes:
            continue
        for sample in e.get("sample", [])[:2]:
            item = {"evidence": e.get("evidence_required", "investigation"),
                    "for": e["class"], "reference": sample,
                    "exposure_display": fmt(e["exposure"])}
            key = (item["for"], item["reference"])
            if key not in seen:
                seen.add(key)
                needed.append(item)
    return {"why": inv.verdict_reason, "needed": needed[:6],
            "would_resolve_paise": policy.residual_paise,
            "note": "Supplying this evidence lets the residual be attributed. "
                    "Whether it is then inside the limit is decided by the "
                    "policy engine, not by the controller."}


# ==========================================================================
def run(src, *, planner_name: str = "auto", budget: Budget | None = None,
        goal: str = "Determine whether this close is safe to certify.") -> dict:
    """Close a month, then investigate it. Returns everything the UI needs."""
    from . import audit as audit_mod
    from . import engine as engine_mod
    from . import policy as policy_mod
    from .close import SOURCES
    from .ingest import load, resolve
    from .recovery import build_claims
    from .run import build_exceptions
    from datetime import date, timedelta

    corpus = load(src)
    resolve(corpus)
    res = engine_mod.run(corpus)
    aud = audit_mod.run(corpus, res.batch_ties)
    exceptions = build_exceptions(corpus, res, aud)

    volume = sum(o.gross for o in corpus.orders.values()) or sum(
        l.gross for b in corpus.batches.values() for l in b.lines)
    pol = policy_mod.assess(volume, exceptions)

    y, m = (int(x) for x in corpus.mdr_invoice["period"].split("-"))
    period_end = date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
    today = max(date.today(), period_end + timedelta(days=1))
    claims = build_claims(exceptions, period_end, today)

    box = Toolbox(corpus, res, aud, exceptions, claims, today, pol)

    planner = None
    # Why the planner is what it is, in words. A model that silently does not
    # run is worse than one that is absent: the panel would claim a
    # deterministic planner was chosen when in fact something failed. Never
    # includes a key value — only whether one is present.
    engine_note = ""
    if planner_name in ("auto", "model"):
        try:
            from . import engines
            eng = engines.get_engine()
            ename = getattr(eng, "name", "rules")
            if ename != "rules" and hasattr(eng, "plan"):
                planner = ModelPlanner(eng)
                engine_note = f"engine {ename} resolved"
            else:
                want = os.environ.get("ATTEST_ENGINE", "").strip() or "unset"
                keys = [k for k in ("GEMINI_API_KEY", "OPENAI_API_KEY",
                                    "ANTHROPIC_API_KEY", "OLLAMA_HOST")
                        if os.environ.get(k, "").strip()]
                engine_note = (f"resolved to '{ename}'; ATTEST_ENGINE={want}; "
                               f"credentials present: {', '.join(keys) or 'none'}")
        except Exception as e:                               # noqa: BLE001
            planner = None
            engine_note = f"engine setup raised {type(e).__name__}: {e}"[:180]
    if planner is None:
        if planner_name == "model":
            # Asked for explicitly and unavailable: say so rather than pretend.
            planner = RulesPlanner()
            unavailable = True
        else:
            planner = RulesPlanner()
            unavailable = False
    else:
        unavailable = False

    inv = investigate(box, pol, goal, planner=planner, budget=budget)
    out = inv.to_dict()
    out["policy"] = {
        "verdict": inv.verdict, "reason": inv.verdict_reason,
        "residual_paise": pol.residual_paise, "residual_bps": pol.residual_bps,
        "limit_bps": pol.limit_bps, "volume_paise": pol.volume_paise,
        "residual_display": fmt(pol.residual_paise),
    }
    out["resolution"] = resolution_advice(inv, box, pol)
    out["model_requested_but_unavailable"] = unavailable
    out["engine_note"] = engine_note
    out["degraded"] = bool(getattr(planner, "degraded", False))
    if getattr(planner, "last_error", ""):
        out["engine_note"] = (engine_note + " | "
                              + planner.last_error).strip(" |")
    return out


if __name__ == "__main__":
    import argparse
    import tempfile
    from pathlib import Path as _P

    ap = argparse.ArgumentParser(description="Investigate a close.")
    ap.add_argument("--data", type=_P, help="a data/sources directory")
    ap.add_argument("--demo", action="store_true", help="use the demo month")
    ap.add_argument("--planner", default="auto", choices=["auto", "rules", "model"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.demo or not a.data:
        from .demo import build
        tmp = tempfile.TemporaryDirectory()
        src = _P(tmp.name) / "sources"
        src.mkdir()
        for name, body in build().items():
            (src / name).write_text(body, encoding="utf-8")
    else:
        src = a.data

    out = run(src, planner_name=a.planner)
    if a.json:
        print(json.dumps(out, indent=2))
        raise SystemExit(0)

    ICON = {"ok": "\u2713", "discrepancy_found": "\u26a0",
            "rejected": "\u2717", "not_found": "\u2717",
            "insufficient_evidence": "\u26a0", "concluded": "\u25cf",
            "escalated": "\u25b3", "evidence_requested": "?"}
    print("\n  ATTEST CONTROLLER")
    print("  " + "=" * 66)
    print(f"  goal      {out['goal']}")
    print(f"  planner   {out['planner']}"
          + ("  (a model was requested but none is configured)"
             if out["model_requested_but_unavailable"] else ""))
    print("  " + "-" * 66)
    for s in out["steps"]:
        print(f"  {ICON.get(s['status'], ' ')} {s['n']:>2}. "
              f"{(s['tool'] or s['action']):<28} {s['summary'][:60]}")
        if s["reason"]:
            print(f"        why: {s['reason'][:82]}")
        if s["rejected"]:
            print(f"        REJECTED: {s['rejected'][:78]}")
    print("  " + "-" * 66)
    print(f"  stopped   {out['stopped_because']}")
    print(f"  steps {len(out['steps'])} \u00b7 tool calls {out['tool_calls']} "
          f"\u00b7 model calls {out['model_calls']} \u00b7 {out['seconds']}s")
    print()
    print(f"  DECISION  {out['policy']['verdict']}")
    print(f"            {out['policy']['reason']}")
    if out["resolution"].get("needed"):
        print("\n  WHAT WOULD RESOLVE IT")
        for item in out["resolution"]["needed"]:
            print(f"    \u00b7 {item['evidence'][:66]}")
            print(f"      for {item['for']} ({item['exposure_display']}), "
                  f"ref {item['reference']}")
    print()
