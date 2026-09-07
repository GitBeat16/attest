"""Is 73.0% a property of the system, or a property of one seed?

Every headline number in the README comes from a single generated world. That is
a fair way to report a benchmark and a poor way to defend one: a reader is
entitled to ask whether the figure survives a different month.

This runs the whole pipeline — generate, ingest, match, audit, score — across N
independently seeded worlds and reports the spread. It reads `data/truth/` only
through `score.py`, exactly as `run.py` does, so invariant 2 holds.

    python3 scripts/stability.py                 # 20 seeds, 1200 orders
    python3 scripts/stability.py --seeds 5       # quicker
    python3 scripts/stability.py --json out.json

The two numbers to read are held-out recall and false positives. Held-out recall
moving around is expected and honest — it is 3 or 4 defects out of a handful.
A false positive appearing in ANY world is a real finding and must be fixed at
the cause, never by dropping the seed that exposed it.
"""
from __future__ import annotations

import argparse
import copy
import json
import statistics
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from attest import audit as audit_mod
from attest import engine as engine_mod
from attest.defects import Ledger, label_world_facts, seed_world_facts
from attest.documents import write_sources, write_truth
from attest.generate import build as generate_corpus
from attest.ingest import load, resolve, resolve_naive
from attest.money import fmt
from attest.recovery import build_claims, summarise
from attest.run import RESIDUAL_LIMIT_BPS, build_exceptions
from attest.score import score
from attest.world import generate_world

HEADLINE_SEED = 20260801


def build_clean(out: Path, seed: int, orders: int) -> None:
    """The same world, with the traps but none of the errors.

    A benchmark of twenty defective months tells you the system refuses. It does
    not tell you the system is *capable* of signing, and a tool that always
    refuses is as uninformative as one that always certifies.

    So: build the world, seed the world facts that make reality hard — the
    same-value same-day order pairs, the refunds that legitimately net into next
    month — and then derive the documents with **no defects injected at all**.
    The traps remain; the errors do not.

    Two things must then be true, and they are the strongest assertions in the
    suite: nothing is flagged (anything flagged here is a false positive by
    construction, since there is nothing to find), and the residual falls under
    the threshold so Attest actually signs.

    This mirrors `generate.build` deliberately rather than adding a flag to it —
    the shipped generator's behaviour must not change to accommodate a test.
    """
    world = generate_world(seed=seed, n_orders=orders)
    seed_world_facts(world, seed=seed % 1000)

    truth = copy.deepcopy(world)
    doc = copy.deepcopy(truth)          # documents == truth: nobody erred

    ledger = Ledger()
    label_world_facts(truth, ledger)    # the traps are still labelled non-errors

    write_sources(doc, out / "sources")
    write_truth(truth, ledger, out / "truth")


def one_world(seed: int, orders: int, clean: bool = False) -> dict:
    """Generate a world at `seed`, close it, and score it against its own truth."""
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        if clean:
            build_clean(out, seed, orders)
        else:
            generate_corpus(out, seed, orders)

        corpus = load(out / "sources")
        # Key resolution MUTATES the corpus — it writes settlement_id onto each
        # bank row, and the engine ties batches through that. Skipping it does
        # not merely omit a statistic, it silently zeroes the match rate. Same
        # order as run.py, deliberately.
        resolve_naive(corpus)
        resolve(corpus)
        res = engine_mod.run(corpus)
        aud = audit_mod.run(corpus, res.batch_ties)

        tied = sum(1 for v in res.batch_ties.values() if v)
        proven = sum(1 for c in res.chains if c.complete)
        total = len(res.chains)
        volume = sum(o.gross for o in corpus.orders.values())

        exceptions = build_exceptions(corpus, res, aud)
        residual = sum(e["exposure"] for e in exceptions if e["class"] in
                       ("CREDIT", "CHARGEBACK_ORPHAN", "UNREFERENCED_ADJ"))
        residual_bps = (residual / volume * 10000) if volume else 0.0

        y, m = (int(x) for x in corpus.mdr_invoice["period"].split("-"))
        period_end = date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
        today = period_end + timedelta(days=3)
        claims = build_claims(exceptions, period_end, today)
        rec = summarise(claims, today)
        late = summarise(claims, today + timedelta(days=30))

        # Tiering must not move the recoverable total; the claim-ready slice is
        # reported so its stability across seeds is visible too.
        tier_sum = sum(v["exposure"] for v in rec["by_tier"].values())
        assert tier_sum == rec["recoverable"], (
            f"tier split {tier_sum} != recoverable {rec['recoverable']}")

        card = score(corpus, res, aud, out / "truth")

        return {
            "seed": seed,
            "records": corpus.record_count(),
            "match_rate": tied / len(res.batch_ties) * 100 if res.batch_ties else 0.0,
            "proof_rate": proven / total * 100 if total else 0.0,
            "false_match_rate": aud.false_match_rate() * 100,
            "designed_recall": card.designed_recall() * 100,
            "holdout_recall": card.holdout_recall() * 100,
            "holdout_caught": sum(c.detected for c in card.by_class.values() if c.held_out),
            "holdout_planted": sum(c.planted for c in card.by_class.values() if c.held_out),
            "false_positives": len(card.false_positives),
            "fp_detail": card.false_positives[:5],
            "suppressed": card.suppressed,
            "recoverable": rec["recoverable"],
            "claim_ready": rec["claim_ready"],
            "cadence_cost": late["lapsed"] - rec["lapsed"],
            "residual_bps": residual_bps,
            "attestable": residual_bps <= RESIDUAL_LIMIT_BPS,
            "exceptions": len(exceptions),
            "clean": clean,
        }


