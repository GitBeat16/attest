# From hackathon project to a tool a CA would rely on

Sequenced by dependency, not by calendar. Each stage has an **exit condition** —
a thing that is true or not true — because "day 30" means nothing to a solo build
and a stage that is not finished does not become finished by a date passing.

Work the stages in order where the dependency is real, and out of order where it
isn't. Stage 1A is a correctness defect and comes first. 1B and Stage 2 are
independent of each other and of 1A; all three must precede Stage 4.

---

## Stage 0 · Where this actually is

**Done and verified:**

- Deterministic match / prove / attack engine, integer paise throughout
- **Confidence tiers** (`tiers.py`) — only PROVEN findings are claim-ready;
  15 adversarial verdicts, 0 became claims
- **Ingest readiness** (`readiness.py`) — 20 fixtures covering BOM, day-first
  ambiguity, Excel serials, Indian grouping, euro decimals, preamble and ragged
  rows. Refuses when the settlement report cannot be *parsed*. **It cannot
  detect rows that were never supplied** — see the gap in Stage 1A
- **Stability harness** — 20 seeded worlds plus a clean control
- **CI with real gates** — and the gates were proven to fail by breaking each
  one deliberately, which matters more than that they pass
- Tamper-evident seal, canonical block at v2
- Close pack rebuilds **byte-identically**, enforced in CI

**Honest numbers, pooled across 20 worlds (61,447 records evaluated):**

| | |
|---|---|
| Held-out recall | **75.0%** — and precisely: 3 classes caught 20/20, `OUT_OF_PERIOD_SETTLEMENT` caught **0/20** |
| Designed-for recall | **99.4%**, not 100% — `UTR_UNRESOLVABLE` 93.3%, `CHARGEBACK_ORPHAN` 95% |
| False positives | 0, in every world and in the control |
| Records trained on | **zero** — nothing here is trained, so nothing can be overfitted |

**Verified this session, not assumed.** Integer paise holds — zero non-integer
money values at runtime, and every division in the money modules is `Decimal`,
a ratio, a pathlib join or a duration. Tier/verdict separation holds. The CI
gates were each proven to fail by breaking them deliberately.

**What is not true yet:** no real data has ever passed through it, no CA has
seen it, `Claim.state` declares five states that nothing reads or writes, and
an incomplete input file closes silently (Stage 1A).

---

## Stage 1A · Detect input that is incomplete rather than unreadable

**The most dangerous open defect, found by auditing Stage 0's own claims.**

Deleting 264 of 463 settlement rows — 57% of the file — produces:

| | full file | 57% deleted |
|---|---|---|
| Readiness verdict | READY | **READY** |
| Reason emitted | *"every supplied source read in full"* | **same** |
| Proof rate | 73.0% | **80.3% — it went UP** |
| Residual | 251.8 bps | 266.4 bps |

The proof rate *improving* is the heart of it: the missing rows took their
unproven lines with them, so the metric that measures trustworthiness rewards
losing data. Match rate does fall (90.9% → 66.7%), but that reads as a bad
month, not a truncated file.

The cause is precise. `assess_coverage` checks only that the **date range**
spans the declared month; truncation left enough batches to still span August.
The layer knows what it was handed and has no notion of what it was owed.

The fix is cheap and uses data already present:

1. **Cross-source count reconciliation.** Bank credits reference
   `settlement_id`s. A credit whose batch is absent from the settlement report
   is a missing-rows fingerprint — and `resolve()` already computes exactly
   this as `unresolved`. Today it is reported as a match failure; it should
   *also* raise a completeness flag.
2. **Batch-internal completeness.** A batch declares its own total; if its lines
   do not sum to it, lines are missing.
3. **Reword the reason string.** "Every supplied source read in full" overstates
   what was verified. Say what was actually checked.

