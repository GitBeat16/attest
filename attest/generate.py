"""Generate the Attest evaluation corpus.

    python -m attest.generate --out data --seed 20260801 --orders 620

Writes two trees:

    data/sources/   the documents the agent is allowed to read
    data/truth/     ground truth and the defect ledger -- HELD OUT

The ordering is the whole point. Truth is generated first and frozen; the
documents are derived from it and then damaged. Nothing downstream may read
data/truth/ except the scorer.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from .defects import inject, seed_world_facts
from .documents import write_sources, write_truth
from .money import fmt
from .world import generate_world


def build(out: Path, seed: int, orders: int) -> dict:
    # 1. the true world
    world = generate_world(seed=seed, n_orders=orders)

    # 2. seed the facts that make reality hard without anyone erring
    seed_world_facts(world, seed=seed % 1000)

    # 3. freeze the truth before anything is damaged
    truth = copy.deepcopy(world)

    # 4. derive the documents, then inject labelled defects into them
    doc, ledger = inject(truth, seed=seed + 1)

    src_counts = write_sources(doc, out / "sources")
    truth_counts = write_truth(truth, ledger, out / "truth")

    manifest = {
        "seed": seed,
        "period": truth.summary()["period"],
        "sources": src_counts,
        "source_record_total": sum(
            v for k, v in src_counts.items() if k.endswith(".csv")
        ),
        "truth": truth_counts,
        "planted_defects": len(ledger.entries),
        "planted_by_class": ledger.counts(),
        "planted_exposure_paise": ledger.exposure(),
        "world": truth.summary(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate the Attest evaluation corpus.")
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--seed", type=int, default=20260801)
    ap.add_argument("--orders", type=int, default=620)
    args = ap.parse_args()

    m = build(args.out, args.seed, args.orders)
    w = m["world"]

    print(f"\n  Attest evaluation corpus  ·  period {m['period']}  ·  seed {m['seed']}")
    print("  " + "-" * 62)
    print(f"  {'orders':<26}{w['orders']:>8}   "
          f"({w['prepaid_orders']} prepaid / {w['cod_orders']} COD)")
    print(f"  {'gross order value':<26}{fmt(w['gross_order_value_paise']):>18}")
    print(f"  {'settlements / lines':<26}{w['settlements']:>8} / "
          f"{w['settlement_lines']}")
    print(f"  {'bank credits':<26}{w['bank_credits']:>8}")
    print(f"  {'COD remittance lines':<26}{w['cod_remittance_lines']:>8}")
    print(f"  {'refunds / disputes':<26}{w['refunds']:>8} / {w['chargebacks']}")
    print("  " + "-" * 62)
    print(f"  {'SOURCE RECORDS (visible)':<26}{m['source_record_total']:>8}")
    print(f"  {'PLANTED ENTRIES (held out)':<26}{m['planted_defects']:>8}")
    print(f"  {'exposure at stake':<26}{fmt(m['planted_exposure_paise']):>18}")
    print("  " + "-" * 62)
    for cls, n in m["planted_by_class"].items():
        print(f"    {cls:<28}{n:>5}")
    print(f"\n  sources -> {args.out / 'sources'}")
    print(f"  truth   -> {args.out / 'truth'}   (held out from the agent)\n")


if __name__ == "__main__":
    main()
