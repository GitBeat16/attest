# Attest — 5-minute pitch, screen recording

**You drive. macOS screen recording, live narration, one take.**

Format below is: **▸ DO** (what you press, click, scroll) then **🎙 SAY** (word
for word). Times are the running clock — glance at them, don't chase them.

**Pacing, honestly.** The narration is **750 words**. At a confident pitch pace
(~160 words a minute) that's **4:41**, leaving about 19 seconds for the pauses
marked in the script. It fits, but it does not fit *slowly*. If your first take
runs over 5:00, don't speed up — **drop the two paragraphs marked ✂**. They are
the two least load-bearing lines in the script and cost ~25 seconds between them.

`PITCH-ROUTE.md` is the longer back-and-forth version. Use it for live Q&A, not
for this recording.

---

# PART 0 · Setup

## macOS recording — the three settings people lose a take to

Press **⌘ ⇧ 5**. A toolbar appears at the bottom.

1. **Options → Microphone → MacBook Air Microphone**
   Default is **None**. If you skip this you will record five perfect minutes of
   silence and not find out until you play it back. Check it every single time.
2. **Options → Show Mouse Clicks** → on.
   A judge follows your cursor much better with click rings.
3. **Options → Save to → Desktop**, **Timer → None**.

Then choose **Record Entire Screen** (the left icon of the two rectangles).
Selected-portion is fiddly and you'll spend takes fighting the crop.

**Stop recording:** the ■ icon in the menu bar, or **⌘ ⌃ Esc**.

## Clean the screen

- [ ] **Hide the Dock** — ⌘ ⌥ D. It's visible in every screenshot you've sent me.
- [ ] **Stage Manager off** (Control Centre) — those window thumbnails down the
      left side will be in the recording.
- [ ] **Do Not Disturb on** (Control Centre → Focus).
- [ ] **Maximise Safari** — hold **⌥** and click the green button. That zooms
      without going true-fullscreen, so your tabs stay visible.
- [ ] Hide the bookmarks bar — ⌘ ⇧ B.

## Five tabs, in this order. ⌘1 – ⌘5 switches between them.

| ⌘ | Tab |
|---|---|
| **1** | `attest-nu.vercel.app` |
| **2** | `attest-nu.vercel.app/app.html` |
| **3** | `github.com/GitBeat16/attest/blob/main/INVARIANTS.md#L40` |
| **4** | `github.com/GitBeat16/attest/blob/main/attest/controller.py#L74` |
| **5** | `github.com/GitBeat16/attest/blob/main/README.md#business-approach` |

Tab 5 is **not used in the recording** — the business beat at 4:05 is on the
landing page. It's there for live Q&A afterwards, when a judge asks who pays and
you want the diagram and the four-band table on screen in one keystroke.

**On tabs 3 and 4: press ⌘ + twice.** GitHub's default font is unreadable once
the video is compressed. An unreadable proof is not a proof.

## Last three things

- [ ] **Run the demo close once, now, then leave tab 2 sitting on the result.**
      The first Gemini call is the slowest. Don't spend it on camera.
- [ ] **Terminal**, in `~/Projects/attest`, font ~16pt, window roughly half
      screen. Type this and **do not press Enter**:
      ```
      python3 -m attest.seal --verify web/close-pack.html
      ```
- [ ] Glass of water. You're talking for five minutes.

---

# PART 1 · The recording

## 0:00 – 0:30 · The hook

**▸ DO** — Start on **⌘1**, scrolled to the very top. Press record, wait two
seconds in silence, then begin. Let the coin finish its flip while you talk.

> **🎙** Every reconciliation tool in the world answers one question: did the
> numbers match.
>
> Here's the settlement batch we close in this demo. Razorpay says it paid
> nineteen lakh, forty thousand, seven hundred and ninety-nine rupees and eighty
> paise. The bank shows exactly the same figure. Variance: zero. Every automated
> check passes.

**▸ DO** — Start scrolling slowly toward the dark exhibit section as you say the
next line.

> **🎙** That batch is wrong by eight and a half thousand rupees.

