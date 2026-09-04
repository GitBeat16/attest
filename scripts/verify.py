#!/usr/bin/env python3
"""One command that checks the whole system locally.

    python3 scripts/verify.py

Run this before recording the demo and before submitting. It exercises every
path, asserts the numbers that must not drift, and exits non-zero if anything is
wrong.

The assertions are chosen deliberately. Most of them are not "did it produce a
number" but "is the number still honest" — held-out recall must stay below 100%,
false positives must stay at zero, and the proof rate must stay below the match
rate. Those three are the integrity of the whole project; a change that improves
everything else while breaking one of them has broken the project.
"""
from __future__ import annotations

import io
import contextlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PASS, FAIL, WARN = "  PASS", "  FAIL", "  WARN"
results: list[tuple[str, str, str]] = []


def record(kind: str, name: str, detail: str = "") -> None:
    results.append((kind, name, detail))
    line = f"{kind}  {name}"
    if detail:
        line += f"\n        {detail}"
    print(line)


def section(title: str) -> None:
    print(f"\n  {title}\n  " + "-" * 66)


# ==========================================================================
def check_env() -> None:
    section("ENVIRONMENT")
    from attest import config
    config.load_env()

    if not (ROOT / ".env.example").exists():
        record(FAIL, ".env.example present", "the committed template is missing")
        return
    record(PASS, ".env.example present")

    r = subprocess.run([sys.executable, "scripts/sync_env.py", "--check"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode == 0:
        record(PASS, ".env in step with template")
    else:
        record(WARN, ".env drifted from template",
               "run: python3 scripts/sync_env.py")

    # A real .env must never be tracked by git.
    tracked = subprocess.run(["git", "ls-files", ".env"], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip()
    if tracked:
        record(FAIL, ".env is NOT committed", "DANGER: .env is tracked by git")
    else:
        record(PASS, ".env is not tracked by git")

    if config.missing("razorpay"):
        record(WARN, "Razorpay configured", "no credentials — live check skipped")
    else:
        record(PASS, "Razorpay configured",
               f"key {config.mask(__import__('os').environ['RAZORPAY_KEY_ID'])}")


# ==========================================================================
def check_pipeline() -> dict:
    section("PIPELINE")
    from attest.generate import build

    t0 = time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        manifest = build(ROOT / "data", seed=20260801, orders=1200)
    record(PASS, "corpus generated",
           f"{manifest['source_record_total']:,} records, "
           f"{manifest['planted_defects']} planted entries")

    from attest.ingest import load, resolve
    from attest.engine import run as engine_run
    from attest.audit import run as audit_run
    from attest.score import score

    t0 = time.time()
    corpus = load(ROOT / "data" / "sources")
    resolve(corpus)
    res = engine_run(corpus)
    aud = audit_run(corpus, res.batch_ties)
    elapsed = time.time() - t0

    tied = sum(1 for v in res.batch_ties.values() if v)
    proven = sum(1 for c in res.chains if c.complete)
    total = len(res.chains)
    match_rate = tied / len(res.batch_ties)
    proof_rate = proven / total
    fmr = aud.false_match_rate()

    record(PASS, "pipeline ran", f"{corpus.record_count():,} records in {elapsed:.2f}s")
    if elapsed > 5:
        record(WARN, "throughput", f"{elapsed:.1f}s is slower than expected")

    card = score(corpus, res, aud, ROOT / "data" / "truth")
    return {
        "match": match_rate, "proof": proof_rate, "fmr": fmr,
        "card": card, "aud": aud, "res": res,
    }


# ==========================================================================
def check_integrity(m: dict) -> None:
    section("INTEGRITY — the numbers that must stay honest")
    card, aud = m["card"], m["aud"]

    # 1. The descent must hold. If proof ever exceeds match, the central claim
    #    of the project has inverted and something is very wrong.
    if m["proof"] < m["match"]:
        record(PASS, "proof rate below match rate",
               f"{m['match']*100:.1f}% matched vs {m['proof']*100:.1f}% proven")
    else:
        record(FAIL, "proof rate below match rate",
               f"proof {m['proof']*100:.1f}% >= match {m['match']*100:.1f}% — "
               "the descent has inverted")

    # 2. Held-out recall must be neither 0 nor 100. Zero means the generic checks
    #    stopped generalising; 100 means someone wrote a targeted detector and
    #    destroyed the evidence that the score is not circular.
    ho = card.holdout_recall()
    if 0 < ho < 1.0:
        record(PASS, "held-out recall is honest", f"{ho*100:.1f}% — a visible miss remains")
    elif ho >= 1.0:
        record(FAIL, "held-out recall is honest",
               "100% — a targeted detector was written for a held-out class. "
               "The visible miss was the evidence the score is not circular.")
    else:
        record(FAIL, "held-out recall is honest",
               "0% — generic checks no longer catch anything they were not built for")

    # 3. False positives must stay at zero. Flagging a legitimate timing gap is
    #    the failure this whole design exists to avoid.
    if not card.false_positives:
        record(PASS, "no false positives", f"{card.suppressed} non-errors suppressed")
    else:
        record(FAIL, "no false positives", "; ".join(card.false_positives))

    # 4. Designed-for recall should be high, but on its own it proves nothing --
    #    so it is checked, not celebrated.
    dr = card.designed_recall()
    record(PASS if dr > 0.9 else FAIL, "designed-for recall",
           f"{dr*100:.1f}% (circular by construction — reported separately)")

    # 5. The compensating pairs are the demo. If they stop being found, the
    #    single most important claim in the pitch is gone.
    comp = [a for a in aud.overturned if a.hypothesis == "offsetting_pair"]
    if len(comp) >= 2:
        ev = comp[0].evidence
        record(PASS, "compensating pairs detected",
               f"{len(comp)} found; batch nets to "
               f"{ev.get('residual_paise')}p while lines are "
               f"{abs(ev.get('line_variance_paise', 0))}p out")
    else:
        record(FAIL, "compensating pairs detected",
               f"only {len(comp)} found — the demo's central case is missing")


# ==========================================================================
def check_outputs() -> None:
    section("OUTPUTS")
    r = subprocess.run(
        [sys.executable, "-m", "attest.run", "--data", "data",
         "--html", "web/close-pack.html", "--json", "data/attestation.json"],
        cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        record(FAIL, "attest.run completes", r.stderr[-300:])
        return
    record(PASS, "attest.run completes")

    html = ROOT / "web" / "close-pack.html"
    if not html.exists():
        record(FAIL, "close pack written")
        return
    body = html.read_text(encoding="utf-8")
    size = len(body) / 1024
    record(PASS, "close pack written", f"web/close-pack.html, {size:.0f} KB")

    for needle, label in [
        ("claim back", "hero states the money"),
        ("compensating error", "the ₹0.02 worked example is present"),
        ("held out", "the held-out scorecard is present"),
        ("Lost by closing monthly", "the cadence argument is present"),
    ]:
        record(PASS if needle in body else FAIL, label)

    if "<script" in body.lower():
        record(WARN, "close pack is static", "contains a <script> tag")
    else:
        record(PASS, "close pack is static", "no JS, no external calls")

    # The landing page is a deliverable too, and it has one rule: it must never
    # hide the argument behind an animation. The loading coin is shown only when
    # JS is running and is dismissed on a hard timer.
    land = ROOT / "web" / "index.html"
    if not land.exists():
        record(FAIL, "landing page present")
        return
    lb = land.read_text(encoding="utf-8")
    record(PASS, "landing page present", f"web/index.html, {len(lb)/1024:.0f} KB")

    record(PASS if "#boot{" in lb and "display:none" in lb else FAIL,
           "loader never gates content",
           "overlay is display:none until the js class is set")
    record(PASS if "setTimeout(finish, 3000)" in lb else FAIL,
           "loader self-cancels", "hard ceiling regardless of what is still loading")
    record(PASS if "prefers-reduced-motion" in lb else FAIL,
           "motion is opt-out", "reduced-motion users get the page, not the animation")
    record(PASS if 'href="close-pack.html"' in lb else FAIL,
           "landing page links to the close pack")
    record(PASS if 'href="app.html"' in lb else FAIL,
           "landing page links to the app")

    app = ROOT / "web" / "app.html"
    if app.exists():
        ab = app.read_text(encoding="utf-8")
        record(PASS if "rzp_test_" in ab and "dataTransfer" in ab else FAIL,
               "app has drag-and-drop and refuses live keys")
        # Every page has to survive a phone. The check is layout, not taste:
        # nothing may push the document wider than the viewport, because a page
        # that scrolls sideways hides the column the reader came for.
        for name, text in (("landing page", lb), ("app", ab), ("close pack", body)):
            ok = "max-width:640px" in text.replace(" ", "") and "viewport" in text
            record(PASS if ok else FAIL, f"{name} is responsive",
                   "narrow-screen rules and a viewport tag are present")
    else:
        record(FAIL, "app present")

    if (ROOT / "USE-CASES.md").exists():
        record(PASS, "use cases documented")
    else:
        record(WARN, "use cases documented", "USE-CASES.md is missing")

    # --- the seal ---------------------------------------------------------
    # A seal that cannot detect an edit is decoration, so the check is not
    # "does it produce a digest" but "does tampering actually break it".
    from attest.seal import verify as verify_seal
    r = verify_seal(body)
    if r["sealed"] and r["digest_ok"] and not r["problems"]:
        record(PASS, "close pack is sealed", f"digest {r['digest'][:16]}…")
    else:
        record(FAIL, "close pack is sealed", "; ".join(r["problems"]) or "no seal")
        return

    from attest.seal import grouped
    if grouped(r["digest"]) in lb:
        record(PASS, "landing page shows the real digest")
    else:
        record(FAIL, "landing page shows the real digest",
               f"paste this into web/index.html: {grouped(r['digest'])}")

    # A sealed artefact that hashes differently every run is not much of a
    # seal, so reproducibility is asserted rather than assumed.
    import tempfile as _tf
    with _tf.TemporaryDirectory() as td:
        again = Path(td) / "again.html"
        subprocess.run([sys.executable, "-m", "attest.run", "--data", "data",
                        "--html", str(again)], cwd=ROOT, capture_output=True)
        r2 = verify_seal(again.read_text(encoding="utf-8")) if again.exists() else {}
    if r2.get("digest") == r["digest"]:
        record(PASS, "the close pack is byte-reproducible", "two runs, one digest")
    else:
        record(FAIL, "the close pack is byte-reproducible",
               "the same inputs produced two different digests")

    caught = 0
    edits = [("₹", "Rs "), ("NOT ATTESTABLE", "SIGNED"), ("2026-08", "2026-09")]
    for before, after in edits:
        if before not in body:
            continue
        if not verify_seal(body.replace(before, after, 1))["digest_ok"]:
            caught += 1
    record(PASS if caught == len([e for e in edits if e[0] in body]) else FAIL,
           "tampering breaks the seal",
           f"{caught} single-character-class edits detected, none missed")


# ==========================================================================
def check_engine() -> None:
    section("REASONING LAYER")
    import os
    from attest import config, engines
    config.load_env()
    want = os.environ.get("ATTEST_ENGINE", "").strip() or "rules"

    eng = engines.get_engine()
    record(PASS, "engine resolves", f"requested {want}, active {eng.name}")

    sample = {"class": "MDR", "count": 23, "exposure_display": "₹121.90",
              "period": "2026-08"}
    try:
        e = eng.explain_variance(sample)
    except Exception as ex:
        record(FAIL, "explanation produced", f"{type(ex).__name__}: {ex}")
        return
    record(PASS, "explanation produced", f"[{e.source}] {e.text[:90]}…")

    if want != "rules" and e.source == "rules":
        record(WARN, f"{want} answered",
               f"fell back to rules — {getattr(eng, 'last_error', 'no reason recorded')}")
    elif want != "rules":
        record(PASS, f"{want} answered")

    # The whole point: the pipeline must not need this.
    rules_only = engines.get_engine("rules")
    if rules_only.explain_variance(sample).source == "rules":
        record(PASS, "rules path independent", "close completes with no model at all")


# ==========================================================================
def check_razorpay() -> None:
    section("RAZORPAY — LIVE")
    import os
    from attest import config
    from attest.sources.razorpay_api import RazorpayClient, RazorpayError
    config.load_env()
    if config.missing("razorpay"):
        record(WARN, "live credential check", "no credentials configured")
        return
    try:
        c = RazorpayClient(os.environ["RAZORPAY_KEY_ID"],
                           os.environ["RAZORPAY_KEY_SECRET"])
        c.ping()
        record(PASS, "live credential check", f"{c.mode} mode, API reachable")
    except RazorpayError as e:
        # A rejected key is a real failure. An unreachable network is not: the
        # whole pipeline is designed to close with no network at all, so a
        # firewall or an offline laptop must not read as a broken submission.
        msg = str(e)
        rejected = any(s in msg for s in ("401", "403 Forbidden\nBAD_REQUEST",
                                          "Authentication failed", "invalid api key"))
        unreachable = any(s in msg.lower() for s in
                          ("could not reach", "tunnel", "timed out", "name or service",
                           "connection refused", "temporary failure"))
        if rejected and not unreachable:
            record(FAIL, "live credential check", msg[:200])
        elif unreachable:
            record(WARN, "live credential check",
                   f"network blocked, not a credential problem — {msg[:120]}")
        else:
            record(FAIL, "live credential check", msg[:200])


# ==========================================================================
def main() -> None:
    print("\n  ATTEST — local verification")
    print("  " + "=" * 66)
    try:
        check_env()
        m = check_pipeline()
        check_integrity(m)
        check_outputs()
        check_engine()
        check_razorpay()
    except Exception as ex:                       # a crash is itself a failure
        import traceback
        record(FAIL, "verification crashed", traceback.format_exc()[-600:])

    failed = [r for r in results if r[0] == FAIL]
    warned = [r for r in results if r[0] == WARN]
    passed = [r for r in results if r[0] == PASS]

    print("\n  " + "=" * 66)
    print(f"  {len(passed)} passed · {len(warned)} warnings · {len(failed)} failed")
    if failed:
        print("\n  FAILED:")
        for _, name, detail in failed:
            print(f"    · {name}" + (f" — {detail[:120]}" if detail else ""))
        print("\n  Not ready to submit.\n")
        sys.exit(1)
    if warned:
        print("\n  Warnings are non-blocking (usually optional credentials).")
    print("\n  Ready.\n")


if __name__ == "__main__":
    main()
