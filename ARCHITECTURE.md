# Architecture

Attest closes one finance-ops loop — a merchant's incoming money for one month,
across five systems — and reports what it can **prove**, not what happened to
match.

This document covers how it works, where AI is and is not used, how the accuracy
figures are produced, and — separately and explicitly — what is built versus what
is roadmap.

---

## 1. The problem it addresses

Reconciliation software reports a match rate. That number is close to meaningless
on its own, for two reasons this system is built around.

**Two numbers can agree for the wrong reasons.** If MDR is overcharged by ₹4.12 on
one line and a refund in the same batch is under-deducted by ₹4.10, the batch is
out by two paise — inside anyone's tolerance. Every check passes. Two real errors
are in the books. Accounting calls these *compensating errors*, and a total-level
check cannot detect them by construction.

**Reconciling a gateway to a bank is self-referential.** Razorpay pays exactly
what its own settlement report says it will pay, so the two agree by
construction. The tie proves the money *arrived*. It proves nothing about whether
the deductions inside were *correct*.

The Track 4 brief states that the bottleneck is *verification capacity, not
generation speed*, and that *"one cherry-picked match proves nothing."* Attest
takes both literally.

---

## 2. Pipeline

```
INGEST      7 source files: settlements, bank, orders, COD remittances,
            shipments, refunds, disputes, plus the MDR tax invoice and
            contract terms
   |
NORMALISE   decimal rupee strings -> integer paise at the boundary;
            settlement rows regrouped into batches with their adjustments
   |
RESOLVE     bank credit -> settlement_id via (value_date, amount).
            The UTR is retained for audit and used for nothing else.
            Ambiguity escalates; it is never broken by guessing.
   |
MATCH       does each batch total agree with its bank credit?
            Reported for contrast, never trusted.
   |
PROVE       per line: order -> mdr -> gst -> net -> credit.
            A line is proven only if every stage holds.
   |
ATTACK      six falsification hypotheses against every surviving match
   |
CLASSIFY    typed exceptions, each with the evidence that would close it
   |
RECOVER     counterparty, claim window, deadline, days remaining
   |
SCORE       against held-out ground truth the pipeline never reads
   |
ATTEST      close pack + exception register + unexplained residual,
            or a refusal to sign
```

### Key resolution

A bank statement does not contain Razorpay's `settlement_id`. It contains a UTR
issued by the correspondent bank. The UTR *looks* like a key and is not one —
matching on it produces confident wrong answers, which is worse than producing
none.

Resolution therefore matches on `(value_date, amount)` against the settlement
report, then carries `settlement_id` forward as the authoritative identity. The
implementation also includes `resolve_naive()`, which keys on narration, purely
so the report can show what that approach costs rather than merely asserting it.

### Proof chains

Each settlement line carries five checks; each COD remittance line carries six.
The chain records which stage broke and by how much, so an exception is never
just "unmatched" — it names the missing link and its rupee value.

### The adversarial pass

Six hypotheses, each trying to falsify a match rather than confirm it:

| Hypothesis | What it looks for |
|---|---|
| `self_referential_tie` | Batch agrees with the bank while its lines disagree with the contract |
| `offsetting_pair` | Two variances of opposite sign that cancel inside tolerance |
| `tolerance_abuse` | Sub-tolerance residuals that all point the same way |
| `coincidental_equality` | Amounts agree while dates do not |
| `ambiguous_collapse` | One candidate chosen where several were plausible |
| `key_substitution` | A match resting on the UTR rather than `settlement_id` |

`offsetting_pair` is the one that matters most, and it is deliberately built to
avoid a trap. It cannot use the settlement report's own refund line to detect a
refund error — that would let the error cancel itself out of the test designed to
find it. Instead it rebuilds the expected refund deduction independently, from the
merchant's own refund export plus the netting rule in the contract, and compares.

---

## 3. Data model

Money is **integer paise throughout**. No float touches the arithmetic.
Reconciliation lives or dies on sub-rupee drift, and a float pipeline manufactures
the very variances the system exists to detect.

| Module | Responsibility |
|---|---|
| `money.py` | Paise arithmetic, Indian-format rendering, percentage with explicit rounding |
| `world.py` | Generates the true world for one merchant-month |
| `defects.py` | Injects labelled defects into documents only |
| `documents.py` | Renders a world into the files a merchant actually receives |
| `generate.py` | Corpus CLI |
| `ingest.py` | Loading, normalisation, key resolution |
| `engine.py` | Deterministic matching, proof chains, batch-level integrity checks |
| `audit.py` | The adversarial pass |
| `recovery.py` | Counterparty, claim window, deadline, state |
| `score.py` | Accuracy against held-out ground truth |
| `report.py` | Self-contained HTML close pack |
| `run.py` | Orchestration and the attestation |

---

## 4. Where AI is used, and where it is deliberately not

