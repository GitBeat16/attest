# Attest

**Razorpay tells you what happened. Attest proves whether it should have.**

Razorpay AI Buildathon — Track 4, AI Finance Controller.

---

## See it in thirty seconds

**Live:** [attest-nu.vercel.app](https://attest-nu.vercel.app) → press **Run the
demo close**. No account, no credentials, no setup. You will see one settlement
batch that agrees with the bank **exactly** — ₹0.00 variance, every automated
check passing — and **₹8,495.80** of real error sitting inside it.

```
                          traditional             Attest
  Settlement           ₹19,40,799.80      fee overcharged      ₹3,600.00
  Bank credit          ₹19,40,799.80      GST on that            ₹648.00
  ─────────────────────────────────      refund under-deducted ₹4,247.80
  Variance                     ₹0.00      ─────────────────────────────
  ✓ Reconciled                            actually wrong by    ₹8,495.80
```

**Understated 42,479×.** That gap is the entire reason this project exists.

Locally, the same thing in one command:

```bash
python3 -m attest.demo
```

## The problem with a match rate

Reconciliation software tells you what percentage of records matched. That number
is close to meaningless on its own, because **two numbers can agree for the wrong
reasons.**

In the batch above, a gateway fee was charged at 2.18% against a contracted 2.00%
— ₹4,248.00 too much including GST. In the same batch, a ₹12,000 refund was
deducted ₹4,247.80 short. The two errors point in opposite directions and cancel
to twenty paise. Every total-level check passes. Two real errors are now in the
books, netted into invisibility.

Accounting calls these *compensating errors*. A total-level check cannot detect
them, by construction. Neither can a match rate.

The Track 4 brief says the bottleneck is **verification capacity, not generation
speed**, and that *"one cherry-picked match proves nothing."* Attest takes both
statements literally.

## What it does instead

Attest closes one finance-ops loop — a merchant's incoming money for one month,
across five systems — and reports three numbers instead of one:

| Metric | Question it answers |
|---|---|
| **Match rate** | Do the totals agree? *(what everyone reports)* |
| **Proof rate** | Can every line be traced end to end through an unbroken evidence chain? |
| **False-match rate** | How many of those matches did an adversarial pass overturn? |

Plus an honest exception register — typed, ranked by rupee exposure, each entry
naming the counterparty it is claimable from, the evidence that would settle it,
and the date the claim window closes — and a stated **unexplained residual** in
rupees.

A line counts as *proven* only when every stage holds:

```
order → payment → fee calculation → tax base → batch → bank credit → ledger
```

Break any link and the line is not proven, however well the totals agree.

## Current results

Run against the generated benchmark corpus (1,200 orders, 3,043 source records,
seven systems):

```
processed 3,043 records in 0.02s

BATCHES TIED (conventional check)     20/22   =  90.9%
LINES PROVEN (evidence chain)        743/1018 =  73.0%
MATCHES OVERTURNED (adversarial)      15/22   =  68.2%

recall, held-out defect classes            75.0%   (3 of 4 found, 1 missed)
false positives                                0
recoverable                           ₹87,256.57   across 283 items
cost of closing monthly                ₹5,365.00   (16 claims expire unfiled)
unexplained residual                  ₹46,617.32   (254.5 bps)  -> NOT ATTESTABLE
```

The 18-point gap between the first two rates is the point of the project, and the
third rate is why: of the 22 claims put under test, 15 were overturned by the
adversarial pass. The held-out recall is reported here rather than buried,
because a scorecard that only grades the defects its own author wrote detectors
for proves nothing; the one missed class is left visible for the same reason.

**The close is refused.** ₹46,617.32 could not be attributed to any cause, which
is 254.5 bps against a 25 bps limit, so the status is `NOT ATTESTABLE`. Refusing
to certify is the point of an attestation — a system that always signs is not
attesting to anything.

## Why the numbers are trustworthy

The benchmark corpus is synthetic, as the track brief specifies — which means
**the design of the dataset is part of the work.** Attest generates it in a
specific order:

1. Build the **true world** — every rupee, as it should have moved.
2. **Freeze** the truth.
3. Derive the source documents from that truth.
4. Inject **labelled defects into the documents only**.

Because the truth exists before the system does, every accuracy figure is
*measured against labels the agent never sees*, rather than asserted.

The dataset also distinguishes **defects** from **world facts** — things that
make reconciliation genuinely hard without anyone having erred, such as two
identical orders on the same day, or a refund that legitimately nets against next
month's batch. Flagging one of those counts as a **false positive**. Most
reconciliation demos have no concept of a false positive at all.

**On your own data, no accuracy figure is quoted at all.** There is no answer key
for a real month; a tool that quotes you recall on your own data is quoting a
number it cannot have measured. The close pack says so on its face.

## The AI Finance Controller

The AI does not compute a number here. It decides **what to investigate next**.

```
        AI CONTROLLER          "what should I look at?"      chooses actions
              |
        DETERMINISTIC TOOLS    "what did the evidence show?"  returns facts
              |
        EVIDENCE               ids, amounts in paise, provenance
              |
        POLICY ENGINE          "what may be certified?"       returns a verdict
              |
      CERTIFIABLE  /  NOT ATTESTABLE  /  HUMAN REVIEW
```

Run it:

```bash
python3 -m attest.controller --demo            # investigate the demo month
python3 -m attest.agentbench                   # benchmark the controller
```

On the demo month the controller chooses this path on its own — nothing below is
scripted, and removing the fee discrepancy changes the route:

```
 →  inspect_close_state        2 of 3 batches tie; 40 of 159 lines proven
 →  list_exceptions            rank by exposure, investigate the largest first
 →  check_fee_contract         100 lines off-contract, ₹3,600.00 over
 →  check_refund_netting       a tie plus a fee error means something absorbed it
 →  find_compensating_errors   confirmed: they cancel to ₹0.20
 →  run_adversarial_check      2 of 4 passing claims overturned
 →  find_unexplained_residual  ₹4,30,592.40 unattributed
 ●  conclude

    POLICY:  NOT_ATTESTABLE — 1414.34 bps against a 25 bps limit
```

**What the controller may not do.** There is no action that certifies — asking
for one is rejected, and so is `sign`, `approve` or `attest`. It cannot widen a
threshold, cite an evidence id no tool returned, or put a number into a finding.
Budgets stop it; an exhausted budget escalates rather than guessing. Every one of
those is a test in `tests/test_controller.py`, driven by a scripted hostile
planner.

**Two planners, one safety envelope.** A `ModelPlanner` emits the action schema
from an LLM; a `RulesPlanner` emits the same schema deterministically. Both go
through the same validator, tools and policy gate, so the safety properties do
not depend on which is driving — and the demo needs no API key. The interface
says which one ran, because claiming "AI" while a rules planner drives would be
the exact overclaim this project argues against.

| Metric (6 scenarios, `python3 -m attest.agentbench`) | |
|---|---|
| False certification rate | **0%** |
| Correct escalation rate | 100% |
| Correct tool selection | 100% |
| AI-assisted safe resolution rate | 100% |
| Unnecessary tool calls | 41.3% |
| Average steps / tool calls | 8.7 / 7.7 |

Six scenarios is a small sample and the perfect scores should be read that way.
The unnecessary-call rate is the honest weak spot: the deterministic planner
sweeps more broadly than it needs to, and it is reported rather than tuned away.

## Where AI is used, and where it is deliberately not

| Component | AI? | Why |
|---|---|---|
| Root-cause explanation | yes | Turning a ₹3,600 variance into a stated cause needs reasoning over context |
| Narration parsing | yes | Bank statement formats drift; free text is a language problem |
| **Match arithmetic** | **no** | Must be auditable. A probabilistic matcher cannot be trusted with money. |
| **Tolerance and threshold logic** | **no** | Deterministic by design |
| **Posting to the ledger** | **no** | Drafted for human approval, never automatic |

The division is visible in the interface. Every explanation is labelled either
**Verified by Attest engine** (deterministic, produced by the same code that
computed the figures) or **AI explanation** (written by a model from figures the
engine had already published). The model is handed a nine-field allowlist and no
source documents, so it has nothing to hallucinate a number from.

**The whole pipeline runs with no API key, no account and no network.** With no
model configured, explanations fall back to the deterministic path and nothing
else changes. That is a design decision, not a limitation — for a product
handling a merchant's financial records, inference you can run locally is an
architectural advantage.

## A close pack you cannot quietly edit

Every close pack carries a SHA-256 digest of the **entire rendered document**,
printed on its face:

```bash
python3 -m attest.seal --verify web/close-pack.html
```

`INTACT` means nobody has changed a figure since it was sealed. Change one rupee,
one date, or one letter of the business name and it reads `ALTERED`. The
benchmark pack is byte-reproducible, so the same inputs always produce the same
digest.

A digest printed inside the file it covers proves integrity, not authorship —
which is why **tamper-evident** is the honest word and tamper-proof would be a
lie. Authorship comes from `attest_seals`, a table with insert and select
policies and deliberately **no update and no delete policy**: a merchant can add
a seal, and nobody can alter one afterwards, including us.

## Quick start

Zero runtime dependencies. Python 3.10+.

```bash
# the compensating-error demo, start to finish
python3 -m attest.demo

# generate the benchmark corpus (sources + held-out ground truth)
python3 -m attest.generate --out data --orders 1200

# close it, and write the pack the site links to
python3 -m attest.run --data data --html web/close-pack.html

# check the seal on what that produced
python3 -m attest.seal --verify web/close-pack.html
```

### One command that checks the whole thing

```bash
python3 scripts/verify.py
```

Fifty checks. It regenerates the corpus, runs the pipeline, exercises both API
endpoints and every failure path, and asserts the three numbers that carry the
argument — proof rate **below** match rate, held-out recall **strictly between 0
and 100%**, false positives **at zero**. Those are integrity checks, not smoke
tests: a change that improves every other number while pushing held-out recall to
100% has broken the project, and this command fails on it.

Money arithmetic has its own suite, runnable without pytest:

```bash
python3 tests/test_money_edges.py
```

## Running against real Razorpay data

`attest/sources/razorpay_api.py` calls the live Settlement Recon API over HTTP
Basic auth, using only the standard library:

```
GET https://api.razorpay.com/v1/settlements/recon/combined?year=YYYY&month=MM
```

```bash
python3 -m attest.connect --year 2026 --month 8 --ping   # check credentials
python3 -m attest.connect --year 2026 --month 8          # pull and audit a month
```

Two things about the real API shaped the design. Amounts arrive as **integers in
paise**, which is why Attest is integer-paise throughout and no float touches the
money. And every row carries **both `settlement_id` and `settlement_utr`** — the
UTR is issued by the correspondent bank, is not a Razorpay key, and grouping on
it produces confident wrong batches. Attest groups on `settlement_id` and keeps
the UTR for the audit trail only.

Before reconciling anything, the importer audits the source data itself: whether
Razorpay's own `credit` agrees with `amount − fee − tax`, and whether any two
batches share a UTR. Both are reported rather than smoothed over.

**Razorpay cannot prove Razorpay.** Connect a Razorpay key and supply nothing
else and both rates are 0% — the report and the payment agree by construction, so
that agreement carries no information. Evidence has to come from somewhere that
is not the party being checked.

## Security posture

| | |
|---|---|
| **Razorpay secrets** | Never stored. The hosted app refuses `rzp_live_…` outright; a test key is used for one request and discarded. The database holds a masked key id and nothing else. |
| **The serverless function** | Holds no credential of its own — no service key, no admin role, no database password. Every write goes to PostgREST bearing the caller's own token, so it can never touch a row its caller could not. If `api/close.py` leaked in full it would grant an attacker nothing. |
| **Data isolation** | Row-level security, tested rather than assumed: as the owner 1 row visible, as another signed-in user 0, anonymous 0, and a forged insert claiming another user's id rejected. |
| **Accounts** | Optional. The demo and the full reconciliation path work signed out; an account only keeps your closes. |
| **AI prompts** | A nine-field allowlist of already-published figures. No source document, key or statement line can reach a model. |

## Two financial bugs found during the audit

Both are the quiet kind — wrong numbers rather than crashes — and both are worth
reading if you are assessing whether this was built carefully.

**A legitimate zero treated as missing.** `ReconRow.net` read
`self.credit if self.credit else (amount - fee - tax)`. A credit of exactly zero
— a fully refunded payment, an adjustment that nets out — is falsy, so it fell
through to a *computed* figure, silently replacing what Razorpay reported with
something else. `net_disagrees_by` had the same shape, so a reported zero that
disagreed was reported as agreeing: the check failed precisely where it mattered
most. `credit` is now `int | None`, absence and zero are distinct, and
`tests/test_money_edges.py` covers it.

**Adversarial verdicts counted as recoverable money.** The exception register
mixes broken proof chains, batch findings and verdicts from the adversarial pass.
An "offsetting pair" is the *same rupees* as the MDR chain break and the refund
variance that compose it — and all three were being summed into the recoverable
total, overstating the benchmark by ₹846.90 and the demo by roughly ₹17,000.
Verdicts are now tagged, excluded from recovery, and shown separately. Overstating
what a merchant can recover is the one direction a finance tool must never err in.

## Repository layout

```
attest/
  money.py       integer paise arithmetic — no float touches money
  world.py       generates the true world for one merchant-month
  defects.py     injects labelled defect classes into the documents only
  documents.py   renders the world into the source files a merchant receives
  generate.py    corpus CLI
  demo.py        the deterministic compensating-error scenario
  ingest.py      loading, normalisation, and key resolution
  engine.py      deterministic matching and proof-chain construction
  audit.py       six falsification hypotheses attacking every match that passed
  score.py       accuracy against held-out ground truth
  recovery.py    counterparties, claim windows and the clock on each one
  close.py       closing a merchant's own month, where no answer key exists
  report.py      the self-contained close pack
  seal.py        tamper-evidence, and a CLI to check it
  engines.py     the reasoning layer — rules, Gemini, OpenAI, Ollama
  config.py      contextual capability checks that never print a secret
  connect.py     the live Razorpay CLI
  sources/       the Razorpay Settlement Recon API client
api/
  close.py       POST /api/close  — run a close; demo mode needs no account
  explain.py     POST /api/explain — put a finding into words
web/             the landing page, the app, and a rendered close pack
brand/           the mark, in every form it is used
scripts/         verify.py, and environment tooling
tests/           money edge cases: zero, refunds, rounding, large amounts
data/            generated; sources/ is readable, truth/ is held out
```

Further reading: [`ARCHITECTURE.md`](ARCHITECTURE.md) for how it works,
[`USE-CASES.md`](USE-CASES.md) for what four different merchants actually do with
it, and [`CLAUDE.md`](CLAUDE.md) for the invariants that must not be broken.

## Environment and secrets

`.env.example` is committed and is the source of truth for **which** variables
exist. `.env` holds the real values, is gitignored, and is never committed.

```bash
python3 scripts/sync_env.py           # bring .env in step with the template
python3 scripts/sync_env.py --check   # report drift only, change nothing (CI)
python3 -m attest.config              # what is configured, without secrets
```

The sync tool is **append-only by design**. It never rewrites, reorders or
reformats `.env`, because that file holds live credentials and every rewrite is a
chance to corrupt one. It creates `.env` with every value blank, appends newly
added variables, leaves existing values byte-for-byte untouched, keeps variables
that have left the template rather than deleting a working credential, and prints
variable **names** only — no value is ever written to stdout.

Validation is contextual. **The core pipeline requires no configuration at all**,
so a missing Razorpay credential is only an error if you invoke the Razorpay path.

## A note on `settlement_id`

A bank statement does not contain Razorpay's `settlement_id`. It contains a UTR
issued by the correspondent bank. The UTR *looks* like a key and is not one —
matching on it produces confident wrong answers, which is worse than producing
none.

Attest resolves bank credits to batches on `(value_date, amount)` and then carries
`settlement_id` forward as the authoritative identity. Where two batches could
both explain a credit, it escalates rather than picking one.

## Licence

MIT