**▸ DO** — **Stop scrolling. Say nothing for two full seconds.** This pause is
the hook. Count it out.

---

## 0:30 – 1:10 · The compensating error

**▸ DO** — Scroll so beat 2 fills the screen. The two bars will grow, slide
together and cancel as you scroll — **control the speed with your trackpad**, a
slow steady scroll, and let the animation play out. If you overshoot, click
**↻ Play it again**.

> **🎙** Two mistakes, almost exactly the same size, in opposite directions.
>
> Razorpay charged two-point-one-eight percent when the contract says two — four
> thousand, two hundred and forty-eight rupees too much. And it under-deducted
> refunds by four thousand, two hundred and forty-seven eighty.

**▸ DO** — Time this next line to land as the bars collapse into the gold sliver.

> **🎙** They cancel to twenty paise. Inside any tolerance a finance team would
> set. So it passes silently, and the money leaves.
>
> Accountants call this a compensating error. A check on totals cannot find it —
> not because the tool is bad, but by construction. Twenty paise visible. Eight
> and a half thousand real. Understated forty-two thousand times.

---

## 1:10 – 1:45 · The three numbers

**▸ DO** — Scroll to the three cards. **Wait for the counters to finish** before
you name each figure — reading a number that's still ticking looks careless.
Move the cursor to each card as you speak about it.

> **🎙** So we report three numbers instead of one.
>
> Match rate — ninety point nine. Did the money arrive. Everyone reports this,
> and it nearly always passes: Razorpay pays what its own report says, so the two
> documents agree by construction.
>
> Proof rate — seventy-three. Can I show every deduction was correct. Order, fee,
> tax, batch, bank credit, no link missing.
>
> False-match rate — sixty-eight point two. Of the batches that passed, this
> share was overturned by a pass that attacks its own results.
>
> The gap is the product.

---

## 1:45 – 2:05 · ▶ CUT ONE — GitHub

**▸ DO** — Press **⌘3**. You land on the highlighted line. **Do not scroll.**
Put the cursor on the bold text and leave it there.

*Why here: you just claimed 73% and 75%. The judge is now wondering whether those
were tuned. Answer it before they finish the thought.*

> **🎙** And here's why you can believe those numbers.
>
> Four defect classes were planted with no detector written for them. Three got
> caught anyway. One is still missed — and this file says, in writing: do not
> write a targeted detector for the missed one.
>
> A visible miss is the evidence the score isn't circular. Seventy-five percent,
> and it stays seventy-five.

**▸ DO** — Press **⌘2** immediately. Don't linger.

---

## 2:05 – 2:55 · The close, and the controller

**▸ DO** — You're on the result from your warm-up run. Scroll to the dark money
band.

> **🎙** ✂ Four hundred and thirty-eight thousand rupees recoverable, ranked by
> exposure, each with a counterparty and a deadline.

