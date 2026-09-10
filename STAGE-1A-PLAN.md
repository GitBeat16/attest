# Stage 1A — plan

**Detect input that is incomplete rather than unreadable.**

Design only. No code until this is agreed.

---

## The defect

`readiness.py` refuses when the settlement report cannot be **parsed**. It has no
notion of rows that were never supplied. Deleting 57% of the settlement file
yields verdict `READY`, the reason *"every supplied source read in full"*, and a
proof rate that **rises** from 73.0% to 80.3% — the missing rows take their
unproven lines with them, so the metric that measures trustworthiness rewards
losing data.

`assess_coverage` runs, but only checks that the **date range** spans the
declared month. Truncation left enough batches to still span August.

---

## Two candidate signals, investigated and ruled out

Recording these so nobody re-proposes them.

**Batch-internal totals — impossible.** `Batch.expected_credit` is a *computed
property* (`ingest.py:138`), derived from the lines present. The settlement CSV
is flat — one row per payment, `settlement_id` repeated, no batch header and no
declared total. Drop lines and the total silently shrinks to match. There is
nothing to compare against.

**"Any unresolved bank credit means missing data" — false-positives every
month.** A healthy benchmark month already carries 2 unresolved credits, because
`ORPHAN_BANK_CREDIT` and `MISSING_CREDIT` are real planted defect classes. A
rule that fires on any unresolved credit would fire on every real close.

---

## The design that survives: a dangling-reference census

The bank statement is produced by a **different party** than the settlement
report. That independence is the whole signal: if the bank shows money arriving
and the settlement report cannot explain it, either rows are missing or the
credits are genuine orphans — and **in both cases the user must be told before
they trust the close, and the required action is the same**: go and get the
missing settlement data, or explain the credits.

So do not try to distinguish the two. Report the fact.

**The general rule:** a reference from source A to a record absent from source B
is a completeness statement about B. Census them, rather than only surfacing
them one line at a time as chain breaks.

### Why it needs no threshold

| | unresolved | unexplained credit |
|---|---|---|
| Clean world, no defects planted | **0** | ₹0.00 |
| Benchmark, defects planted | 2 | ₹32,343.60 |
| Truncated to 349 rows | 8 | ₹1,88,895.99 |
| Truncated to 199 rows | 16 | ₹3,96,619.79 |
| Truncated to 99 rows | 19 | ₹4,51,440.94 |

A spotless month is **exactly zero**. The rule can therefore be *"any unexplained
bank credit downgrades the verdict"* with no invented cut-off — and inventing a
cut-off was the thing most likely to make the cure worse than the disease.

### Signals, by source

| Reference | Absent from | Strength | Status today |
|---|---|---|---|
| Bank credit → `settlement_id` | settlement report | **strong** — independent party | `resolve()` already computes it as `unresolved` |
| Settlement line → `order_id` | orders | moderate | surfaces per-line as an `order` chain break |
| COD remittance → `awb` | shipments | moderate | surfaces per-line as a `shipment` chain break |

The first is the one that matters and is already computed. The other two exist
as per-line findings; the change is to *also* count them as a statement about
the completeness of the file they point into.

---

## What changes

### 1. A completeness pass, after resolution

Readiness is currently computed inside `load()`, before `resolve()` runs — so it
cannot see resolution results. `close()` checks `rd.refused` before resolving.

Reorder minimally in `attest/close.py`:

```
corpus = load(src)
if corpus.readiness.refused: raise        # parse-level, unchanged
resolve_naive(corpus); keys = resolve(corpus)
corroborate(corpus.readiness, corpus, keys)   # NEW — may downgrade
if corpus.readiness.refused: raise        # completeness-level
```

`corroborate()` lives in `readiness.py` and may only ever *downgrade*
(`READY → PARTIAL`). The existing `degrade()` already refuses to silently
upgrade.

### 2. Reword the misleading reason

*"every supplied source read in full"* means "every row I was handed, I parsed" —
not "I received every row that exists." It should say what was actually
verified.

### 3. Make PARTIAL impossible to miss

**A design decision for you.** Today `PARTIAL` is a label, not a block: deleting
the entire bank statement yields PARTIAL and the close still completes. That may
be right — a missing courier file should not block a gateway close — but the
verdict must then be unmissable on the close pack's first screen and in the app
headline, not buried in a payload field.

My recommendation: keep PARTIAL non-blocking, but make a month with unexplained
bank credits **ineligible to be ATTESTABLE**. That is already effectively true
via the residual; making it explicit costs nothing and states the rule plainly.

---

## Consequences to expect

- **The benchmark month becomes PARTIAL**, correctly — it genuinely holds
  ₹32,343.60 of bank credit the settlement report cannot explain, and it is
  already NOT ATTESTABLE.
- **The close pack digest changes**, because `readiness_verdict` is in the sealed
  payload. Regenerate and reseal in the same commit; `verify.py` will fail until
  `web/index.html` and `README.md` match. That is the seal working.
- **`verify.py`'s check count changes** as new assertions land.
- **The clean control stays READY** — that is the false-positive gate.

---

## Tests

New `tests/test_completeness.py`:

1. Truncate the settlement report to 75% / 50% / 25% → **never READY**
2. Truncate orders and shipments likewise → **never READY**
3. Delete a whole source → PARTIAL or REFUSED, never READY
4. Full corpus → PARTIAL, and the reason names the count and the amount
5. **Clean world → READY** — the false-positive guard
6. A vacuity guard: assert the clean world genuinely has zero unexplained
   credits, so the suite cannot quietly stop testing anything

Plus the existing harness, unchanged: 20 worlds must still show 0 false
positives and the control must still sign at 0.0 bps. **The stability harness is
the false-positive test for this change** — if the new check misfires, CI fails.

---

## Risks

**The real risk is a false positive, not a miss.** A completeness check that
fires on legitimate months would be worse than the bug it fixes, because it
teaches the user to ignore the warning. Mitigated by: no threshold, a clean-world
baseline of exactly zero, and the 20-world gate already in CI.

**Second risk: PARTIAL fatigue.** If most real months are PARTIAL, the state
stops meaning anything. Watch this at Stage 4 with real data — if every real
month has unexplained credits, the rule needs revisiting, and that is a finding
about reality rather than about the code.

---

## Exit condition

Deleting rows from any source file produces PARTIAL or REFUSED, never READY —
with a regression test that fails if it ever returns to READY, and the clean
control still READY.

---

## Files touched

- `attest/readiness.py` — `corroborate()`, reworded reason
- `attest/close.py` — reorder, second refusal check
- `attest/report.py`, `web/app.html` — surface the verdict prominently
- `tests/test_completeness.py` — new
- `scripts/verify.py` — assertions
- `web/close-pack.html`, `web/index.html`, `README.md` — reseal