**BUILT.** `corroborate()` cross-checks each source against the records that
point into it, after resolution. Six checks: unexplained bank credits, batches
the bank never confirms, dangling order references, dangling AWBs, and — by
*direction* rather than magnitude — refunds and chargebacks netted against
records that were not supplied. No thresholds anywhere: a clean world scores
zero on the first four, and the last two were negative or zero on all six clean
worlds measured, turning positive only under truncation.

**Exit condition — met for 6 of 7 sources.** Halving any of
`razorpay_settlements`, `bank_statement`, `orders`, `shipments`, `refunds` or
`disputes` now yields PARTIAL; the clean control stays READY; and a month that
is not READY cannot be attested. 13 tests in `tests/test_completeness.py`, five
of which fail if `corroborate` is disabled.

**Knowingly uncovered: `cod_remittances.csv`.** Nothing references a remittance
row, and the obvious mirror — COD shipments delivered but never remitted — runs
at 125–147 on clean worlds against 223 when halved. No separation, no honest
threshold, so it is left uncovered rather than covered badly.
`test_the_cod_remittance_gap_is_still_a_gap` pins the current behaviour so the
caveat cannot go stale.

---

## Stage 1B · Finish the evaluation story

Cheap, self-contained, no dependencies. Do it because every later claim rests on
these numbers.

- **Pooled scoring.** Per-world, each held-out class has one instance, so
  held-out recall can only read 0/25/50/75/100. Pooling the ledger across the 20
  worlds already generated gives 80 instances — granularity 1.25% instead of
  25%, from data that already exists.
- **Publish the pooled figures**, including the correction from 100% to 99.4%
  designed-for. A self-reported 99.4% is worth more than a 100% that held on one
  seed.
- **Move the CI gate to the pooled number.** It is the sensitive one.

**BUILT.** `scripts/stability.py` prints pooled per-class recall across the 20
worlds, the CI gate reads the pooled held-out figure rather than a per-world
maximum, and `verify.py` now checks the README's pooled claims against what the
code actually produces — a README that disagrees with the engine fails the build.

**Exit condition — met.** The README quotes 75.0% held-out (60 of 80) and
**99.4%** designed-for (1054 of 1060), corrected from the 100% that held only on
one seed, with the per-class table showing `OUT_OF_PERIOD_SETTLEMENT` at 0/20.
Two further gates: pooled held-out reaching 100% fails the build, and so does a
run in which no held-out defects were planted at all — a vacuous score must not
pass silently.

---

## Stage 2 · Make a close explainable six months later

The seal proves *"this was not edited."* It cannot answer the question an auditor
actually asks about an old pack: **"what rules produced this?"**

- `attest/__init__.py` is empty. Nothing anywhere records a version.
- Put inside the sealed block: engine version, ruleset version, the contract
  rates used, and the tolerance and residual thresholds in force.
- Add `CHANGELOG.md` so a ruleset version means something specific. A version
  number with nothing behind it is worse than none.
- Reproducibility is already CI-enforced — this builds on it.

**Exit condition:** a pack from an old engine version can be explained without
reading today's `main`.

---

## Stage 3 · The gate before real data

Everything here matters at exactly one moment: when a real CA uploads a real
client's files. Not before, and not optional after.

Scope it to that, not to a generic threat model:

- Tenant isolation and RLS, actually tested — not assumed from the policy text
- File handling: size limits, type validation, what happens to a malicious CSV
- What is stored versus derived and discarded; a working delete
- Secrets: nothing in logs, errors, prompts, or the generated pack
- AI provider data retention — what leaves the machine when the model plans
- An audit log: who ran which close, against which inputs, when

**Exit condition:** you would be comfortable if a CA uploaded a real client's
settlement file this afternoon.

---

## Stage 4 · One real month

**The unlock.** Every number above describes a world we generated. This is the
only step that tests what the generator does not know how to be wrong about.

Escalating, each worth doing:

1. Your own Razorpay test account, through the existing `razorpay_test` mode.
2. A real, tiny merchant — a friend's store, one month, with permission.
   Anonymise it and keep it as a permanent fixture.
