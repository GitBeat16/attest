-- 002 — what was read, and what produced the answer.
--
-- Additive only. Every statement is `if not exists`, so this is safe to re-run
-- and safe to apply to a project that already holds closes: existing rows get
-- NULL, which reads correctly as "produced before this was recorded" rather
-- than as a false value.
--
--     psql "$SUPABASE_DB_URL" -f db/002_audit_trail.sql
--
-- APPLY THIS BEFORE DEPLOYING THE CODE THAT WRITES THESE COLUMNS. PostgREST
-- rejects an unknown key outright, so a deploy that runs ahead of the
-- migration does not degrade — it fails every close.
--
-- What this deliberately does NOT add: `actor` and `ran_at`. Both already
-- exist. `user_id` defaults to auth.uid() and `created_at` to now(), so who
-- ran a close and when are recorded already. Adding second columns for them
-- would look like an audit trail without being one.

-- ==========================================================================
-- attest_closes — the input fingerprint
-- ==========================================================================
-- The gap this closes: a close run over a truncated settlement file and a
-- close run over a complete one are indistinguishable in this table. That is
-- the exact failure Stage 1A was built around — deleting 57% of the settlement
-- rows produced READY, and the proof rate went UP, because the missing rows
-- took their unproven lines with them. The verdict now lands on the row, so
-- "show me every close that ran on incomplete input" is one query rather than
-- an afternoon of opening packs.

alter table public.attest_closes
  add column if not exists readiness_verdict text,      -- READY | PARTIAL | REFUSED
  add column if not exists claims_as_at     date,      -- recoverable is relative to it
  add column if not exists rows_in           integer,   -- rows the files contained
  add column if not exists rows_read         integer,   -- rows the engine understood
  add column if not exists rows_rejected     integer,   -- the difference, stated
  add column if not exists engine_version    text,      -- CHANGELOG.md
  add column if not exists ruleset_digest    text;      -- attest/ruleset.py

comment on column public.attest_closes.readiness_verdict is
  'READY, PARTIAL or REFUSED at ingest. PARTIAL cannot be attested.';
comment on column public.attest_closes.ruleset_digest is
  'Computed from rule VALUES, not source text. Moves when a threshold moves.';

-- Find the closes nobody should be relying on.
create index if not exists attest_closes_partial_idx
  on public.attest_closes(user_id, created_at desc)
  where readiness_verdict is distinct from 'READY';

-- ==========================================================================
-- attest_findings — confidence tier
-- ==========================================================================
-- PROVEN | UNPROVEN | NEEDS_INPUT. Only PROVEN may be framed as claim-ready.
-- The value is already computed on every exception; it simply had nowhere to
-- go, so the database has been storing findings without the one field that
-- says how much weight they carry.

alter table public.attest_findings
  add column if not exists tier text;

comment on column public.attest_findings.tier is
  'PROVEN, UNPROVEN or NEEDS_INPUT. Only PROVEN is claim-ready.';

-- ==========================================================================
-- Verify
-- ==========================================================================
-- select column_name, data_type
--   from information_schema.columns
--  where table_schema = 'public' and table_name = 'attest_closes'
--  order by ordinal_position;
