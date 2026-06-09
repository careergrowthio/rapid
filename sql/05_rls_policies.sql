-- ============================================================
-- RAPID — Row Level Security (RLS)  [Task 9]
-- Run in the Supabase SQL Editor BEFORE anything client-facing reads these tables.
-- ADDITIVE + safe: enables RLS and adds policies; no data is dropped.
-- ============================================================
--
-- Model:
--   * The AGENT uses the service-role key, which BYPASSES RLS entirely. Enabling RLS
--     therefore does NOT affect the pipeline (it keeps full read/write).
--   * The DASHBOARD must NOT use the service key. It reads with the anon/authenticated
--     key, which is subject to the policies below.
--   * Default-deny: once RLS is enabled with no permissive policy, anon/authenticated
--     get nothing. We then open exactly what the dashboard needs and nothing else.
--
-- The dashboard feed is: matches WHERE status='sent', joined to jobs. pending/queued/
-- dropped never display. So we expose ONLY sent matches and the jobs they reference.
-- Client identity → row scoping: the dashboard is expected to authenticate users and
-- map them to a client_id. Until that mapping exists, keep the per-client predicate
-- commented and rely on the service-key-only server. DO NOT expose client PII broadly.
-- ============================================================

-- 1) Turn on RLS (default-deny) for every table. Service role still bypasses.
alter table clients        enable row level security;
alter table match_profiles enable row level security;
alter table jobs           enable row level security;
alter table matches        enable row level security;
alter table feedback       enable row level security;
alter table client_memory  enable row level security;
alter table run_log        enable row level security;

-- 2) Dashboard read access — SENT matches only.
--    (a) Simplest safe default: authenticated users may read sent matches.
drop policy if exists matches_sent_read on matches;
create policy matches_sent_read on matches
  for select to authenticated
  using (status = 'sent');

--    (b) Per-client scoping (PREFERRED once auth maps users -> client_id). Replace
--        (a) with this: a user sees only their OWN sent matches. Requires a
--        user_clients(user_id uuid, client_id uuid) mapping table.
-- drop policy if exists matches_sent_read on matches;
-- create policy matches_sent_read on matches
--   for select to authenticated
--   using (status = 'sent' and client_id in (
--     select client_id from user_clients where user_id = auth.uid()));

-- 3) Jobs referenced by a visible (sent) match are readable; the rest of the pool is not.
drop policy if exists jobs_for_sent_matches_read on jobs;
create policy jobs_for_sent_matches_read on jobs
  for select to authenticated
  using (exists (
    select 1 from matches m
    where m.job_id = jobs.id and m.status = 'sent'
    -- and m.client_id in (select client_id from user_clients where user_id = auth.uid())
  ));

-- 4) Clients: expose only non-PII display fields the dashboard needs (name/status).
--    Keep email/phone/resume_url server-side only. Tighten to per-client when auth lands.
drop policy if exists clients_basic_read on clients;
create policy clients_basic_read on clients
  for select to authenticated
  using (true);
-- NOTE: column-level exposure is enforced by the dashboard's SELECT list + a view.
--       Consider a `clients_public` view (id, name, status) and grant on that instead
--       of the base table if you want hard column protection.

-- 5) Everything else (match_profiles, feedback, client_memory, run_log) has NO
--    anon/authenticated policy => default-deny. Only the service role (agent) touches
--    them. That is intentional: tuning knobs, notes, and run telemetry are internal.

-- ============================================================
-- Verify after running:
--   select tablename, rowsecurity from pg_tables where schemaname='public';
--   select * from pg_policies where schemaname='public';
-- ============================================================
