#!/usr/bin/env python3
"""Keep .env in step with .env.example, without ever touching a secret.

    python3 scripts/sync_env.py            # sync
    python3 scripts/sync_env.py --check    # report only, change nothing (CI-safe)

.env.example is the source of truth for which variables should exist. .env holds
the real values and is gitignored.

The design decision that matters here is that this tool is **append-only**. It
never rewrites, reorders or reformats .env, because that file contains live
credentials and any rewrite is a chance to corrupt or lose one. New variables are
appended with empty values; everything already present is left byte-for-byte
alone. Variables that have disappeared from the template are reported and kept,
never deleted -- a template can be wrong, and silently dropping a working
credential is a worse failure than carrying a stale one.

No value is ever printed. Only names.

Standard library only.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
ENV = ROOT / ".env"


# --------------------------------------------------------------------------
def parse(path: Path) -> tuple[list[str], dict[str, str]]:
    """Return (ordered variable names, {name: raw value}).

    Values are read so we can tell "present but empty" from "absent". They are
    never printed, logged, or written anywhere by this script.
    """
    names: list[str] = []
    values: dict[str, str] = {}
    if not path.exists():
        return names, values

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        if key not in values:
            names.append(key)
        values[key] = value.strip()
    return names, values


def leading_comment(path: Path, target: str) -> list[str]:
    """The contiguous comment block immediately above `target` in the template.

    Carried across so a newly added variable arrives with its documentation
    attached rather than as a bare name someone has to go and look up.
    """
    if not path.exists():
        return []
    block: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("#"):
            block.append(raw.rstrip())
        elif not line:
            block = []
        elif "=" in line:
            if line.partition("=")[0].strip() == target:
                return block
            block = []
    return []


# --------------------------------------------------------------------------
def sync(check_only: bool = False) -> int:
    if not EXAMPLE.exists():
        print(f"  error: {EXAMPLE.name} not found. It is the source of truth "
              "and must be committed.")
        return 2

    expected, _ = parse(EXAMPLE)          # template values are placeholders
    if not expected:
        print(f"  error: {EXAMPLE.name} declares no variables.")
        return 2

    created = False
    if not ENV.exists():
        if check_only:
            print(f"  .env is missing. Run: python3 scripts/sync_env.py")
            return 1
        # Create from the template verbatim, so the comments come too, then
        # blank every value. A fresh .env should never carry a placeholder that
        # looks like a real credential.
        out = []
        for raw in EXAMPLE.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                out.append(f"{line.partition('=')[0].strip()}=")
            else:
                out.append(raw.rstrip())
        ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
        created = True

    present, values = parse(ENV)
    missing = [k for k in expected if k not in values]
    orphaned = [k for k in present if k not in expected]
    empty = [k for k in expected if k in values and values[k] == ""]

    # --- report ----------------------------------------------------------
    print()
    if created:
        print(f"  created .env from .env.example  ({len(expected)} variables, "
              "all empty)")
    print(f"  template  {EXAMPLE.name:<16} {len(expected)} variables")
    print(f"  local     {ENV.name:<16} {len(present)} variables")
    print()

    if missing and not created:
        if check_only:
            print(f"  {len(missing)} variable(s) in the template are absent from .env:")
            for k in missing:
                print(f"    + {k}")
        else:
            lines = ["", "# " + "-" * 72,
                     "# Added by scripts/sync_env.py — fill these in.",
                     "# " + "-" * 72]
            for k in missing:
                doc = leading_comment(EXAMPLE, k)
                if doc:
                    lines.extend(doc)
                lines.append(f"{k}=")
            with ENV.open("a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            print(f"  added {len(missing)} new variable(s) with empty values:")
            for k in missing:
                print(f"    + {k}")
        print()

    if orphaned:
        print(f"  {len(orphaned)} variable(s) in .env are no longer in the template.")
        print("  Kept, not deleted — the template may simply be out of date.")
        for k in orphaned:
            print(f"    ? {k}")
        print()

    still_empty = [k for k in (empty + (missing if not check_only else []))]
    if still_empty and not created:
        print(f"  {len(set(still_empty))} variable(s) have no value yet:")
        for k in sorted(set(still_empty)):
            print(f"    · {k}")
        print()

    if not missing and not orphaned:
        print("  .env is in step with the template.\n")

    # Non-zero only in --check mode, so a normal sync never fails a script.
    return 1 if (check_only and (missing or not ENV.exists())) else 0


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Sync .env against .env.example without exposing values.")
    ap.add_argument("--check", action="store_true",
                    help="report drift and exit non-zero; change nothing")
    args = ap.parse_args()
    sys.exit(sync(check_only=args.check))


if __name__ == "__main__":
    main()
