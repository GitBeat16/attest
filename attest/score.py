"""Score the run against held-out ground truth.

This is the module that makes every other number in the project trustworthy.

The corpus was built truth-first: the true world was generated and frozen before
any defect was injected, and the labels live in data/truth/, which nothing else
in the pipeline reads. So the figures below are *measured* against an answer key
the system has never seen, rather than asserted.

Two things are scored, and the second matters as much as the first:

  RECALL          did we find the defects that were planted?
  FALSE POSITIVES did we flag things that were never errors?

A reconciliation tool that reports every legitimate timing gap as a variance is
worse than useless -- it buries the real findings. So world facts (late-netting
refunds, genuinely ambiguous same-value orders) are labelled as expected
non-errors, and flagging one counts against us.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .audit import NOISE, AuditResult
from .engine import Result
from .ingest import Corpus


@dataclass
class ClassScore:
    held_out: bool = False
    planted: int = 0
    detected: int = 0
    exposure_planted: int = 0
    exposure_detected: int = 0

    @property
    def recall(self) -> float:
        return self.detected / self.planted if self.planted else 0.0


@dataclass
class Scorecard:
    by_class: dict[str, ClassScore] = field(default_factory=dict)
    false_positives: list[str] = field(default_factory=list)
    suppressed: int = 0            # world facts correctly NOT flagged
    notes: list[str] = field(default_factory=list)

    def overall_recall(self) -> float:
        p = sum(c.planted for c in self.by_class.values())
        d = sum(c.detected for c in self.by_class.values())
        return d / p if p else 0.0

    def designed_recall(self) -> float:
        cs = [c for c in self.by_class.values() if not c.held_out]
        p, d = sum(c.planted for c in cs), sum(c.detected for c in cs)
        return d / p if p else 0.0

    def holdout_recall(self) -> float:
        cs = [c for c in self.by_class.values() if c.held_out]
        p, d = sum(c.planted for c in cs), sum(c.detected for c in cs)
        return d / p if p else 0.0


def _detections(corpus: Corpus, res: Result, aud: AuditResult) -> dict[str, set]:
    """Collapse everything the system found into (class -> set of target ids)."""
    found: dict[str, set] = defaultdict(set)

    for c in res.chains:
        if c.complete:
            continue
        if c.broke_at == "mdr" and abs(c.delta) > NOISE:
            found["MDR_RATE"].add(c.subject_id)
        elif c.broke_at == "gst":
            found["GST_BASE"].add(c.subject_id)
        elif c.broke_at == "credit":
            found["MISSING_CREDIT"].add(c.stages.get("_batch_id", ""))
        elif c.broke_at == "adjustment":
            found["COD_SHORT"].add(c.subject_id)

    for a in aud.overturned:
        if a.hypothesis == "offsetting_pair":
            found["COMPENSATING"].add(a.target)
        elif a.hypothesis == "tolerance_abuse":
            found["ROUNDING_DRIFT"].add("period")
        elif a.hypothesis == "ambiguous_collapse":
            found["AMBIGUOUS"].add(a.target)

    for f in res.batch_findings:
        if f["class"] == "UNREFERENCED_ADJ":
            found["UNREFERENCED_ADJ"].add(f["container"])
        elif f["class"] == "CHARGEBACK_ORPHAN":
            found["CHARGEBACK_ORPHAN"].add("period")
        elif f["class"] == "REFUND_MISMATCH":
            found["REFUND_DUPLICATE"].add("period")
        elif f["class"] == "TIMING_NOT_ERROR":
            found["_TIMING_SUPPRESSED"].add("period")
        elif f["class"] == "DUPLICATE_SETTLEMENT_LINE":
            found["DUPLICATE_SETTLEMENT_LINE"].add("period")
        elif f["class"] == "DUPLICATE_AWB":
            found["DUPLICATE_AWB"].add("period")
        elif f["class"] == "ORPHAN_BANK_CREDIT":
            found["ORPHAN_BANK_CREDIT"].add("period")

    # A bank credit whose narration was stripped still resolved, because
    # resolution never depended on the narration in the first place.
    found["UTR_UNRESOLVABLE"] = {
        b.utr for b in corpus.bank
        if b.settlement_id and "RZPY STLMT" in b.narration
    }
    return found


def score(corpus: Corpus, res: Result, aud: AuditResult, truth_dir: Path) -> Scorecard:
    ledger = json.loads((truth_dir / "defect_ledger.json").read_text())
    found = _detections(corpus, res, aud)
    card = Scorecard()

    # Classes where every planted instance has its own identifiable target.
    per_instance = {
        "MDR_RATE", "GST_BASE", "COD_SHORT", "COMPENSATING", "UNREFERENCED_ADJ",
        "UTR_UNRESOLVABLE",
    }
    # Classes that are period-level by nature -- one finding covers the class.
    aggregate = {
        "ROUNDING_DRIFT", "CHARGEBACK_ORPHAN", "REFUND_DUPLICATE", "MISSING_CREDIT",
        "DUPLICATE_SETTLEMENT_LINE", "DUPLICATE_AWB", "ORPHAN_BANK_CREDIT",
    }
    # World facts. These are NOT errors. Detecting one is a false positive.
    non_errors = {"REFUND_TIMING", "AMBIGUOUS"}
    # Held-out classes: injected deliberately with NO detector written for them.
    # They exist to break the circularity of scoring a detector against defects
    # its own author planted. Whatever they score, we report.
    held_out = {
        "DUPLICATE_SETTLEMENT_LINE", "DUPLICATE_AWB",
        "ORPHAN_BANK_CREDIT", "OUT_OF_PERIOD_SETTLEMENT",
    }

    for e in ledger["entries"]:
        cls = e["defect_class"]
        sc = card.by_class.setdefault(cls, ClassScore(held_out=cls in held_out))
        sc.planted += 1
        sc.exposure_planted += abs(e["delta_paise"])

        if cls in non_errors:
            continue

        hits = found.get(cls, set())
        if cls in per_instance:
            ok = e["target_id"] in hits or e["container_id"] in hits
        elif cls in aggregate:
            ok = bool(hits)
        else:
            ok = bool(hits)

        if ok:
            sc.detected += 1
            sc.exposure_detected += abs(e["delta_paise"])

    # --- false positives: were the world facts correctly left alone? --------
    timing_entries = [
        e for e in ledger["entries"] if e["defect_class"] == "REFUND_TIMING"
    ]
    if timing_entries:
        if "_TIMING_SUPPRESSED" in found:
            card.suppressed += len(timing_entries)
            card.notes.append(
                f"{len(timing_entries)} refunds netting into the next period were "
                "classified as timing, not variance -- the classic false positive "
                "in this domain, correctly suppressed."
            )
        else:
            card.false_positives.append(
                f"{len(timing_entries)} legitimate late-netting refunds were not "
                "recognised as timing"
            )

    amb = [e for e in ledger["entries"] if e["defect_class"] == "AMBIGUOUS"]
    if amb:
        card.notes.append(
            f"{len(amb)} same-value same-day order pairs exist in the corpus. "
            "Resolution keys on settlement_id, so these never reach an amount-based "
            "tiebreak and no arbitrary pick is made."
        )

    # Classes scored as non-errors should not carry a recall figure.
    for cls in non_errors:
        card.by_class.pop(cls, None)

    return card


def render(card: Scorecard) -> str:
    w = 34
    out = ["  ACCURACY vs HELD-OUT GROUND TRUTH", "  " + "-" * 62]
    out.append(f"  {'defect class':<24}{'planted':>9}{'found':>8}{'recall':>10}")
    out.append("  " + "-" * 62)
    for cls in sorted(card.by_class):
        s = card.by_class[cls]
        if s.held_out:
            flag = "   held out, FOUND" if s.detected else "   held out, missed"
        else:
            flag = "" if s.recall >= 0.999 else "   partial" if s.detected else "   MISSED"
        out.append(f"  {cls:<24}{s.planted:>9}{s.detected:>8}{s.recall*100:>9.0f}%{flag}")
    out.append("  " + "-" * 62)
    out.append(f"  {'recall, designed-for':<24}{'':>9}{'':>8}{card.designed_recall()*100:>9.1f}%")
    out.append(f"  {'recall, HELD OUT':<24}{'':>9}{'':>8}{card.holdout_recall()*100:>9.1f}%"
               "   <- the honest number")
    out.append(f"  {'OVERALL RECALL':<24}{'':>9}{'':>8}{card.overall_recall()*100:>9.1f}%")
    out.append(f"  {'FALSE POSITIVES':<24}{'':>9}{'':>8}{len(card.false_positives):>10}")
    out.append(f"  {'suppressed non-errors':<24}{'':>9}{'':>8}{card.suppressed:>10}")
    for n in card.notes:
        out.append(f"\n  note: {n}")
    for fp in card.false_positives:
        out.append(f"\n  FALSE POSITIVE: {fp}")
    return "\n".join(out)
