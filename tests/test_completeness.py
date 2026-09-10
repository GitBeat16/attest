"""Input that is incomplete rather than unreadable.

`readiness.py` answers "could I read what I was given?". That question cannot
detect rows that were never supplied. Truncating 57% of the settlement report
parsed cleanly, reported READY, and *raised* the proof rate from 73.0% to 80.3%
— the missing rows took their unproven lines with them, so the metric measuring
trustworthiness rewarded losing data.

`corroborate()` closes that gap by cross-checking each source against the
records that point into it. These tests hold it shut.

Two of them are guards rather than assertions. `test_clean_world_is_ready`
is the false-positive guard: a completeness check that fires on a legitimate
month is worse than the bug it fixes, because it teaches the user to ignore the
warning. `test_the_clean_baseline_is_actually_zero` is the vacuity guard: the
threshold-free rule is only sound while a spotless month scores exactly zero, so
if that ever stops being true these tests must fail rather than quietly pass.

Runs under pytest, and standalone:  python3 tests/test_completeness.py
"""
from __future__ import annotations

import copy
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from attest.close import close
from attest.defects import Ledger, label_world_facts, seed_world_facts
from attest.documents import write_sources, write_truth
from attest.ingest import load, resolve, resolve_naive
from attest.readiness import READY, corroborate
from attest.world import generate_world

SEED = 20260801
ORDERS = 1200
BENCH = Path(__file__).resolve().parent.parent / "data" / "sources"


def _clean_sources(out: Path) -> Path:
    """The same world, with the traps but no defects planted."""
    world = generate_world(seed=SEED, n_orders=ORDERS)
    seed_world_facts(world, seed=SEED % 1000)
    truth = copy.deepcopy(world)
    doc = copy.deepcopy(truth)
    ledger = Ledger()
    label_world_facts(truth, ledger)
    write_sources(doc, out / "sources")
    write_truth(truth, ledger, out / "truth")
    return out / "sources"


def _closed(src: Path) -> dict:
    return close(src, [p.name for p in src.glob("*")], [])["summary"]


def _truncated(td: Path, filename: str, keep_rows: int) -> Path:
    """A copy of the benchmark corpus with one file cut short."""
    src = td / "sources"
    shutil.copytree(BENCH, src)
    f = src / filename
    lines = f.read_text(encoding="utf-8").splitlines()
    f.write_text("\n".join(lines[: keep_rows + 1]) + "\n", encoding="utf-8")
    return src


# ---------------------------------------------------------------- guards ---
def test_clean_world_is_ready() -> None:
    """The false-positive guard. A spotless month must not be flagged."""
    with tempfile.TemporaryDirectory() as td:
        src = _clean_sources(Path(td))
        s = _closed(src)
        assert s["readiness_verdict"] == READY, (
            "a month with nothing wrong in it was downgraded: "
            + "; ".join(s.get("readiness_reasons") or [])
        )


def test_the_clean_baseline_is_actually_zero() -> None:
    """The vacuity guard.

    Every rule in `corroborate` is threshold-free *because* a clean world scores
    zero. If that stops being true the rules need thresholds, and this test must
    be the thing that says so.
    """
    with tempfile.TemporaryDirectory() as td:
        src = _clean_sources(Path(td))
        corpus = load(src)
        resolve_naive(corpus)
        resolve(corpus)

        unexplained = [b for b in corpus.bank if not b.settlement_id]
        credited = {b.settlement_id for b in corpus.bank if b.settlement_id}
        uncredited = [sid for sid in corpus.batches if sid not in credited]
        dangling_orders = {
            ln.order_id for b in corpus.batches.values() for ln in b.lines
            if ln.order_id not in corpus.orders
        }
        dangling_awbs = {r.awb for r in corpus.cod if r.awb not in corpus.shipments}

        assert not unexplained, f"{len(unexplained)} unexplained bank credits"
        assert not uncredited, f"{len(uncredited)} uncredited batches"
        assert not dangling_orders, f"{len(dangling_orders)} dangling order refs"
        assert not dangling_awbs, f"{len(dangling_awbs)} dangling AWBs"


# ------------------------------------------------------ truncation cases ---
def test_a_short_settlement_report_is_never_ready() -> None:
    """The original defect. 57% of the file removed used to report READY."""
    for keep in (350, 200, 100):
        with tempfile.TemporaryDirectory() as td:
            src = _truncated(Path(td), "razorpay_settlements.csv", keep)
            s = _closed(src)
            assert s["readiness_verdict"] != READY, (
                f"settlement report cut to {keep} rows still reported READY")


def test_a_short_orders_export_is_never_ready() -> None:
    with tempfile.TemporaryDirectory() as td:
        src = _truncated(Path(td), "orders.csv", 600)
        s = _closed(src)
        assert s["readiness_verdict"] != READY
        assert any("orders export" in r for r in s["readiness_reasons"]), \
            s["readiness_reasons"]


