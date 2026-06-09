-- ============================================================
-- RAPID — patch: one match row per (client, job)  [Task 8 idempotency]
-- Run in the Supabase SQL Editor. ADDITIVE + safe: creates an index only.
-- ============================================================
-- A client should never have two match rows for the same job. This unique index
-- makes a duplicate INSERT fail at the DB instead of relying solely on the in-memory
-- matched_ids guard, so two overlapping pipeline runs can't double-send the same job.
-- The pipeline already loads matched_ids and skips known jobs; this is belt + braces.
--
-- If a duplicate already exists this will error — de-dupe first (keep the newest):
--   delete from matches a using matches b
--    where a.client_id = b.client_id and a.job_id = b.job_id and a.ctid < b.ctid;

create unique index if not exists matches_client_job_uniq on matches (client_id, job_id);

-- ------------------------------------------------------------
-- Global pool dedup: a job exists once (content_hash). Migration 02 intended this,
-- but the live table was missing it (PostgREST upsert ON CONFLICT failed). The agent
-- no longer requires it (upsert_jobs does select-then-insert/merge), but the index
-- makes dedup bulletproof and speeds the existence lookups. Safe + additive.
-- If a duplicate hash already exists, de-dupe first (keep newest scraped_at):
--   delete from jobs a using jobs b
--    where a.content_hash = b.content_hash and a.scraped_at < b.scraped_at;
create unique index if not exists jobs_content_hash on jobs (content_hash);