| Component | AI? | Reasoning |
|---|---|---|
| Statement ingestion, narration parsing | yes | Formats drift; free-text bank narration is a language problem |
| Root-cause explanation | yes | Turning a ₹412 variance into a stated cause requires reasoning over context |
| Adversarial hypothesis forming | yes | Generative, not enumerable |
| Claim and journal drafting | yes | The output is a document a human will send |
| **Match arithmetic** | **no** | Must be auditable. A probabilistic matcher cannot be trusted with money. |
| **Tolerance and thresholds** | **no** | Deterministic by design |
| **Posting to a ledger** | **no** | Drafted for human approval, never automatic |

**The entire pipeline runs with no API key, no account and no network.** The
default reasoning engine is deterministic and produces every metric on its own;
the AI layer enriches explanations. For a system handling a merchant's financial
records, inference that can run locally is an architectural advantage rather than
a compromise.

---

## 5. How the accuracy figures are produced

This is the part that makes every other number trustworthy.

The corpus is generated **truth-first**:

1. Build the true world — every rupee, as it should have moved.
2. **Freeze** it.
3. Derive the source documents from that truth.
4. Inject labelled defects into the **documents only**.

Ground truth exists before the system does, and lives in `data/truth/`, which no
module except the scorer reads. Accuracy is therefore *measured against labels the
pipeline never sees*, not asserted.

Three categories are scored, and the distinction is the point:

**Designed-for classes** — planted by the same author who wrote the detectors.
Finding them proves very little on its own, so this number is reported separately
and never as the headline.

**Held-out classes** — four defect types injected with **no detector written for
them**: a payment settled twice, an AWB remitted twice, a bank credit no
settlement explains, and a settlement dated outside the period. Three are caught
by generic integrity checks that were not aimed at them; one is missed and
reported as missed. **This is the honest number.**

**World facts** — things that make reconciliation genuinely hard without anyone
having erred: refunds that legitimately net into next month's batch, and two
identical orders on the same day. These are labelled as expected *non-errors*, and
flagging one counts as a **false positive**. Most reconciliation demos have no
concept of a false positive at all.

---

## 6. Failure handling

| Failure | Behaviour |
|---|---|
| Statement schema drifts | Halt that source and flag it. Never guess a column mapping — silently wrong finance data is worse than none. |
| Ambiguous match candidates | Escalate with all candidates shown; counted as an escalation, not a match |
| Adversarial pass overturns a match | Match reverts to proposed and moves to the exception register with the attack's reasoning attached |
| A record dated outside the period | The period is anchored to the declared invoice month, not to `max(settled_on)`. A stray record must not be able to redefine the month being closed. |
| Residual exceeds 25 bps | The close is **not signed**. Refusing to certify is the point of an attestation. |

---

## 7. What is built, and what is not

Stated plainly, because a roadmap presented as a feature is a lie.

**Built and working:**
corpus generator with 14 labelled defect classes · ingestion and key resolution
across 7 sources · deterministic match engine · proof-chain construction ·
adversarial auditor with 6 hypotheses · typed exception register ranked by
exposure · deadline-aware recovery layer · accuracy scoring against held-out
ground truth · self-contained HTML close pack · attestation with a refusal path.

**Not built — roadmap:**

1. **Connectors.** OAuth into Razorpay; bank via statement upload or account
   aggregator; courier and marketplace accounts by API. Currently file-based.
2. **Continuous ingest.** Settlement webhooks on arrival, bank pulled daily,
   courier remittances weekly — replacing the batch run.
3. **Multi-tenancy.** Per-merchant isolation, and a CA-firm view across clients.
4. **Claim filing.** The system drafts claims; it does not yet file or track them.
5. **Ledger export.** Journal entries to Tally or Zoho.
6. **Learning loop.** Which claim types actually get paid, feeding exception
   ranking.

**The one architectural change that matters most** is (2). Almost every finding
here is recoverable only inside a deadline — courier disputes close in 14 days. A
month-end close surfaces a day-8 finding on day 31, by which point the money is
gone. The system already computes this cost: on the sample corpus, closing monthly
instead of daily lets **₹5,365 across 16 claims expire unfiled**. Running daily is
not a performance detail; it is the difference between finding money and
recovering it.

---

## 8. Honest limitations

- **The corpus is deliberately defect-dense.** The false-match rate reflects a
  stress-test dataset, not a real-world error rate.
- **Designed-for recall is circular** and is reported separately for that reason.
  The held-out figure is the one that carries information.
- **No real merchant data has been tested.** Every figure comes from a seeded
  synthetic corpus, reproducible with
  `python -m attest.generate --out data --orders 1200`.
- **Razorpay's live APIs are not called.** The track brief specifies synthetic
  data; the settlement schema is modelled on the documented export format, but no
  live integration has been exercised.
- **The AI layer is optional and off by default.** All figures in this repository
  were produced by the deterministic engine.

---

## 9. Reproducing every number

```bash
python -m attest.generate --out data --orders 1200
python -m attest.run --data data --html docs/index.html
```

The corpus is seeded, so the figures are identical on any machine. No
dependencies, no network, no key.
