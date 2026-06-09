-- ============================================================
-- RAPID — Supabase schema (single source of truth)
-- ============================================================
-- HOW TO RUN THIS (you can do this yourself):
--   1. Open your Supabase project
--   2. Left sidebar -> SQL Editor -> New query
--   3. Paste this whole file in and click "Run"
--   4. Left sidebar -> Table Editor: you should now see 7 tables
--
-- This replaces the Postgres + Supabase + Airtable sprawl with ONE store.
-- Comments explain each piece in plain English. Decisions you can change
-- later are marked  -- DECISION:
-- ============================================================

-- Needed for gen_random_uuid()
create extension if not exists pgcrypto;

-- ------------------------------------------------------------
-- 1. CLIENTS — one row per client (identity + status)
-- ------------------------------------------------------------
create table clients (
  id                uuid primary key default gen_random_uuid(),
  name              text not null,
  email             text not null,
  phone             text,
  linkedin_url      text,
  resume_url        text,                       -- master resume (Google Drive link; human-built at onboarding, consistent format)
  base_resume       text,                       -- cached text of the master (read from Drive once on activation/update); tailored per job from this, never re-parsed live
  subscription_tier text,                       -- DECISION: 'Guided' | 'Elite'
  status            text not null default 'rapid_ready',
                    -- 'rapid_ready' (onboarded) | 'rapid_active' (searching) | 'paused'
                    -- only promote to 'rapid_active' once the master resume exists in Drive (resume_url set + base_resume cached)
  engagement_start  date,
  engagement_end    date,                        -- ~4 months after start
  last_scored_at    timestamptz,                 -- pool jobs newer than this get scored next run (so each job is scored at most once per client)
  created_at        timestamptz not null default now()
);

-- ------------------------------------------------------------
-- 2. MATCH_PROFILES — the targeting criteria + the tuning knobs
--    This is the "small brain": weights + thresholds live here and
--    get adjusted by the learning loop over time.
-- ------------------------------------------------------------
create table match_profiles (
  client_id            uuid primary key references clients(id) on delete cascade,

  -- Targeting (what the matcher scores against)
  seniority_summary    text,                     -- the long seniority/scope blurb
  scope                text,
  industries           text[],                   -- e.g. {'CPG food & beverage','retail'}
  job_titles           text[],                   -- the human-readable target titles
  title_search_queries text[],                   -- the Boolean queries Apify uses
  expanded_queries     text[],                   -- adjacent-title + metro-widened queries (generated at onboarding); used when search_mode='expanded'
  search_mode          text not null default 'standard',  -- 'standard' | 'expanded' (auto-set after 7 starved days; queries widen, the 70% bar NEVER does)
  skills               text[],
  preferred_locations  text[],                   -- e.g. {'Mesa, Arizona'}
  work_arrangements    text[],                   -- {'Remote'} | {'Remote','Hybrid'} etc.
  salary_min           integer,                  -- bottom of stated range
  salary_max           integer,
  salary_floor_hard    integer,                  -- HARD knockout: below this = drop
  deal_breakers        text[],                   -- e.g. {'relocation'}
  geography_constraint text default 'US_only',   -- DECISION: keep US-only or widen

  -- Tuning knobs (the methodology)
  -- weights: how much each SOFT factor counts toward the composite (0-1, sum ~1)
  weights              jsonb not null default '{
      "seniority": 0.20, "industry": 0.10, "skills": 0.20,
      "title": 0.15, "salary": 0.25, "location": 0.10 }'::jsonb,   -- DECISION: salary weighted high by default
  -- per-factor minimums a soft factor must hit to count as a real fit (0-100)
  factor_thresholds    jsonb not null default '{
      "seniority": 50, "industry": 40, "skills": 50,
      "title": 50, "salary": 50, "location": 40 }'::jsonb,
  -- email floor: nothing below this fit % is ever emailed (the sacred 70% bar)
  email_floor          numeric not null default 70,
  -- strong-fit cutoff: >= this is "Strong fit"; 70-84 is "Possible fit". Both emailed.
  strong_threshold     numeric not null default 85,   -- raised for high-volume clients

  updated_at           timestamptz not null default now()
);

-- ------------------------------------------------------------
-- 3. JOBS — ONE shared, global pool. Each job is scraped and stored ONCE,
--    then matched against ANY client. We never re-scrape a job we already
--    have. Per-client "already surfaced" is tracked via the matches table.
-- ------------------------------------------------------------
create table jobs (
  id              uuid primary key default gen_random_uuid(),
  source          text,                          -- 'fantastic_linkedin', 'fantastic_greenhouse', etc.
  external_job_id text,
  job_title       text,
  company         text,
  location        text,
  is_remote       boolean,
  salary_range    text,
  job_url         text,
  job_description text,
  posted_at       timestamptz,
  scraped_at      timestamptz not null default now(),
  content_hash    text unique,                    -- GLOBAL dedup: a job lives in the pool exactly once
  matched_queries text[]                          -- WHICH search queries found this job (provenance).
                                                  -- A client only scores jobs whose tags intersect THEIR queries --
                                                  -- this is what keeps Claude cost sane at 100+ clients.
                                                  -- Upsert must MERGE tags on conflict (job re-found by another query).
);
create index jobs_scraped_at on jobs (scraped_at desc);
create index jobs_posted_at  on jobs (posted_at desc);

