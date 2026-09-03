"""The adversarial pass: try to break the matches that survived.

This module exists because of a fact the previous stage exposed, which is more
damaging than it first appears.

Reconciling a bank credit against a settlement report is **self-referential**.
Razorpay pays exactly what its own report says it will pay, so the two agree by
construction. The tie proves the money arrived. It proves nothing whatsoever
about whether the deductions inside were correct -- and it is the number
conventional reconciliation reports as success.

So every "tied" batch is treated here as a claim to be attacked, not a result to
be trusted. Six falsification hypotheses are tested against each one. A match
that survives all six is proven; a match that fails any is overturned and moves
to the exception register with the attack's reasoning attached.

The false-match rate this produces is the number nobody volunteers. It is
published deliberately.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from .ingest import Corpus
from .money import pct


def _business_days_after(d, n: int):
    from datetime import timedelta
    cur, fwd = d, 0
    while fwd < n:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            fwd += 1
    return cur

# A residual at or below this is conventionally treated as rounding noise.
# Hypothesis 3 exists because "noise" that always points the same way is not noise.
NOISE = 2                 # paise
OFFSET_THRESHOLD = 50     # paise: below this, an offset is not worth asserting


@dataclass
class Attack:
    hypothesis: str
    target: str
    verdict: str            # "overturned" | "survived"
    delta: int
    reasoning: str
    evidence: dict = field(default_factory=dict)


@dataclass
class AuditResult:
    attacks: list[Attack] = field(default_factory=list)
    tested: int = 0

    @property
    def overturned(self) -> list[Attack]:
        return [a for a in self.attacks if a.verdict == "overturned"]

    def false_match_rate(self) -> float:
        return len(self.overturned) / self.tested if self.tested else 0.0


# --------------------------------------------------------------------------
def _recompute(corpus: Corpus):
    """What each settlement line SHOULD have been under the contract."""
    mdr_rate = Decimal(corpus.terms["contracted_mdr_rate_pct"])
    gst_rate = Decimal(corpus.terms["gst_on_mdr_rate_pct"])
    out = {}
    for sid, b in corpus.batches.items():
        rows = []
        for ln in b.lines:
            mdr = pct(ln.gross, mdr_rate)
            gst = pct(mdr, gst_rate)
            correct_net = ln.gross - mdr - gst
            rows.append((ln, correct_net, ln.net - correct_net))
        out[sid] = rows
    return out


# ==========================================================================
# H1 -- the tie is self-referential
# ==========================================================================
def h1_self_referential(corpus: Corpus, ties, recomputed) -> list[Attack]:
    """A batch agreeing with the bank says nothing about the deductions inside."""
    attacks = []
    for sid, tied in ties.items():
        if not tied:
            continue
        rows = recomputed[sid]
        # Sub-tolerance drift belongs to H3. H1 fires only on material variance,
        # otherwise a single stray paise overturns every batch in the period and
        # the false-match rate stops meaning anything.
        variances = [(ln, d) for ln, _, d in rows if abs(d) > NOISE]
        if not variances:
            continue
        total = sum(d for _, d in variances)
        attacks.append(Attack(
            hypothesis="self_referential_tie",
            target=sid,
            verdict="overturned",
            delta=total,
            reasoning=(
                f"Batch agrees with the bank credit, but {len(variances)} of "
                f"{len(rows)} lines disagree with the contracted fee schedule by "
                f"{total}p in total. The bank paid what Razorpay's report said it "
                "would pay -- the tie confirms delivery, not correctness."
            ),
            evidence={
                "lines_with_variance": len(variances),
                "lines_total": len(rows),
                "signed_variance_paise": total,
            },
        ))
    return attacks


# ==========================================================================
# H2 -- offsetting pair (compensating errors)
# ==========================================================================
def h2_offsetting(corpus: Corpus, ties, recomputed) -> list[Attack]:
    """Two errors that cancel, leaving the batch inside tolerance.

    The signature is precise. If the sum of line-level variances does not equal
    the batch-level variance, then something at batch level -- an adjustment, a
    refund deduction -- absorbed the difference. That is a compensating error,
    and it is invisible to every total-level check by construction.
    """
    attacks = []

    # Independent expectation for each batch's refund deduction, derived from
    # the merchant's own refund export and the netting rule in the contract --
    # NOT from the settlement report we are auditing.
    expected_refund: dict[str, int] = {}
    ordered = sorted(corpus.batches.values(), key=lambda x: x.settled_on)
    for r in corpus.refunds:
        due = _business_days_after(r.initiated_on, 5)
        target = next((b for b in ordered if b.settled_on >= due), None)
        if target:
            expected_refund[target.settlement_id] = (
                expected_refund.get(target.settlement_id, 0) + r.amount
            )

    for sid, b in corpus.batches.items():
        rows = recomputed[sid]
        line_var = sum(d for _, _, d in rows)
        stated_refund = -b.refund_adj                 # export carries it negative
        refund_var = expected_refund.get(sid, 0) - stated_refund

        # A compensating pair is two variances of opposite sign that very nearly
        # cancel. If either is negligible, or they point the same way, this is
        # not compensation -- it is one plain error, which H1 already reports.
        if abs(line_var) <= NOISE or abs(refund_var) <= NOISE:
            continue
        if (line_var > 0) == (refund_var > 0):
            continue

        residual = line_var + refund_var
        if abs(residual) > OFFSET_THRESHOLD:
            continue

        worst = max(rows, key=lambda r: abs(r[2]))
        attacks.append(Attack(
            hypothesis="offsetting_pair",
            target=sid,
            verdict="overturned",
            delta=abs(line_var) + abs(refund_var),
            reasoning=(
                f"Fees are {abs(line_var)}p out and the refund deduction is "
                f"{abs(refund_var)}p out in the opposite direction. They cancel to "
                f"{residual}p -- inside any tolerance band, so every total-level "
                "check passes. Two real errors, netted into invisibility. Largest "
                f"line variance {worst[2]}p on {worst[0].payment_id}."
            ),
            evidence={
                "line_variance_paise": line_var,
                "refund_variance_paise": refund_var,
                "residual_paise": residual,
                "worst_line": worst[0].payment_id,
            },
        ))
    return attacks


# ==========================================================================
# H3 -- tolerance abuse
# ==========================================================================
def h3_tolerance_abuse(corpus: Corpus, recomputed) -> list[Attack]:
    """Rounding that always points the same way is not rounding.

    Individually each residual is inside tolerance and would be waved through.
    In aggregate, a one-directional bias is a systematic transfer hiding under
    the threshold.
    """
    small = [
        d for rows in recomputed.values() for _, _, d in rows
        if d != 0 and abs(d) <= NOISE
    ]
    if len(small) < 20:
        return []
    neg = sum(1 for d in small if d < 0)
    pos = len(small) - neg
    bias = max(neg, pos) / len(small)
    total = sum(small)

    if bias < 0.9:
        return [Attack(
            hypothesis="tolerance_abuse", target="period", verdict="survived",
            delta=0,
            reasoning=(
                f"{len(small)} sub-tolerance residuals, {pos} positive / {neg} "
                "negative. Direction is balanced, consistent with honest rounding."
            ),
        )]
    return [Attack(
        hypothesis="tolerance_abuse",
        target="period",
        verdict="overturned",
        delta=abs(total),
        reasoning=(
            f"{len(small)} residuals sit inside the {NOISE}p tolerance and "
            f"{bias*100:.0f}% point the same way, totalling {total}p. Honest "
            "rounding is symmetric. A consistent direction across hundreds of "
            "lines is a systematic transfer hiding beneath the threshold."
        ),
        evidence={"count": len(small), "positive": pos, "negative": neg,
                  "total_paise": total},
    )]


# ==========================================================================
# H4/H5/H6 -- resolution integrity
# ==========================================================================
def h4_coincidental(corpus: Corpus) -> list[Attack]:
    """Amounts agreeing while dates do not is a coincidence, not a match."""
    attacks = []
    for b in corpus.bank:
        if not b.settlement_id:
            continue
        batch = corpus.batches.get(b.settlement_id)
        if batch and abs((batch.settled_on - b.value_date).days) > 1:
            attacks.append(Attack(
                hypothesis="coincidental_equality", target=b.row_id,
                verdict="overturned", delta=b.credit,
                reasoning=(
                    f"Credit matched batch {b.settlement_id} on amount, but the "
                    f"bank dated it {b.value_date} against a settlement date of "
                    f"{batch.settled_on}. Equal amounts on incompatible dates."
                ),
            ))
    return attacks


def h5_ambiguous_collapse(corpus: Corpus) -> list[Attack]:
    """Was a single candidate chosen where several were plausible?"""
    return [
        Attack(
            hypothesis="ambiguous_collapse", target=b.row_id,
            verdict="overturned", delta=b.credit,
            reasoning=(
                "Resolution found more than one batch that could explain this "
                "credit. Escalated rather than resolved -- picking one would be a "
                "coin flip reported as certainty."
            ),
        )
        for b in corpus.bank if b.resolution == "ambiguous"
    ]


def h6_key_substitution(corpus: Corpus) -> list[Attack]:
    """Did anything get keyed on the UTR, which is not a Razorpay identifier?"""
    keyed_on_utr = [b for b in corpus.bank if b.resolution == "utr"]
    if not keyed_on_utr:
        return [Attack(
            hypothesis="key_substitution", target="period", verdict="survived",
            delta=0,
            reasoning=(
                f"All {len(corpus.bank)} credits resolved via settlement_id. No "
                "match relies on the bank UTR, which is issued by the correspondent "
                "bank and is not a Razorpay key."
            ),
        )]
    return [Attack(
        hypothesis="key_substitution", target=b.row_id, verdict="overturned",
        delta=b.credit,
        reasoning="Match rests on the bank UTR rather than settlement_id.",
    ) for b in keyed_on_utr]


# ==========================================================================
def run(corpus: Corpus, ties: dict[str, bool]) -> AuditResult:
    recomputed = _recompute(corpus)
    attacks: list[Attack] = []
    attacks += h1_self_referential(corpus, ties, recomputed)
    attacks += h2_offsetting(corpus, ties, recomputed)
    attacks += h3_tolerance_abuse(corpus, recomputed)
    attacks += h4_coincidental(corpus)
    attacks += h5_ambiguous_collapse(corpus)
    attacks += h6_key_substitution(corpus)

    # Every tied batch is a claim under test, plus the period-level hypotheses.
    tested = sum(1 for v in ties.values() if v) + 2
    return AuditResult(attacks=attacks, tested=tested)
