"""POST /api/close — run a close for one signed-in merchant.

Two things about this function are deliberate and are the reason it is safe to
run as a hosted service at all.

**It holds no credential of its own.** There is no service key here, no admin
role, no database password. Every write goes out to PostgREST bearing the
caller's own access token, so the function can never touch a row its caller
could not touch. Row-level security in Postgres is the authorisation boundary;
this code is only compute. If this file leaked in full it would grant an
attacker nothing.

**It never stores a Razorpay secret.** A key arrives in the request, is used for
the lifetime of that request, and goes out of scope. Nothing writes it to disk,
to the database, or to a log line. Live keys are refused outright: a hosted page
has no business holding a credential that can move a merchant's money. The local
CLI still accepts them, because that runs on the merchant's own laptop and the
key never leaves it.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from attest.close import (CloseError, close, evidence_chain,  # noqa: E402
                          naive_view, stage)
from attest.report import render                            # noqa: E402

# Both of these are public by design. The project URL and the publishable key
# are already in the page source of every Supabase app ever shipped; they
# identify the project and grant nothing on their own, because RLS decides what
# a request may see. They are defaulted here rather than left to environment
# configuration so there is one fewer thing to get wrong on deploy. The secret
# that would matter -- the service role key -- is not in this repository, is not
# in this function's environment, and is never used by this app.
SUPABASE_URL = os.environ.get(
    "SUPABASE_URL", "https://fsqozbfghumfconnhowq.supabase.co").rstrip("/")
SUPABASE_ANON = os.environ.get(
    "SUPABASE_ANON_KEY", "sb_publishable_tO-3LIT3VVJNzFUtWsZNvw_4ePwcohx")

MAX_BODY = 6 * 1024 * 1024
COUNTERPARTY = {
    "MDR": "Razorpay", "GST": "Razorpay", "CREDIT": "Razorpay",
    "REFUND_DUPLICATE": "Razorpay", "ITC_MISMATCH": "Razorpay",
    "DUPLICATE_SETTLEMENT_LINE": "Razorpay", "UNREFERENCED_ADJ": "Razorpay",
    "ADJUSTMENT": "Courier", "RTO_FREIGHT": "Courier", "FREIGHT": "Courier",
    "COD_FEE": "Courier", "COD_VALUE": "Courier", "DUPLICATE_AWB": "Courier",
    "CHARGEBACK_ORPHAN": "Card network", "ORPHAN_BANK_CREDIT": "Bank",
}
LABELS = {
    "MDR": "Gateway fee overcharged",
    "GST": "Tax computed on the wrong base",
    "CREDIT": "Settled but never reached the bank",
    "ADJUSTMENT": "COD short-remitted by the courier",
    "REFUND_DUPLICATE": "Refund deducted twice",
    "CHARGEBACK_ORPHAN": "Chargeback with no dispute record",
    "UNREFERENCED_ADJ": "Adjustment with no order behind it",
    "ITC_MISMATCH": "Input credit at risk",
    "DUPLICATE_SETTLEMENT_LINE": "Same payment settled twice",
    "DUPLICATE_AWB": "Same shipment remitted twice",
    "ORPHAN_BANK_CREDIT": "Bank credit no batch explains",
    "RTO_FREIGHT": "Return freight charged",
    "FREIGHT": "Return freight charged on a delivered order",
    "COD_FEE": "COD fee charged off-contract",
    "COD_VALUE": "COD value does not match the manifest",
    "TIMING_NOT_ERROR": "Timing, not an error",
    # Verdicts from the adversarial pass. These describe money already listed
    # above under the finding that produced it, so they are never claimable.
    "OFFSETTING_PAIR": "Two errors cancelling inside one batch",
    "SELF_REFERENTIAL_TIE": "Batch ties, but the fees inside disagree",
    "REFUND_MISMATCH": "Refund deduction does not match the refund export",
    "TOLERANCE_ABUSE": "Rounding that always favours one side",
    "COINCIDENTAL_EQUALITY": "Two records equal by coincidence, not by identity",
    "AMBIGUOUS_COLLAPSE": "More than one batch could explain this credit",
    "KEY_SUBSTITUTION": "Matched on a key that is not a key",
}


# --------------------------------------------------------------------------
def postgrest(table: str, token: str, rows: list[dict]) -> list[dict]:
    """Insert under the caller's identity. RLS decides whether it is allowed."""
    if not SUPABASE_URL:
        raise CloseError("the server is not configured with SUPABASE_URL.")
    req = urllib.request.Request(
        f"{SUPABASE_URL}/rest/v1/{table}",
        data=json.dumps(rows).encode(),
        method="POST",
        headers={
            "apikey": SUPABASE_ANON,
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or "[]")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        if e.code in (401, 403):
            raise CloseError("your session has expired — sign in again.") from None
        raise CloseError(f"could not save the close ({e.code}): {detail}") from None
    except urllib.error.URLError as e:
        raise CloseError(f"could not reach the database: {e.reason}") from None


def from_razorpay(key_id: str, key_secret: str, year: int, month: int) -> str:
    """Pull one month of settlements and render the settlement CSV the engine
    reads. The secret is a local; it is never returned, stored or logged."""
    from attest.sources.razorpay_api import RazorpayClient, RazorpayError

    if not key_id.startswith("rzp_test_"):
        raise CloseError(
            "This hosted app accepts test keys only. A live Razorpay secret can "
            "create refunds and move real money, and no web page should be "
            "holding one — including this one. Run the CLI on your own machine "
            "for live data, or upload your settlement report instead.")
    try:
        rows = RazorpayClient(key_id, key_secret).fetch_recon(year, month)
    except RazorpayError as e:
        raise CloseError(str(e)[:300]) from None

    def rupees(paise: int) -> str:
        sign = "-" if paise < 0 else ""
        paise = abs(int(paise))
        return f"{sign}{paise // 100}.{paise % 100:02d}"

    kind = {"payment": "captured", "refund": "refund_adjustment",
            "adjustment": "hold_release", "transfer": "hold_release"}
    out = ["settlement_id,settled_on,payment_id,order_id,gross_amount,mdr,"
           "gst_on_mdr,net_amount,row_type"]
    for r in rows:
        if not r.settlement_id or not r.settled_on:
            continue
        out.append(",".join([
            r.settlement_id, r.settled_on.isoformat(),
            r.payment_id or r.entity_id, r.order_id or "",
            rupees(r.amount), rupees(r.fee), rupees(r.tax), rupees(r.net),
            kind.get(r.type, "captured"),
        ]))
    if len(out) == 1:
        raise CloseError(
            f"Razorpay returned no settled rows for {year}-{month:02d}. Test "
            "accounts only have settlements if test payments were captured and "
            "settled in that month.")
    return "\n".join(out)


def _investigate(src) -> dict | None:
    """Run the controller over the same close, for the investigation timeline.

    Failure here must never take the close down with it: the reconciliation
    result is the product, and the controller sits above it. If the loop cannot
    run, the close is still returned and the panel is simply absent.
    """
    try:
        from attest.controller import Budget, run as controller_run
        return controller_run(src, planner_name="auto",
                              budget=Budget(max_steps=12, max_tool_calls=20,
                                            timeout_seconds=12))
    except Exception:                                        # noqa: BLE001
        return None


def _top(exceptions: list[dict], claims=None, today=None) -> list[dict]:
    """The exception register, ranked by rupee exposure, in the merchant's
    language and with the counterparty and claim window attached."""
    from attest.money import fmt
    # Attach the clock. A claim window that has already closed must say so
    # rather than sit in the list looking recoverable -- the whole cadence
    # argument is that findings surfaced late are findings surfaced too late.
    clock = {}
    if claims and today:
        for c in claims:
            prev = clock.get(c.exception_class)
            if prev is None or c.deadline < prev[0]:
                clock[c.exception_class] = (c.deadline, c.days_left(today),
                                            c.urgency(today), c.counterparty)

    claimable = [e for e in exceptions if e.get("kind") != "verdict"][:8]
    verdicts = [e for e in exceptions if e.get("kind") == "verdict"][:4]
    out = []
    for e in claimable + verdicts:
        out.append({
            "class": e["class"],
            "kind": e.get("kind", "chain"),
            "label": LABELS.get(e["class"], e["class"].replace("_", " ").title()),
            "count": e["count"],
            "exposure": fmt(e["exposure"]),
            "exposure_paise": e["exposure"],
            "evidence": e.get("evidence_required", ""),
            "reasoning": e.get("reasoning", ""),
            "counterparty": COUNTERPARTY.get(e["class"], "Razorpay"),
            **({"deadline": clock[e["class"]][0].isoformat(),
                "days_left": clock[e["class"]][1],
                "urgency": clock[e["class"]][2]}
               if e["class"] in clock else {}),
        })
    return out


# --------------------------------------------------------------------------
def handle(body: dict) -> dict:
    token = (body.get("token") or "").strip()
    mode = body.get("mode") or "upload"

    # The demo needs no account and writes nothing. A judge should be able to
    # see the whole argument in one click; requiring a signup first would put a
    # form between them and the only screen that matters.
    if mode == "demo":
        from attest.demo import MERCHANT, PERIOD, build
        files, merchant, period = build(), MERCHANT, PERIOD
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "sources"
            supplied, missing = stage(files, merchant, period, None, src)
            result = close(src, supplied, missing)
            corpus = result["corpus"]
            nv = naive_view(corpus, result["res"], result["aud"])
            chain = evidence_chain(corpus, nv["worst_line"]) if nv else []
            investigation = _investigate(src)
        s = result["summary"]
        s["exceptions_top"] = _top(s["exceptions"], result["claims"], result["today"])
        s.pop("exceptions", None)
        s["demo"] = True
        s["close_id"] = None
        return {"ok": True, "summary": s, "naive": nv, "chain": chain,
                "investigation": investigation,
                "pack_html": render(result["payload"]), "saved": False}

    if not token:
        raise CloseError("not signed in.")

    merchant = (body.get("merchant") or "").strip()
    period = (body.get("period") or "").strip()
    if not merchant:
        raise CloseError("a merchant name is required — it goes on the close pack.")
    if len(period) != 7 or period[4] != "-":
        raise CloseError("period must look like 2026-08.")
    year, month = int(period[:4]), int(period[5:])

    files = {k: v for k, v in (body.get("files") or {}).items() if isinstance(v, str)}
    key_masked = None

    if mode == "razorpay_test":
        key_id = (body.get("key_id") or "").strip()
        key_secret = (body.get("key_secret") or "").strip()
        if not key_id or not key_secret:
            raise CloseError("both the key id and the key secret are required.")
        files["razorpay_settlements.csv"] = from_razorpay(
            key_id, key_secret, year, month)
        key_masked = key_id[:12] + "…" + key_id[-4:]
        del key_secret                        # gone before anything else runs

    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "sources"
        supplied, missing = stage(files, merchant, period, body.get("terms"), src)
        result = close(src, supplied, missing)
        corpus = result["corpus"]
        nv = naive_view(corpus, result["res"], result["aud"])
        chain = evidence_chain(corpus, nv["worst_line"]) if nv else []
        investigation = _investigate(src)

    s = result["summary"]
    pack = render(result["payload"])

    # The seal is read back off the finished document rather than recomputed,
    # so what gets recorded is provably the digest of the file the merchant
    # actually holds.
    from attest.seal import verify as verify_seal
    sealed = verify_seal(pack)
    s["seal_digest"] = sealed.get("digest")
    s["seal_ok"] = bool(sealed.get("digest_ok"))

    row = postgrest("attest_closes", token, [{
        "merchant": s["merchant"], "period": s["period"], "source": mode,
        "key_id_masked": key_masked,
        "records": s["records"],
        "batches_total": s["batches_total"], "batches_tied": s["batches_tied"],
        "lines_total": s["lines_total"], "lines_proven": s["lines_proven"],
        "claims_tested": s["claims_tested"],
        "claims_overturned": s["claims_overturned"],
        "volume_paise": s["volume_paise"], "residual_paise": s["residual_paise"],
        "recoverable_paise": s["recoverable_paise"],
        "residual_bps": s["residual_bps"], "attestable": s["attestable"],
        "evidence_supplied": supplied, "evidence_missing": missing,
        "pack_html": pack,
    }])
    close_id = row[0]["id"] if row else None

    if s.get("seal_digest") and s["seal_ok"]:
        # Append-only by policy: this row can never be updated or deleted, which
        # is what turns a self-contained checksum into something an auditor can
        # rely on.
        try:
            postgrest("attest_seals", token, [{
                "digest": s["seal_digest"], "close_id": close_id,
                "merchant": s["merchant"], "period": s["period"],
                "records": s["records"], "attestable": s["attestable"],
            }])
        except CloseError:
            s["seal_recorded"] = False      # the close still stands
        else:
            s["seal_recorded"] = True

    findings = [{
        "close_id": close_id,
        "class": e["class"],
        "label": LABELS.get(e["class"], e["class"].replace("_", " ").title()),
        "line_count": e["count"],
        "exposure_paise": e["exposure"],
    } for e in s["exceptions"][:20]]
    if close_id and findings:
        postgrest("attest_findings", token, findings)

    s["exceptions_top"] = _top(s["exceptions"], result["claims"], result["today"])
    s.pop("exceptions", None)
    s["close_id"] = close_id
    return {"ok": True, "summary": s, "naive": nv, "chain": chain,
            "investigation": investigation,
            "pack_html": pack, "saved": bool(close_id)}


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:                      # noqa: N802
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return self._send(413, {"ok": False, "error":
                                        "that file is too large to upload here."})
            body = json.loads(self.rfile.read(length) or "{}")
        except Exception:
            return self._send(400, {"ok": False, "error": "malformed request."})

        try:
            self._send(200, handle(body))
        except CloseError as e:
            self._send(400, {"ok": False, "error": str(e)})
        except Exception as e:                      # never leak internals
            self._send(500, {"ok": False, "error":
                             f"the close failed: {type(e).__name__}"})

    def do_GET(self) -> None:                       # noqa: N802
        self._send(200, {"ok": True, "service": "attest", "method": "POST"})
