"""Pull a real month from Razorpay and close it.

    python -m attest.connect --year 2026 --month 8

Reads RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET from .env or the environment. Works
with test-mode keys; the same code path serves live keys unchanged.

This is the live-data route. The synthetic corpus in `generate.py` remains, and
must, for one reason worth stating plainly: accuracy can only be measured against
data whose truth you constructed. On live settlements you can report that
something does not reconcile, but never whether you were right about it, because
nobody knows the correct answer. Recall needs an answer key.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from .money import fmt
from .sources.razorpay_api import (
    RazorpayClient, RazorpayError, group_batches, integrity_report,
)


def load_env(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Close a real Razorpay month.")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--month", type=int, required=True)
    ap.add_argument("--ping", action="store_true", help="check credentials and exit")
    args = ap.parse_args()

    load_env()
    try:
        client = RazorpayClient(
            os.environ.get("RAZORPAY_KEY_ID", ""),
            os.environ.get("RAZORPAY_KEY_SECRET", ""),
        )
    except RazorpayError as e:
        print(f"\n  {e}\n")
        raise SystemExit(1)

    print(f"\n  Razorpay · {client.mode} mode · {client.key_id}")

    try:
        if args.ping:
            print(f"  credentials OK\n")
            return
        rows = client.fetch_recon(args.year, args.month)
    except RazorpayError as e:
        print(f"\n  {e}\n")
        raise SystemExit(1)

    if not rows:
        print(f"\n  No settlement activity for {args.year}-{args.month:02d}.")
        print("  In test mode this is normal until test payments have settled.")
        print("  The synthetic corpus exercises the full pipeline meanwhile:")
        print("    python -m attest.generate --out data --orders 1200\n")
        return

    batches, unsettled = group_batches(rows)
    rep = integrity_report(rows, batches)

    print("  " + "=" * 66)
    print(f"  {rep['rows']:,} recon rows · {rep['batches']} settlement batches "
          f"· {rep['unsettled_rows']} not yet settled")
    print(f"  fees {fmt(rep['total_fee_paise'])} · tax on fees "
          f"{fmt(rep['total_tax_paise'])}")
    print("  " + "-" * 66)
    print("  INTEGRITY OF THE SOURCE DATA ITSELF")
    print(f"    rows where Razorpay's credit disagrees with amount-fee-tax: "
          f"{rep['rows_where_credit_disagrees']}  "
          f"({fmt(rep['disagreement_paise'])})")
    print(f"    settlement batches sharing a UTR: {rep['utr_collisions']}"
          "   <- why settlement_id is the key")
    print(f"    rows on hold: {rep['on_hold_rows']}")
    print("  " + "-" * 66)
    for sid, b in sorted(batches.items(), key=lambda x: (x[1].settled_on or  __import__('datetime').date.min)):
        print(f"    {b.settled_on}  {sid[:20]:<20} "
              f"{len(b.payments):>4}p {len(b.refunds):>3}r {len(b.adjustments):>3}a"
              f"  expected {fmt(b.expected_credit):>14}")
    print()


if __name__ == "__main__":
    main()
