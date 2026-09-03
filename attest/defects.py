"""Inject labelled defects into the DOCUMENTS only.

Two categories live here, and the distinction matters:

  * DEFECTS are things the documents say that the true world does not. Real money
    was moved wrongly, or a record was lost. These are injected into a copy of
    the world; the original stays clean and becomes ground truth.

  * WORLD FACTS are properties of reality that make reconciliation hard without
    anyone having done anything wrong -- two genuinely identical orders on the
    same day, or a refund that legitimately nets against a later batch. These are
    seeded into the true world BEFORE the truth is frozen. Classifying these
    correctly as "not an error" is as important as catching the real defects,
    and a system that flags them is producing false positives.

Every entry carries an expected_classification, which is what a correct system
should conclude. That field is the held-out label. The agent never reads this file.
"""
from __future__ import annotations

import copy
import random
from dataclasses import dataclass, asdict
from datetime import timedelta
from decimal import Decimal

from .money import rupees, pct
from .world import (
    World, BankCredit, CodRemittanceLine, MDR_RATE, GST_RATE, COD_FEE_RATE,
)

# The rate the merchant is actually charged in the defective batches.
INFLATED_MDR_RATE = Decimal("2.36")


@dataclass
class Defect:
    defect_id: str
    defect_class: str
    expected_classification: str   # what a correct system should conclude
    target_type: str
    target_id: str
    container_id: str              # settlement_id / remittance_id / ""
    true_value_paise: int
    document_value_paise: int
    delta_paise: int
    note: str

    def as_dict(self) -> dict:
        return asdict(self)


class Ledger:
    """The held-out answer key."""

    def __init__(self) -> None:
        self.entries: list[Defect] = []
        self._n = 0

    def add(self, **kw) -> Defect:
        self._n += 1
        d = Defect(defect_id=f"DFCT{self._n:04d}", **kw)
        self.entries.append(d)
        return d

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.entries:
            out[e.defect_class] = out.get(e.defect_class, 0) + 1
        return dict(sorted(out.items()))

    def exposure(self) -> int:
        """Total absolute rupee value at stake across all planted defects."""
        return sum(abs(e.delta_paise) for e in self.entries)


# ==========================================================================
# WORLD FACTS -- seeded into the truth before it is frozen
# ==========================================================================
def seed_world_facts(world: World, seed: int = 7) -> None:
    """Make reality genuinely hard, without anyone doing anything wrong."""
    rng = random.Random(seed)

    # Six pairs of orders with identical amounts on identical days. A naive
    # matcher will pair these arbitrarily and be right ~50% of the time while
    # reporting 100% confidence. Correct behaviour is to escalate.
    prepaid = [o for o in world.orders if o.payment_mode == "prepaid"]
    by_day: dict = {}
    for o in prepaid:
        by_day.setdefault(o.placed_on, []).append(o)
    candidate_days = [d for d, os in by_day.items() if len(os) >= 2]
    rng.shuffle(candidate_days)

    pay_by_order = {p.order_id: p for p in world.payments}
    line_by_payment = {
        l.payment_id: l for s in world.settlements for l in s.lines
    }

    for day in candidate_days[:6]:
        a, b = by_day[day][0], by_day[day][1]
        b.gross = a.gross
        for o in (a, b):
            p = pay_by_order.get(o.order_id)
            if not p:
                continue
            p.gross = o.gross
            p.mdr = pct(p.gross, MDR_RATE)
            p.gst_on_mdr = pct(p.mdr, GST_RATE)
            p.net = p.gross - p.mdr - p.gst_on_mdr
            ln = line_by_payment.get(p.payment_id)
            if ln:
                ln.gross, ln.mdr, ln.gst_on_mdr, ln.net = (
                    p.gross, p.mdr, p.gst_on_mdr, p.net
                )

    # Rebuild bank credits so the truth stays internally consistent.
    amt = {s.settlement_id: s.net_credit for s in world.settlements}
    for bc in world.bank_credits:
        if bc.settlement_id in amt:
            bc.amount = amt[bc.settlement_id]


