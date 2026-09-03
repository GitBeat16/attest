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
