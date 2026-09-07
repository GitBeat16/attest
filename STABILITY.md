# Benchmark stability across seeds

Every headline figure in `README.md` comes from **one** generated world
(seed `20260801`, 1,200 orders). That is a fair way to report a benchmark and a
poor way to defend one. A reader is entitled to ask whether 73.0% is a property
of the system or a property of that seed.

```bash
python3 scripts/stability.py --seeds 20
```

Twenty independently seeded worlds plus a clean control, generated, closed and
scored in about two seconds — cheap enough to gate every change.

---

## Result

| | submitted month (`20260801`) | across 20 worlds |
|---|---|---|
| Match rate | 90.9% | 86.4% – 90.9% (median 90.9%) |
| Proof rate | **73.0%** | **69.9% – 75.0%** (median 73.9%) |
| False-match rate | 68.2% | 63.6% – 95.5% (median 72.7%) |
| Recall, designed-for | 100.0% | **96.2% – 100.0%** (median 100.0%) |
| Recall, held out | 75.0% | 75.0% in **every** world |
| **False positives** | **0** | **0 in every world** |
| Signed the close | no | no, in all 20 |

**Control — the same world, world facts intact, no defects planted:**
0 exceptions, 0.0 bps residual, **ATTESTABLE**.

So it refuses twenty defective months and signs the clean one. The refusal is a
decision, not a default — which is the thing twenty refusals on their own cannot
establish.

---

## The control found a bug that twenty defective worlds could not

This is the reason the clean case earns its place.

`engine.py` compared **two different scopes**: chargebacks summed over *every*
batch against disputes filtered to the declared month. The chargebacks carried by
out-of-period batches therefore had nothing to match against and were reported as
orphans — a phantom `CHARGEBACK_ORPHAN` on a month where nobody erred.

On seed `20260801` the phantom was ₹490.67, exactly the chargeback total of the
two batches settled after 31 August:

```
AS SHIPPED    chargebacks (all 22 batches)   ₹11,700.81
              disputes (in period)           ₹11,210.14
              -> phantom finding                ₹490.67

SCOPE-MATCHED chargebacks (20 in scope)      ₹11,210.14
              disputes (in period)           ₹11,210.14
              -> delta                            ₹0.00
```

This is **invariant 6 a second time** — a stray out-of-period record redefining
the month — caught in the anchor but missed in a check that used the anchor. It
is also the same shape as the refund bug already in the bug list: a raw
period-total comparison with mismatched scope on the two sides.

It mattered because `CHARGEBACK_ORPHAN` is one of the three classes counted into
the unexplained residual, which is what decides whether a close can be signed. A
phantom was contributing to the refusal.

**Twenty defective worlds could not surface it.** A phantom finding hides inside
real findings; the residual is large either way and nothing looks wrong. A world
with nothing to find has nowhere to hide it.

### The fix, and what it changed

One line — sum chargebacks over in-scope batches, matching the dispute filter.

| | before | after |
|---|---|---|
| Clean world | 1 exception, 2.7 bps | **0 exceptions, 0.0 bps, ATTESTABLE** |
| Seed `20260801` residual | 254.5 bps | **251.8 bps** |
| Match / proof / false-match | 90.9 / 73.0 / 68.2 | **unchanged** |
| Designed & held-out recall | 100% / 75% | **unchanged** |
| False positives, 20 worlds | 0 | **0** |
| Close-pack digest | `4e008d98 …` | `3e7dd72f 1a322b80 f56448c6 11ac9bc6` |

Only phantoms disappeared; no real detection changed, and held-out recall stayed
at 75%, so invariant 4 is intact. The verdict on the submitted month is still
NOT ATTESTABLE — 251.8 bps against a 25 bps limit.

The seal caught the consequence unprompted: `scripts/verify.py` failed on the
close-pack digest, because the pack legitimately changed. That is the
tamper-evidence working on its own author.

`tests/test_clean_world.py` locks it in. Reverting the one-line fix fails
`test_a_clean_month_raises_no_exceptions` with exactly `CHARGEBACK_ORPHAN(49067p)`,
and one test asserts the corpus still *contains* an out-of-period batch carrying a
chargeback, so the suite cannot quietly become vacuous.

---

## Two things to keep honest

### "100% designed-for recall" is the best case, not the typical case

Across the twenty worlds, `UTR_UNRESOLVABLE` catches 2 of 3 on several seeds and
`CHARGEBACK_ORPHAN` is missed entirely on seed `20260820`. Median is 100%; the
honest range is 96.2%–100%. The README should quote the range.

Both are designed-for classes, so improving them is legitimate — invariant 4 only
protects the held-out set. But a detector written specifically to rescue seed
`20260820` is the same mistake as a targeted detector for a held-out class,
wearing a different hat.

### Held-out recall being identical everywhere means less than it looks

75.0% in all twenty is not evidence of a robust score. The same four classes are
planted and the same three generic checks catch the same three every time — the
miss is **systematic, not stochastic**. That is exactly what invariant 4 predicts,
but do not describe it as "stable across twenty worlds," which implies a variance
nothing was testing.

---

## Still worth doing

Wire the two integrity assertions into `scripts/verify.py` so they fail the build
rather than wait to be noticed:

- false positives **must** be 0 in every world, and in the clean control
- held-out recall **must not** reach 100%
