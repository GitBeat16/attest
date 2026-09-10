# From hackathon project to a tool a CA would actually rely on

Post-submission plan. Ordered by what stands between Attest and a chartered
accountant putting a client's books through it.

---

## Done since this plan was written

- **§1.2 Ingest that fails loudly** — `attest/readiness.py`, 20 fixtures, 30
  tests. Encoding and BOM detection, delimiter sniffing, header aliasing,
  Indian-grouped and euro-decimal money, Excel serial dates, day-first ambiguity,
  preamble and ragged rows, coverage assessment against the declared month.
- **§1.3 Confidence tiers** — `attest/tiers.py`, invariant 11. Only PROVEN
  findings are framed as claim-ready. Verified: 15 adversarial verdicts, 0 became
  claims. The seal's canonical block went `v1 → v2` to carry the tier, and an
  unmapped class seals as UNPROVEN.
- **The chargeback scope mismatch**, found by the clean-world control — plus
  `scripts/stability.py` (20 worlds + control) and `tests/test_clean_world.py`.

Gates now: **71 verify checks, 76 tests**, 0 false positives across 20 worlds,
control signs at 0.0 bps.

## Measured, and not a problem

**Scale.** 15,148 records close in 0.02 s; 3,043 in under 0.01 s. A realistic
merchant is comfortably inside this and the cost is near-linear. No work needed —
don't spend time optimising something that isn't slow.

---

## What to add next, ranked

### 1. Continuous integration — the highest-leverage thing missing

There are 71 verify checks, 76 tests, a 20-world stability run and a clean-world
control. **None of them run unless someone remembers to run them.** Until a
machine enforces them, every invariant in `INVARIANTS.md` is aspirational.

A workflow on push and pull request that fails the build on:

- any test failure, or `verify.py` reporting a failure
- **held-out recall reaching 100%** (invariant 4 — that means a targeted detector
  was written, deliberately or by accident)
- **any false positive**, in any of the 20 worlds or in the clean control
- **false certification rate ≠ 0.0%** in `agentbench`

Those last three are the project's integrity conditions. They should break the
build, not wait to be noticed by a person who is looking at something else.

*Note the CI runner needs no credentials — everything except the live-Razorpay
check is offline and stdlib-only, which is exactly why this is cheap.*

### 2. Run it on one real month

Still the highest-value thing on the list, and **now unblocked** — the readiness
work is what made real files survivable. Escalating:

1. Your own Razorpay test account, via the existing `razorpay_test` mode.
2. A real, tiny merchant — a friend's D2C store, one month, with permission.
   Anonymise and keep it as a permanent fixture.
3. A CA with several clients. Write down every question they ask.

Quote **no accuracy figure** on real data — there is no answer key. The output is
a list of everything that broke.

### 3. Version the pack, and keep a changelog

`attest/__init__.py` is empty. Nothing anywhere records a version.

The seal currently proves *"this pack was not edited."* It cannot answer the
question an auditor actually asks about an eight-month-old pack: **"what rules
produced this?"** That answer must live inside the sealed block, not in whatever
`main` says today.

Record inside the seal: engine version, ruleset version, the contracted rates
used, and the tolerance and residual thresholds in force. Then a `CHANGELOG.md`
so that "ruleset v3" means something specific. The two are one piece of work —
a version number with nothing behind it is worse than none.

### 4. Reproducibility, as a test

Same inputs, same version → **byte-identical pack**. Never tested. For a document
whose whole value is a hash, this is foundational, and it is about twenty lines.

It also protects the seal from a subtle failure: dict ordering, locale, or a
timestamp leaking into the render would make two honest closes of the same month
disagree, and there is currently nothing that would catch it.

### 5. Give `agentbench` headroom, then run the model

Unchanged and still open. `RulesPlanner` scores 100% on all six scenarios, so the
benchmark cannot show the model adds anything. Write scenarios a fixed decision
tree should struggle with, keep ground truth out of the planner's view, then run
both planners and publish the comparison — **including if the model loses**.

That remains the honest answer to whether this is an AI product or a rules
product with a model attached.

### 6. Claim state is currently decorative

`Claim.state` declares five states — `open | evidence_ready | filed | recovered |
lapsed` — and **nothing ever writes or reads it**. Every close starts from
nothing, so a claim cannot be tracked across closes.

That is the gap between the pitch and the artifact: *"cadence is the product"*
requires a claim raised on the 3rd to still be a claim on the 10th, with its
window ticking. Persisting claim state is what turns the recovery layer from a
report into a workflow — and it is the precondition for §3.4 calibration, since
you cannot learn which claims get paid without recording which were filed.

### 7. Differential and property testing of the money math

A second, independent implementation of the fee/tax/net computation that must
agree with `money.py` on random inputs. For a system whose credibility rests on
arithmetic, two implementations disagreeing is the strongest bug detector
available — and randomised properties (no float ever reaches a money path; ₹0
takes the same path as ₹100; recoverable never exceeds total exposure; verdicts
never enter recovery) cost little and hold forever.

Hand-roll the random loop with stdlib `random` rather than adding `hypothesis` —
the zero-dependency claim is worth more than the ergonomics.

---

## Later — the product a CA actually uses

Only after a real month. Building these on untested assumptions about real data
is how you build the wrong thing carefully.

1. **Multi-client.** A CA has twenty merchants. One view across all of them,
   ranked by what is expiring.
2. **Daily cadence.** Needs §6 first — cadence without claim persistence is just
   running the same report more often.
3. **Claim filing and tracking**, with outcomes feeding calibration.
4. **Ledger export** to Tally and Zoho.
5. **Razorpay OAuth**, so nobody pastes a key.

## Operational trust

- Retention and deletion policy, and a working delete
- What is stored versus derived and discarded
- An audit log: who ran which close, against which inputs, when
- Supabase leaked-password protection on; keys rotated off any demo machine

---

## What not to do

- **Don't add ML to detection.** Rules are why a finance professional trusts the
  output. The model plans; it must never decide what is wrong.
- **Don't write a targeted detector for the missed held-out class.**
- **Don't optimise the close.** It is already fast; see *Measured, and not a
  problem*.
- **Don't chase more synthetic defect classes before one real month.**
- **Don't add dependencies.** Stdlib-only is a real selling point.
- **Don't "improve" the numbers.** If a change makes proof rate or false-match
  rate look better without the system getting better, it is the wrong change.

---

## If there is only time for two things

1. **CI**, so the integrity conditions stop depending on memory.
2. **One real month**, because every number so far describes a world we invented.

Everything else is easier once those two exist.
