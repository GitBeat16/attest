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

    # The benchmark corpus is clean ISO with exact headers. If it stops reading
    # READY, ingest has started rejecting or silently dropping rows it should
    # not — fail the build rather than publish numbers over a partial read.
    rd = corpus.readiness
    if rd.ready and rd.rows_rejected() == 0:
        record(PASS, "benchmark corpus reads READY",
               f"{rd.rows_read():,}/{rd.rows_in():,} rows, 0 rejected")
    else:
        record(FAIL, "benchmark corpus reads READY",
               f"verdict {rd.verdict}, {rd.rows_rejected()} rejected: "
               + "; ".join(rd.reasons[:2]))

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
def check_tiers() -> None:
    """Confidence tiers (ROADMAP §1.3, invariant 11). Classification only:
    the recoverable total must not move, verdicts stay out of recovery, and an
    unmapped class must never come out claim-ready."""
    section("CONFIDENCE TIERS")
    from datetime import date, timedelta

    from attest import tiers
    from attest.audit import run as audit_run
    from attest.engine import run as engine_run
    from attest.ingest import load, resolve
    from attest.money import fmt
    from attest.recovery import build_claims, summarise
    from attest.run import build_exceptions

    corpus = load(ROOT / "data" / "sources")
    resolve(corpus)
    res = engine_run(corpus)
    aud = audit_run(corpus, res.batch_ties)
    ex = build_exceptions(corpus, res, aud)

    # 1. every row is tiered, in one of the three known values
    bad = [e for e in ex if e.get("tier") not in tiers.TIERS]
    record(PASS if not bad else FAIL, "every exception carries a known tier",
           "all rows" if not bad else f"{len(bad)} untiered/unknown")

    # 2. an unmapped class tiers to UNPROVEN, never PROVEN
    made_up = tiers.for_class("SOME_CLASS_THAT_DOES_NOT_EXIST")
    record(PASS if made_up == tiers.UNPROVEN else FAIL,
           "an unmapped class is UNPROVEN", f"got {made_up}")

    # 3. weakest() cannot be talked up
    w = tiers.weakest([tiers.PROVEN, tiers.NEEDS_INPUT, tiers.PROVEN])
    record(PASS if w == tiers.NEEDS_INPUT else FAIL,
           "a mixed group takes the weakest tier", f"got {w}")

    y, mth = (int(x) for x in corpus.mdr_invoice["period"].split("-"))
    period_end = date(y + (mth // 12), (mth % 12) + 1, 1) - timedelta(days=1)
    today = period_end + timedelta(days=3)
    claims = build_claims(ex, period_end, today)
    rec = summarise(claims, today)

    # 4. the tier split sums exactly to recoverable — nothing lost or doubled
    tier_sum = sum(v["exposure"] for v in rec["by_tier"].values())
    record(PASS if tier_sum == rec["recoverable"] else FAIL,
           "tier split reconciles to recoverable",
           f"{fmt(tier_sum)} vs {fmt(rec['recoverable'])}")

    # 5. the constraint: recoverable did not increase. Pinned to the benchmark
    #    figure so a regression that inflates it fails here.
    PINNED = 8676590
    record(PASS if rec["recoverable"] == PINNED else FAIL,
           "recoverable unchanged by tiering",
           f"{fmt(rec['recoverable'])} (expected {fmt(PINNED)})")
    record(PASS if rec["claim_ready"] <= rec["recoverable"] else FAIL,
           "claim-ready is a subset of recoverable",
           f"{fmt(rec['claim_ready'])} of {fmt(rec['recoverable'])}")

    # 6. no verdict reaches recovery, at any tier (invariant 9 still holds).
    #    build_claims drops every row whose kind is "verdict", so the total
    #    claim exposure can never exceed the non-verdict exposure.
    verdict_rows = [e for e in ex if e.get("kind") == "verdict"]
    non_verdict_exposure = sum(e["exposure"] for e in ex
                               if e.get("kind") != "verdict")
    record(PASS if sum(c.exposure for c in claims) <= non_verdict_exposure else FAIL,
           "verdicts excluded from recovery",
           f"{len(verdict_rows)} verdict rows, none summed into claims")


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
        ("claim-ready", "hero states what is claim-ready"),
        ("compensating error", "the ₹0.02 worked example is present"),
        ("held out", "the held-out scorecard is present"),
        ("Lost by closing monthly", "the cadence argument is present"),
        ("Proven wrong", "the confidence tiers are present"),
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
    # Exactly once, in both files -- not merely present, and not only on the
    # landing page. The `in` test this replaces passed happily while both files
    # printed the 32-character prefix TWICE on consecutive lines, which reads as
    # a full 64-character digest and is not one. Comparing the site against
    # `attest.seal --verify` by eye is the one manual check this product invites
    # a reader to perform, and they would have found two lines where the tool
    # prints one. The README was never checked at all.
    want = grouped(r["digest"])
    for rel, text in (("web/index.html", lb),
                      ("README.md",
                       (ROOT / "README.md").read_text(encoding="utf-8"))):
        n = text.count(want)
        if n == 1:
            record(PASS, f"{rel} shows the real digest, once")
        elif n == 0:
            record(FAIL, f"{rel} shows the real digest, once",
                   f"paste this into {rel}: {want}")
        else:
            record(FAIL, f"{rel} shows the real digest, once",
                   f"printed {n} times — it must appear exactly once, or it "
                   "reads as a longer digest than it is")

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
def check_demo() -> None:
    """The demo is the first thing a judge touches, so it is checked hardest.
    A demo that silently stops demonstrating its own point is worse than none."""
    section("THE DEMO")
    from attest.demo import check as demo_check

    try:
        r = demo_check()
    except AssertionError as e:
        record(FAIL, "compensating pair still found", str(e))
        return
    record(PASS, "compensating pair still found",
           f"fee out {r['fee_overcharge_paise']}p vs refund out "
           f"{r['refund_under_paise']}p")

    # The whole argument is the ratio. If it collapses, the demo has stopped
    # being a demonstration.
    ratio = r["exposure_paise"] // max(r["batch_variance_paise"], 1)
    if ratio > 1000:
        record(PASS, "the gap is still dramatic",
               f"batch looks {r['batch_variance_paise']}p out, is "
               f"{r['exposure_paise']}p wrong — {ratio:,}x")
    else:
        record(FAIL, "the gap is still dramatic",
               f"only {ratio}x — the demo no longer makes its point")

    record(PASS if r["tied"] >= 1 else FAIL, "a batch still ties",
           f"{r['tied']}/{r['batches']} tie, so the naive view passes")

    # Determinism: two runs, identical findings.
    r2 = demo_check()
    record(PASS if r2 == r else FAIL, "the demo is deterministic",
           "two runs produced identical findings")


def check_api() -> None:
    """The endpoints a judge's browser will actually call."""
    section("ENDPOINTS")
    import importlib.util

    spec = importlib.util.spec_from_file_location("capi", ROOT / "api" / "close.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)

    out = api.handle({"mode": "demo"})
    record(PASS if out.get("ok") else FAIL, "demo close needs no account",
           "runs signed out and writes nothing")
    record(PASS if out.get("saved") is False else FAIL,
           "demo writes nothing to the database")
    record(PASS if out.get("naive") and out.get("chain") else FAIL,
           "demo returns the comparison and the evidence chain",
           f"{len(out.get('chain') or [])} chain stages")

    # A live key must be refused before it can reach the network.
    try:
        api.from_razorpay("rzp_live_ABCDEF", "secret", 2026, 8)
        record(FAIL, "live keys are refused", "a live key was accepted")
    except api.CloseError as e:
        record(PASS if "test keys only" in str(e).lower() else FAIL,
               "live keys are refused")

    # Malformed and empty input must fail as messages, never as tracebacks.
    for bad, label in (({"token": "t", "merchant": "", "period": "2026-08"},
                        "empty merchant"),
                       ({"token": "t", "merchant": "x", "period": "nonsense"},
                        "malformed period"),
                       ({"token": "t", "merchant": "x", "period": "2026-08",
                         "files": {"razorpay_settlements.csv": ""}},
                        "empty settlement file"),
                       ({"token": "t", "merchant": "x", "period": "2026-08",
                         "files": {"razorpay_settlements.csv": "a,b,c\n1,2,3"}},
                        "a settlement file with the wrong columns"),
                       ({"token": "t", "merchant": "x", "period": "2026-08",
                         "files": {"razorpay_settlements.csv":
                                   "settlement_id,settled_on,payment_id,order_id,"
                                   "gross_amount,mdr,gst_on_mdr,net_amount,row_type",
                                   "razorpay_mdr_invoice.json": "{broken"}},
                        "malformed JSON")):
        try:
            api.handle(bad)
            record(FAIL, f"rejects {label}", "it was accepted")
        except api.CloseError:
            record(PASS, f"rejects {label}")
        except Exception as e:                       # noqa: BLE001
            record(FAIL, f"rejects {label}",
                   f"crashed instead of explaining: {type(e).__name__}")

    spec = importlib.util.spec_from_file_location("eapi", ROOT / "api" / "explain.py")
    ex = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ex)
    r = ex.explain({"class": "MDR", "count": 3, "exposure_display": "₹1.00"})
    record(PASS if r.get("ok") and r.get("text") else FAIL,
           "explanations work with no model configured",
           f"source: {r.get('source')}")
    record(PASS if r.get("verified") is True else WARN,
           "the deterministic path is labelled as verified")


def check_money_tests() -> None:
    section("MONEY")
    import subprocess as sp
    r = sp.run([sys.executable, "tests/test_money_edges.py"], cwd=ROOT,
               capture_output=True, text=True)
    line = (r.stdout.strip().splitlines() or ["no output"])[-1]
    record(PASS if r.returncode == 0 else FAIL, "money edge cases", line.strip())


def check_readiness() -> None:
    """The ingest gate: never close on data that could not be fully read.
    ROADMAP §1.2. A real export differs from the generator's in every boring
    way, and each of those must produce a stated verdict, never a silent
    partial close."""
    section("READINESS")
    import subprocess as sp
    r = sp.run([sys.executable, "tests/test_ingest_readiness.py"], cwd=ROOT,
               capture_output=True, text=True)
    line = (r.stdout.strip().splitlines() or ["no output"])[-1]
    record(PASS if r.returncode == 0 else FAIL, "ingest readiness",
           line.strip() or r.stderr[-300:])

    # The refuse/partial verdict must actually reach the close: a PARTIAL ingest
    # can never be SIGNED, whatever the residual.
    from attest.ingest import load
    import tempfile as _tf, json as _json
    d = Path(_tf.mkdtemp()) / "sources"
    d.mkdir(parents=True)
    (d / "razorpay_settlements.csv").write_text(
        (ROOT / "tests/fixtures/ingest/half_month_settlements.csv").read_text())
    (d / "razorpay_mdr_invoice.json").write_text(_json.dumps(
        {"period": "2026-08", "total_tax": ""}))
    (d / "contract_terms.json").write_text(_json.dumps(
        {"merchant": "T", "period": "2026-08", "contracted_mdr_rate_pct": "2.00",
         "gst_on_mdr_rate_pct": "18.00", "courier_cod_fee_pct": "1.50",
         "courier_rto_freight_inr": "85.00"}))
    verdict = load(d).readiness.verdict
    record(PASS if verdict == "PARTIAL" else FAIL,
           "a half-month reads PARTIAL, not READY", f"verdict {verdict}")


# ==========================================================================
def check_controller() -> None:
    """The layer above the engine. Checked for safety first, ability second."""
    section("THE CONTROLLER")
    import subprocess as sp

    r = sp.run([sys.executable, "tests/test_controller.py"], cwd=ROOT,
               capture_output=True, text=True)
    line = (r.stdout.strip().splitlines() or ["no output"])[-1]
    record(PASS if r.returncode == 0 else FAIL,
           "adversarial tests against the controller", line.strip())

    from attest.agentbench import run_all
    bench = run_all()
    m = bench["metrics"]

    # The one that matters. Everything else is diagnostic.
    if m["false_certification_rate"] == 0:
        record(PASS, "false certification rate is zero",
               f"{len(bench['scenarios'])} scenarios, none wrongly certified")
    else:
        record(FAIL, "false certification rate is zero",
               f"{m['false_certification_rate']*100:.0f}% — a close was signed "
               "that should have been refused")

    record(PASS if m["correct_escalation_rate"] == 1.0 else FAIL,
           "escalates exactly when it should",
           f"{m['correct_escalation_rate']*100:.0f}%")
    record(PASS if m["ai_assisted_safe_resolution_rate"] > 0.5 else WARN,
           "AI-assisted safe resolution rate",
           f"{m['ai_assisted_safe_resolution_rate']*100:.1f}% over "
           f"{len(bench['scenarios'])} scenarios — a small n, reported as such")

    # A clean month must certify. A controller that refuses everything is not
    # safe, it is useless, and the distinction has to be tested.
    clean = next(s for s in bench["scenarios"] if s["key"] == "F")
    record(PASS if clean["verdict"] == "CERTIFIABLE" else FAIL,
           "a clean month is still certifiable",
           "refusing everything is not safety")

    # The demo month must not be.
    comp = next(s for s in bench["scenarios"] if s["key"] == "D")
    record(PASS if comp["verdict"] == "NOT_ATTESTABLE" else FAIL,
           "the compensating month is refused")

    from attest.controller import Budget, RulesPlanner, run as crun
    import tempfile as _tf
    from attest.demo import build as _build
    with _tf.TemporaryDirectory() as td:
        src = Path(td) / "sources"
        src.mkdir()
        for n, b in _build().items():
            (src / n).write_text(b, encoding="utf-8")
        out = crun(src, planner_name="rules", budget=Budget(max_steps=3))
    record(PASS if "budget" in out["stopped_because"] else FAIL,
           "budgets actually stop it", out["stopped_because"])
    record(PASS if out["policy"]["verdict"] == "HUMAN_REVIEW_REQUIRED" else FAIL,
           "an exhausted budget escalates rather than guessing")


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
def check_versioning() -> None:
    """Can an old pack say what produced it?"""
    section("VERSIONING")
    from attest import __version__, ruleset
    from attest.seal import extract

    record(PASS, "engine version declared", __version__)
    record(PASS, "ruleset digest computed", ruleset.short())

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## {__version__}" in changelog and ruleset.short() in changelog:
        record(PASS, "CHANGELOG describes this version",
               f"{__version__} @ {ruleset.short()}")
    else:
        record(FAIL, "CHANGELOG describes this version",
               f"no entry pairing {__version__} with {ruleset.short()}")

    _, canon = extract((ROOT / "web" / "close-pack.html").read_text(encoding="utf-8"))
    rs = (canon or {}).get("ruleset") or {}
    if rs.get("engine_version") == __version__ and rs.get("ruleset_digest") == ruleset.short():
        record(PASS, "sealed pack states what produced it",
               f"v{canon.get('v')} carries {rs['engine_version']} @ {rs['ruleset_digest']}")
    else:
        record(FAIL, "sealed pack states what produced it", f"got {rs}")


def check_pooled_recall() -> None:
    """The README's pooled figures, checked rather than asserted.

    A per-world recall percentage cannot be averaged into anything meaningful:
    each held-out class plants one instance per world, so per-world held-out
    recall only ever reads 0, 25, 50, 75 or 100. The pooled figure sums the
    counts first and divides once, which is the number the README quotes -- so
    it is the number that has to be true.
    """
    section("POOLED RECALL")
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        from stability import HEADLINE_SEED, one_world
    except Exception as e:                      # pragma: no cover
        record(WARN, "pooled recall", f"harness unavailable: {type(e).__name__}")
        return

    pooled: dict[str, dict] = {}
    n = 20
    for i in range(n):
        for cls, c in one_world(HEADLINE_SEED + i, 1200)["by_class"].items():
            d = pooled.setdefault(cls, {"planted": 0, "detected": 0,
                                        "held_out": c["held_out"]})
            d["planted"] += c["planted"]
            d["detected"] += c["detected"]

    def pool(held: bool) -> tuple[int, int, float]:
        pl = sum(d["planted"] for d in pooled.values() if d["held_out"] is held)
        dt = sum(d["detected"] for d in pooled.values() if d["held_out"] is held)
        return pl, dt, (dt / pl * 100 if pl else 0.0)

    ho_pl, ho_dt, ho = pool(True)
    df_pl, df_dt, df = pool(False)

    if ho_pl == 0:
        record(FAIL, "held-out defects are planted",
               "none planted -- the held-out score is vacuous")
    else:
        record(PASS, "held-out defects are planted", f"{ho_pl} across {n} worlds")

    # Invariant 4. Not a target to beat -- a condition that must keep failing.
    if ho < 100.0:
        record(PASS, "held-out recall stays under 100%",
               f"{ho:.1f}% ({ho_dt}/{ho_pl}) pooled")
    else:
        record(FAIL, "held-out recall stays under 100%",
               f"{ho:.1f}% -- a targeted detector was written for a held-out class")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for label, got, planted, found in (
            ("held-out", ho, ho_pl, ho_dt), ("designed-for", df, df_pl, df_dt)):
        quoted = f"{got:.1f}%"
        if quoted in readme and f"{found} of {planted}" in readme:
            record(PASS, f"README quotes pooled {label} recall", f"{quoted} ({found} of {planted})")
        else:
            record(FAIL, f"README quotes pooled {label} recall",
                   f"code says {quoted} ({found} of {planted}) -- README disagrees")


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
        check_tiers()
        check_outputs()
        check_money_tests()
        check_readiness()
        check_demo()
        check_api()
        check_controller()
        check_engine()
        check_versioning()
        check_pooled_recall()
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
