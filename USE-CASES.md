# Who uses Attest, and what actually happens

Written from the user's side of the screen. Every number quoted here is produced
by `python3 scripts/verify.py` against the benchmark corpus, not estimated.

---

## Case 1 — Priya, D2C apparel, ₹18 lakh a month on Razorpay

**Her situation.** She runs a Shopify store with COD on about a third of orders.
Her CA closes the books monthly. Razorpay's dashboard says everything settled,
her bank shows the credits, and her CA has never raised a reconciliation problem.
She has no reason to think anything is wrong, which is exactly the point.

**What she does.**

1. Razorpay Dashboard → Settlements → Reports → downloads the recon report for
   August.
2. Net banking → statement for August → export as CSV.
3. Opens the app, drags both files onto the page, types her business name, picks
   August.

**What she gets, about a second later.**

| | |
|---|---|
| Match rate | 90.9% — 20 of 22 batches tie to a bank credit |
| Proof rate | 0.0% |
| Recoverable | itemised, with a deadline on each claim |

**The proof rate is zero and that is not a malfunction.** With only settlements
and a bank statement, no line can be traced back to what the customer was
actually charged, because she has not supplied the order export. The app says
so, in those words. She adds `orders.csv` and `refunds.csv` from Shopify and
re-runs: proof rate 41.5%. Add the courier's COD remittance file and the
shipment export: 73.0%.

**What she does about it.** The exception register is ranked by rupee exposure.
Top of her list is COD short-remittance — the courier deducted more than the
contracted 1.5% on a batch of orders — with the counterparty, the evidence that
would settle it, and **the date the claim expires**. Courier disputes die in 14
days. Because her CA closes monthly, most of them were already dead before
anyone looked.

> On the benchmark month, closing monthly instead of daily lets **₹5,365 across
> 16 claims expire unfiled.**

---

## Case 2 — Ramesh, a chartered accountant with 20 client merchants

**His situation.** He is the actual buyer. His clients never feel reconciliation
pain; he absorbs it, twenty times a month, in Tally. For each client he is
looking for one thing: can he sign this month off?

**What he does.** Runs the CLI on his own laptop, one client at a time, from a
folder of their exports:

```bash
python3 -m attest.run --data ./clients/acme --html acme-aug.html
```

**What he gets.** A self-contained close pack — one HTML file, no server, no
dependencies, opens in any browser — ending in a verdict rather than a summary:

```
close status             NOT ATTESTABLE
reason                   residual exceeds the 25 bps limit; the close is
                         not certified until it is investigated.
```

**Why the refusal is the feature.** A system that always signs is not attesting
to anything. ₹46,617 of ₹18,31,760 could not be attributed to any cause — 254.5
basis points against a 25 bps limit — so the close stays open. He now has a
specific list of what to chase rather than a vague feeling that the numbers look
about right.

**What he hands over.** The pack carries a SHA-256 digest of the whole rendered
document. His client, their lender, or a tax officer can check it:

```bash
python3 -m attest.seal --verify acme-aug.html
```

`INTACT` means nobody has touched a figure since he sealed it. Change one rupee
and it reads `ALTERED`.

---

## Case 3 — Nikhil, finance lead at a ₹40 crore marketplace seller

**His situation.** Three sales channels, two couriers, marketplace payouts, and
a two-person team burning 20–50 hours a month across five systems. He already
has a reconciliation process. What he does not have is a way to tell whether it
works.

**What he uses.** The three rates, tracked month over month. The match rate is
the number his existing tooling reports and it is close to useless — it sits
above 90% whatever happens. The **false-match rate** is the one he watches: of
the batches that passed, how many did the adversarial pass overturn? On the
benchmark month, 15 of 22.

**The case that sold him.** One batch was out by ₹0.02 — inside any tolerance
anyone would set. Inside it: a gateway fee overcharged by ₹4.12 and a refund
under-deducted by ₹4.10. Two real errors, netting to nothing, invisible to every
total-level check ever written. Accounting calls these *compensating errors*, and
a match rate cannot detect them by construction.

Attest finds them by rebuilding what the refund deduction **should** have been
from the merchant's own refund records and the netting rule in the contract —
never from the settlement report it is auditing. Using the audited document to
check itself is how the error cancels out of the very test built to find it.

---

## Case 4 — the merchant who connects Razorpay and nothing else

Worth stating because it is the most instructive result in the project.

Connect a Razorpay test key, supply no bank statement, no orders, nothing else:

```
match rate   0.0%
proof rate   0.0%
```

**Razorpay cannot prove Razorpay.** Razorpay pays exactly what its own settlement
report specifies, so the report and the payment agree by construction — that
agreement carries no information. Evidence has to come from somewhere that is not
the party being checked. Any reconciliation product that connects to one gateway
and reports a green tick is reporting a tautology.

---

## What Attest refuses to do

| It will not | Because |
|---|---|
| Store your Razorpay secret key | A live key can create refunds. The hosted app refuses live keys outright and discards test keys after one request. |
| Quote you an accuracy figure on your own data | There is no answer key for a real month. Recall is measured once, on a corpus whose truth was constructed, and that figure is not transferred to your close pack. |
| Post anything to your ledger | Every claim is drafted for a human to approve. |
| Guess when two batches could both explain a credit | It escalates instead. A confident wrong answer is worse than no answer. |
| Sign a close it cannot support | Residual above 25 bps means NOT ATTESTABLE, however good the other numbers look. |
| Hide what it missed | One held-out defect class is still undetected and is published as missed. |

---

## The files, and what each one buys you

Only the first is required. Everything else raises the proof rate.

| File | Where it comes from | What you lose without it |
|---|---|---|
| `razorpay_settlements.csv` | Dashboard → Settlements → Reports | Nothing to reconcile. Required. |
| `bank_statement.csv` | Your net-banking portal | No line can be proven to have reached your bank. |
| `orders.csv` | Storefront or ERP export | Settled amounts cannot be checked against what the customer was charged. |
| `refunds.csv` | Dashboard → Refunds | Refund deductions cannot be independently rebuilt, so an offsetting pair can hide in a batch that ties. |
| `cod_remittances.csv` | Courier panel | COD short-remittance goes unchecked. |
| `shipments.csv` | Courier panel | RTO freight and duplicate AWBs go unchecked. |
| `disputes.csv` | Dashboard → Disputes | Chargebacks cannot be tied to a dispute. |
| `razorpay_mdr_invoice.json` | Dashboard → Invoices | The GST you claim as input credit is not checked against the GST actually deducted. |

Filenames do not have to match. Drop `HDFC_Statement_Aug2026.csv` or
`settlement-report-aug-2026.csv` and they are routed to the right slot; anything
unrecognised is reported rather than silently ignored.