-- ------------------------------------------------------------
-- 4. MATCHES — the scored result for each job
-- ------------------------------------------------------------
create table matches (
  id                    uuid primary key default gen_random_uuid(),
  client_id             uuid references clients(id) on delete cascade,
  job_id                uuid references jobs(id) on delete cascade,
  composite_score       numeric,                  -- 0-100 weighted blend
  dimension_scores      jsonb,                    -- {"seniority":80,"salary":90,...}
  deal_breaker_triggered boolean default false,
  tier                  text,                     -- 'strong' | 'possible' | 'below'
  match_reason          text,                     -- Claude's justification + evidence
  confidence            numeric,                  -- 0-1, to suppress weak/false matches
  resume_url            text,                     -- tailored resume (Drive link)
  status                text not null default 'pending',
                        -- 'pending' | 'sent' | 'shown' | 'queued' | 'dropped'
  created_at            timestamptz not null default now(),
  sent_at               timestamptz
);
create index matches_client_created on matches (client_id, created_at desc);

-- ------------------------------------------------------------
-- 5. FEEDBACK — client reactions (feeds the learning loop)
-- ------------------------------------------------------------
create table feedback (
  id          uuid primary key default gen_random_uuid(),
  client_id   uuid references clients(id) on delete cascade,
  match_id    uuid references matches(id) on delete set null,
  reaction    text,                               -- 'rejected'|'applied'|'interviewed'|'liked'
  reason      text,                               -- why (drives threshold/weight nudges)
  created_at  timestamptz not null default now()
);

-- ------------------------------------------------------------
-- 6. CLIENT_MEMORY — freeform per-client notes Claude curates
-- ------------------------------------------------------------
create table client_memory (
  client_id  uuid primary key references clients(id) on delete cascade,
  memory     text,                                -- the "small brain" in prose
  updated_at timestamptz not null default now()
);

-- ------------------------------------------------------------
-- 7. RUN_LOG — observability + the HONEST health metric
--    (quality matches per window, not "did the pipeline run")
-- ------------------------------------------------------------
create table run_log (
  id                 uuid primary key default gen_random_uuid(),
  client_id          uuid references clients(id) on delete set null,
  run_started_at     timestamptz not null default now(),
  run_finished_at    timestamptz,
  jobs_scraped       integer default 0,
  jobs_after_filter  integer default 0,
  jobs_scored        integer default 0,
  strong_matches     integer default 0,
  possible_matches   integer default 0,
  sent_count         integer default 0,
  claude_tokens      integer default 0,
  apify_cost_usd     numeric default 0,
  funnel             jsonb,                      -- per-stage kill counts + near-misses: WHY a client got few/zero matches this run
  error              text
);
create index run_log_client_time on run_log (client_id, run_started_at desc);

-- ------------------------------------------------------------
-- HEALTH VIEW — answers "is this client getting value?"
-- Replaces the false-positive "pipeline ran = Success" signal.
-- ------------------------------------------------------------
create view client_health as
select
  c.id   as client_id,
  c.name,
  c.status,
  -- "quality match" = any match that was actually emailed (>=70%, strong or possible)
  count(m.*) filter (where m.status = 'sent'
                       and m.created_at > now() - interval '7 days')   as quality_last_7d,
  count(m.*) filter (where m.tier = 'strong' and m.status = 'sent'
                       and m.created_at > now() - interval '7 days')   as strong_last_7d,
  max(m.created_at) filter (where m.status = 'sent')                   as last_quality_match,
  lf.funnel                                                            as latest_funnel,  -- the WHY
  case
    -- 5-day grace for new clients, then ALERT within 5 starved days (with the WHY attached)
    when coalesce(c.engagement_start::timestamptz, c.created_at) < now() - interval '5 days'
     and count(m.*) filter (where m.status = 'sent'
                             and m.created_at > now() - interval '5 days') = 0
      then 'ALERT: 0 emailed matches in 5 days -- see latest_funnel for the limiting gate'
    when count(m.*) filter (where m.tier = 'strong' and m.status = 'sent'
                             and m.created_at > now() - interval '14 days') = 0
     and coalesce(c.engagement_start::timestamptz, c.created_at) < now() - interval '14 days'
      then 'WATCH: only possibles, no strong matches in 14 days -- consider tuning'
    else 'ok'
  end as health
from clients c
left join matches m on m.client_id = c.id
left join lateral (
  select r.funnel from run_log r
  where r.client_id = c.id and r.funnel is not null
  order by r.run_started_at desc limit 1
) lf on true
where c.status = 'rapid_active'
group by c.id, c.name, c.status, lf.funnel;

-- ============================================================
-- NOTE FOR THE DEV (the ~20% to finish):
--   Row Level Security (RLS) policies are NOT set here. The agent
--   will use the Supabase service role (bypasses RLS); the dashboard
--   needs read policies scoped to the right users. Add RLS before
--   anything client-facing touches these tables.
-- ============================================================
