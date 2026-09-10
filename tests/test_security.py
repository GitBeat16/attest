"""What must hold before a real CA uploads a real client's files.

Most of what is here guards properties that were already true when this suite
was written. That is the point: they were true by accident, and an accident is
not a guarantee. Two in particular would be easy to undo without noticing —

  * `test_no_merchant_data_reaches_the_model_provider` — the planner sees
    aggregate counts and one-line tool summaries, never records. That falls out
    of the architecture rather than from care, and one more detailed tool
    summary would silently end it.
  * `test_a_pack_sealed_before_the_escaping_still_verifies` — the escaping fix
    must never invalidate a pack somebody already holds.

And one guards a defect that was real: a merchant name containing `-->` closed
the seal's HTML comment early and turned the rest of a document an auditor
opens into live markup, while the seal still read INTACT.

Runs under pytest, and standalone:  python3 tests/test_security.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from attest.close import close
from attest.report import render
from attest.seal import MARK_CLOSE, MARK_OPEN, verify

BENCH = ROOT / "data" / "sources"

HOSTILE = [
    'Acme --> <img src=x onerror=alert(1)> <!-- Ltd',   # the real one
    'Acme <script>alert(1)</script> Ltd',
    'Acme <svg onload=alert(1)> Ltd',
    'Acme "><iframe src=javascript:alert(1)> Ltd',
]


def _pack_with_merchant(name: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "sources"
        shutil.copytree(BENCH, src)
        terms = json.loads((src / "contract_terms.json").read_text(encoding="utf-8"))
        terms["merchant"] = name
        (src / "contract_terms.json").write_text(json.dumps(terms), encoding="utf-8")
        r = close(src, [p.name for p in src.glob("*")], [])
        return r.get("pack_html") or render(r["payload"])


# ------------------------------------------------------------ the pack ---
def test_a_hostile_merchant_name_cannot_break_out_of_the_seal() -> None:
    for name in HOSTILE:
        html = _pack_with_merchant(name)
        region = html[html.find(MARK_OPEN):]
        assert region, "no seal marker found"
        early = region.find("-->")
        proper = region.find(MARK_CLOSE)
        assert early >= proper, (
            f"the seal comment closed early for {name!r} — everything after it "
            "renders as live HTML")


def test_no_live_markup_survives_into_the_pack() -> None:
    for name in HOSTILE:
        html = _pack_with_merchant(name)
        for probe in ("<img src=x", "<script>alert", "<svg onload", "<iframe src="):
            assert probe not in html, f"{probe!r} rendered live in the pack from {name!r}"


def test_a_hostile_pack_still_seals_correctly() -> None:
    """Escaping must not cost tamper-evidence."""
    for name in HOSTILE:
        r = verify(_pack_with_merchant(name))
        assert r["sealed"] and r["digest_ok"], (name, r["problems"])


def test_a_pack_sealed_before_the_escaping_still_verifies() -> None:
    """The compatibility guarantee. Escaping is applied when a pack is BUILT;
    `verify()` re-serialises the PARSED dict, so a pack sealed under the old
    unescaped form must still verify. If this fails, every pack anyone already
    holds has been invalidated."""
    out = subprocess.run(["git", "show", "HEAD:web/close-pack.html"],
                         cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        return                      # no committed pack to compare against yet
    r = verify(out.stdout)
    assert r["sealed"] and r["digest_ok"], r["problems"]


def test_the_seal_comment_is_built_in_exactly_one_place() -> None:
    """report.py used to inline the blob, so escaping added to seal.embed()
    missed the path that actually renders the pack. One builder, or the fix
    silently applies to the wrong code."""
    report = (ROOT / "attest" / "report.py").read_text(encoding="utf-8")
    assert "MARK_OPEN" not in report, (
        "report.py builds the seal marker itself again — it must call "
        "seal.marker()")


# --------------------------------------------------- the model provider ---
def test_no_merchant_data_reaches_the_model_provider() -> None:
    """The strongest privacy property this design has, and nothing else holds it.

    A full investigation is run with a planner that captures every prompt. No
    merchant name, order id, payment id or settlement id may appear in any of
    them.
    """
    from attest import audit as A, engine as E, policy as P
    from attest.controller import Budget, ModelPlanner, RulesPlanner, investigate
    from attest.ingest import load, resolve, resolve_naive
    from attest.recovery import build_claims
    from attest.run import build_exceptions
    from attest.tools import Toolbox

    c = load(BENCH); resolve_naive(c); resolve(c)
    r = E.run(c); a = A.run(c, r.batch_ties)
    ex = build_exceptions(c, r, a)
    y, m = (int(x) for x in c.mdr_invoice["period"].split("-"))
    period_end = date(y + (m // 12), (m % 12) + 1, 1) - timedelta(days=1)
    today = period_end + timedelta(days=3)
    claims = build_claims(ex, period_end, today)
    pol = P.assess(sum(o.gross for o in c.orders.values()), ex)
    box = Toolbox(c, r, a, ex, claims, today, pol)

    prompts: list[str] = []
    rules = RulesPlanner()

    class Capture:
        name = "model"
        def plan(self, system, prompt):
            prompts.append(prompt)
            return json.dumps(rules.propose({"state": {}}, []))

    investigate(box, pol, "Decide whether this close can be certified.",
                planner=ModelPlanner(Capture()), budget=Budget(max_steps=10))
    assert prompts, "no prompt was captured — the test proves nothing"

    merchant = c.terms.get("merchant", "")
    order_id = next(iter(c.orders), "")
    payment_id = next((ln.payment_id for b in c.batches.values()
                       for ln in b.lines), "")
    settlement_id = next(iter(c.batches), "")

    for i, p in enumerate(prompts):
        for value, label in ((merchant, "merchant name"),
                             (order_id, "an order id"),
                             (payment_id, "a payment id"),
                             (settlement_id, "a settlement id")):
            assert not (value and value in p), (
                f"{label} reached the model provider in prompt {i + 1}")


# ---------------------------------------------------------- the secrets ---
def test_no_secret_appears_in_an_error_message() -> None:
    from attest.sources.razorpay_api import RazorpayClient, RazorpayError
    secret = "rzp_secret_DO_NOT_LEAK_9f3a2b"
    try:
        RazorpayClient("rzp_test_bogus", secret).fetch_recon(2026, 8)
    except RazorpayError as e:
        assert secret not in str(e), "the key secret is in the error text"
    except Exception as e:                        # network, TLS, proxy — fine
        assert secret not in str(e), f"the key secret leaked via {type(e).__name__}"


def test_the_package_logs_nothing() -> None:
    """Nothing can leak into a log that does not exist. Keep it that way."""
    offenders = [
        f"{p.name}:{i}"
        for p in sorted((ROOT / "attest").rglob("*.py"))
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if line.strip().startswith(("logging.", "logger.", "log."))
    ]
    assert not offenders, f"logging introduced at: {offenders}"


def test_uploads_are_bounded_before_the_body_is_read() -> None:
    src = (ROOT / "api" / "close.py").read_text(encoding="utf-8")
    assert "MAX_BODY" in src, "no upload bound"
    guard = src.index("if length > MAX_BODY")
    read = src.index("rfile.read")
    assert guard < read, "the size guard must come before the body is read"


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
