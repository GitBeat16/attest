"""What produced a conclusion, six months after it was produced.

The seal proves a pack was not edited. These tests hold the *other* half: that a
pack can say what rules were applied, and that the statement cannot quietly stop
being true.

Two properties carry the whole mechanism, and both are here:

  1. changing ANY rule value moves the ruleset digest -- without this the
     version is decoration, and decoration in a finance tool is worse than
     nothing because it implies an assurance it does not provide;
  2. changing a comment, a docstring or formatting does NOT move it -- a digest
     that churns on every edit teaches people to ignore it.

Runs under pytest, and standalone:  python3 tests/test_versioning.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from attest import audit, defects, demo, engine, policy, recovery, ruleset, tiers
from attest import __version__


# ------------------------------------------------------- property 1: moves ---
def test_every_scalar_rule_moves_the_digest() -> None:
    base = ruleset.digest()
    cases = [
        (engine, "BATCH_TOLERANCE", 101),
        (engine, "LINE_TOLERANCE", 1),
        (audit, "NOISE", 3),
        (audit, "OFFSET_THRESHOLD", 51),
        (policy, "RESIDUAL_LIMIT_BPS", 26),
        (policy, "UNEXPLAINED", ("CREDIT",)),
    ]
    for mod, name, new in cases:
        old = getattr(mod, name)
        setattr(mod, name, new)
        try:
            assert ruleset.digest() != base, (
                f"{mod.__name__}.{name} changed but the ruleset digest did not")
        finally:
            setattr(mod, name, old)
        assert ruleset.digest() == base, f"restoring {name} did not restore the digest"


def test_a_claim_window_moves_the_digest() -> None:
    base = ruleset.digest()
    saved = dict(recovery.WINDOWS)
    recovery.WINDOWS["MDR"] = ("razorpay", 61)
    try:
        assert ruleset.digest() != base, "a claim window changed silently"
    finally:
        recovery.WINDOWS.clear()
        recovery.WINDOWS.update(saved)
    assert ruleset.digest() == base


def test_a_tier_mapping_moves_the_digest() -> None:
    """What a finding may assert is a rule, and must be versioned like one."""
    base = ruleset.digest()
    saved = dict(tiers._MAP)
    tiers._MAP["MDR"] = tiers.UNPROVEN
    try:
        assert ruleset.digest() != base, "a class changed tier silently"
    finally:
        tiers._MAP.clear()
        tiers._MAP.update(saved)
    assert ruleset.digest() == base


# --------------------------------------------- property 2: does not churn ---
def test_generator_and_demo_constants_do_not_move_the_digest() -> None:
    """Scope. These shape the benchmark, not anyone's month.

    Folding them in would make the version churn for reasons no auditor cares
    about, and a version people learn to ignore is worse than none.
    """
    base = ruleset.digest()
    old_rate, old_gross = defects.INFLATED_MDR_RATE, demo.A_GROSS
    defects.INFLATED_MDR_RATE = Decimal("9.99")
    demo.A_GROSS = 1
    try:
        assert ruleset.digest() == base, (
            "a corpus-generator constant leaked into the ruleset")
    finally:
        defects.INFLATED_MDR_RATE, demo.A_GROSS = old_rate, old_gross


def test_the_digest_is_stable_in_a_fresh_interpreter() -> None:
    """No salted hash, no dict ordering, no repr() may reach the digest."""
    here = ruleset.digest()
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys;sys.path.insert(0,%r);from attest import ruleset;"
         "print(ruleset.digest())" % str(ROOT)],
        capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-400:]
    assert out.stdout.strip() == here, (
        "the digest differs between interpreters — something unstable is being "
        "hashed")


def test_the_canonical_form_is_json_not_repr() -> None:
    """A guard on the mechanism itself, not on its output."""
    text = ruleset._canonical(ruleset.rules())
    json.loads(text)                       # must parse
    assert text == ruleset._canonical(ruleset.rules())


# ----------------------------------------------------------- single owner ---
def test_the_residual_limit_has_exactly_one_definition() -> None:
    """It had two, in policy.py and run.py, and they could have drifted."""
    defs = [f"{p.name}:{i}"
            for p in sorted((ROOT / "attest").glob("*.py"))
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
            if line.startswith("RESIDUAL_LIMIT_BPS") and "=" in line
            and "import" not in line]
    assert len(defs) == 1, f"expected one definition, found: {defs}"
    assert policy.RESIDUAL_LIMIT_BPS is __import__(
        "attest.run", fromlist=["x"]).RESIDUAL_LIMIT_BPS


# ------------------------------------------------------------ in the pack ---
def _pack() -> str:
    p = ROOT / "web" / "close-pack.html"
    assert p.exists(), "run: python3 -m attest.run --data data --html web/close-pack.html"
    return p.read_text(encoding="utf-8")


def test_the_sealed_block_states_what_produced_the_close() -> None:
    from attest.seal import extract
    _, canon = extract(_pack())
    assert canon, "the pack carries no canonical block"
    rs = canon.get("ruleset") or {}
    assert rs.get("engine_version") == __version__, rs
    assert rs.get("ruleset_digest") == ruleset.short(), rs


def test_an_older_canonical_version_still_verifies() -> None:
    """The point of the whole stage.

    A pack must not stop verifying because the code moved on. `verify()`
    re-hashes the block embedded in the pack rather than recomputing today's
    shape, so this holds by construction — pinned here so it stays true.
    """
    from attest.seal import MARK_CLOSE, MARK_OPEN, PH, PH_GROUPED, stamp, verify

    # Built through the real sealing path -- `stamp()` hashes the document with
    # its placeholders still in place -- but carrying a v2 canonical block with
    # no ruleset, as a pack sealed before versioning existed would.
    canon = {"v": 2, "merchant": "Old Co", "period": "2026-01", "records": 10}
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    body = ("<html><body><p>digest <span>" + PH_GROUPED + "</span></p>"
            + MARK_OPEN + PH + " " + blob + MARK_CLOSE + "</body></html>")

    r = verify(stamp(body, canon))
    assert r["sealed"], r["problems"]
    assert r["digest_ok"], r["problems"]
    assert r["canonical"]["v"] == 2, r["canonical"]
    assert "ruleset" not in r["canonical"], "a v2 pack must not gain today's fields"


def test_the_changelog_describes_this_version() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## {__version__}" in text, f"CHANGELOG.md has no entry for {__version__}"
    assert ruleset.short() in text, (
        f"CHANGELOG.md does not record ruleset digest {ruleset.short()}")


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