3. A CA with several clients.

**Quote no accuracy figure on real data.** There is no answer key. The
deliverable is a list of everything that broke.

**A commercial caution.** The benchmark shows ₹86,765 recoverable on ₹18.3 lakh
of volume — about 4.7%. **Do not quote that as expected yield.** The corpus
plants 81 defects in 3,043 records because that density exercises the detectors,
not because it models a real merchant. Real leakage runs far lower. Sell the
capability — *"it finds compensating errors that total-level checks cannot"* —
and let a real month produce the real rate.

**Exit condition:** a real month has been closed, and the list of what broke is
written down.

---

## Stage 5 · Only after a real CA has used it

Everything below is deliberately deferred, because it is designed from
imagination until someone real has touched the product.

- **Claim lifecycle persistence.** `Claim.state` is decorative today, so a claim
  cannot survive from one close to the next. *"Cadence is the product"* requires
  a claim raised on the 3rd to still be a claim on the 10th with its window
  ticking. It is also the precondition for calibration — you cannot learn which
  claims get paid without recording which were filed.
- **Daily cadence.** Needs the above first; cadence without claim persistence is
  just running the same report more often. And design it incrementally rather
  than running the monthly process every day.
- **Multi-client CA view.** Which clients have not closed, which have money
  expiring, which are NOT ATTESTABLE, which need input.
- **The data model and the production user journey.** Both cheap and correct
  after Stage 4, both expensive and wrong before it.
- Razorpay OAuth · claim filing · Tally/Zoho export.

---

## Questions to ask a real CA

This list replaces speculative design. Take it to the first conversation.

1. When you close a client's month today, what do you actually open first?
2. What do you do when the numbers tie but something feels wrong?
3. Have you ever filed a claim that got rejected? What happened next?
4. Who reviews your work, and what do they look at?
5. What do you hand the auditor, and what do they ask about it?
6. How many clients, and what does a bad month look like across all of them?
7. What would make you *not* trust a tool like this?
8. What would you have to see before you ran it on a client without checking it
   by hand afterwards?

Question 7 is the important one. Write the answer down verbatim.

---

## A discipline, not a stage

**Do not trust the documentation, including your own.** For any claim in
`README.md`, `INVARIANTS.md` or `ARCHITECTURE.md`, find the code and check it.
Everything real that this project found came from that habit:

- a chargeback check comparing two different period scopes, inventing a finding
  on a month where nobody erred
- integrity gates that printed failures and exited 0
- five claim states that nothing reads or writes
- "100% designed-for recall" that was true on one seed and 99.4% across twenty

Re-run that audit whenever a subsystem lands. A claim enforced only by
convention, rather than by code or a test, is the finding.

---

## What not to build

- **No ML in detection.** Rules are why a finance professional trusts the output.
  The model plans; it must never decide what is wrong.
- **No targeted detector for the missed held-out class.** The visible miss is
  what makes the other numbers believable.
- **No dependencies.** Stdlib-only is a genuine selling point, and CI passing on
  a bare runner is the proof.
- **No chatbot, no generic analytics, no dashboard for its own sake.**
- **No automatic ledger posting**, ever.
- **No optimising the close.** 15,148 records in 0.02s. It is not slow.
- **No more synthetic defect classes before Stage 4.** You would be getting
  better at a world you invented.
- **Don't "improve" the numbers.** A change that makes proof rate or false-match
  rate look better without the system getting better is the wrong change.

---

## If you only do two things

1. **Stage 1A**, because a tool that closes confidently on half a file is the
   one failure a CA would never forgive — and **Stage 1B**, because it is half a
   day and makes every published number twenty times more precise.
2. **Stage 4**, because everything after it is guesswork until it exists.

Stage 2 and 3 are the price of admission for Stage 4 being safe. Stage 5 is not
work yet — it is a list of things to decide once someone real has an opinion.
