"""POST /api/explain — put a finding into words.

The division of labour here is the point, and it is visible in the response.

The **engine** decided that something is wrong, by how much, and on which
records. That is arithmetic over documents, it is deterministic, and it is the
only thing anyone should act on. Those numbers arrive in the request; this
function never recomputes them and never invents one.

The **model**, when configured, is given those already-decided facts and asked
to write a paragraph a finance person can read. It receives no source
documents, so it has nothing to hallucinate a figure from: every number in its
prompt is a number the engine already published.

Every response says which produced it — `engine` or the model's name — so the
interface can label them differently. A reader must always be able to tell the
arithmetic from the prose. If no model is configured, or the call fails, the
deterministic explanation is returned and the product is unaffected: the
reasoning layer is a convenience, never load-bearing.
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MAX_BODY = 64 * 1024

# The only fields that reach a prompt. Anything not on this list -- a key, a
# filename, a merchant's raw statement line -- cannot leave the server, because
# it is never copied into the request the model sees.
ALLOWED = ("class", "label", "count", "exposure_display", "period",
           "counterparty", "effective_rate", "contract_rate", "evidence")


def explain(finding: dict) -> dict:
    from attest import config, engines

    config.load_env()
    safe = {k: finding.get(k) for k in ALLOWED if finding.get(k) not in (None, "")}
    if not safe.get("class"):
        return {"ok": False, "error": "no finding supplied."}

    try:
        engine = engines.get_engine()
        result = engine.explain_variance(safe)
        text, source = result.text, result.source
    except Exception:                                   # noqa: BLE001
        # A reasoning layer that can fail the close is a reasoning layer in the
        # wrong place. Fall back and carry on.
        from attest.engines import RulesEngine
        result = RulesEngine().explain_variance(safe)
        text, source = result.text, "rules"

    return {
        "ok": True,
        "text": text,
        # 'rules' is the deterministic template that ships with the engine, so
        # it is attributed to the engine rather than to a model.
        "source": "engine" if source == "rules" else source,
        "verified": source == "rules",
        "facts": safe,
    }


class handler(BaseHTTPRequestHandler):
    def _send(self, code: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:                          # noqa: N802
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                return self._send(413, {"ok": False, "error": "request too large."})
            body = json.loads(self.rfile.read(n) or "{}")
        except Exception:                               # noqa: BLE001
            return self._send(400, {"ok": False, "error": "malformed request."})

        try:
            self._send(200, explain(body.get("finding") or {}))
        except Exception as e:                          # noqa: BLE001
            self._send(500, {"ok": False,
                             "error": f"could not explain: {type(e).__name__}"})

    def do_GET(self) -> None:                           # noqa: N802
        self._send(200, {"ok": True, "service": "attest-explain", "method": "POST"})
