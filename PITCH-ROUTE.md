# The back-and-forth pitch

**Two windows. Product on the left, repo on the right.**

The move is always the same: **claim it, then prove it exists.** Most hackathon
demos are a polished front end over nothing. You make it impossible to think
that, by never letting a claim sit unbacked for more than fifteen seconds.

Say the switch out loud every time — *"and here it is"*, *"that's this file"*.
The judge should feel you inviting them to check, not performing.

Open both before you start:

- **Left:** `attest-nu.vercel.app`
- **Right:** `github.com/GitBeat16/attest`

---

## The route

### 1 · Landing → the claim
**Product.** Hero, then the exhibit. Let the bars cancel.

> Two errors, opposite signs, cancelling to twenty paise. Eight and a half
> thousand rupees of real error inside a batch that reconciles perfectly.

### 2 · → GitHub · `attest/demo.py`
**Repo.** Jump to `def check()` — line 244.

> Here is why you should believe that. This demo builds **inputs**, not results.
> The real engine finds the pair. And this function recomputes the arithmetic
> independently — if a tolerance change ever stops the engine finding it, this
> **raises**. The demo cannot quietly stop demonstrating.

*Why it lands:* every judge has seen a hardcoded demo. You just showed them the
guard against being one.

### 3 · ← Landing · the three numbers
**Product.** Match 90.9 → proof 73.0 → false-match 68.2.

> Every tool reports the first. We report all three, and the gap is the product.

### 4 · → GitHub · `INVARIANTS.md`, invariant 4
**Repo.** Invariant 4.

> Four defect classes were planted with **no detector written for them**. Three
> got caught anyway. One is still missed — and this file says, in writing, **do
> not write a targeted detector for the missed one.** A visible miss is the
> evidence the score isn't circular. Held-out recall is 75%, and it stays 75%.

*Why it lands:* you are showing them a rule you wrote to stop yourself cheating.
Nobody fakes that.

### 5 · ← App · run the demo close
**Product.** Money band, then the controller timeline.

> The controller chooses what to investigate. A deterministic tool answers.
> *AI controller asks* — *engine answers.*

### 6 · → GitHub · `attest/controller.py`, line 74
**Repo.** The `ACTIONS` tuple.

> This is every action the AI can take. Read them: inspect, trace,
> run_verification, investigate, request_evidence, escalate, conclude.
>
> **Certify is not on that list.** Not blocked, not permission-checked —
> *absent*. It cannot sign a month because there is no word for it in its
> vocabulary.

Then scroll to **line 143**:

> And if it tries, the validator says exactly this: *"Certification is not an
> action available to you."*

*Why it lands:* this is the single strongest thirty seconds you have. Everyone
claims guardrails. You are showing a capability that structurally does not exist.

### 7 · → GitHub · `tests/test_controller.py`, line 232
**Repo.** `test_there_is_no_tool_that_writes_anything()`

> And a test that fails the build if anyone ever adds one.

### 8 · ← App · evidence and the clock
**Product.** Evidence chain, then the recovery list.

> A chain of records you can put in front of Razorpay, with the broken link
> named. And this claim has already expired — courier disputes die in fourteen
> days.

### 9 · → GitHub · `README.md`, *Business approach*
**Repo.** `…/blob/main/README.md#business-approach`. Let the diagram load, then
the four-band table under it.

> The merchant loses the money and never sees it — it leaves as a rounding
> difference inside a batch that balanced. The person who *feels* it is the CA
> doing this across twenty clients a month. **The CA is the buyer, not the
> merchant.**
>
> Two of these four bands are customers. Under forty lakh you can eyeball it;
> over a hundred crore an ERP recon module already does it. We'd be wrong to sell
> to either.
>
> So it isn't priced per seat — it's an outcome: money recovered before its claim
> window shuts, with the evidence chain attached so the claim can actually be
> filed. Which is why cadence is the product: monthly instead of daily lets
> ₹5,365 across 16 claims expire unfiled, computed from the findings.

*Why it lands:* a market you have deliberately narrowed reads as research.
"Every merchant in India" reads as nobody having checked.

### 10 · → Terminal · the seal
```bash
python3 -m attest.seal --verify web/close-pack.html
```
> `INTACT`. Change one rupee and it reads `ALTERED`.

### 11 · → GitHub · `scripts/verify.py`
**Repo.**

> Fifty-eight checks. If any number on that landing page is wrong, this fails.
> You can run it yourself in one command.

### 12 · ← Landing · the close
**Product.** The closing panel.

> Don't just close the books. Prove the close.

---

## Two more, if a judge digs

**"Have you actually used our API?"**
→ `attest/sources/razorpay_api.py`, line 18.

> Every row carries `settlement_id` **and** `settlement_utr`. The UTR is issued
> by the correspondent bank — it is not a Razorpay key, so it is never what we
> resolve on. We match on `settlement_id`.

That one sentence tells a Razorpay engineer you read the docs rather than
guessing. Have it ready.

**"What went wrong while building it?"**
→ `attest/sources/razorpay_api.py`, line 93.

> A credit of exactly zero is falsy in Python. The code read
> `self.credit if self.credit else (amount - fee - tax)` — so a legitimate ₹0
> credit fell through to a *computed* figure and silently replaced what Razorpay
> reported. The check failed precisely where it mattered. Found by auditing for
> the pattern, not by a failing test. There's a test suite for it now.

---

## The four sentences that carry the pitch

If you get cut short, these are the ones:

1. **"A match rate is not evidence of a correct close."**
2. **"The AI investigates. Deterministic tools prove. Policy decides."**
3. **"It refused to sign the month — and a system that always signs isn't
   attesting to anything."**
4. **"The CA is the buyer, not the merchant — the merchant loses the money and
   never sees it."**

---

## Don't

- **Don't scroll the repo looking for things.** Open the files as tabs first, or
  use direct line links (`…/blob/main/attest/controller.py#L74`). Fumbling in a
  file tree kills the momentum you just built.
- **Don't switch more than these ~7 times.** Any more and it reads as nervous
  rather than confident.
- **Don't read code aloud line by line.** Point at one thing and say what it
  means.