def band(rows: list[dict], key: str) -> tuple[float, float, float]:
    vals = [r[key] for r in rows]
    return min(vals), statistics.median(vals), max(vals)


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark stability across seeds.")
    ap.add_argument("--seeds", type=int, default=20, help="how many worlds")
    ap.add_argument("--orders", type=int, default=1200)
    ap.add_argument("--json", type=Path, help="write the full table here")
    args = ap.parse_args()

    # The headline seed first, so the submitted figures are the first row and
    # any drift is visible immediately rather than averaged away.
    seeds = [HEADLINE_SEED] + [HEADLINE_SEED + i for i in range(1, args.seeds)]

    P = print
    P(f"\n  BENCHMARK STABILITY  ·  {len(seeds)} worlds  ·  {args.orders} orders each")
    P("  " + "=" * 76)
    P(f"  {'seed':>9}  {'match':>7} {'proof':>7} {'falsem':>7}  "
      f"{'desgn':>6} {'held':>10}  {'FP':>3}  {'residual':>9}  status")
    P("  " + "-" * 76)

    rows = []
    for s in seeds:
        try:
            r = one_world(s, args.orders)
        except Exception as e:                       # a world that will not build
            P(f"  {s:>9}  FAILED: {type(e).__name__}: {e}")
            continue
        rows.append(r)
        P(f"  {r['seed']:>9}  {r['match_rate']:>6.1f}% {r['proof_rate']:>6.1f}% "
          f"{r['false_match_rate']:>6.1f}%  {r['designed_recall']:>5.0f}% "
          f"{r['holdout_caught']}/{r['holdout_planted']}={r['holdout_recall']:>3.0f}%  "
          f"{r['false_positives']:>3}  {r['residual_bps']:>7.1f}bps  "
          f"{'ATTESTABLE' if r['attestable'] else 'NOT ATTESTABLE'}")

    if not rows:
        P("\n  no world completed — nothing to report.")
        sys.exit(1)

    P("  " + "-" * 76)
    P(f"\n  ACROSS {len(rows)} WORLDS")
    P("  " + "-" * 76)
    for label, key, unit in [("match rate", "match_rate", "%"),
                             ("proof rate", "proof_rate", "%"),
                             ("false-match rate", "false_match_rate", "%"),
                             ("recall, designed-for", "designed_recall", "%"),
                             ("recall, HELD OUT", "holdout_recall", "%")]:
        lo, mid, hi = band(rows, key)
        P(f"    {label:<22}{lo:>6.1f}{unit} – {hi:.1f}{unit}   (median {mid:.1f}{unit})")

    fp_total = sum(r["false_positives"] for r in rows)
    worlds_with_fp = [r["seed"] for r in rows if r["false_positives"]]
    P(f"    {'FALSE POSITIVES':<22}{fp_total:>6}   "
      + ("in no world" if not fp_total else f"in worlds {worlds_with_fp}"))

    lo, mid, hi = band(rows, "recoverable")
    P(f"    {'recoverable':<22}{fmt(lo):>12} – {fmt(hi)}")
    lo, mid, hi = band(rows, "claim_ready")
    P(f"    {'  of which claim-ready':<22}{fmt(lo):>12} – {fmt(hi)}")
    lo, mid, hi = band(rows, "cadence_cost")
    P(f"    {'cost of monthly close':<22}{fmt(lo):>12} – {fmt(hi)}")
    n_att = sum(1 for r in rows if r["attestable"])
    P(f"    {'signed the close':<22}{n_att:>6} of {len(rows)}   "
      f"({len(rows) - n_att} refused)")

    # ---- the control: a world with the traps but no errors ----------------
    P("")
    P("  CONTROL — same world, world facts intact, no defects planted")
    P("  " + "-" * 76)
    ctl = one_world(HEADLINE_SEED, args.orders, clean=True)
    P(f"    {'false positives':<24}{ctl['false_positives']:>6}"
      f"   {'(nothing to find, so anything found is one)'}")
    P(f"    {'exceptions raised':<24}{ctl['exceptions']:>6}")
    P(f"    {'residual':<24}{ctl['residual_bps']:>6.1f} bps   "
      f"(threshold {RESIDUAL_LIMIT_BPS})")
    P(f"    {'verdict':<24}{'ATTESTABLE' if ctl['attestable'] else 'NOT ATTESTABLE':>6}")
    if ctl["false_positives"]:
        P(f"      flagged: {', '.join(ctl['fp_detail'])}")

    P("")
    if fp_total:
        P("  A false positive appeared. That is a finding, not noise — fix the")
        P("  cause. Dropping the seed that exposed it is the one response that")
        P("  makes the benchmark worthless.")
    else:
        P(f"  Zero false positives across {len(rows)} independently seeded worlds.")

    if ctl["attestable"] and not ctl["false_positives"]:
        P("  It refused all twenty defective months and signed the clean one, so")
        P("  the refusal is a decision rather than a default.")
    elif not ctl["attestable"]:
        P("  It refused a month with nothing wrong in it. Either the residual")
        P("  threshold is too tight or something is being counted that should")
        P("  not be — worth finding out before claiming the refusal means much.")

    if args.json:
        args.json.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        P(f"  full table -> {args.json}")
    P("")


if __name__ == "__main__":
    main()
