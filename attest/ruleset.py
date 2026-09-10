"""What the rules were, at the moment a close was produced.

The seal proves a pack was not edited. It cannot answer the question an auditor
actually asks about an eight-month-old pack -- *"what rules produced this?"* --
and the honest answer must live inside the pack, not in whatever `main` says
today, because `main` is precisely the thing that has changed.

There are two versions here, because they have different natures.

**The engine version** is hand-maintained, in `attest/__init__.py`, described in
`CHANGELOG.md`. Code cannot be usefully hashed for this: a pure refactor would
bump the version while changing no answer, and a subtle logic change inside a
large function would not stand out. That is a judgement, and judgements belong
to people.

**The ruleset digest is computed**, and that is the point. A hand-maintained
ruleset number drifts the moment somebody changes a tolerance and forgets to
bump it -- which is exactly the change an auditor most needs to see. A digest
derived from the values themselves cannot drift. It is the same principle as the
close-pack seal, one level up: do not assert it, compute it.

Two properties, both tested in `tests/test_versioning.py`:

  * changing ANY value below changes the digest;
  * changing a comment, a docstring or the formatting does NOT.

The second is why this hashes *values* rather than source text. A digest that
churns on every edit teaches people to ignore it, and an ignored version is
worse than none.

SCOPE. These are the engine's rules -- the things that decide an answer about a
real close. Corpus-generator constants (`defects.INFLATED_MDR_RATE`) and demo
fixtures (`demo.A_GROSS`) are deliberately excluded: they shape the benchmark,
not anyone's month, and folding them in would make the version churn for reasons
no auditor cares about.

CONTRACT TERMS ARE NOT RULES. The MDR rate, the GST rate, the courier fee and
the RTO freight come from the merchant's own contract and differ per merchant.
They are recorded beside the ruleset, never inside it: *these were our rules;
that was your contract.*
"""
from __future__ import annotations

import hashlib
import json

from . import audit, engine, policy, recovery, tiers


def rules() -> dict:
    """Every value that can change an answer. The single inventory.

    Kept as one function rather than duplicated into prose, so a rule cannot be
    added to the engine and forgotten here without the omission being visible in
    one place.
    """
    return {
        # matching
        "engine.batch_tolerance_paise": engine.BATCH_TOLERANCE,
        "engine.line_tolerance_paise": engine.LINE_TOLERANCE,
        # the adversarial pass
        "audit.noise_paise": audit.NOISE,
        "audit.offset_threshold_paise": audit.OFFSET_THRESHOLD,
        # certification
        "policy.residual_limit_bps": policy.RESIDUAL_LIMIT_BPS,
        "policy.unexplained_classes": sorted(policy.UNEXPLAINED),
        # what may be claimed, from whom, and for how long
        "recovery.windows": {k: [v[0], int(v[1])]
                             for k, v in recovery.WINDOWS.items()},
        # what a finding is allowed to assert
        "tiers.map": dict(tiers._MAP),
    }


def _canonical(obj) -> str:
    """Stable text for hashing.

    `sort_keys` because dicts are insertion-ordered and two identical rulesets
    built in a different order must not hash differently. `default=str` so a
    Decimal or a date normalises the same way on every interpreter. Neither
    Python's salted `hash()` nor `repr()` of a container may ever reach this.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def digest() -> str:
    """SHA-256 over the rule values. Full hex; callers usually show 12."""
    return hashlib.sha256(_canonical(rules()).encode("utf-8")).hexdigest()


def short() -> str:
    """The form a person reads and quotes."""
    return digest()[:12]


def summary() -> dict:
    """Version, digest, and the scalars worth carrying in the sealed block.

    The full tier map and claim windows are not repeated here -- they are in the
    pack body for a human to read, and the digest already covers them. What is
    here is the handful of numbers somebody is most likely to argue about.
    """
    from . import __version__
    r = rules()
    return {
        "engine_version": __version__,
        "ruleset_digest": short(),
        "batch_tolerance_paise": r["engine.batch_tolerance_paise"],
        "line_tolerance_paise": r["engine.line_tolerance_paise"],
        "residual_limit_bps": r["policy.residual_limit_bps"],
    }
