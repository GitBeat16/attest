# Attest

**A finance-ops agent that reports what it can *prove*, not what it can match.**

Razorpay AI Buildathon — Track 4, AI Finance Controller.

---

## The problem with a match rate

Reconciliation software tells you what percentage of records matched. That number
is close to meaningless on its own, because **two numbers can agree for the wrong
reasons.**

Consider a settlement batch where the MDR is overcharged by ₹4.12 on one line,
and a refund in the same batch is under-deducted by ₹4.10. The batch total is off
by **two paise** — inside anyone's tolerance band. Every automated check passes.
Two real errors are now in the books, netted into invisibility.

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
naming the specific evidence that would resolve it — and a stated **unexplained
residual** in rupees.

A line counts as *proven* only when every stage holds:

```
order → payment → fee calculation → tax base → batch → bank credit → ledger
```

Break any link and the line is not proven, however well the totals agree.

## Current results

Run against the generated corpus (1,200 orders, 3,043 source records, five systems):

```
processed 3,043 records in 0.01s

BATCHES TIED (conventional check)     20/22   =  90.9%
LINES PROVEN (evidence chain)        743/1018 =  73.0%
MATCHES OVERTURNED (adversarial)      15/22   =  68.2%

where proof broke:
  mdr           225 lines    ₹121.90     fee rate + tolerance drift
  credit         32 lines    ₹38,902.15  batches that never reached the bank
  adjustment     14 lines    ₹312.84     COD short-remittance
  gst             4 lines    ₹710.59     tax computed on the wrong base

recall, held-out defect classes            75.0%   (3 of 4 found, 1 missed)
false positives                                0
```

The 18-point gap between the first two rates is the point of the project, and the
third rate is why: of the 22 claims put under test — the 20 tied batches plus two
period-level hypotheses — 15 were overturned by the adversarial pass. The held-out
recall is reported here rather than buried, because a scorecard that only grades
the defects its own author wrote detectors for proves nothing; the one missed
class is left visible for the same reason.

## Why the numbers are trustworthy

The corpus is synthetic, as the track brief specifies — which means **the design
of the dataset is part of the work.** Attest generates it in a specific order:

1. Build the **true world** — every rupee, as it should have moved.
2. **Freeze** the truth.
3. Derive the source documents from that truth.
4. Inject **labelled defects into the documents only**.

Because the truth exists before the system does, every accuracy figure is
*measured against labels the agent never sees*, rather than asserted. Twelve
defect classes are planted, including two compensating pairs that each leave the
batch tying to within two paise.

The dataset also distinguishes **defects** from **world facts** — things that make
reconciliation genuinely hard without anyone having erred, such as two identical
orders on the same day, or a refund that legitimately nets against next month's
batch. Flagging one of those counts as a **false positive**. Most reconciliation
demos have no concept of a false positive at all.

## Where AI is used, and where it is deliberately not

| Component | AI? | Why |
|---|---|---|
| Statement ingestion, narration parsing | yes | Formats drift; free-text narration is a language problem |
| Root-cause explanation | yes | Turning a ₹412 variance into a stated cause needs reasoning over context |
| Adversarial audit | yes | Forming falsification hypotheses is generative, not enumerable |
| **Match arithmetic** | **no** | Must be auditable. A probabilistic matcher cannot be trusted with money. |
| **Tolerance and threshold logic** | **no** | Deterministic by design |
| **Posting to the ledger** | **no** | Drafted for human approval, never automatic |

**The whole pipeline runs with no API key, no account and no network.** The
default reasoning engine is deterministic and produces every metric on its own;
the AI layer enriches explanations. That is a design decision, not a limitation —
for a product handling a merchant's financial records, inference you can run
locally is an architectural advantage.

## Quick start

Zero dependencies. Python 3.10+.

```bash
# generate the corpus (sources + held-out ground truth)
python -m attest.generate --out data --orders 1200

# run the pipeline
python -m attest.run --data data
```

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
chance to corrupt one. Specifically:

- creates `.env` from the template if absent, with **every value blank** — a new
  `.env` never carries a placeholder that could be mistaken for a real credential
- appends variables newly added to the template, with empty values, carrying
  their documentation comment across
- leaves every existing value byte-for-byte untouched
- **reports** variables that have left the template and **keeps** them; a
  template can be out of date, and silently deleting a working credential is a
  worse failure than carrying a stale one
- prints variable **names** only. No value is ever written to stdout, and no
  value is copied anywhere

Validation is contextual rather than global. **The core pipeline requires no
configuration at all** — no key, no account, no network — so a missing Razorpay
credential is only an error if you actually invoke the Razorpay path:

```
Cannot use 'razorpay' — 2 environment variable(s) missing.

  RAZORPAY_KEY_ID
    what it does : identifies your Razorpay account
    where to get : dashboard.razorpay.com > Settings > API Keys (Test Mode)
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

Test keys (`rzp_test_…`) and live keys (`rzp_live_…`) use the identical code
path; only the key decides which data returns.

Two things about the real API shaped the design. Amounts arrive as **integers in
paise**, which is why Attest is integer-paise throughout and no float touches the
money. And every row carries **both `settlement_id` and `settlement_utr`** — the
UTR is issued by the correspondent bank, is not a Razorpay key, and grouping on
it produces confident wrong batches. Attest groups on `settlement_id` and keeps
the UTR for the audit trail only.

Before reconciling anything, the importer audits the source data itself: whether
Razorpay's own `credit` agrees with `amount − fee − tax`, and whether any two
batches share a UTR. Both are reported rather than smoothed over.

**The synthetic corpus remains, and must.** Accuracy can only be measured against
data whose truth you constructed. On live settlements you can report that
something does not reconcile — never whether you were *right*, because nobody
knows the correct answer. Recall needs an answer key. So the live path is real
ingestion; the accuracy benchmark is synthetic by necessity.


## Repository layout

```
attest/
  money.py       integer paise arithmetic — no float touches money
  world.py       generates the true world for one merchant-month
  defects.py     injects 12 labelled defect classes into the documents only
  documents.py   renders the world into the source files a merchant receives
  generate.py    corpus CLI
  ingest.py      loading, normalisation, and key resolution
  engine.py      deterministic matching and proof-chain construction
data/
  sources/       what the agent may read
  truth/         ground truth and the defect ledger — held out
```

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
