"""Environment loading and validation.

Validation here is **contextual**, not global. Attest's core pipeline requires no
configuration at all — it runs the whole close, produces every metric and writes
the close pack with no key, no account and no network. Demanding a Razorpay
credential from someone who only wants to run the corpus would be wrong.

So requirements are declared per capability, and a capability only validates when
you actually reach for it. When something is missing the message says which
variable, what it is for, and where to get it — never a bare KeyError.

No value is ever printed. Where a credential is echoed for confirmation it is
masked to its first characters, which are the non-secret half of a Razorpay key
id and are what you need to tell test mode from live.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


# --------------------------------------------------------------------------
def load_env(path: Path | None = None, override: bool = False) -> int:
    """Read .env into os.environ. Returns how many variables were set.

    Real environment variables win by default, so a value exported in a shell or
    injected by a CI runner is never silently overridden by a stale local file.
    """
    path = path or ENV_FILE
    if not path.exists():
        return 0
    n = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            n += 1
    return n


# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Requirement:
    name: str
    purpose: str
    where: str


CAPABILITIES: dict[str, list[Requirement]] = {
    "razorpay": [
        Requirement("RAZORPAY_KEY_ID", "identifies your Razorpay account",
                    "dashboard.razorpay.com > Settings > API Keys (Test Mode)"),
        Requirement("RAZORPAY_KEY_SECRET", "authenticates API calls",
                    "shown once when you generate the key — regenerate if lost"),
    ],
    "gemini": [
        Requirement("GEMINI_API_KEY", "the optional AI reasoning layer",
                    "aistudio.google.com/apikey — free tier, no card required"),
    ],
    "openai": [
        Requirement("OPENAI_API_KEY", "the optional AI reasoning layer",
                    "platform.openai.com/api-keys — paid, $5 minimum"),
    ],
    "anthropic": [
        Requirement("ANTHROPIC_API_KEY", "the optional AI reasoning layer",
                    "console.anthropic.com — paid, $5 minimum"),
    ],
    "ollama": [
        Requirement("OLLAMA_HOST", "where the local model server is listening",
                    "defaults to http://localhost:11434"),
    ],
}


class MissingConfig(RuntimeError):
    """Raised when a capability is used without the variables it needs."""


def missing(capability: str) -> list[Requirement]:
    """Which requirements for this capability are absent or empty."""
    return [r for r in CAPABILITIES.get(capability, [])
            if not os.environ.get(r.name, "").strip()]


def check(capability: str) -> None:
    """Raise a message a human can act on, listing exactly what is absent."""
    gaps = missing(capability)
    if not gaps:
        return
    lines = [
        "",
        f"  Cannot use '{capability}' — {len(gaps)} environment variable(s) missing.",
        "",
    ]
    for r in gaps:
        lines += [f"    {r.name}", f"      what it does : {r.purpose}",
                  f"      where to get : {r.where}", ""]
    lines += [
        "  Fix:",
        "    1. python3 scripts/sync_env.py     # adds any missing names to .env",
        "    2. open .env and fill in the value(s)",
        "",
        "  Note: Attest's core pipeline needs none of this. To run the full",
        "  close with no credentials at all:",
        "    python3 -m attest.generate --out data --orders 1200",
        "    python3 -m attest.run --data data --html docs/index.html",
        "",
    ]
    raise MissingConfig("\n".join(lines))


def mask(value: str, keep: int = 12) -> str:
    """Show only enough to identify a key. Never the secret part."""
    if not value:
        return "(not set)"
    return value if len(value) <= keep else f"{value[:keep]}…"


def status() -> str:
    """A human-readable summary of what is configured. No secret values."""
    load_env()
    out = ["", "  ENVIRONMENT", "  " + "-" * 58]
    for cap, reqs in CAPABILITIES.items():
        gaps = missing(cap)
        mark = "ready" if not gaps else f"{len(gaps)} missing"
        out.append(f"    {cap:<12} {mark}")
        for r in reqs:
            v = os.environ.get(r.name, "")
            shown = mask(v) if r.name.endswith("KEY_ID") or r.name.endswith("HOST") \
                else ("set" if v.strip() else "(not set)")
            out.append(f"      {r.name:<22} {shown}")
    # An empty value is not an absent one: .get(k, default) returns "" when the
    # key exists but is blank, which is exactly what sync_env writes.
    engine = os.environ.get("ATTEST_ENGINE", "").strip() or "rules"
    out += ["  " + "-" * 58,
            f"    reasoning engine: {engine}",
            "    core pipeline requires no configuration", ""]
    return "\n".join(out)


if __name__ == "__main__":
    print(status())
