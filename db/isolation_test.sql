-- Tenant isolation — the only question that matters.
--
-- With one merchant's identity, can I reach another merchant's month?
--
-- Run it in the Supabase SQL editor, or:
--     psql "$SUPABASE_DB_URL" -f db/isolation_test.sql
--
-- It ends by RAISING its own result. That is deliberate: the exception rolls
-- back every row it created, so the test leaves the database exactly as it
-- found it and can be run against a live project without depositing evidence
-- of itself. Read the message; the "ERROR:" prefix is the mechanism, not a
-- failure. A real failure appears as a count that disagrees with its (expect n).
--
-- WHAT THIS PROVES: the policy expressions, evaluated by Postgres, with the
-- JWT claims set exactly as PostgREST sets them per request. This is where
-- authorisation actually happens.
--
-- WHAT IT DOES NOT PROVE: the layer above — that PostgREST validates the token
-- signature, honours expiry, and maps the claim to the `authenticated` role.
-- That is Supabase's code, not ours, and asserting it here would be theatre.
-- What we own is what is tested.
--
-- Last run 2026-09-10 against the Mumbai project: 10/10.

do $$
declare
  a uuid := '11111111-1111-4111-8111-111111111111';
  b uuid := '22222222-2222-4222-8222-222222222222';
  a_close uuid; b_close uuid; n int; r text := '';
begin
  set local role authenticated;

  perform set_config('request.jwt.claims',
    json_build_object('sub', a, 'role', 'authenticated')::text, true);
  insert into public.attest_closes(merchant, period, source)
    values ('Tenant A', '2026-08', 'test') returning id into a_close;

  perform set_config('request.jwt.claims',
    json_build_object('sub', b, 'role', 'authenticated')::text, true);
  insert into public.attest_closes(merchant, period, source)
    values ('Tenant B', '2026-08', 'test') returning id into b_close;

  perform set_config('request.jwt.claims',
    json_build_object('sub', a, 'role', 'authenticated')::text, true);

  select count(*) into n from public.attest_closes;
  r := r || format('[1] A sees %s close(s) total (expect 1) | ', n);

  select count(*) into n from public.attest_closes where id = b_close;
  r := r || format('[2] A reading B''s close: %s rows (expect 0) | ', n);

  update public.attest_closes set merchant = 'hacked' where id = b_close;
  get diagnostics n = row_count;
  r := r || format('[3] A updating B''s close: %s rows (expect 0) | ', n);

  -- Not a bug: the update policy was dropped deliberately. A close is a
  -- statement about a month as it was found; correcting it means running the
  -- month again, which produces a new row and a new seal.
  update public.attest_closes set merchant = 'edited' where id = a_close;
  get diagnostics n = row_count;
  r := r || format('[4] A updating its OWN close: %s rows (expect 0) | ', n);

  delete from public.attest_closes where id = b_close;
  get diagnostics n = row_count;
  r := r || format('[5] A deleting B''s close: %s rows (expect 0) | ', n);

  begin
    insert into public.attest_findings(close_id, class, label)
      values (b_close, 'MDR', 'x');
    r := r || '[6] A attaching a finding to B''s close: ALLOWED (expect refused) | ';
  exception when others then
    r := r || '[6] A attaching a finding to B''s close: refused | ';
  end;

  begin
    insert into public.attest_findings(close_id, class, label)
      values (a_close, 'MDR', 'x');
    r := r || '[7] A attaching a finding to its own close: allowed | ';
  exception when others then
    r := r || '[7] A attaching a finding to its OWN close: REFUSED (expect allowed) | ';
  end;

  begin
    insert into public.attest_closes(user_id, merchant, period, source)
      values (b, 'Forged', '2026-08', 'test');
    r := r || '[8] A inserting a close owned by B: ALLOWED (expect refused) | ';
  exception when others then
    r := r || '[8] A inserting a close owned by B: refused | ';
  end;

  -- The append-only claim, tested rather than asserted. There is no update
  -- policy and no delete policy on seals, and their ABSENCE is the guarantee.
  insert into public.attest_seals(digest, close_id, merchant, period)
    values ('deadbeef', a_close, 'Tenant A', '2026-08');

  update public.attest_seals set merchant = 'rewritten' where digest = 'deadbeef';
  get diagnostics n = row_count;
  r := r || format('[9] A rewriting its own seal: %s rows (expect 0) | ', n);

  delete from public.attest_seals where digest = 'deadbeef';
  get diagnostics n = row_count;
  r := r || format('[10] A deleting its own seal: %s rows (expect 0)', n);

  raise exception 'ISOLATION >> %', r;
end $$;
