"""The deterministic tools the controller is allowed to call.

Every tool here is a thin, validated wrapper over machinery that already
existed: `engine.py` builds the proof chains, `audit.py` runs the falsification
hypotheses, `recovery.py` owns the claim clocks, `money.py` owns the
arithmetic. Nothing in this module computes a financial value of its own — it
reads what the deterministic engine already established and returns it in a
shape a planner can reason over.

The contract, which is the entire safety story:

  · a tool call is a **request**, validated before it runs — unknown tool
    rejected, bad arguments rejected, no free-form anything
  · a tool returns a **structured result** with `verified: true` and the ids of
    the records the answer rests on, never prose
  · a tool never writes. There is no mutation in this file, by construction:
    no ledger, no database, no filesystem, no network
  · every monetary figure is an integer in paise, taken from the engine

The planner above this layer decides *what to ask*. It never decides what is
true. That division is why a model can be given the wheel here without being
given the money.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable

from .money import fmt, pct


# ==========================================================================
@dataclass
class ToolResult:
    """What every tool returns. Deliberately boring and always the same shape."""
    status: str                      # ok | discrepancy_found | not_found | insufficient_evidence
    tool: str
    verified: bool = True            # produced by deterministic code, not a model
    summary: str = ""
    amount_paise: int | None = None
    expected: Any = None
    actual: Any = None
    evidence_ids: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"status": self.status, "tool": self.tool, "verified": self.verified,
             "summary": self.summary, "evidence_ids": self.evidence_ids}
        if self.amount_paise is not None:
            d["amount_paise"] = self.amount_paise
            d["amount_display"] = fmt(self.amount_paise)
        if self.expected is not None:
            d["expected"] = self.expected
        if self.actual is not None:
            d["actual"] = self.actual
        if self.detail:
            d["detail"] = self.detail
        return d


class ToolError(Exception):
    """A rejected call. Never a crash, and never a partial result."""


# ==========================================================================
@dataclass
class Spec:
    name: str
    args: dict[str, bool]            # argument name -> required
    purpose: str
    fn: Callable


class Toolbox:
    """The only surface the controller can reach.

    Construction takes an already-closed corpus. The controller is handed the
    box, not the corpus — so there is no path from a planner to the raw records
    except through a validated call.
    """

    def __init__(self, corpus, res, aud, exceptions, claims, today, policy):
        self._c, self._res, self._aud = corpus, res, aud
        self._ex, self._claims, self._today = exceptions, claims, today
        self._policy = policy
        self._specs: dict[str, Spec] = {}
        self._register()

    # ---------------------------------------------------------------- registry
    def _add(self, name, args, purpose, fn):
        self._specs[name] = Spec(name, args, purpose, fn)

    def catalogue(self) -> list[dict]:
        """What the planner is told it may call. No implementation detail."""
        return [{"tool": s.name,
                 "arguments": {k: ("required" if v else "optional")
                               for k, v in s.args.items()},
                 "purpose": s.purpose}
                for s in self._specs.values()]

    def names(self) -> list[str]:
        return list(self._specs)

    # ---------------------------------------------------------------- calling
    def call(self, tool: str, arguments: dict | None = None) -> ToolResult:
        """Validate, then run. Every rejection path is here and explicit."""
        if not isinstance(tool, str) or tool not in self._specs:
            raise ToolError(
                f"unknown tool {tool!r}. Available: {', '.join(sorted(self._specs))}")
        spec = self._specs[tool]
        args = arguments if isinstance(arguments, dict) else {}

        unknown = set(args) - set(spec.args)
        if unknown:
            raise ToolError(
                f"{tool} does not take {', '.join(sorted(unknown))}. "
                f"It takes: {', '.join(spec.args) or 'no arguments'}")
        missing = [k for k, req in spec.args.items() if req and not args.get(k)]
        if missing:
            raise ToolError(f"{tool} requires {', '.join(missing)}")
        for k, v in args.items():
            if not isinstance(v, (str, int)):
                raise ToolError(f"{tool}: {k} must be a string or an integer")
            if isinstance(v, str) and len(v) > 120:
                raise ToolError(f"{tool}: {k} is too long to be a record id")

        return spec.fn(**{k: v for k, v in args.items()})

    # ================================================================ the tools
    def _register(self) -> None:
        self._add("inspect_close_state", {},
                  "The three rates, the residual, and what is still unproven.",
                  self._close_state)
        self._add("list_exceptions", {},
                  "Every open exception, ranked by rupee exposure, with its class.",
                  self._list_exceptions)
        self._add("inspect_exception", {"exception_class": True},
                  "One exception class in detail: exposure, count, evidence needed.",
                  self._inspect_exception)
        self._add("check_fee_contract", {"batch_id": False},
                  "Compare the fee actually charged against the contracted rate.",
                  self._check_fee)
        self._add("check_tax_calculation", {"batch_id": False},
                  "Check GST was computed on the correct base.",
                  self._check_tax)
        self._add("check_refund_netting", {"batch_id": False},
                  "Rebuild the expected refund deduction from the merchant's own "
                  "export and the contract, independently of the settlement report.",
                  self._check_refund)
        self._add("find_compensating_errors", {},
                  "Look for two errors of opposite sign that cancel inside a batch.",
                  self._compensating)
        self._add("run_adversarial_check", {},
                  "Run every falsification hypothesis against the matches that passed.",
                  self._adversarial)
        self._add("check_settlement_resolution", {},
                  "How bank credits were resolved to batches, and what stayed ambiguous.",
                  self._resolution)
        self._add("trace_payment", {"payment_id": True},
                  "The full evidence chain for one payment, and where it breaks.",
                  self._trace_payment)
        self._add("trace_settlement", {"batch_id": True},
                  "One settlement batch: lines, refunds, whether it tied to the bank.",
                  self._trace_settlement)
        self._add("find_unexplained_residual", {},
                  "The exposure that cannot be attributed to any cause, in bps.",
                  self._residual)
        self._add("get_claim_deadline", {"exception_class": True},
                  "Counterparty, claim window and days remaining for an exception.",
                  self._deadline)
        self._add("get_evidence_chain", {"payment_id": True},
                  "The order-to-bank chain for a payment, stage by stage.",
                  self._trace_payment)

    # ---------------------------------------------------------------- state
    def _close_state(self) -> ToolResult:
        ties = self._res.batch_ties
        tied = sum(1 for v in ties.values() if v)
        proven = sum(1 for c in self._res.chains if c.complete)
        total = len(self._res.chains)
        unproven = {}
        for c in self._res.chains:
            if not c.complete and c.broke_at:
                unproven[c.broke_at] = unproven.get(c.broke_at, 0) + 1
        return ToolResult(
            status="ok", tool="inspect_close_state",
            summary=(f"{tied} of {len(ties)} batches tie to the bank; "
                     f"{proven} of {total} lines are proven end to end."),
            evidence_ids=sorted(ties),
            detail={
                "batches_total": len(ties), "batches_tied": tied,
                "lines_total": total, "lines_proven": proven,
                "match_rate_pct": round(tied / len(ties) * 100, 1) if ties else 0,
                "proof_rate_pct": round(proven / total * 100, 1) if total else 0,
                "matches_overturned": len(self._aud.overturned),
                "matches_tested": self._aud.tested,
                "unproven_by_stage": unproven,
                "residual_paise": self._policy.residual_paise,
                "residual_bps": self._policy.residual_bps,
                "policy_limit_bps": self._policy.limit_bps,
            })

    def _list_exceptions(self) -> ToolResult:
        rows = [{"exception_class": e["class"], "kind": e.get("kind", "chain"),
                 "count": e["count"], "exposure_paise": e["exposure"],
                 "exposure_display": fmt(e["exposure"]),
                 "occurred_on": e.get("occurred_on")}
                for e in self._ex[:20]]
        return ToolResult(
            status="ok", tool="list_exceptions",
            summary=f"{len(self._ex)} exception groups, ranked by exposure.",
            evidence_ids=[r["exception_class"] for r in rows],
            detail={"exceptions": rows})

    def _inspect_exception(self, exception_class: str) -> ToolResult:
        hits = [e for e in self._ex
                if e["class"].upper() == str(exception_class).upper()]
        if not hits:
            return ToolResult(status="not_found", tool="inspect_exception",
                              summary=f"no exception of class {exception_class}",
                              detail={"known": sorted({e['class'] for e in self._ex})})
        total = sum(e["exposure"] for e in hits)
        return ToolResult(
            status="discrepancy_found", tool="inspect_exception",
            summary=(f"{exception_class}: {sum(e['count'] for e in hits)} items, "
                     f"{fmt(total)} exposure."),
            amount_paise=total,
            evidence_ids=[s for e in hits for s in e.get("sample", [])][:8],
            detail={"groups": len(hits), "kind": hits[0].get("kind", "chain"),
                    "evidence_required": hits[0].get("evidence_required", ""),
                    "claimable": hits[0].get("kind") != "verdict"})

    # ---------------------------------------------------------------- checks
    def _rate(self, key: str) -> Decimal:
        return Decimal(self._c.terms[key])

    def _fee_scope(self, batch_id):
        items = self._c.batches.items()
        if batch_id:
            items = [(k, v) for k, v in items if k == batch_id]
            if not items:
                raise ToolError(f"no settlement batch {batch_id}")
        return items

    def _check_fee(self, batch_id: str = "") -> ToolResult:
        rate = self._rate("contracted_mdr_rate_pct")
        over, lines, worst = 0, 0, None
        for sid, b in self._fee_scope(batch_id):
            for ln in b.lines:
                d = ln.mdr - pct(ln.gross, rate)
                if d:
                    over += d
                    lines += 1
                    if worst is None or abs(d) > abs(worst[1]):
                        worst = (ln.payment_id, d, ln.gross, ln.mdr)
        if not lines:
            return ToolResult(status="ok", tool="check_fee_contract",
                              summary=f"every fee matches the contracted {rate}%.",
                              expected=f"{rate}%", actual=f"{rate}%")
        eff = (Decimal(worst[3]) / Decimal(worst[2]) * 100) if worst[2] else Decimal(0)
        return ToolResult(
            status="discrepancy_found", tool="check_fee_contract",
            summary=(f"{lines} line(s) charged off-contract; {fmt(over)} "
                     f"{'over' if over > 0 else 'under'} in total."),
            amount_paise=over,
            expected=f"{rate}%", actual=f"{eff:.2f}%",
            evidence_ids=[worst[0]] if worst else [],
            detail={"lines_affected": lines, "worst_line": worst[0] if worst else "",
                    "scope": batch_id or "all batches"})

    def _check_tax(self, batch_id: str = "") -> ToolResult:
        mdr_rate, gst_rate = (self._rate("contracted_mdr_rate_pct"),
                              self._rate("gst_on_mdr_rate_pct"))
        over, lines = 0, 0
        ids = []
        for sid, b in self._fee_scope(batch_id):
            for ln in b.lines:
                d = ln.gst_on_mdr - pct(pct(ln.gross, mdr_rate), gst_rate)
                if d:
                    over += d
                    lines += 1
                    if len(ids) < 3:
                        ids.append(ln.payment_id)
        if not lines:
            return ToolResult(status="ok", tool="check_tax_calculation",
                              summary=f"GST computed on the contracted base throughout.")
        return ToolResult(
            status="discrepancy_found", tool="check_tax_calculation",
            summary=(f"GST on {lines} line(s) does not follow from the contracted "
                     f"fee; {fmt(over)} in total."),
            amount_paise=over, expected=f"{gst_rate}% of the contracted fee",
            actual=f"{gst_rate}% of the fee actually charged",
            evidence_ids=ids,
            detail={"lines_affected": lines,
                    "note": "the tax inherits any error in the fee it is charged on"})

    def _check_refund(self, batch_id: str = "") -> ToolResult:
        """Rebuild the expected deduction from the merchant's own export."""
        from .audit import _business_days_after

        ordered = sorted(self._c.batches.values(), key=lambda x: x.settled_on)
        expected: dict[str, int] = {}
        for r in self._c.refunds:
            due = _business_days_after(r.initiated_on, 5)
            target = next((b for b in ordered if b.settled_on >= due), None)
            if target:
                expected[target.settlement_id] = (
                    expected.get(target.settlement_id, 0) + r.amount)

        rows, worst = [], None
        for sid, b in self._fee_scope(batch_id):
            var = expected.get(sid, 0) - (-b.refund_adj)
            if var:
                rows.append({"batch_id": sid, "expected_paise": expected.get(sid, 0),
                             "stated_paise": -b.refund_adj, "variance_paise": var})
                if worst is None or abs(var) > abs(worst["variance_paise"]):
                    worst = rows[-1]
        if not rows:
            return ToolResult(status="ok", tool="check_refund_netting",
                              summary="every refund deduction matches the refund export.")
        return ToolResult(
            status="discrepancy_found", tool="check_refund_netting",
            summary=(f"{len(rows)} batch(es) deduct a different refund amount than "
                     f"the refund export implies; worst is {fmt(worst['variance_paise'])} "
                     f"on {worst['batch_id']}."),
            amount_paise=worst["variance_paise"],
            expected=fmt(worst["expected_paise"]), actual=fmt(worst["stated_paise"]),
            evidence_ids=[r["batch_id"] for r in rows][:5],
            detail={"batches": rows[:5],
                    "method": "rebuilt from refunds.csv plus the contract netting "
                              "rule, never from the settlement report under audit"})

    def _compensating(self) -> ToolResult:
        pairs = [a for a in self._aud.overturned
                 if a.hypothesis == "offsetting_pair"]
        if not pairs:
            return ToolResult(
                status="ok", tool="find_compensating_errors",
                summary="no offsetting pair found in any batch.")
        worst = max(pairs, key=lambda a: a.delta)
        ev = worst.evidence
        return ToolResult(
            status="discrepancy_found", tool="find_compensating_errors",
            summary=(f"{len(pairs)} compensating pair(s). In {worst.target} the fee "
                     f"is {fmt(abs(ev['line_variance_paise']))} out and the refund "
                     f"{fmt(abs(ev['refund_variance_paise']))} out the other way; "
                     f"they cancel to {fmt(abs(ev['residual_paise']))}."),
            amount_paise=abs(ev["line_variance_paise"]) + abs(ev["refund_variance_paise"]),
            expected="a variance proportional to the error",
            actual=fmt(abs(ev["residual_paise"])),
            evidence_ids=[worst.target, ev.get("worst_line", "")],
            detail={"pairs": len(pairs), "batch_id": worst.target,
                    "line_variance_paise": ev["line_variance_paise"],
                    "refund_variance_paise": ev["refund_variance_paise"],
                    "residual_paise": ev["residual_paise"],
                    "why_it_matters": "a total-level check cannot see this by "
                                      "construction; the batch total is inside any "
                                      "tolerance while both components are wrong"})

    def _adversarial(self) -> ToolResult:
        by = {}
        for a in self._aud.overturned:
            by[a.hypothesis] = by.get(a.hypothesis, 0) + 1
        return ToolResult(
            status="discrepancy_found" if self._aud.overturned else "ok",
            tool="run_adversarial_check",
            summary=(f"{len(self._aud.overturned)} of {self._aud.tested} passing "
                     f"claims were overturned."),
            evidence_ids=[a.target for a in self._aud.overturned][:8],
            detail={"overturned_by_hypothesis": by,
                    "false_match_rate_pct": round(self._aud.false_match_rate() * 100, 1),
                    "note": "an overturned match is a verdict on money counted "
                            "elsewhere, not a separate claim"})

    def _resolution(self) -> ToolResult:
        from .ingest import resolve_naive
        naive = resolve_naive(self._c)
        exact = sum(1 for b in self._c.bank if b.resolution == "exact")
        ambiguous = sum(1 for b in self._c.bank if b.resolution == "ambiguous")
        unresolved = sum(1 for b in self._c.bank if b.resolution == "unresolved")
        return ToolResult(
            status="ok" if not (ambiguous or unresolved) else "insufficient_evidence",
            tool="check_settlement_resolution",
            summary=(f"{exact} bank credits resolved on settlement_id, "
                     f"{ambiguous} ambiguous, {unresolved} unresolved."),
            evidence_ids=[b.utr for b in self._c.bank
                          if b.resolution != "exact"][:6],
            detail={"exact": exact, "ambiguous": ambiguous, "unresolved": unresolved,
                    "naive_utr_keying_would_resolve": naive["resolved"],
                    "note": "settlement_id is authoritative; the bank UTR is issued "
                            "by the correspondent bank and is not a Razorpay key"})

    # ---------------------------------------------------------------- traces
    def _trace_payment(self, payment_id: str) -> ToolResult:
        from .close import evidence_chain
        chain = evidence_chain(self._c, payment_id)
        if not chain:
            return ToolResult(status="not_found", tool="trace_payment",
                              summary=f"no settlement line for payment {payment_id}")
        broken = [s for s in chain if s["status"] != "ok"]
        return ToolResult(
            status="discrepancy_found" if broken else "ok", tool="trace_payment",
            summary=(f"chain breaks at {', '.join(s['stage'] for s in broken)}"
                     if broken else "every stage holds, order through bank."),
            evidence_ids=[s["id"] for s in chain],
            detail={"stages": chain, "broken_at": [s["stage"] for s in broken]})

    def _trace_settlement(self, batch_id: str) -> ToolResult:
        b = self._c.batches.get(batch_id)
        if b is None:
            return ToolResult(status="not_found", tool="trace_settlement",
                              summary=f"no settlement batch {batch_id}",
                              detail={"known": list(self._c.batches)[:8]})
        credit = next((x for x in self._c.bank if x.settlement_id == batch_id), None)
        net = sum(l.net for l in b.lines) + b.refund_adj
        return ToolResult(
            status="ok" if self._res.batch_ties.get(batch_id) else "discrepancy_found",
            tool="trace_settlement",
            summary=(f"{len(b.lines)} lines, net {fmt(net)}; "
                     + (f"bank credited {fmt(credit.credit)} on "
                        f"{credit.value_date.isoformat()}." if credit
                        else "no bank credit resolves to this batch.")),
            amount_paise=net,
            evidence_ids=[batch_id] + ([credit.utr] if credit else []),
            detail={"lines": len(b.lines), "settled_on": b.settled_on.isoformat(),
                    "refund_adjustment_paise": b.refund_adj,
                    "bank_credit_paise": credit.credit if credit else None,
                    "tied": bool(self._res.batch_ties.get(batch_id))})

    # ---------------------------------------------------------------- policy
    def _residual(self) -> ToolResult:
        p = self._policy
        return ToolResult(
            status="discrepancy_found" if not p.within_limit else "ok",
            tool="find_unexplained_residual",
            summary=(f"{fmt(p.residual_paise)} unattributed — {p.residual_bps} bps "
                     f"of volume against a {p.limit_bps} bps limit."),
            amount_paise=p.residual_paise,
            expected=f"at most {p.limit_bps} bps", actual=f"{p.residual_bps} bps",
            evidence_ids=p.contributing_classes,
            detail={"volume_paise": p.volume_paise,
                    "contributing_classes": p.contributing_classes})

    def _deadline(self, exception_class: str) -> ToolResult:
        hits = [c for c in self._claims
                if c.exception_class.upper() == str(exception_class).upper()]
        if not hits:
            return ToolResult(status="not_found", tool="get_claim_deadline",
                              summary=f"{exception_class} is not a claimable class")
        soonest = min(hits, key=lambda c: c.deadline)
        days = soonest.days_left(self._today)
        return ToolResult(
            status="ok" if days >= 0 else "insufficient_evidence",
            tool="get_claim_deadline",
            summary=(f"claimable from {soonest.counterparty}; "
                     + (f"{days} days left." if days >= 0
                        else f"the window closed {-days} days ago.")),
            amount_paise=sum(c.exposure for c in hits),
            evidence_ids=[soonest.exception_class],
            detail={"counterparty": soonest.counterparty,
                    "deadline": soonest.deadline.isoformat(),
                    "days_left": days, "urgency": soonest.urgency(self._today),
                    "evidence_required": soonest.evidence_required})