def label_world_facts(truth: World, ledger: Ledger) -> None:
    """Record the world facts as expected NON-errors, so false positives cost."""
    # Ambiguous same-amount same-day prepaid pairs.
    seen: dict[tuple, list[str]] = {}
    for o in truth.orders:
        if o.payment_mode == "prepaid":
            seen.setdefault((o.placed_on, o.gross), []).append(o.order_id)
    for (day, gross), ids in seen.items():
        if len(ids) >= 2:
            ledger.add(
                defect_class="AMBIGUOUS",
                expected_classification="ESCALATE",
                target_type="order_pair",
                target_id="+".join(sorted(ids)),
                container_id="",
                true_value_paise=gross,
                document_value_paise=gross,
                delta_paise=0,
                note=(
                    f"{len(ids)} prepaid orders of identical value on {day}. "
                    "Correct behaviour is to escalate, not to pick one."
                ),
            )

    # Refunds that legitimately net against a later settlement batch.
    settled_on = {s.settlement_id: s.settled_on for s in truth.settlements}
    for r in truth.refunds:
        if not r.deducted_in_settlement:
            continue
        sd = settled_on.get(r.deducted_in_settlement)
        if sd and (sd - r.initiated_on).days >= 5:
            ledger.add(
                defect_class="REFUND_TIMING",
                expected_classification="TIMING_NOT_ERROR",
                target_type="refund",
                target_id=r.refund_id,
                container_id=r.deducted_in_settlement,
                true_value_paise=r.amount,
                document_value_paise=r.amount,
                delta_paise=0,
                note=(
                    f"Refund initiated {r.initiated_on}, deducted from batch settled "
                    f"{sd}. Legitimate timing gap -- flagging this is a false positive."
                ),
            )


