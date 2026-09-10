# Stage 3 — plan

**The gate before real data.**

Design only. No code until this is agreed.

Everything here matters at exactly one moment: when a real CA uploads a real
client's files. Not before, and not optional after.

---

## Found while auditing: the close pack is an XSS vector

**This is the reason to do Stage 3 before Stage 4, and it was found by testing
rather than reading.**

The seal embeds the canonical block inside an HTML comment. The block contains
the merchant name, which is merchant-controlled input. A merchant name
containing `-->` terminates that comment early, and everything after it renders
as **live HTML**:

```
merchant: Acme --> <img src=x onerror=…> <!-- Ltd
→ comment closes 330 characters early
→ the img tag renders and its handler executes
→ the seal still verifies, so the document looks legitimate
```

The close pack is the artefact this product exists to produce: a self-contained
file emailed to an auditor and opened in their browser, trusted precisely
because it is sealed. Script running inside it can rewrite what the auditor sees
while the seal still reads INTACT.

**The fix is small and must not break verification.** Escape `<` and `>` as
`<` / `>` in the *embedded* blob only. JSON decodes those back to the
same characters, so `extract()` recovers an identical dict and `verify()` — which
re-serialises the parsed dict — still matches. The digest is unaffected because
it is computed over the canonical *dict*, not over the embedded text.

Escaping must be applied in `embed()` and nowhere else, or the two paths
disagree and every existing pack stops verifying. A test must cover exactly that:
a pack sealed with a hostile merchant name still verifies, *and* contains no
live markup.

---

## Already strong — preserve, and pin with tests

Verified in this audit, not assumed. Each of these is a property with nothing
currently holding it in place.

**No merchant data reaches the model provider.** Across all ten prompts of a
full investigation: zero merchant names, zero order ids, zero payment ids, zero
settlement ids. The only large number is the residual in paise. This falls out
of the architecture — tools return one-line summaries to the planner, not
records — and it is the single biggest privacy property an AI finance tool can
have. **Nothing enforces it.** A more detailed tool summary would silently
undo it.

**No service-role key server-side.** `api/close.py` forwards the *caller's own*
access token to PostgREST, so the function cannot touch a row the caller's token
does not permit. The boundary is real.

**Secrets do not leak.** `del key_secret` immediately after use, and there is no
logging anywhere in the package — nothing to leak into.

**Uploads are bounded.** `MAX_BODY = 6 MB`, enforced before the body is read.

**Seals are immutable by design** — the closes table has no update and no delete
policy, so a merchant can add a seal and nobody can alter one afterwards.

---

## The real gaps

### 1. The RLS policies are not in the repository

The entire authorization boundary lives in the Supabase project and nowhere
else. It cannot be reviewed, cannot be tested, cannot be recreated, and cannot
be shown to anyone. `api/close.py` writes `attest_closes`, `attest_findings` and
`attest_seals` — and what stops one tenant reading another's rows is a policy no
one can see.

**Fix:** commit the policies as SQL under `db/`, and add a test that runs the
tenant-isolation question against a real project: with tenant A's token, can I
read tenant B's row? That test needs credentials, so it warns rather than fails
when they are absent — the same way the live Razorpay check already behaves.

### 2. No audit trail

Nothing records who ran which close, against which inputs, when. Ownership is
implied by RLS but never stated. For a tool whose output an auditor relies on,
"who produced this" is not an optional field.

**Fix:** an `actor` and a `ran_at` on the close row, and the input readiness
fingerprint — rows in, rows read, sources supplied — which already exists in the
readiness object and is already inside the seal.

### 3. No deletion path

A merchant cannot get their data removed. This interacts with seal immutability
and the interaction has to be deliberate rather than accidental: **the financial
rows should be deletable; the seal record should not.** A seal is a claim that a
document existed in a given state at a given time, and deleting it destroys the
evidence the merchant themselves may later need.

**Fix:** delete the findings and close rows; retain the seal digest and its
timestamp, with the merchant identity removed. Document this in plain words
before anyone uploads anything — it is a promise, not a feature.

### 4. File type validation is size-only

`MAX_BODY` bounds the request. Nothing checks that what arrived is a CSV. The
readiness layer refuses to *parse* nonsense, which is most of the protection,
but the boundary should be explicit rather than emergent.

---

## Deliberately out of scope

Naming these so they are not quietly assumed to be covered.

- **SSRF, SQL injection.** No user-supplied URL is fetched; PostgREST is
  parameterised. Nothing to do.
- **Prompt injection.** The model chooses *which tool to call*, from a fixed
  tuple, validated against `ACTIONS`. It cannot invent a tool, compute a rupee
  or certify. A hostile string in a merchant name can at worst waste a step.
  This is already the strongest part of the design and needs no work.
- **Encryption at rest, backups, employee access.** These belong to Supabase's
  posture, not to this codebase. Worth *stating* in the honest-scope section
  rather than implementing.

---

## Tests — `tests/test_security.py`

1. **A hostile merchant name cannot break out of the seal comment** — no live
   markup in the pack, and the seal still verifies.
2. **Hostile input is escaped wherever it renders** — name, sample ids,
   evidence strings.
3. **A pack sealed before the escaping change still verifies** — the fix must
   not invalidate existing packs.
4. **No merchant-identifying data in any model prompt** — run a full
   investigation, assert merchant name, order ids, payment ids and settlement
   ids appear in none of them. This is the test that keeps the privacy property
   true.
5. **No secret in any error message** — force failures on the Razorpay path with
   a known secret and assert it never appears in the raised text.
6. **Oversized upload is refused** before the body is read.
7. **Tenant isolation** — warns without credentials, fails with them.

---

## Exit condition

You would be comfortable if a CA uploaded a real client's settlement file this
afternoon: the pack cannot execute anything, the RLS policies are in the repo
and tested, every close records who ran it, deletion is documented and works,
and the properties that are currently true by accident are true by test.

---

## Files touched

- `attest/seal.py` — escape the embedded blob
- `attest/report.py` — audit any unescaped interpolation
- `api/close.py` — actor, content-type check
- `db/policies.sql` — **new**: the RLS policies, committed
- `tests/test_security.py` — new
- `scripts/verify.py` — assertions
- `README.md` — the honest-scope note on what is and is not covered
