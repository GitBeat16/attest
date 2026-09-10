# Changelog

What changed, and whether it could change a close's conclusion.

Two identifiers appear in every sealed pack:

- **Engine version** — this file. Hand-maintained, bumped when a change could
  alter an answer. A refactor that changes no answer does not need a bump; if
  you are unsure, it does.
- **Ruleset digest** — computed by `attest/ruleset.py` from the rule *values*:
  the tolerances, the residual limit, the unexplained classes, the claim windows
  and the tier map. Nobody maintains it, which is the point. Change a threshold
  and it moves on its own.

A pack states both, inside its seal. To explain an old pack, find its engine
version here and check its ruleset digest against the entry.

---

## 0.3.0

**Ruleset digest `82be09261684`**

The first version. Everything before this predates versioning: those packs carry
no engine version and no ruleset digest, and they still verify, because
`seal.verify()` re-hashes the canonical block embedded in the pack rather than
recomputing today's shape. An old pack does not break when the code moves on —
that is the whole purpose of this stage.

**Could change a conclusion:**

- **Input completeness is now checked.** `corroborate()` cross-checks six of the
  seven sources against the records that point into them. A month whose files
  cannot be corroborated is `PARTIAL` and cannot be attested. Previously a
  truncated settlement report reported `READY` and *raised* the proof rate,
  because the missing rows took their unproven lines with them.
- **Chargeback scope corrected.** The chargeback check summed over every batch
  while filtering disputes to the declared month, reporting the chargebacks of
  out-of-period batches as orphans. On the benchmark this was a phantom
  ₹490.67 counted into the unexplained residual, which decides whether a close
  can be signed.
- **Confidence tiers.** Every finding is `PROVEN`, `UNPROVEN` or `NEEDS_INPUT`,
  and only `PROVEN` may be framed as claim-ready. Recoverable totals are
  unchanged; what changed is what may be *asserted* about them.
- **`RESIDUAL_LIMIT_BPS` had two definitions** — `policy.py` and `run.py`, both
  25. They could have drifted silently, leaving the pack and the controller
  disagreeing about whether the same month was attestable. `policy.py` now owns
  it.

**Reporting only, no conclusion changed:**

- Recall is now pooled across 20 seeded worlds rather than averaged per world.
  Per world each held-out class plants one instance, so per-world held-out
  recall could only read 0/25/50/75/100. Designed-for recall is restated as
  **99.4%** pooled, corrected from a 100% that held only on one seed.
- The close pack carries a *What produced this* section: engine version, ruleset
  digest, the tolerances in force, and the full claim-window and tier tables.

**Rule values at this digest**

| | |
|---|---|
| `engine.BATCH_TOLERANCE` | 100 paise |
| `engine.LINE_TOLERANCE` | 0 |
| `audit.NOISE` | 2 paise |
| `audit.OFFSET_THRESHOLD` | 50 paise |
| `policy.RESIDUAL_LIMIT_BPS` | 25 |
| `policy.UNEXPLAINED` | `CREDIT`, `CHARGEBACK_ORPHAN`, `UNREFERENCED_ADJ` |
| `recovery.WINDOWS` | courier 14 days · Razorpay 60 · bank 90 |
| `tiers._MAP` | 24 classes |

---

## Before 0.3.0

Unversioned. The engine, the proof chains, the adversarial pass, the seal, the
recovery layer and the ingest readiness work all predate this file. Packs from
that period verify but cannot say what produced them, which is the gap this
version closes.