*(✂ — the recovery list at 3:20 makes this point again with the lapsed claim
attached. Cut here first if you're long.)*

**▸ DO** — Scroll to the controller timeline. Go **slowly** — about one step per
two seconds. Move the cursor along the right-hand labels as you talk.

> **🎙** Now the part that matters for this track.
>
> The controller chooses what to investigate next — one step at a time, not from
> a script. It asks a question in plain English. A deterministic tool answers it.
> Look at the labels: *AI controller asks* — *engine answers*.

**▸ DO** — Keep scrolling until you reach a step labelled **RULES PLANNER ASKS**.
Stop there. Put the cursor on that label.

> **🎙** ✂ A model call over the network takes about eight seconds, and a full
> investigation is ten steps. That doesn't fit inside a serverless request. So
> the model gets a slice of the clock — and when it's spent, the deterministic
> planner picks up mid-investigation and finishes.
>
> Same validator. Same tools. Same policy gate. Only the chooser changed.

*(✂ — the handover paragraph. Keep the "same validator, same tools" line either
way; it's the one that lands. Cut the four sentences above it only if you're
still long after the first ✂.)*

---

## 2:55 – 3:20 · ▶ CUT TWO — GitHub

**▸ DO** — Press **⌘4**. You land on line 74. **This is your strongest twenty
seconds in the whole video.** Slow down. Put the cursor on the tuple.

> **🎙** This is every action the AI can take. Inspect. Trace. Run verification.
> Investigate. Request evidence. Escalate. Conclude.

**▸ DO** — Pause for one second. Then:

> **🎙** Certify is not on that list. Not blocked, not permission-checked —
> absent. It cannot sign off a month, because there is no word for it in its
> vocabulary.

**▸ DO** — Scroll down to about **line 143**. It's a short scroll — roughly one
trackpad swipe. Cursor on the string.

> **🎙** And if it tries, the validator answers: *certification is not an action
> available to you.*

**▸ DO** — Press **⌘2**.

---

## 3:20 – 3:45 · Evidence, and the clock

**▸ DO** — Scroll to the evidence chain. Cursor down the chain as you speak.

> **🎙** This is what "attest" means. Not "there is a discrepancy" — a chain of
> records you can put in front of Razorpay, with the broken link named.

**▸ DO** — Scroll to the recovery list. Find the row with the **struck-through
amount** — the lapsed courier claim. Put the cursor on it and leave it.

> **🎙** And every claim carries the date its window shuts.
>
> This one has already expired. Courier disputes die in fourteen days, and this
> close ran too late.

---

## 3:45 – 4:05 · The seal

**▸ DO** — **⌘ Tab** to Terminal. The command is already typed. **Press Enter on
camera** — a judge seeing a real command run is worth far more than being told
about one.

> **🎙** The close pack carries a SHA-256 of the entire rendered document.

**▸ DO** — Wait for `INTACT` to print. Point at it.

> **🎙** Change one rupee, one date, one letter of the business name, and it
> reads ALTERED. It proves the pack hasn't been edited since it was sealed — not
> who made it. So the honest word is tamper-evident, not tamper-proof.

**▸ DO** — **⌘ Tab** back to Safari, then **⌘1**.

---

## 4:05 – 4:35 · Who pays

**▸ DO** — On **⌘1**, scroll to **Who it's for** — the four-band table. Put the
cursor on the two rows marked **Yes** as you name them.

*Why here: you have just spent four minutes proving the thing works. A finance
judge's next thought is "who writes the cheque." Answer it before they ask — and
answer it with a table that rules two bands out. A market you have deliberately
narrowed reads as research; a market of "every merchant in India" reads as
nobody having checked.*

> **🎙** Last thing: who buys this. Not the merchant — the merchant loses the
> money and never sees it. The CA feels it, across twenty clients a month.
>
> Two of these four bands are customers. Under forty lakh you can eyeball it;
> over a hundred crore, an ERP recon module already does it.

**▸ DO** — Scroll a little further, to the **cadence** line beneath the table.

> **🎙** So it isn't priced per seat — it's an outcome: money recovered before
> its claim window shuts. Which is why cadence is the product. Closing monthly
> instead of daily lets five thousand three hundred and sixty-five rupees expire
> unfiled.

---

## 4:35 – 5:00 · The refusal, and the close

**▸ DO** — Keep scrolling to *Honest scope* — the two-column built / roadmap
section. It is the next section, so this is one continuous scroll.

> **🎙** The benchmark is synthetic — accuracy can only be measured against data
> whose truth you constructed. On your own data we quote no accuracy figure at
> all.
>
> And the close you just watched was refused. Forty-six thousand rupees couldn't
> be attributed to any cause, so the status is NOT ATTESTABLE.
>
> A system that always signs isn't attesting to anything.

**▸ DO** — Scroll to the final dark panel. Let it sit still.

> **🎙** Don't just close the books. Prove the close.

**▸ DO** — Stay silent for **three seconds** on the final frame. Then **⌘ ⌃ Esc**.

---

# PART 2 · If it goes wrong

| What happens | What you do |
|---|---|
| **Controller shows rules only** | *"No model configured on this deployment — and the investigation is identical, because the safety properties belong to the validator, not the planner."* True, and a good sentence. |
| **The close is slow** | Talk over it: *"three thousand records, seven systems, and the model is choosing its next question."* |
| **Gemini rate-limits** | It degrades to the rules planner and keeps going. Name it in one line and carry on. |
| **You fluff a sentence** | Keep going. Say it again cleanly. You trim in QuickTime. |
| **Notification appears** | Stop, turn on Do Not Disturb, start again. Don't try to talk over it. |
| **You lose your place** | The four anchors in order: *cancel · three numbers · controller · seal.* Get back to one of those. |

---

# PART 3 · After you stop

1. The `.mov` lands on your **Desktop**, named with the date and time.
2. **Play the first ten seconds and check you can hear yourself.** Do this before
   anything else. If the mic was off, you find out now instead of after
   uploading.
3. **Trim in QuickTime** — open it, **⌘ T**, drag the yellow handles to cut the
   dead air at each end, **Enter**, then **⌘ S**.
4. Check the duration is **under 5:00**. If it's over, re-record with the **✂
   paragraphs dropped** rather than trimming mid-sentence in QuickTime — a cut
   inside a spoken line is audible and reads as a mistake.
5. Upload, then **watch it through once** at normal speed before you submit.

---

# Say this, not that

| Don't | Do |
|---|---|
| "100% accurate" | "100% on what we designed for — which proves nothing on its own. 75% on held-out." |
| "The AI finds the errors" | "The AI chooses what to investigate. Deterministic tools find the errors." |
| "Tamper-proof" | "Tamper-evident" |
| "We recovered ₹87,000" | "₹87,256 identified as recoverable" |
| "It reconciles your books" | "It tells you what your books can prove" |
| "Every merchant in India needs this" | "Two of four volume bands. The other two we'd be wrong to sell to." |
| "We'd charge per seat" | "It's an outcome — money recovered before the claim window shuts" |

Under pressure the instinct is to round up and simplify. Every one of those
trades credibility for nothing, and a finance judge is listening for exactly
them. The refusals are what make everything else believable.

---

# Every number, so you never guess on camera

**Demo close** — Lakeview Naturals, August 2026

| | |
|---|---|
| Settlement / bank | ₹19,40,799.80 both sides · variance **₹0.00** |
| Fee overcharged | ₹3,600.00 (2.18% charged vs 2.00% contracted) |
| GST on that | ₹648.00 |
| Refund under-deducted | ₹4,247.80 |
| Batch appears out by | **₹0.20** |
| Actually wrong by | **₹8,495.80** · understated **42,479×** |
| Recoverable | ₹4,38,440.20 |
| Courier claim | **lapsed** |

**Benchmark** — 1,200 orders · 3,043 records · 7 systems · closed in 0.02s

| | |
|---|---|
| Match rate | 90.9% (20/22) |
| Proof rate | **73.0%** (743/1018) |
| False-match rate | **68.2%** (15/22) |
| Held-out recall | **75%** — 3 of 4, one honestly missed |
| False positives | **0** |
| Recoverable | ₹87,256.57 across 283 items |
| Lost to monthly cadence | ₹5,365.00 across 16 claims |
| Unexplained residual | ₹46,617.32 = 254.5 bps → **NOT ATTESTABLE** |

**Market** — two of four bands, in case a judge asks you to defend the narrowing

| Annual volume | Who reconciles today | Customer |
|---|---|---|
| Under ₹40 lakh | The founder, in a notebook | No — small enough to eyeball |
| ₹40 lakh – ₹5 crore | An external CA, in Tally | **Yes** — the CA feels it across ~20 clients |
| ₹5 – ₹100 crore | A 1–3 person finance team | **Yes** — 20–50 hrs/month, 3–5 systems. Best fit |
| ₹100 crore + | Controller + ERP recon module | No — already automated |

If pushed on pricing: it isn't per seat. It's the recovered amount, filed before
its window shuts, with the evidence chain attached — and the close pack is the
artefact the CA hands an auditor, which is why it's sealed.
