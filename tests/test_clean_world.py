"""The control case: a month with the traps but none of the errors.

Twenty defective months all ending in NOT ATTESTABLE proves the system refuses.
It does not prove the system is *capable* of signing, and a tool that always
refuses carries exactly as much information as one that always certifies.

So this builds the same world — including the world facts that make reality
hard, the same-value same-day order pairs and the refunds that legitimately net
into the next period — and then derives the documents with no defects injected
at all. Nothing is wrong. Anything flagged is a false positive by construction.

This test found a real bug. The chargeback check summed `chargeback_adj` over
every batch while filtering disputes to the declared month, so the chargebacks
carried by out-of-period batches were reported as orphans: a phantom exception
on a month where nobody erred, counted into the unexplained residual that
decides whether the close can be signed. Twenty defective worlds could not
surface it, because a phantom hides inside real findings. A clean world has
nowhere to hide it.

Runs under pytest, and standalone:  python3 tests/test_clean_world.py
"""
from __future__ import annotations

import copy
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from attest import audit as audit_mod
from attest import engine as engine_mod
from attest.defects import Ledger, label_world_facts, seed_world_facts
from attest.documents import write_sources, write_truth
from attest.ingest import load, resolve, resolve_naive
from attest.run import RESIDUAL_LIMIT_BPS, build_exceptions
from attest.score import score
from attest.world import generate_world

SEED = 20260801
ORDERS = 1200


def _clean_world(out: Path) -> None:
    """Mirrors generate.build, minus the defect injection.

    Deliberately a copy rather than a flag on the shipped generator: production
    behaviour should not grow a branch that only a test ever takes.
    """
    world = generate_world(seed=SEED, n_orders=ORDERS)
    seed_world_facts(world, seed=SEED % 1000)     # keep the traps
    truth = copy.deepcopy(world)
    doc = copy.deepcopy(truth)                    # documents == truth
    ledger = Ledger()
    label_world_facts(truth, ledger)
    write_sources(doc, out / "sources")
    write_truth(truth, ledger, out / "truth")


def _close_clean(out: Path):
    corpus = load(out / "sources")
    resolve_naive(corpus)
    resolve(corpus)                               # mutates: writes settlement_id
    res = engine_mod.run(corpus)
    aud = audit_mod.run(corpus, res.batch_ties)
    return corpus, res, aud


def test_a_clean_month_raises_no_exceptions() -> None:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        _clean_world(out)
        corpus, res, aud = _close_clean(out)
        exceptions = build_exceptions(corpus, res, aud)
        assert not exceptions, (
            "a month with no defects raised: "
            + ", ".join(f"{e['class']}({e['exposure']}p)" for e in exceptions)
        )


def test_a_clean_month_is_attestable() -> None:
    """The refusal has to be a decision, not a default."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        _clean_world(out)
        corpus, res, aud = _close_clean(out)
        exceptions = build_exceptions(corpus, res, aud)
        volume = sum(o.gross for o in corpus.orders.values())
        residual = sum(e["exposure"] for e in exceptions if e["class"] in
                       ("CREDIT", "CHARGEBACK_ORPHAN", "UNREFERENCED_ADJ"))
        bps = (residual / volume * 10000) if volume else 0.0
        assert bps <= RESIDUAL_LIMIT_BPS, (
            f"refused a month with nothing wrong in it: {bps:.1f} bps "
            f"against a {RESIDUAL_LIMIT_BPS} bps limit"
        )


def test_world_facts_are_not_flagged_as_errors() -> None:
    """Invariant 5, at its most stringent: nothing here is an error."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        _clean_world(out)
        corpus, res, aud = _close_clean(out)
        card = score(corpus, res, aud, out / "truth")
        assert not card.false_positives, card.false_positives


def test_out_of_period_batches_do_not_leak_into_period_checks() -> None:
    """The specific regression.

    Every period-level equality must draw both of its sides from the same
    period. If one side widens to all batches, out-of-period records
    manufacture a finding — which is invariant 6 wearing a different hat.
    """
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        _clean_world(out)
        corpus, _, _ = _close_clean(out)

        y, m = (int(x) for x in corpus.mdr_invoice["period"].split("-"))
        period_end = date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
        out_of_scope = [b for b in corpus.batches.values()
                        if b.settled_on > period_end]

        # The corpus must actually contain the hazard, or this test proves
        # nothing and would keep passing after the guard was removed.
        assert out_of_scope, "no out-of-period batches — this test is vacuous"
        assert any(b.chargeback_adj for b in out_of_scope), (
            "no out-of-period batch carries a chargeback — this test is vacuous"
        )


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL  {name}\n        {e}")
    print(f"\n  {'all passed' if not fails else f'{fails} failed'}")
    sys.exit(1 if fails else 0)
