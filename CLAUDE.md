# Working on Attest

Context for Claude Code. Read `README.md` for the thesis and `ARCHITECTURE.md`
for how it works. This file covers what you must not break, and what is left.

**Submission: Razorpay AI Buildathon, Track 4 (AI Finance Controller). Due 5 Sep 2026.**
Repo must stay public. Deliverables: this repo, a 5-minute pitch video, and an
architecture doc (already written).

---

## The one idea

Everything here exists to support a single argument:

> A match rate is not evidence of a correct close. Two numbers can agree for the
> wrong reasons — an MDR overcharge of ₹4.12 against a refund under-deducted by
> ₹4.10 leaves the batch out by two paise, inside any tolerance, with two real
> errors inside it. So Attest reports a **proof rate** and a **false-match rate**
> alongside the match rate, and the gap between them is the product.

If a change makes those three numbers less honest, it is the wrong change, even
if it makes them look better.

---

## Invariants — do not break these

1. **Money is integer paise. No floats, ever.** `money.py` is the only place
   arithmetic on money happens. A float pipeline manufactures exactly the
   sub-rupee drift the system exists to detect.

2. **The corpus is generated truth-first.** `world.py` builds the true world,
   `generate.py` freezes it, then `defects.py` injects defects into a *copy*.
   `data/truth/` is read by `score.py` and by nothing else. If any other module
   starts reading truth, every accuracy number becomes worthless.

3. **Matching is deterministic. Never make it probabilistic.** Rules, tolerances,
   explicit thresholds. A finance judge trusts rules over a model here, correctly.

4. **Held-out defect classes must stay held out.** `DUPLICATE_SETTLEMENT_LINE`,
   `DUPLICATE_AWB`, `ORPHAN_BANK_CREDIT`, `OUT_OF_PERIOD_SETTLEMENT` were planted
   with no detector written for them. Three are now caught by *generic* integrity
   checks; one is missed. **Do not write a targeted detector for the missed one.**
   A visible miss is the evidence that the score is not circular. Losing it costs
   more than the point gained.

5. **World facts are not errors.** Refunds that legitimately net into next month,
   and same-value same-day order pairs, are labelled expected non-errors.
   Flagging one is a false positive and must be scored as such.

6. **The period is anchored to the declared invoice month**, never to
   `max(settled_on)`. A stray out-of-period record must not redefine the month
   being closed. (This was a real bug; it silently killed false-positive
   suppression.)

7. **Nothing posts to a ledger automatically.** Draft for human approval only.

---

## Expected output

```bash
python3 -m attest.generate --out data --orders 1200
python3 -m attest.run --data data --html web/close-pack.html
```

Roughly (exact figures depend on seed):

```
3,043 records from 7 sources closed in ~0.02s
match rate    20/22  = 90.9%    <- what everyone reports
proof rate   743/1018 = 73.0%   <- what we report
false-match   15/22   = 68.2%   <- what nobody reports
recall, designed-for   100.0%
recall, HELD OUT        75.0%   <- the honest number
FALSE POSITIVES             0
cost of closing monthly  ₹5,365.00 (16 claims expire unfiled)
```

**If the held-out recall hits 100% or false positives rise above 0, something
regressed.** Those two numbers are the integrity check on the whole project.

---

## Still to do

1. **Gemini reasoning adapter** (`attest/engines.py`, not yet written).
   Interface with four implementations: `rules` (default, zero deps, already
   effectively what runs today), `gemini`, `openai`, `ollama`. Config via
   `ATTEST_ENGINE` in `.env` — see `.env.example`. Gemini free tier is the chosen
   provider. Use it for three things only: parsing bank narration, explaining
   root causes, drafting claims. **Never for match arithmetic.** Cache responses
   to disk so re-runs cost nothing. The pipeline must still run fully with the
   engine unavailable.

2. **5-minute pitch video.** Structure: the queue → run the pipeline live in the
   terminal (0.02s, proves it's real) → open the close pack → walk the descent
   90.9% → 73.0% → 68.2% → the compensating pair (₹4.12 vs ₹4.10) → the scorecard
   with the visible MISS → the ₹5,365 cost-of-monthly number → close.

3. **Two form answers**: Project Objectives, and Build Challenges & Technical
   Obstacles.

4. **Hosting** — `web/` is deployed on Vercel (project `attest`, root directory
   `web`), linked to this repo, so every push to `main` redeploys. `docs/` was the
   old GitHub Pages copy and has been removed; do not reintroduce it.

---

## Real bugs hit during the build

Useful for the "Build Challenges" answer, and all genuine:

- **A 95.7% false-match rate.** The first adversarial pass fired on every tied
  batch because a 1-paise rounding drift counted as material variance. Not a
  finding — a broken metric. Fixed by separating sub-tolerance drift (tolerance
  abuse) from material variance.
- **A test that cancelled its own subject.** Offsetting-pair detection originally
  used the settlement report's own refund line, so the refund error cancelled out
  of the very check designed to find it. Fixed by rebuilding the expected refund
  independently from the merchant's refund export plus the contract netting rule.
- **A ₹14.75 lakh exposure figure that should have been ₹27,852.** The
  missing-credit check counted the whole batch once per line.
- **Building the false positive we claimed to avoid.** The first refund check
  compared raw period totals and flagged three legitimate month-end refunds as
  errors.
- **A held-out defect causing an unrelated regression.** Moving a settlement into
  September shifted `max(settled_on)`, which silently disabled timing suppression.

---

## Things not to do

- Don't add cloud, a server, or a framework. Zero runtime dependencies is a
  selling point for a tool a CA runs on their own laptop.
- Don't clone Razorpay's UI. It isn't a Razorpay product.
- Don't build connectors, schedulers or multi-tenancy before the deadline. They
  are documented as roadmap in `ARCHITECTURE.md` and that is the right place.
- Don't commit `data/` (gitignored — it regenerates from a seed) or a real `.env`.
- Don't "improve" the numbers. The honest ones are the point.
