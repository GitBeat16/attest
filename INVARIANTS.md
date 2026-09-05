# Engineering invariants

What must not break in Attest, and why. This file is the reason the numbers in
[`README.md`](README.md) can be trusted: several of these rules exist
specifically to stop the project from flattering itself.

**Razorpay AI Buildathon — Track 4, AI Finance Controller.**

---

## The one idea

Everything here exists to support a single argument:

> A match rate is not evidence of a correct close. Two numbers can agree for the
> wrong reasons — a gateway fee overcharged by ₹4,248.00 against a refund
> under-deducted by ₹4,247.80 leaves the batch out by twenty paise, inside any
> tolerance, with ₹8,495.80 of real error inside it. So Attest reports a **proof
> rate** and a **false-match rate** alongside the match rate, and the gap between
> them is the product.

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

8. **Falsiness is not absence.** A money value of exactly zero is a real value.
   Never write `if not credit` or `x if x else y` on an amount — a legitimate
   ₹0 credit taking a different code path was a real bug, and
   `tests/test_money_edges.py` exists to stop it recurring.

9. **Adversarial verdicts are not claimable money.** An overturned match
   describes the same rupees as the chain break that produced it. Verdicts carry
   `kind: "verdict"`, are excluded from recovery, and are shown separately.
   Summing them into a recoverable total overstates what a merchant can get
   back, which is the one direction a finance tool must never err in.

10. **The demo must keep demonstrating.** `attest/demo.py` builds inputs, not
    results; the real engine finds the pair. `demo.check()` recomputes the
    arithmetic independently and raises if the engine stops finding it. Never
    hardcode a finding into the UI.

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

## Real bugs hit during the build

Every one of these was a wrong number rather than a crash, which is the
dangerous kind in finance software:

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
- **A legitimate zero treated as missing.** `ReconRow.net` read
  `self.credit if self.credit else (amount - fee - tax)`. A credit of exactly
  zero is falsy, so it fell through to a computed figure, silently replacing what
  Razorpay reported. `net_disagrees_by` had the same shape, so a reported zero
  that disagreed was reported as agreeing — the check failed exactly where it
  mattered. Found by auditing for the pattern, not by a failing test.
- **Adversarial verdicts counted as recoverable money.** An offsetting pair is
  the same rupees as the MDR chain break and refund variance composing it, and
  all three were summed into the recoverable total — overstating the benchmark by
  ₹846.90 and the demo by ~₹17,000. Found while building the money-first
  dashboard, i.e. at the moment the number was about to be shown in 72pt type.
- **A demo that could silently stop demonstrating.** The compensating pair is
  found by the engine, not scripted — so a tolerance change could quietly remove
  it and leave a demo that shows nothing. `demo.check()` now recomputes the
  arithmetic independently and raises.
- **The whole app dying because a CDN was blocked.** The Supabase SDK was a
  top-level import, so when jsdelivr was unreachable the module never executed
  and every button was dead — including the demo, which does not use auth at all.
  Now loaded on demand.

---

## Things not to do

- Don't add cloud, a server, or a framework. Zero runtime dependencies is a
  selling point for a tool a CA runs on their own laptop.
- Don't clone Razorpay's UI. It isn't a Razorpay product.
- Don't build connectors, schedulers or multi-tenancy before the deadline. They
  are documented as roadmap in `ARCHITECTURE.md` and that is the right place.
- Don't commit `data/` (gitignored — it regenerates from a seed) or a real `.env`.
- Don't "improve" the numbers. The honest ones are the point.
