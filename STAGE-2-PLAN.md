# Stage 2 — plan

**Make a close explainable six months later.**

Design only. No code until this is agreed.

---

## The gap

The seal proves *"this document was not edited."* It cannot answer the question
an auditor actually asks about an old pack:

> **"What rules produced this?"**

`attest/__init__.py` is empty. Nothing anywhere records a version. A pack sealed
today and a pack sealed after a threshold change are indistinguishable, and the
only way to explain an old one is to read whatever `main` says now — which is
precisely the thing that has changed.

---

## What actually decides an answer

Inventoried from the code, not from the docs.

| Where | Rule | Value |
|---|---|---|
| `engine.py` | `BATCH_TOLERANCE` | 100 paise |
| `engine.py` | `LINE_TOLERANCE` | 0 |
| `audit.py` | `NOISE` | 2 paise |
| `audit.py` | `OFFSET_THRESHOLD` | 50 paise |
| `policy.py` | `RESIDUAL_LIMIT_BPS` | 25 |
| `policy.py` | `UNEXPLAINED` | 3 classes |
| `recovery.py` | `WINDOWS` | counterparty + days, ~15 classes |
| `tiers.py` | `_MAP` | 24 classes → tier |
| `contract_terms.json` | MDR, GST, COD fee, RTO freight | per merchant |

**Scope decision:** the ruleset covers the *engine's* rules only. Generator
constants (`defects.INFLATED_MDR_RATE`) and demo fixtures (`demo.A_GROSS`) do not
belong in it — they shape the benchmark, not a real close, and including them
would make the version churn for reasons no auditor cares about.

**Contract terms are not rules.** They are inputs, and they differ per merchant.
They must be recorded, but separately: *these were our rules; that was your
contract.*

---

## A defect found while inventorying: the threshold exists twice

```
attest/run.py:30      RESIDUAL_LIMIT_BPS = 25
attest/policy.py:30   RESIDUAL_LIMIT_BPS = 25
```

Two definitions of the number that decides whether a close can be signed. Change
one and they diverge silently — the pack and the controller would then disagree
about whether the same month is attestable, and nothing would notice.

**This must be fixed before anything else in Stage 2**, because versioning a
threshold that exists in two places records a number that is not the whole truth.
`policy.py` owns it; `run.py` imports it. A test asserts there is exactly one
definition.

---

## The design

Two versions, because they have genuinely different natures.

### A. Engine version — hand-maintained

`__version__` in `attest/__init__.py`, semver, bumped by a person, described in
`CHANGELOG.md`.

Code cannot be meaningfully hashed for this purpose: a pure refactor would bump
the version while changing no answer, and a subtle logic change inside a large
function would not stand out. Human judgement is the right instrument, and the
CHANGELOG is where the judgement is recorded.

### B. Ruleset digest — computed, never hand-maintained

A SHA-256 over the **actual runtime values** of every rule in the table above.

This is the important choice. A hand-maintained ruleset number drifts the moment
someone changes a tolerance and forgets to bump it — and that is exactly the
change an auditor most needs to see. A digest derived from the values themselves
**cannot** drift: change the threshold and the digest changes, with no discipline
required from anyone.

It is the same principle as the close-pack seal, applied one level up: don't
assert it, compute it.

Two properties it must have, both testable:

- Changing **any** rule value changes the digest.
- Changing a **comment, formatting, or a docstring** does *not*. It hashes
  values, not source text — otherwise it churns and people learn to ignore it.

That requires a canonical serialisation: sorted keys, values normalised through
`str()`, so `Decimal("2.00")` and dict ordering cannot make two identical
rulesets hash differently on different runs or Python versions.

### C. Where each piece goes

| | Sealed canonical block | Pack body |
|---|---|---|
| Engine version | ✓ | ✓ |
| Ruleset digest | ✓ | ✓ |
| The scalar thresholds | ✓ | ✓ |
| Full tier map and claim windows | — (covered by the digest) | ✓ readable table |
| Contract terms | ✓ (already partly) | ✓ |

The sealed block stays compact and carries the digest; the body carries the
full readable rules so an auditor can read them without running anything. The
digest is what binds the two together.

---

## Consequences to expect

- **The seal's canonical version goes v2 → v3.** Packs sealed under v2 still
  verify as v2 — `seal.py` already tolerates older shapes, and that behaviour
  must be preserved and tested, because a pack that stops verifying when the
  code moves on is the opposite of what this stage is for.
- **The pack digest changes.** Reseal in the same commit; `verify.py` fails
  until `web/index.html` and `README.md` match.
- **`verify.py` check count rises.**

---

## Tests — `tests/test_versioning.py`

1. **Every rule value changes the digest.** Parametrised over each constant:
   perturb it, assert the digest moves. This is the test that stops the whole
   mechanism becoming decorative.
2. **Comments and formatting do not change it.**
3. **The digest is stable** across repeated computation in one process and
   across a fresh interpreter.
4. `RESIDUAL_LIMIT_BPS` has **exactly one** definition in the package.
5. A sealed pack **contains** the engine version and the ruleset digest.
6. **A v2 pack still verifies** — old packs do not break.
7. The ruleset excludes generator and demo constants — perturbing
   `defects.INFLATED_MDR_RATE` must *not* move the digest.

---

## Risks

**Over-versioning.** If the digest changes on trivial edits, it becomes noise
and people stop reading it. Mitigated by hashing values rather than source, and
by test 2.

**Under-versioning.** A rule that decides an answer but is not in the ruleset is
worse than no version at all, because the digest then implies a completeness it
does not have. Mitigated by test 1 and by keeping the inventory table above in
the code as the single list, not duplicated in prose.

**Cross-version instability.** Python's `hash()` is salted and dict order is
insertion-ordered — neither may leak into the digest. Mitigated by canonical
serialisation and test 3.

---

## Exit condition

A pack states the engine version and the ruleset digest inside its sealed block;
changing any rule changes that digest; a `CHANGELOG.md` entry explains what each
version changed; and a pack sealed under the previous canonical version still
verifies.

---

## Files touched

- `attest/__init__.py` — `__version__`
- `attest/ruleset.py` — **new**: the inventory and the digest
- `attest/policy.py` — sole owner of `RESIDUAL_LIMIT_BPS`
- `attest/run.py` — import it rather than redefine it
- `attest/seal.py` — canonical v3
- `attest/report.py` — the readable rules table
- `tests/test_versioning.py` — new
- `scripts/verify.py` — assertions
- `CHANGELOG.md` — new
- `web/close-pack.html`, `web/index.html`, `README.md` — reseal