def test_a_short_shipment_manifest_is_never_ready() -> None:
    with tempfile.TemporaryDirectory() as td:
        src = _truncated(Path(td), "shipments.csv", 400)
        s = _closed(src)
        assert s["readiness_verdict"] != READY
        assert any("AWB" in r for r in s["readiness_reasons"]), s["readiness_reasons"]


def test_a_short_bank_statement_is_never_ready() -> None:
    """Check 1 cannot see this — nothing references a bank row. Check 2 can."""
    with tempfile.TemporaryDirectory() as td:
        src = _truncated(Path(td), "bank_statement.csv", 11)
        s = _closed(src)
        assert s["readiness_verdict"] != READY
        assert any("no corresponding bank credit" in r
                   for r in s["readiness_reasons"]), s["readiness_reasons"]


def test_a_short_refund_export_is_never_ready() -> None:
    """Direction, not magnitude.

    A settlement cannot deduct a refund the export does not contain, so a
    POSITIVE delta means money was netted against records nobody supplied. The
    delta was negative or zero on all six clean worlds measured and turned
    positive only under truncation, which is why no threshold is needed.
    """
    with tempfile.TemporaryDirectory() as td:
        src = _truncated(Path(td), "refunds.csv", 9)
        s = _closed(src)
        assert s["readiness_verdict"] != READY
        assert any("refund export" in r for r in s["readiness_reasons"]), \
            s["readiness_reasons"]


def test_a_short_dispute_export_is_never_ready() -> None:
    with tempfile.TemporaryDirectory() as td:
        src = _truncated(Path(td), "disputes.csv", 5)
        s = _closed(src)
        assert s["readiness_verdict"] != READY
        assert any("dispute export" in r for r in s["readiness_reasons"]), \
            s["readiness_reasons"]


def test_ordinary_timing_lag_is_not_flagged() -> None:
    """The other half of the direction rule.

    A NEGATIVE delta -- refunds or disputes recorded but not yet netted -- is
    ordinary month-end timing and must be left alone. The clean world carries
    exactly that, and it must still be READY.
    """
    with tempfile.TemporaryDirectory() as td:
        src = _clean_sources(Path(td))
        joined = " ".join(_closed(src)["readiness_reasons"])
        assert "refund export" not in joined and "dispute export" not in joined, \
            "legitimate timing lag was reported as missing records: " + joined


def test_an_incomplete_month_cannot_be_attested() -> None:
    """A close cannot be signed over input nobody can vouch for."""
    with tempfile.TemporaryDirectory() as td:
        src = _truncated(Path(td), "razorpay_settlements.csv", 200)
        s = _closed(src)
        assert s["readiness_verdict"] != READY
        assert not s.get("attestable"), "a truncated month was marked attestable"


def test_the_cod_remittance_gap_is_still_a_gap() -> None:
    """Pins a KNOWN limitation so the note in `corroborate` cannot go stale.

    Nothing references a remittance row, and the obvious mirror -- COD
    shipments delivered but never remitted -- runs at 125-147 on clean worlds
    against 223 when halved, so there is no honest threshold. If this test ever
    fails, the gap was closed: delete the test and the caveat together.
    """
    with tempfile.TemporaryDirectory() as base:
        clean = _clean_sources(Path(base))
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "sources"
            shutil.copytree(clean, src)
            f = src / "cod_remittances.csv"
            lines = f.read_text(encoding="utf-8").splitlines()
            f.write_text("\n".join(lines[: len(lines) // 2]) + "\n", encoding="utf-8")
            assert _closed(src)["readiness_verdict"] == READY, (
                "a short COD remittance file is now detected -- good. Remove "
                "this test and the limitation note in corroborate().")


# ------------------------------------------------------------- messaging ---
def test_the_all_clear_note_is_retracted_on_downgrade() -> None:
    """A PARTIAL close must not lead with a line saying everything was read."""
    s = _closed(BENCH)
    assert s["readiness_verdict"] != READY, "the benchmark month has known orphans"
    joined = " ".join(s["readiness_reasons"])
    assert "every row supplied was parsed" not in joined, (
        "the all-clear note survived a downgrade: " + joined)


def test_corroborate_only_ever_downgrades() -> None:
    """It must never talk a bad close back up into a good one."""
    with tempfile.TemporaryDirectory() as td:
        src = _truncated(Path(td), "razorpay_settlements.csv", 200)
        corpus = load(src)
        resolve_naive(corpus)
        resolve(corpus)
        rd = corpus.readiness
        rd.refuse("forced, for this test")
        corroborate(rd, corpus)
        assert rd.refused, "corroborate upgraded a REFUSED close"


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
