"""What may be certified, decided by arithmetic and nothing else.

This is a small module on purpose. It exists so that the answer to *"can this
close be signed?"* lives somewhere a planner cannot reach, cannot argue with,
and cannot be persuaded to widen. The controller may investigate for as long as
its budget allows and reach whatever conclusion it likes; the verdict is
computed here, from the deterministic engine's own figures, and the controller
is told the answer rather than asked for it.

Three outcomes, and only three:

  CERTIFIABLE       the unexplained residual is inside the limit
  NOT_ATTESTABLE    it is not, and the close stays open
  HUMAN_REVIEW      the investigation could not establish enough either way

The third is the one most systems lack. A controller that runs out of budget,
or hits contradictory evidence, must be able to stop and say so. Escalation is
a successful outcome, not a failure — the failure mode this module exists to
prevent is a confident signature on evidence nobody checked.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .money import fmt

# Basis points of period volume that may remain unattributed and still be
# signed. Deliberately a module constant, not a parameter the controller can
# pass: a threshold an investigator can widen is not a threshold.
RESIDUAL_LIMIT_BPS = 25

# Exception classes whose exposure counts as genuinely unexplained. A fee
# overcharge is understood and claimable; a credit that never arrived is not
# understood until someone finds it.
UNEXPLAINED = ("CREDIT", "CHARGEBACK_ORPHAN", "UNREFERENCED_ADJ")

CERTIFIABLE = "CERTIFIABLE"
NOT_ATTESTABLE = "NOT_ATTESTABLE"
HUMAN_REVIEW = "HUMAN_REVIEW_REQUIRED"


@dataclass
class Policy:
    """The computed position of one close against the certification rule."""
    volume_paise: int
    residual_paise: int
    residual_bps: float
    limit_bps: int = RESIDUAL_LIMIT_BPS
    contributing_classes: list[str] = field(default_factory=list)
    false_positives: list[str] = field(default_factory=list)

    @property
    def within_limit(self) -> bool:
        return self.residual_bps <= self.limit_bps

    def decide(self, *, evidence_sufficient: bool = True,
               reason: str = "") -> tuple[str, str]:
        """The verdict, and why. Nothing else in the system may return this."""
        if not evidence_sufficient:
            return HUMAN_REVIEW, (
                reason or "the investigation did not establish enough evidence "
                          "to decide either way.")
        if self.false_positives:
            return HUMAN_REVIEW, (
                f"{len(self.false_positives)} finding(s) look like false "
                "positives; a close cannot be signed over a disputed exception.")
        if self.within_limit:
            return CERTIFIABLE, (
                f"{fmt(self.residual_paise)} unattributed — {self.residual_bps} bps "
                f"of volume, inside the {self.limit_bps} bps limit.")
        return NOT_ATTESTABLE, (
            f"{fmt(self.residual_paise)} cannot be attributed to any cause — "
            f"{self.residual_bps} bps of volume against a {self.limit_bps} bps "
            "limit. The close stays open until that is investigated.")


def assess(volume_paise: int, exceptions: list[dict],
           false_positives: list[str] | None = None) -> Policy:
    """Compute the policy position from the engine's own exception register.

    Verdicts from the adversarial pass are excluded: an overturned match
    describes rupees already counted under the finding that produced it, and
    counting them again would inflate the residual and refuse closes that should
    have been signed. Erring toward refusal is safer than erring toward
    certification, but it is still an error.
    """
    residual = sum(e["exposure"] for e in exceptions
                   if e["class"] in UNEXPLAINED and e.get("kind") != "verdict")
    bps = (residual / volume_paise * 10000) if volume_paise else 0.0
    classes = sorted({e["class"] for e in exceptions
                      if e["class"] in UNEXPLAINED and e.get("kind") != "verdict"})
    return Policy(volume_paise=volume_paise, residual_paise=residual,
                  residual_bps=round(bps, 2), contributing_classes=classes,
                  false_positives=list(false_positives or []))
