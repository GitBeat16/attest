"""Make a close pack tamper-evident.

A close pack is a document a merchant hands to their auditor, their lender or
their board. Handed over as an HTML file it can be opened in any editor and a
number changed, and nothing about the document says so. An attestation you can
quietly edit is not an attestation.

**What this does.** Every figure the pack asserts is serialised into a canonical
form -- sorted keys, integer paise, no floats, no formatting -- and hashed with
SHA-256. The digest is printed on the pack, and the canonical facts are embedded
in it, so the pack carries everything needed to check itself:

    python3 -m attest.seal --verify web/close-pack.html

**What this does not do, and why saying so matters.** A digest printed inside
the file it covers proves integrity, not authorship. Anyone holding this code
can edit the facts, recompute the digest and produce a consistent forgery. That
is true of every self-contained checksum and it is why "tamper-evident" is the
honest word and "tamper-proof" would be a lie.

Authorship comes from somewhere the merchant cannot reach: when a close runs in
the hosted app the digest is written to `attest_seals`, a table with insert and
select policies and deliberately **no update and no delete policy**. A merchant
can add a seal. Nobody can alter one, including its owner and including us. So
an auditor holding a pack can ask one question -- *was this exact digest
recorded, and when?* -- and a pack edited after the close cannot answer it.

Three properties follow, and only these three:

  · edited pack, seal untouched      -> digest mismatch, caught locally
  · edited pack, digest recomputed   -> no matching record, caught on lookup
  · pack never registered            -> no record at all, and that is the answer
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

MARK_OPEN = "<!--attest-seal:v1 "
MARK_CLOSE = " :attest-seal-->"

# The document is rendered with these standing in for the digest, hashed whole,
# and the placeholders then replaced. Verification puts them back and re-hashes.
# Hashing the rendered page rather than a list of facts is the difference
# between catching every edit and catching only the edits you thought to check.
PH = "{{ATTEST-DIGEST}}"
PH_GROUPED = "{{ATTEST-DIGEST-GROUPED}}"


def canonical(p: dict) -> dict:
    """The facts a close asserts, in a form two machines cannot disagree about.

    Only integers and short strings. Rates are stored as the integer count they
    came from -- 743 of 1018 -- rather than as 73.0%, because a percentage is a
    rendering of a fact and not the fact itself, and rounding it would let two
    materially different closes seal identically.
    """
    return {
        "v": 1,
        "merchant": str(p.get("merchant", ""))[:200],
        "period": str(p.get("period", "")),
        "records": int(p.get("records", 0)),
        "batches": int(p.get("batches", 0)),
        "tied": int(p.get("tied", 0)),
        "lines": int(p.get("lines", 0)),
        "proven": int(p.get("proven", 0)),
        "tested": int(p.get("tested", 0)),
        "overturned": int(p.get("overturned", 0)),
        "volume_paise": int(p.get("volume", 0)),
        "residual_paise": int(p.get("residual_paise", 0)),
        "attestable": bool(p.get("signed", False)),
        "exceptions": sorted(
            [[str(e["class"]), int(e["count"]), int(e["exposure"])]
             for e in p.get("exceptions", [])]
        ),
    }


def digest(canon: dict) -> str:
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def seal(p: dict) -> dict:
    c = canonical(p)
    return {"canonical": c, "digest": digest(c)}


def grouped(d: str, size: int = 8, groups: int = 4) -> str:
    """Print the first 32 characters in fours. A human comparing two digests by
    eye compares the groups, so make the groups the unit."""
    return " ".join(d[i:i + size] for i in range(0, size * groups, size))


def embed(d: str, canon: dict) -> str:
    """The machine-readable half, kept out of the visible document."""
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return f"{MARK_OPEN}{d} {blob}{MARK_CLOSE}"


# ==========================================================================
def extract(html: str) -> tuple[str | None, dict | None]:
    m = re.search(re.escape(MARK_OPEN) + r"([0-9a-f]{64})\s+(\{.*?\})"
                  + re.escape(MARK_CLOSE), html, re.S)
    if not m:
        return None, None
    try:
        return m.group(1), json.loads(m.group(2))
    except json.JSONDecodeError:
        return m.group(1), None


def stamp(html: str, canon: dict) -> str:
    """Hash the whole rendered document, then write the digest into it."""
    d = hashlib.sha256(
        (html + "\n" + json.dumps(canon, sort_keys=True, separators=(",", ":")))
        .encode("utf-8")).hexdigest()
    return html.replace(PH_GROUPED, grouped(d)).replace(PH, d)


def verify(html: str) -> dict:
    """Recover the placeholders and re-hash. Any edit anywhere breaks this."""
    out = {"sealed": False, "digest_ok": False, "digest": None,
           "canonical": None, "problems": []}

    d, canon = extract(html)
    if not d or canon is None:
        out["problems"].append(
            "no seal found — this pack was produced before sealing existed, "
            "or the seal was stripped out")
        return out
    out["sealed"] = True
    out["digest"] = d
    out["canonical"] = canon

    if grouped(d) not in html:
        out["problems"].append(
            "the digest shown on the page is not the one in the seal")

    restored = html.replace(grouped(d), PH_GROUPED).replace(d, PH)
    recomputed = hashlib.sha256(
        (restored + "\n" + json.dumps(canon, sort_keys=True, separators=(",", ":")))
        .encode("utf-8")).hexdigest()

    if recomputed == d:
        out["digest_ok"] = True
    else:
        out["problems"].append(
            "the document does not hash to its own digest — something in it "
            "changed after it was sealed")
    return out


# ==========================================================================
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify that a close pack still says what it was sealed saying.")
    ap.add_argument("--verify", type=Path, required=True, metavar="PACK.html")
    args = ap.parse_args(argv)

    if not args.verify.exists():
        print(f"  no such file: {args.verify}")
        return 2

    r = verify(args.verify.read_text(encoding="utf-8"))
    print(f"\n  ATTEST — seal check on {args.verify.name}")
    print("  " + "=" * 62)

    if not r["sealed"]:
        for p in r["problems"]:
            print(f"  UNSEALED   {p}")
        print()
        return 1

    c = r["canonical"]
    print(f"  merchant   {c['merchant']}")
    print(f"  period     {c['period']}")
    print(f"  digest     {grouped(r['digest'])}")
    print(f"  covers     {c['records']:,} records · {c['tied']}/{c['batches']} tied · "
          f"{c['proven']:,}/{c['lines']} proven · "
          f"{len(c['exceptions'])} exception classes")
    print("  " + "-" * 62)

    if r["digest_ok"] and not r["problems"]:
        print("  INTACT     the whole document hashes to its printed digest.")
        print()
        print("  This proves the pack has not been edited since it was sealed.")
        print("  It does NOT prove who produced it — anyone with this code can")
        print("  seal a document. For that, check the digest against the close")
        print("  record, which cannot be altered after the fact.")
        print()
        return 0

    print("  ALTERED    this pack no longer matches its own seal.")
    for p in r["problems"]:
        print(f"             · {p}")
    print()
    return 1


if __name__ == "__main__":
    sys.exit(main())
