# Attest — the mark

An **A** standing on a **double rule**. In accounting a double underline means a
figure is final and verified; it is the mark a bookkeeper draws under a total
they are willing to stand behind. That is the whole product in one gesture, and
it is legible to the only audience that matters — the person closing the month.

Three other directions were drawn and rejected, for reasons worth keeping:

- **the audit tick** as the A's crossbar — collided with the legs and read as a
  crossed-out letter below 48px
- **a seal roundel** — handsome at 96px, but the honest notch in the ring
  vanished small and it drifted toward a generic circled A
- **the proof chain**, the A drawn as linked segments — the gaps closed up
  visually and it read as a rendering fault rather than a concept

## Files

| File | Use |
|---|---|
| `attest-mark.svg` | Primary. Accent navy `#1B4B73`. |
| `attest-mark-bold.svg` | 24px and below — the light rules merge at small sizes, so the strokes are cut heavier. This is the favicon. |
| `attest-mark-light.svg` | On ink or any dark ground. |
| `attest-mark-gold.svg` | Accent only, paired with an ink wordmark. |
| `attest-mark-black.svg` | Single-colour print, stamps, fax-quality reproduction. |
| `attest-icon.svg` / `-512.png` | App icon — reversed out of a navy tile with the margin a rounded corner needs. |
| `attest-lockup.svg` | Horizontal lockup, mark plus wordmark. |

In the pages the mark is **inlined as SVG using `currentColor`**, so it takes the
colour of whatever it sits in and never costs a request.

## Rules

- Clear space on all sides is the height of the double rule stack.
- Never below **16px**, and use the bold cut below 24px.
- The wordmark is IBM Plex Mono 600 at `letter-spacing: .22em`, uppercase.
- Do not recolour the rules separately from the letter; it is one mark.
- Do not add a shadow, a gradient or an outline. It is a pen stroke on paper.

## Palette

```
accent   #1B4B73    the mark, links, primary buttons
ink      #16181D    body text, the wordmark
paper    #FCFBF8    background
gold     #DFA82A    the coin, and the mark's accent variant
good     #1D6B4F    proof rate
bad      #A03A22    false-match rate, refusals
```
