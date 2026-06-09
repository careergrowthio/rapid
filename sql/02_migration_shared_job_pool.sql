-- ============================================================
-- RAPID — migration: shared global job pool
-- Run in the SQL Editor of your rebuild project. Safe on the empty DB.
-- Converts the per-client jobs table into ONE shared pool so a job is
-- scraped and stored once, then reused for every client.
-- ============================================================

-- 1. Drop the per-client column (also drops the client-scoped indexes on it)
alter table jobs drop column if exists client_id cascade;

-- 2. Global dedup: a job exists in the pool exactly once
create unique index if not exists jobs_content_hash on jobs (content_hash);
create index if not exists jobs_scraped_at on jobs (scraped_at desc);
create index if not exists jobs_posted_at  on jobs (posted_at desc);

-- 3. Per-client scoring cursor: only score pool jobs newer than this,
--    so we never re-score (or re-pull) a job for a client we've already shown it to
alter table clients add column if not exists last_scored_at timestamptz;
