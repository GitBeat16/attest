# From hackathon project to a tool a CA would actually rely on

Post-submission plan. Ordered by what stands between Attest and a chartered
accountant putting a client's books through it.

---

## The honest starting position

Three things are true, and the plan follows from them.

**The engine is ahead of the product.** The matching, the proof chains, the
adversarial pass and the money arithmetic are stronger than the surface around
them. The thing that breaks first with a real user is not the analysis — it is
reading their files.

**Everything measured so far was measured against a world we generated.** The
73.0%, the 75% held-out recall, the zero false positives across twenty seeds, the
₹87,256.57 — all of it is honest, all of it is synthetic. Not one number has been
tested against data Attest did not create. That is the gap, and no amount of
additional synthetic work closes it.

**The architecture proves the AI cannot do harm. Nothing yet proves it does
good.** `certify` is absent from the action tuple, the validator refuses it, a
test fails the build if a writing tool appears — that is a genuinely strong
safety story. But `agentbench` scores the *deterministic* planner at 100% on path
selection, verdict and escalation, with 0% false certification, and it has never
been run against a model at all. A benchmark the non-AI baseline saturates cannot
show the AI is worth having. Right now, on the evidence, the model is decoration
with excellent seatbelts.

For a product whose entire claim is trustworthiness, that third point is not a
side issue. It is the one a serious reviewer will find.

---

## The failure modes to design against

Trust is asymmetric. Rank the work by what would end it.

| Failure | Consequence | Status |
|---|---|---|
| **A confident false positive on real data** | The CA files a claim, Razorpay says the charge was correct, the CA looks foolish in front of their client. They never open it again. | **Fatal. Undefended on real data.** |
| Overstating recoverable money | Same, slower. The one direction a finance tool must never err in. | Defended (invariant 9) |
| Silent partial ingest | Closes on the 80% of rows it could parse and reports a clean month. | **Undefended** |
| Missing a real error | Value not delivered; rarely noticed | Measured, synthetic only |
| Leaking client financial data | Ends the company | Partly defended (RLS, no secrets) |

Everything in Phase 1 exists to defend the first and third rows.

---

## Phase 1 · Earn the right to be trusted on real data

### 1.1 One real month — the highest-value thing on this list

One real merchant month teaches more than another twenty synthetic ones. It is
the only way to discover what the generator does not know how to be wrong about.

Escalating in cost, each worth doing:

1. **Your own Razorpay test account.** `api/close.py` already has a
   `razorpay_test` mode that pulls live settlements. Run it end-to-end, write up
   what it found, and quote **no accuracy figure** — there is no answer key.
2. **A real, tiny merchant.** A friend's D2C store, one month, with permission.
   Anonymise and keep it as a permanent fixture.
3. **A CA with several clients.** The actual buyer. Sit with them while they run
   it and write down every question they ask.

The output is not a number. It is a list of everything that broke.

### 1.2 Ingest that fails loudly

Real exports differ from generated ones in every boring way: renamed columns,
`DD/MM/YYYY`, BOM-prefixed headers, `₹1,23,456.78` as text, half-months, refunds
in a separate export with a different key.

The rule to build toward: **never close on data you could not fully read.** A
close over 80% of the rows, reported as if it were the month, is the silent
partial ingest failure — the most dangerous thing in the table above, because
it produces a confident clean result.

- A pre-close **readiness report**: rows read, rows rejected, columns not
  recognised, date range actually covered vs the month declared
- Refuse, or mark the close explicitly partial. Never silently proceed
- Column aliasing with an explicit map, not guessing
- A fixture per real-world format oddity, added as each is found

### 1.3 Confidence tiers — findings do not all mean the same thing

There is currently no confidence or severity concept in the engine. Every
exception arrives with equal epistemic weight, and that is precisely how a CA
files a claim that gets rejected.

Three tiers, carried through the register, the recovery list and the pack:

- **Proven wrong** — the arithmetic disagrees with the contract. `mdr`, `gst`,
  `net`. Recomputable, defensible in front of a counterparty. File these.
- **Cannot prove** — a link is missing. The money may be perfectly correct;
  the evidence is absent. Ask, do not accuse.
- **Needs your input** — depends on a fact Attest cannot see: an off-system
  agreement, a negotiated rate, a credit note.

Only the first tier should carry a claim-ready framing. Today the register ranks
by exposure alone, which puts the largest *unproven* item at the top of the list
a CA is most likely to act on.

### 1.4 Version the rules inside the close pack

