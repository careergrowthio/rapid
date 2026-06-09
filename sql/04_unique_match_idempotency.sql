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
