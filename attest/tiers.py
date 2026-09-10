"""Confidence tiers on findings — how much a finding may honestly claim.

Every exception the engine produces is classified into one of three tiers. This
is not new detection: it changes nothing about what is found, no tolerance and
no threshold — only what may be *asserted* about a finding to a counterparty.
The rule lives here, in one small module, for the same reason the certification
threshold lives in `policy.py`: so the answer to *"is this claim-ready?"* sits
somewhere a planner, a renderer or a hurried operator cannot quietly widen.

  PROVEN       the arithmetic disagrees with the contract, or with the line's
               own stated components. Independently recomputable, defensible in
               front of the counterparty. Only these may be framed as
               claim-ready.

  UNPROVEN     a link in the evidence chain is missing. The money may be
               perfectly correct; the evidence is absent. Ask, don't accuse.

  NEEDS_INPUT  depends on a fact Attest cannot see from any of the seven
               sources — an off-system agreement, a negotiated rate, a credit
               note, the reason a reserve moved.

An unmapped class is UNPROVEN. A finding we cannot characterise is never
claim-ready.
"""
from __future__ import annotations

PROVEN = "PROVEN"
UNPROVEN = "UNPROVEN"
NEEDS_INPUT = "NEEDS_INPUT"

TIERS = (PROVEN, UNPROVEN, NEEDS_INPUT)

# Strength, weakest first. `weakest()` uses this so a grouped register row
# holding a mix of tiers can only ever be pulled DOWN to its least provable
# member — tiering never overstates a row.
_STRENGTH = {NEEDS_INPUT: 0, UNPROVEN: 1, PROVEN: 2}

LABEL = {
    PROVEN: "Proven wrong",
    UNPROVEN: "Cannot prove",
    NEEDS_INPUT: "Needs your input",
}

# One sentence each, for the close pack and the app.
BLURB = {
    PROVEN: "The arithmetic disagrees with the contract. Recomputable, and "
            "defensible to the counterparty.",
    UNPROVEN: "A link in the evidence chain is missing. The money may be "
              "correct; the evidence is absent. Ask, don't accuse.",
    NEEDS_INPUT: "Depends on a fact Attest cannot see — an off-system "
                 "agreement, a negotiated rate, a credit note.",
}

# The mapping. Chain-break stages arrive here upper-cased (MDR, GST, NET, ...);
# batch findings and adversarial verdicts arrive as their own class names.
_MAP = {
    # -- PROVEN: recomputable against the contract or the line's own parts ---
    "MDR": PROVEN,            # charged fee vs pct(gross, contracted_mdr_rate)
    "GST": PROVEN,            # charged tax vs pct(mdr, gst_on_mdr_rate)
    "NET": PROVEN,            # stated net vs gross - mdr - gst (its own parts)
    "COD_FEE": PROVEN,        # charged COD fee vs pct(cod_value, courier_rate)
    "FREIGHT": PROVEN,        # RTO freight vs the status-conditional expectation
    "ITC_MISMATCH": PROVEN,   # Razorpay's own tax invoice vs GST it deducted
    # Adversarial verdicts that rest on an independent recomputation. Still
    # never claimable (invariant 9) — but PROVEN as *statements*.
    "SELF_REFERENTIAL_TIE": PROVEN,
    "OFFSETTING_PAIR": PROVEN,

    # -- UNPROVEN: a link in the chain is missing ---------------------------
    "CREDIT": UNPROVEN,       # settlement has no matching bank credit
    "ORDER": UNPROVEN,        # order absent (the value-disagrees case is
                             # promoted to PROVEN per-finding, see for_chain)
    "SHIPMENT": UNPROVEN,     # AWB not in the manifest
    "COD_VALUE": UNPROVEN,    # remitted value != manifest; who erred is unknown
    "ORPHAN_BANK_CREDIT": UNPROVEN,
    "CHARGEBACK_ORPHAN": UNPROVEN,
    "REFUND_MISMATCH": UNPROVEN,
    "DUPLICATE_SETTLEMENT_LINE": UNPROVEN,
    "DUPLICATE_AWB": UNPROVEN,
    "OUT_OF_PERIOD_SETTLEMENT": UNPROVEN,
    # Resolution-integrity verdicts: inferences about the match, not arithmetic.
    "TOLERANCE_ABUSE": UNPROVEN,
    "COINCIDENTAL_EQUALITY": UNPROVEN,
    "AMBIGUOUS_COLLAPSE": UNPROVEN,
    "KEY_SUBSTITUTION": UNPROVEN,

    # -- NEEDS_INPUT: depends on a fact that is off-system -----------------
    "ADJUSTMENT": NEEDS_INPUT,        # courier deduction with no AWB reason
    "UNREFERENCED_ADJ": NEEDS_INPUT,  # hold released into a batch, no reference
}

DEFAULT = UNPROVEN


def for_class(cls: str) -> str:
    """Tier for an exception class name. Unknown -> UNPROVEN."""
    return _MAP.get(str(cls).upper(), DEFAULT)


def for_chain(chain) -> str:
    """Tier for one proof chain, refined past the class where the break is
    genuinely two-sided.

      ORDER   : broke with a non-zero delta -> the order was found and its
                gross disagrees, a document-vs-document conflict -> PROVEN.
                broke with delta 0          -> the order is absent  -> UNPROVEN.
      FREIGHT : the freight expectation is only real if the shipment lookup
                already passed. `prove_cod` checks `shipment` before `freight`
                and `broke_at` is the FIRST failure, so a freight break implies
                the manifest was found — but guard on it explicitly, so this
                stays correct if that check order ever moves.
    """
    stage = (chain.broke_at or "").upper()
    if stage == "ORDER":
        return PROVEN if chain.delta else UNPROVEN
    if stage == "FREIGHT":
        return PROVEN if chain.stages.get("shipment") else UNPROVEN
    return for_class(stage)


def weakest(tiers) -> str:
    """The least-provable tier in an iterable. Empty -> UNPROVEN."""
    found = [t for t in tiers if t in _STRENGTH]
    return min(found, key=_STRENGTH.__getitem__) if found else DEFAULT


def rank(exceptions: list[dict]) -> list[dict]:
    """Presentation order: claim-ready first, then by exposure within a tier,
    verdicts last regardless of tier.

    A CA works this list top-down. The claim-ready findings must be what they
    reach first — the exact inversion of ranking by rupee exposure alone, which
    puts the largest *unprovable* item at the top. Used by the CLI register, the
    close pack and the app. `build_exceptions` itself still returns
    exposure-sorted output, because the controller keys its investigation
    subject off the first row and that must not shift.
    """
    return sorted(
        exceptions,
        key=lambda e: (
            e.get("kind") == "verdict",                      # verdicts sink
            -_STRENGTH.get(e.get("tier", DEFAULT), 1),       # PROVEN rises
            -e.get("exposure", 0),                           # then by size
        ),
    )