An auditor's question about a pack from eight months ago is *"what rules produced
this?"* — and the answer must be in the pack, not in whatever `main` says today.

Record engine version, rule-set version, contracted rates used and tolerance
thresholds, inside the sealed document. Cheap, and it converts the seal from
"this was not edited" into "this was not edited, and here is exactly what
produced it."

---

## Phase 2 · Make the AI earn its place

The safety architecture is finished. The value case has not started.

### 2.1 Give the benchmark headroom

`agentbench` has six scenarios and the rules planner solves all six. Until there
are scenarios it *fails*, no result can distinguish a good controller from a good
rule set.

Write scenarios where a fixed decision tree should struggle and judgement should
win: contradictory evidence pointing two ways; a defect class the rules do not
enumerate; an investigation where the informative next question depends on what
the last three answers were; a month where the correct action is to stop early
and ask a human.

**Keep the ground truth out of the planner's view, as it already is.**

### 2.2 Then actually run the model against it

`run_all(planner_factory)` is already parameterised and has never been called
with a model. Run both planners over the same scenarios and publish the
comparison — including if the model loses.

### 2.3 Cut the wasted calls

41.3% unnecessary tool calls. On a serverless budget where the model gets a
13-second slice, wasted calls are the difference between finishing and handing
over.

### 2.4 Be willing to conclude the model does not help

If, with a benchmark that has real headroom, the deterministic planner still
wins — say so, in the README, and keep the model for explanation rather than
planning. That is a *better* outcome for a finance product than an AI that plans
because AI is the category. It also happens to be the most credible sentence in
this whole document.

---

## Phase 3 · Trust engineering that compounds

Cheap, permanent, and it is the work that lets you change things later without
fear.

- **Differential testing of the money math.** A second, independent
  implementation of the fee/tax/net computation that must agree with `money.py`
  on random inputs. For a system whose credibility rests on arithmetic, two
  implementations disagreeing is the strongest bug detector available.
- **Property-based tests** on the invariants: no float ever reaches a money path;
  a ₹0 value takes the same path as ₹100; recoverable never exceeds total
  exposure; verdicts never enter recovery.
- **CI gates** from what already exists: `stability.py` must show 0 false
  positives in every world and in the clean control, and held-out recall must
  **not** reach 100%. Fail the build rather than wait to notice.
- **Calibration, once real claims exist.** Of the claims Attest called
  recoverable, what fraction were actually paid? That number — not proof rate —
  is what a CA will eventually judge it on.

---

## Phase 4 · The product a CA actually uses

Only after Phase 1. Building these first means building them on assumptions
about real data that have not been tested.

1. **Multi-client.** A CA has twenty merchants. One close per merchant, one view
   across all of them, ranked by what is expiring.
2. **Daily cadence.** The pitch says *"cadence is the product"* and the tool runs
   monthly. That gap between claim and artifact is the most honest thing left to
   close.
3. **Claim filing and tracking**, with outcomes fed back into 3.4 calibration.
4. **Ledger export** to Tally and Zoho.
5. **Razorpay OAuth**, so nobody pastes a key.

---

## Phase 5 · Operational trust

Boring, and non-negotiable the moment someone else's financial data is involved.

- A written **retention and deletion** policy, and a working delete
- **PII discipline** — what is stored, what is derived and discarded
- An **audit log**: who ran which close, against which inputs, when
- **Reproducibility**: same inputs, same engine version, byte-identical pack.
  This is testable and should be tested.
- Supabase leaked-password protection on; keys rotated off any machine that has
  seen a demo

---

## What not to do

- **Don't add ML to detection.** Rules are the reason a finance professional
  trusts the output. The model plans; it must never decide what is wrong.
- **Don't write a targeted detector for the missed held-out class.** The visible
  miss is what makes the other numbers believable.
- **Don't chase more synthetic defect classes before one real month.** You would
  be getting better at a world you invented.
- **Don't build multi-tenancy before there is one real user.**
- **Don't add dependencies.** Stdlib-only is a real selling point for a tool a CA
  runs on their own laptop.
- **Don't "improve" the numbers.** If a change makes proof rate or false-match
  rate look better without the system getting better, it is the wrong change.

---

## If there is only time for three things

1. **Run it on one real month** and write down everything that broke. (1.1)
2. **Make ingest refuse to close on data it could not fully read.** (1.2)
3. **Give `agentbench` scenarios the rules planner fails**, then run the model
   against them and publish the result either way. (2.1, 2.2)

The first two decide whether it is a product. The third decides whether it is
honestly an AI product.