# ==========================================================================
# DEFECTS -- injected into the documents only
# ==========================================================================
def inject(truth: World, seed: int = 42) -> tuple[World, Ledger]:
    rng = random.Random(seed)
    doc = copy.deepcopy(truth)
    ledger = Ledger()

    label_world_facts(truth, ledger)

    all_lines = [l for s in doc.settlements for l in s.lines]
    settlement_of = {s.settlement_id: s for s in doc.settlements}
    used: set[str] = set()

    # Reserve the compensating batches BEFORE anything else touches them.
    # The whole point of a compensating pair is that the batch total lands
    # inside tolerance -- so these batches must carry no other defect, or the
    # cancellation is drowned out and the case stops demonstrating anything.
    _eligible = [
        s for s in doc.settlements
        if s.refund_deductions > rupees(500)
        and s.chargeback_deductions == 0
        and len(s.lines) >= 8
    ]
    _eligible.sort(key=lambda s: -len(s.lines))
    protected_batches = {s.settlement_id for s in _eligible[:2]}

    def pick(n: int) -> list:
        avail = [
            l for l in all_lines
            if l.payment_id not in used and l.settlement_id not in protected_batches
        ]
        rng.shuffle(avail)
        chosen = avail[:n]
        used.update(l.payment_id for l in chosen)
        return chosen

    # --- 1. MDR_RATE: charged at 2.36% against a contracted 2.00% ----------
    for ln in pick(23):
        true_mdr = ln.mdr
        bad_mdr = pct(ln.gross, INFLATED_MDR_RATE)
        bad_gst = pct(bad_mdr, GST_RATE)
        ln.mdr, ln.gst_on_mdr = bad_mdr, bad_gst
        ln.net = ln.gross - bad_mdr - bad_gst
        ledger.add(
            defect_class="MDR_RATE",
            expected_classification="FEE_DEDUCTION",
            target_type="settlement_line",
            target_id=ln.payment_id,
            container_id=ln.settlement_id,
            true_value_paise=true_mdr,
            document_value_paise=bad_mdr,
            delta_paise=bad_mdr - true_mdr,
            note=f"MDR applied at {INFLATED_MDR_RATE}% against contracted {MDR_RATE}%.",
        )

    # --- 2. GST_BASE: GST computed on gross instead of on MDR -------------
    for ln in pick(4):
        true_gst = ln.gst_on_mdr
        bad_gst = pct(ln.gross, GST_RATE)
        ln.gst_on_mdr = bad_gst
        ln.net = ln.gross - ln.mdr - bad_gst
        ledger.add(
            defect_class="GST_BASE",
            expected_classification="TAX_DEDUCTION",
            target_type="settlement_line",
            target_id=ln.payment_id,
            container_id=ln.settlement_id,
            true_value_paise=true_gst,
            document_value_paise=bad_gst,
            delta_paise=bad_gst - true_gst,
            note="GST computed on gross transaction value rather than on MDR. "
                 "Also inflates the ITC claimed, so this is a compliance exposure.",
        )

    # --- 3. COMPENSATING: two errors that cancel inside one batch ---------
    # The star case. Batch total lands within tolerance; both lines are wrong.
    # Only batches that actually carry a refund deduction can host this defect,
    # because the cancelling half has to hide inside the refund line.
    for s in _eligible[:2]:
        cands = [l for l in s.lines if l.payment_id not in used]
        if not cands:
            continue
        ln = cands[0]
        used.add(ln.payment_id)
        over = rupees(4.12)
        under = rupees(4.10)
        true_mdr = ln.mdr
        ln.mdr += over
        ln.net = ln.gross - ln.mdr - ln.gst_on_mdr
        s.refund_deductions -= under
        ledger.add(
            defect_class="COMPENSATING",
            expected_classification="COMPENSATING",
            target_type="settlement_line+batch_refund",
            target_id=ln.payment_id,
            container_id=s.settlement_id,
            true_value_paise=true_mdr,
            document_value_paise=ln.mdr,
            delta_paise=over - under,
            note=(
                f"MDR over by {over} paise and batch refund under-deducted by "
                f"{under} paise. Batch nets to {over - under} paise -- inside any "
                "tolerance band. Two real errors, invisible to a total-level check."
            ),
        )

    # --- 4. ROUNDING_DRIFT: systematic 1-paise drift, always one way ------
    # Individually within tolerance. In aggregate, always in Razorpay's favour,
    # which is the signature of tolerance abuse rather than honest rounding.
    drift_lines = pick(200)
    for ln in drift_lines:
        ln.mdr += 1
        ln.net -= 1
    ledger.add(
        defect_class="ROUNDING_DRIFT",
        expected_classification="TOLERANCE_ABUSE",
        target_type="settlement_line_set",
        target_id=f"{len(drift_lines)}_lines",
        container_id="",
        true_value_paise=0,
        document_value_paise=len(drift_lines),
        delta_paise=len(drift_lines),
        note=(
            f"{len(drift_lines)} lines each drift +1 paise on MDR, always in the "
            "same direction. Each is inside tolerance; the aggregate is not noise."
        ),
    )

    # --- 5. REFUND_DUPLICATE: same refund deducted twice ------------------
    dupable = [
        r for r in doc.refunds
        if r.deducted_in_settlement
        and r.deducted_in_settlement not in protected_batches
    ][:2]
    for r in dupable:
        s = settlement_of[r.deducted_in_settlement]
        s.refund_deductions += r.amount
        ledger.add(
            defect_class="REFUND_DUPLICATE",
            expected_classification="DUPLICATE",
            target_type="refund",
            target_id=r.refund_id,
            container_id=s.settlement_id,
            true_value_paise=r.amount,
            document_value_paise=r.amount * 2,
            delta_paise=r.amount,
            note="Refund deducted twice from the same settlement batch.",
        )

    # --- 6. UNREFERENCED_ADJ: hold release with no order reference --------
    _adj_pool = [s for s in doc.settlements if s.settlement_id not in protected_batches]
    for s in rng.sample(_adj_pool, 2):
        amt = rupees(rng.randrange(2000, 9000))
        s.hold_release += amt
        ledger.add(
            defect_class="UNREFERENCED_ADJ",
            expected_classification="ESCALATE",
            target_type="settlement",
            target_id=s.settlement_id,
            container_id=s.settlement_id,
            true_value_paise=0,
            document_value_paise=amt,
            delta_paise=amt,
            note="Hold released into the batch with no order or payment reference. "
                 "Cannot be proven from merchant-side data -- must escalate.",
        )

    # --- 7. CHARGEBACK_ORPHAN: netted, but no dispute record --------------
    if doc.chargebacks:
        cb = doc.chargebacks[0]
        doc.chargebacks = doc.chargebacks[1:]
        ledger.add(
            defect_class="CHARGEBACK_ORPHAN",
            expected_classification="ESCALATE",
            target_type="chargeback",
            target_id=cb.dispute_id,
            container_id="",
            true_value_paise=cb.amount,
            document_value_paise=0,
            delta_paise=cb.amount,
            note="Chargeback deducted from a settlement batch but absent from the "
                 "dispute export. Cross-system gap.",
        )

    # ---- rebuild bank credits from the DEFECTIVE settlements -------------
    # Real money moved the wrong way, so the bank reflects the defect. Only
    # after this do we damage the bank records themselves.
    doc.bank_credits = [
        BankCredit(
            utr=bc.utr,
            narration=bc.narration,
            amount=settlement_of[bc.settlement_id].net_credit,
            credited_on=bc.credited_on,
            settlement_id=bc.settlement_id,
        )
        for bc in doc.bank_credits
        if bc.settlement_id in settlement_of
    ]

    # --- 8. MISSING_CREDIT: settlement reported, money never arrived ------
    if doc.bank_credits:
        idx = max(range(len(doc.bank_credits)), key=lambda i: doc.bank_credits[i].amount)
        missing = doc.bank_credits.pop(idx)
        ledger.add(
            defect_class="MISSING_CREDIT",
            expected_classification="UNEXPLAINED",
            target_type="bank_credit",
            target_id=missing.settlement_id or "",
            container_id=missing.settlement_id or "",
            true_value_paise=missing.amount,
            document_value_paise=0,
            delta_paise=missing.amount,
            note="Settlement present in the Razorpay report with no corresponding "
                 "bank credit. Highest single rupee exposure in the period.",
        )

    # --- 9. UTR_UNRESOLVABLE: narration scrambled, settlement_id absent ---
    for bc in rng.sample(doc.bank_credits, min(3, len(doc.bank_credits))):
        true_sid = bc.settlement_id
        bc.settlement_id = None
        bc.narration = f"NEFT CR-{bc.utr}-RZPY STLMT"
        ledger.add(
            defect_class="UTR_UNRESOLVABLE",
            expected_classification="KEY_MISMATCH",
            target_type="bank_credit",
            target_id=bc.utr,
            container_id=true_sid or "",
            true_value_paise=bc.amount,
            document_value_paise=bc.amount,
            delta_paise=0,
            note="Bank narration carries only a UTR. The UTR is issued by the "
                 "correspondent bank, not Razorpay -- matching on it produces "
                 "confident wrong matches. Must be re-keyed on settlement_id.",
        )

    # --- 10. COD_SHORT: remitted 1.8% light behind an adjustment line -----
    cod_lines = [l for r in doc.cod_remittances for l in r.lines if l.cod_value > 0]
    rng.shuffle(cod_lines)
    for ln in cod_lines[:14]:
        short = pct(ln.cod_value, Decimal("1.80"))
        ln.adjustment = -short
        ln.net = ln.cod_value - ln.cod_fee - ln.rto_freight - short
        ledger.add(
            defect_class="COD_SHORT",
            expected_classification="SHORT_REMITTANCE",
            target_type="cod_remittance_line",
            target_id=ln.awb,
            container_id=ln.remittance_id,
            true_value_paise=ln.cod_value - ln.cod_fee - ln.rto_freight,
            document_value_paise=ln.net,
            delta_paise=short,
            note="COD remitted 1.8% short behind an unexplained adjustment line "
                 "with no AWB-level justification. Recoverable from the courier.",
        )

    return doc, ledger
