"""Regression tests for the arithmetic that handles money.

These exist because of one real bug found during the audit: a legitimate credit
of **zero** was being treated as a *missing* credit, which silently substituted a
computed figure for a reported one. Every test here covers a case where a
plausible-looking shortcut produces a wrong number rather than an obvious crash —
the only kind of bug that matters in a reconciliation engine.

Runs under pytest, and also under `python3 tests/test_money_edges.py` so the
project keeps its zero-dependency promise.
"""
from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from attest.money import fmt, pct, rupees, to_rupees            # noqa: E402
from attest.sources.razorpay_api import ReconBatch, _row        # noqa: E402


def row(**kw):
    base = {"entity_id": "pay_1", "type": "payment", "amount": 100000,
            "fee": 2000, "tax": 360, "settled": True}
    base.update(kw)
    return _row(base)


# ------------------------------------------------------ zero versus missing
def test_reported_zero_credit_is_kept():
    """A fully refunded payment legitimately credits zero. It must not be
    replaced by amount - fee - tax."""
    r = row(credit=0)
    assert r.credit == 0
    assert r.net == 0, "a reported zero credit was overwritten by a computed one"


def test_missing_credit_falls_back_to_computation():
    r = row()                                  # no 'credit' key at all
    assert r.credit is None
    assert r.net == 100000 - 2000 - 360


def test_explicit_null_credit_is_missing_not_zero():
    r = row(credit=None)
    assert r.credit is None
    assert r.net == 97640


def test_zero_credit_that_disagrees_is_reported():
    """The whole point: Razorpay reporting 0 where the arithmetic says 97,640 is
    a finding, not a rounding artefact."""
    assert row(credit=0).net_disagrees_by == -97640


def test_missing_credit_cannot_disagree():
    assert row().net_disagrees_by == 0


def test_agreeing_credit_reports_no_disagreement():
    assert row(credit=97640).net_disagrees_by == 0


def test_zero_fee_is_a_real_fee():
    """A zero-MDR promotional transaction is legitimate, not missing data."""
    r = row(fee=0, tax=0, credit=100000)
    assert r.net == 100000 and r.net_disagrees_by == 0


# -------------------------------------------------------------- refunds
def test_full_refund_nets_the_batch_to_zero():
    b = ReconBatch("setl_1", None, None)
    b.payments.append(row(credit=97640))
    b.refunds.append(row(type="refund", amount=97640, fee=0, tax=0))
    assert b.expected_credit == 0


def test_partial_refund_leaves_the_remainder():
    b = ReconBatch("setl_1", None, None)
    b.payments.append(row(credit=97640))
    b.refunds.append(row(type="refund", amount=40000, fee=0, tax=0))
    assert b.expected_credit == 57640


def test_refund_larger_than_the_payment_goes_negative():
    """An over-refund is a real condition and must not be clamped to zero."""
    b = ReconBatch("setl_1", None, None)
    b.payments.append(row(credit=97640))
    b.refunds.append(row(type="refund", amount=150000, fee=0, tax=0))
    assert b.expected_credit == -52360


def test_zero_value_refund_changes_nothing():
    b = ReconBatch("setl_1", None, None)
    b.payments.append(row(credit=97640))
    b.refunds.append(row(type="refund", amount=0, fee=0, tax=0))
    assert b.expected_credit == 97640


# ----------------------------------------------------------- adjustments
def test_negative_adjustment_reduces_the_batch():
    b = ReconBatch("setl_1", None, None)
    b.payments.append(row(credit=97640))
    b.adjustments.append(row(type="adjustment", amount=0, fee=0, tax=0,
                             credit=0, debit=5000))
    assert b.expected_credit == 97640 - 5000


def test_adjustment_with_reported_zero_credit_is_not_recomputed():
    """The bug in miniature: a zero-credit adjustment contributes zero, not
    amount - fee - tax."""
    b = ReconBatch("setl_1", None, None)
    b.adjustments.append(row(type="adjustment", amount=100000, fee=2000,
                             tax=360, credit=0, debit=0))
    assert b.expected_credit == 0


def test_empty_batch_is_zero_not_an_error():
    assert ReconBatch("setl_1", None, None).expected_credit == 0


# ------------------------------------------------------------------ money
def test_paise_are_integers_end_to_end():
    assert rupees("4.12") == 412
    assert isinstance(rupees("4.12"), int)
    assert to_rupees(412) == Decimal("4.12")


def test_mdr_and_gst_on_a_real_amount():
    """2% MDR on ₹1,000 is ₹20.00, and 18% GST on that is ₹3.60 — exactly."""
    gross = rupees("1000.00")
    mdr = gross * 200 // 10000
    gst = mdr * 1800 // 10000
    assert mdr == 2000 and gst == 360
    assert fmt(gross - mdr - gst) == "₹976.40"


def test_indian_digit_grouping():
    assert fmt(183176093) == "₹18,31,760.93"
    assert fmt(0) == "₹0.00"
    assert fmt(-412) == "-₹4.12"


def test_large_amounts_do_not_lose_precision():
    big = rupees("100000000.99")
    assert big == 10000000099
    assert fmt(big) == "₹10,00,00,000.99"


def test_one_paise_survives_a_round_trip():
    assert to_rupees(1) == Decimal("0.01")
    assert fmt(1) == "₹0.01"


def test_percentage_of_zero_does_not_divide_by_zero():
    assert pct(0, 0) == Decimal("0.00")


# ------------------------------------------------------ the compensating pair
def test_two_errors_can_cancel_to_almost_nothing():
    """The case the whole product exists for, as pure arithmetic: an MDR
    overcharge and an under-deducted refund leave a batch inside any tolerance
    while both lines are wrong."""
    over, under = rupees("4.12"), rupees("4.10")
    assert over - under == 2                      # two paise
    assert over + under == 822                    # ₹8.22 actually at stake
    assert abs(over - under) < 100                # inside a ₹1 tolerance band


def _run_standalone() -> int:
    """No pytest? Run them anyway. The project has no runtime dependencies and
    its tests should not need one either."""
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = []
    for name, fn in fns:
        try:
            fn()
        except AssertionError as e:
            failed.append((name, str(e) or "assertion failed"))
        except Exception as e:                     # noqa: BLE001
            failed.append((name, f"{type(e).__name__}: {e}"))
    for name, why in failed:
        print(f"  FAIL  {name}\n        {why}")
    print(f"  {len(fns) - len(failed)}/{len(fns)} money tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run_standalone())
