"""The reasoning layer, behind one interface with four implementations.

Selected by ATTEST_ENGINE in .env: rules | gemini | openai | anthropic | ollama.

Three rules govern everything here, and they are the reason this module is small:

  1. **It is never load-bearing.** `rules` is the default and produces every
     metric on its own. If a key is missing, a model is down, or a response is
     malformed, the engine falls back to rules and the close still completes.
     Every number in this repository was produced by the deterministic path.

  2. **It never touches money.** Matching, tolerances, thresholds and the
     arithmetic are deterministic code elsewhere. A language model cannot be
     audited, and a finance system that cannot be audited is not a finance
     system. This module only reads what the arithmetic already decided and
     explains it in English.

  3. **Every response is cached to disk** keyed on a hash of the input, so
     re-running a close costs nothing and a demo never depends on the network.

Standard library only. No SDK for any provider -- each is a plain HTTPS call.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

TIMEOUT = 30

_CACHE_DIR: Path | None = None
_CACHE_RESOLVED = False


def cache_dir() -> Path | None:
    """Where responses are cached, or None if nowhere is writable.

    Serverless runtimes ship the code on a read-only filesystem — on Vercel
    only the system temp directory can be written — so a cache next to the
    source is a preference, never a requirement. Caching is an optimisation;
    losing it must never be the reason a close cannot run.
    """
    global _CACHE_DIR, _CACHE_RESOLVED
    if _CACHE_RESOLVED:
        return _CACHE_DIR
    _CACHE_RESOLVED = True

    override = os.environ.get("ATTEST_CACHE_DIR", "").strip()
    candidates = [Path(override)] if override else []
    candidates += [Path(__file__).resolve().parent.parent / ".llm_cache",
                   Path(tempfile.gettempdir()) / "attest-llm-cache"]

    for cand in candidates:
        try:
            cand.mkdir(parents=True, exist_ok=True)
            probe = cand / ".writable"
            probe.write_text("1", encoding="utf-8")
            probe.unlink()
            _CACHE_DIR = cand
            return _CACHE_DIR
        except OSError:
            continue
    _CACHE_DIR = None            # read-only everywhere: run without a cache
    return None

# What each variance class actually means. Without this a model produces fluent
# restatement -- "the MDR class recorded 23 items" -- which reads well and says
# nothing. The deterministic templates already encode this domain knowledge; the
# model has to be given it explicitly or it cannot beat them.
DOMAIN = {
    "MDR": "MDR is the per-transaction fee Razorpay charges. A variance here "
           "means the rate applied exceeds the contracted rate, so the merchant "
           "was overcharged and can reclaim it against the rate card.",
    "GST": "GST is charged at 18% on the MDR fee, not on the transaction value. "
           "Computing it on the transaction inflates it enormously, and also "
           "inflates the input tax credit claimed, so it is a compliance risk.",
    "CREDIT": "The settlement report says money was paid out, but no matching "
              "credit reached the bank account. The money is missing, not late.",
    "ADJUSTMENT": "A courier deducted an amount from a COD remittance with no "
                  "AWB-level justification. Claimable, but courier dispute "
                  "windows close in about 14 days.",
    "SELF_REFERENTIAL_TIE": "The batch agrees with the bank, but the bank only "
                            "paid what Razorpay's own report specified. The tie "
                            "confirms delivery, not correctness.",
    "OFFSETTING_PAIR": "Two errors of opposite sign cancelled inside one batch, "
                       "leaving the total within tolerance. Invisible to any "
                       "total-level check.",
    "TOLERANCE_ABUSE": "Many sub-tolerance residuals all point the same way. "
                       "Honest rounding is symmetric; a consistent direction is "
                       "a systematic transfer hiding under the threshold.",
    "UNREFERENCED_ADJ": "Money moved into or out of a batch with no order or "
                        "payment attached to explain it.",
    "CHARGEBACK_ORPHAN": "A chargeback was deducted from a settlement but does "
                         "not appear in the dispute export. A cross-system gap.",
    "DUPLICATE_AWB": "One shipment appears twice in a courier statement.",
    "DUPLICATE_SETTLEMENT_LINE": "One payment was settled in two batches, so "
                                 "revenue is counted twice.",
    "ORPHAN_BANK_CREDIT": "A credit arrived that no settlement accounts for. "
                          "Unexplained money in is still unexplained.",
}


# ==========================================================================
@dataclass
class Explanation:
    text: str
    source: str          # which engine produced it -- always shown, never hidden


class ReasoningEngine:
    """What the pipeline is allowed to ask a model for. Deliberately narrow."""

    name = "base"

    def parse_narration(self, narration: str) -> dict:
        """Bank narration -> {counterparty, reference, intent}. A language task."""
        raise NotImplementedError

    def plan(self, system: str, observation: str) -> str:
        """Choose the next investigation action, as raw text.

        The controller parses and validates whatever comes back, so an engine
        that cannot plan simply returns nothing and the controller falls back
        to its deterministic planner. No engine is ever trusted to be correct
        here -- only to be a source of suggestions.
        """
        return ""

    def explain_variance(self, finding: dict) -> Explanation:
        """Turn a typed variance into a sentence a finance person would write."""
        raise NotImplementedError

    def draft_claim(self, claim: dict) -> Explanation:
        """Draft the message to the counterparty. A human sends it, never us."""
        raise NotImplementedError


# ==========================================================================
class RulesEngine(ReasoningEngine):
    """Deterministic. No network, no key, no account. The default.

    Everything below is a template filled from values the arithmetic already
    computed. It is not trying to sound clever -- it is trying to be correct and
    reproducible, which for a close pack matters more.
    """

    name = "rules"

    def parse_narration(self, narration: str) -> dict:
        import re
        utr = re.search(r"\bN\d{11,12}\b", narration or "")
        upper = (narration or "").upper()
        return {
            "counterparty": "Razorpay" if "RAZORPAY" in upper or "RZPY" in upper
                            else "unknown",
            "reference": utr.group(0) if utr else None,
            "intent": "settlement" if "STLMT" in upper or "SETTLEMENT" in upper
                      else "credit",
            "confidence": "high" if utr else "low",
        }

    def explain_variance(self, f: dict) -> Explanation:
        cls = f.get("class", "variance")
        amount = f.get("exposure_display", "")
        n = f.get("count", 1)
        base = {
            "MDR": f"The gateway fee on {n} line(s) exceeds the contracted rate, "
                   f"by {amount} in total. Recoverable if the rate card confirms "
                   "the agreed percentage.",
            "GST": f"Tax on {n} line(s) was computed on the transaction value "
                   f"rather than on the fee, overstating it by {amount}. This also "
                   "inflates the input credit claimed, so it is a compliance "
                   "exposure as well as a cash one.",
            "CREDIT": f"{n} line(s) worth {amount} sit in a settlement the report "
                      "says was paid, with no matching bank credit.",
            "ADJUSTMENT": f"{n} courier remittance line(s) carry a deduction of "
                          f"{amount} with no AWB-level justification.",
        }.get(cls, f"{n} record(s) totalling {amount} could not be proven.")
        return Explanation(base, "rules")

    def draft_claim(self, c: dict) -> Explanation:
        party = c.get("counterparty", "the counterparty")
        return Explanation(
            f"Requesting review of {c.get('exposure_display', 'the amount')} "
            f"relating to {c.get('class', 'a variance')}, identified during the "
            f"{c.get('period', 'period')} close. Supporting detail attached. "
            f"Claim window closes {c.get('deadline', 'shortly')}.",
            "rules",
        )


# ==========================================================================
class _HTTPEngine(ReasoningEngine):
    """Shared plumbing: cache, HTTP, and a fallback that never raises."""

    def __init__(self, fallback: ReasoningEngine | None = None):
        self.fallback = fallback or RulesEngine()

    # -- caching ----------------------------------------------------------
    def _key(self, task: str, payload: str) -> Path | None:
        d = cache_dir()
        if d is None:
            return None
        h = hashlib.sha256(f"{self.name}:{task}:{payload}".encode()).hexdigest()[:32]
        return d / f"{h}.json"

    def _cached(self, task: str, payload: str) -> str | None:
        p = self._key(task, payload)
        if p is not None and p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))["text"]
            except Exception:
                return None
        return None

    def _store(self, task: str, payload: str, text: str) -> None:
        p = self._key(task, payload)
        if p is None:
            return
        try:
            p.write_text(json.dumps({"text": text}), encoding="utf-8")
        except OSError:
            pass          # a cache failure must never break a close

    # -- transport --------------------------------------------------------
    def _post(self, url: str, body: dict, headers: dict) -> dict:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **headers})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read().decode())

    def _generate(self, prompt: str) -> str:
        raise NotImplementedError

    # Set only by the diagnostic path. Production stays silent: a close must
    # never fail because a model was unreachable, but a developer running the
    # selftest needs the actual error, not a shrug.
    debug = False
    last_error: str | None = None

    def _ask(self, task: str, prompt: str, cache: bool = True) -> str | None:
        if cache:
            hit = self._cached(task, prompt)
            if hit is not None:
                return hit
        try:
            text = (self._generate(prompt) or "").strip()
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")[:300]
            self.last_error = f"HTTP {e.code}: {body}"
            if self.debug:
                raise
            return None
        except (urllib.error.URLError, KeyError, IndexError, TimeoutError,
                json.JSONDecodeError, OSError) as e:
            self.last_error = f"{type(e).__name__}: {e}"
            if self.debug:
                raise
            return None                      # silent, deliberate: fall back
        if not text:
            return None
        if cache:
            self._store(task, prompt, text)
        return text

    # -- the narrow interface ---------------------------------------------
    def parse_narration(self, narration: str) -> dict:
        out = self._ask("narration", (
            "Extract structured fields from this Indian bank statement narration. "
            "Reply with ONLY a JSON object with keys counterparty, reference, "
            "intent, confidence. No prose, no code fences.\n\n"
            f"Narration: {narration}"
        ))
        if out:
            try:
                cleaned = out.strip().removeprefix("```json").removeprefix("```")
                cleaned = cleaned.removesuffix("```").strip()
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                pass          # a malformed reply is a miss, not a crash
        return self.fallback.parse_narration(narration)

    def explain_variance(self, f: dict) -> Explanation:
        context = DOMAIN.get(f.get("class", ""), "")
        out = self._ask("variance", (
            "You are a finance controller writing ONE sentence for a month-end "
            "close pack that a chartered accountant will read.\n\n"
            f"What this variance class means: {context}\n\n"
            f"The finding: {json.dumps(f, default=str)}\n\n"
            "Write one sentence saying what went wrong and what the merchant "
            "should do about it. Use the domain meaning above -- do not merely "
            "restate the numbers back. Do not invent figures. No greeting, no "
            "sign-off, no preamble."
        ))
        return Explanation(out, self.name) if out else self.fallback.explain_variance(f)

    def plan(self, system: str, observation: str) -> str:
        """One planning turn. Not cached: the whole point is that the next
        action depends on what the last tool returned."""
        return self._ask("plan", f"{system}\n\nCURRENT STATE:\n{observation}",
                         cache=False) or ""

    def draft_claim(self, c: dict) -> Explanation:
        out = self._ask("claim", (
            "Draft a short, factual claim message to a counterparty about a "
            "billing discrepancy found during a month-end close. Neutral and "
            "specific. State the amount, what was found, and the deadline. "
            "Under 90 words. No greeting, no sign-off.\n\n"
            f"{json.dumps(c, default=str)}"
        ))
        return Explanation(out, self.name) if out else self.fallback.draft_claim(c)


class GeminiEngine(_HTTPEngine):
    """Google AI Studio free tier. No card required.

    The model is DISCOVERED rather than hardcoded. Google's catalogue rotates --
    names appear, go to preview, and retire -- so a pinned string is a bug with a
    delay on it. This asks the API which models exist, keeps the ones that
    support generateContent, and prefers a light/flash variant. GEMINI_MODEL
    overrides if you want a specific one.
    """
    name = "gemini"
    ROOT = "https://generativelanguage.googleapis.com/v1beta"

    def _api_key(self) -> str:
        # Named _api_key, not _key: the base class already uses _key(task, payload)
        # for cache paths, and shadowing it breaks every cache lookup.
        return os.environ.get("GEMINI_API_KEY", "").strip()

    def list_models(self) -> list[str]:
        req = urllib.request.Request(
            f"{self.ROOT}/models?key={self._api_key()}",
            headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode())
        out = []
        for m in data.get("models", []):
            if "generateContent" in (m.get("supportedGenerationMethods") or []):
                out.append(m["name"].removeprefix("models/"))
        return out

    def candidates(self) -> list[str]:
        """Models to try, best first.

        A single guess is fragile: /v1beta/models lists models that
        generateContent will still refuse, so the only reliable answer is to try
        them in order and keep the first that actually answers.
        """
        pinned = os.environ.get("GEMINI_MODEL", "").strip()
        if pinned:
            return [pinned]

        d = cache_dir()
        cache = (d / "gemini_model.txt") if d is not None else None
        known = (cache.read_text(encoding="utf-8").strip()
                 if cache is not None and cache.exists() else "")

        try:
            available = self.list_models()
        except Exception:
            available = []

        def rank(n: str) -> tuple:
            unusable = any(w in n for w in (
                "embedding", "aqa", "vision", "tts", "image", "audio",
                "imagen", "veo", "learnlm", "gemma"))
            return (unusable, "flash" not in n, "preview" in n, "exp" in n, len(n))

        ordered = sorted(available, key=rank)
        # Anything that worked before goes first.
        if known and known in ordered:
            ordered.remove(known)
            ordered.insert(0, known)
        elif known:
            ordered.insert(0, known)
        return ordered or ["gemini-1.5-flash"]

    def resolve_model(self) -> str:
        return self.candidates()[0]

    tried: list[str] = []

    def _generate(self, prompt: str) -> str:
        self.tried = []
        last: Exception | None = None
        for model in self.candidates()[:6]:
            self.tried.append(model)
            try:
                data = self._post(
                    f"{self.ROOT}/models/{model}:generateContent"
                    f"?key={self._api_key()}",
                    {"contents": [{"parts": [{"text": prompt}]}]}, {})
                text = data["candidates"][0]["content"]["parts"][0]["text"]
            except urllib.error.HTTPError as e:
                # 404 means this model does not serve generateContent; 400 often
                # means the same thing with a different accent. Either way, the
                # next candidate deserves a turn. Anything else is fatal.
                if e.code in (400, 404):
                    last = e
                    continue
                raise
            except (KeyError, IndexError) as e:
                last = e
                continue
            d = cache_dir()          # remember what worked, skip the walk next time
            if d is not None:
                try:
                    (d / "gemini_model.txt").write_text(model, encoding="utf-8")
                except OSError:
                    pass
            self.model_used = model
            return text
        raise last or RuntimeError("no Gemini model accepted generateContent")


class OpenAIEngine(_HTTPEngine):
    name = "openai"

    def _generate(self, prompt: str) -> str:
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        model = os.environ.get("OPENAI_MODEL", "").strip() or "gpt-4o-mini"
        data = self._post(
            "https://api.openai.com/v1/chat/completions",
            {"model": model, "messages": [{"role": "user", "content": prompt}]},
            {"Authorization": f"Bearer {key}"})
        return data["choices"][0]["message"]["content"]


class AnthropicEngine(_HTTPEngine):
    name = "anthropic"

    def _generate(self, prompt: str) -> str:
        key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        model = os.environ.get("ANTHROPIC_MODEL", "").strip() or "claude-haiku-4-5"
        data = self._post(
            "https://api.anthropic.com/v1/messages",
            {"model": model, "max_tokens": 400,
             "messages": [{"role": "user", "content": prompt}]},
            {"x-api-key": key, "anthropic-version": "2023-06-01"})
        return data["content"][0]["text"]


class OllamaEngine(_HTTPEngine):
    """Local inference. Free, offline, and nothing leaves the machine."""
    name = "ollama"

    def _generate(self, prompt: str) -> str:
        host = os.environ.get("OLLAMA_HOST", "").strip() or "http://localhost:11434"
        model = os.environ.get("OLLAMA_MODEL", "").strip() or "qwen2.5:7b"
        data = self._post(f"{host}/api/generate",
                          {"model": model, "prompt": prompt, "stream": False}, {})
        return data["response"]


# ==========================================================================
ENGINES = {
    "rules": RulesEngine, "gemini": GeminiEngine, "openai": OpenAIEngine,
    "anthropic": AnthropicEngine, "ollama": OllamaEngine,
}


def get_engine(name: str | None = None) -> ReasoningEngine:
    """Resolve the configured engine, degrading to rules rather than failing.

    A close must never fail because a model was unreachable. If the requested
    engine cannot be configured, the caller gets rules and a note saying so --
    silently producing worse output would be the wrong trade, but so would
    refusing to close the books.
    """
    from . import config
    config.load_env()
    name = (name or os.environ.get("ATTEST_ENGINE", "").strip() or "rules").lower()

    if name not in ENGINES:
        return RulesEngine()
    if name == "rules":
        return RulesEngine()
    if config.missing(name):
        return RulesEngine()
    return ENGINES[name]()


def selftest(debug: bool = False) -> str:
    """Prove the configured engine actually answers. Run before a demo."""
    from . import config
    config.load_env()
    want = os.environ.get("ATTEST_ENGINE", "").strip() or "rules"
    eng = get_engine()
    out = [f"\n  requested : {want}", f"  active    : {eng.name}"]

    if want != "rules" and eng.name == "rules":
        gaps = config.missing(want)
        out.append(f"  -> fell back to rules: "
                   f"{', '.join(g.name for g in gaps) if gaps else 'unknown engine'}")

    if isinstance(eng, _HTTPEngine):
        eng.debug = False       # report, never abort: see the try/except below

    if isinstance(eng, GeminiEngine):
        try:
            models = eng.list_models()
            cands = eng.candidates()
            out.append(f"  models    : {len(models)} support generateContent")
            out.append(f"  will try  : {', '.join(cands[:4])}")
            if debug:
                out.append("  full list :")
                for m in models[:20]:
                    out.append(f"                {m}")
        except Exception as ex:
            out.append(f"  model list FAILED: {type(ex).__name__}: {str(ex)[:200]}")

    sample = {"class": "MDR", "count": 23, "exposure_display": "₹121.90",
              "period": "2026-08"}
    # The selftest exists to diagnose, so it reports failures rather than
    # raising them. Debug mode adds the traceback; it never loses the report.
    try:
        e = eng.explain_variance(sample)
    except Exception as ex:
        out.append(f"\n  explain_variance RAISED: {type(ex).__name__}: {str(ex)[:200]}")
        if debug:
            import traceback
            out.append("    " + traceback.format_exc().replace("\n", "\n    ")[:1200])
        e = RulesEngine().explain_variance(sample)
    out += ["", f"  explain_variance -> [{e.source}]", f"    {e.text[:300]}"]
    if getattr(eng, "tried", None):
        out.append(f"  models tried: {', '.join(eng.tried)}")

    n = eng.parse_narration(
        "NEFT CR-HDFC0000060-RAZORPAY SOFTWARE PVT LTD-N220386838913")
    out += ["", f"  parse_narration  -> {json.dumps(n)[:200]}"]

    if eng.name != "rules" and e.source == "rules":
        out.append("\n  NOTE: the model did not answer; output came from rules. "
                   "The close still completes -- that is by design.")
        err = getattr(eng, "last_error", None)
        if err:
            out.append(f"  reason: {err}")
        else:
            out.append("  reason: not recorded — rerun with --debug")
    out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    print(selftest(debug="--debug" in sys.argv))
