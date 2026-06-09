# Rapid — Complete Handoff

*Developer build specification. Everything needed in one document: what Rapid is, why the current build fails, every locked decision, the scoring methodology, the target architecture and cost model, the phased build plan, the scope of work, the client-provided inputs, and the runnable artifacts (Appendices A-H, also attached as standalone files).*

## Contents
1. Overview
2. How it works today
3. Why it isn't working
4. Decisions & resolved logic (LOCKED)
5. Scoring methodology
6. Target architecture, stack & cost model
7. Build plan (phased)
8. Developer scope of work
9. Client-provided inputs & configuration
10. Launch sequence
- Appendix A — Supabase schema (SQL)
- Appendix B — Migration: shared job pool (SQL)
- Appendix C — Patch: location weights (SQL)
- Appendix D — Patch: coverage ladder + funnel telemetry (SQL)
- Appendix E — Patch: 100-client scale + 5-day alerts (SQL)
- Appendix F — Matching skill
- Appendix G — Resume-tailoring skill
- Appendix H — Pipeline scaffold (Python)

---

## 1. Overview
**Rapid is a be-first-in-line job-search engine.** The moment a fitting role is posted to a major job board, Rapid finds it, confirms it's a genuine match for the client, rewrites their resume to fit that exact posting, and emails it with a ready-to-apply link — often before most other candidates have seen the listing.

The product is built around one edge: speed. Application odds are highest in the first hours after a job goes live, so Rapid aims to deliver within an hour of posting and never surfaces anything more than a week old. It scrapes LinkedIn, Greenhouse, and other large platforms (via Apify), scores every new posting against the client's real criteria — title, seniority, skills, industry, location, salary, and hard deal-breakers — and sends only the roles that clear that client's bar, each with a resume already tailored to the specific job. Clients onboard once through an AI interview; from there Rapid runs on autopilot for the length of their engagement (~4 months) and is designed to get sharper for each person over time.

## 2. How it works today
The current system runs in n8n across four nested workflows over a tangle of Postgres + Supabase + Airtable.

- **Schedule:** hourly, ~6am-9pm, weekdays only, with a 1-hour scrape window — so nights and weekends (the majority of the week) are never scraped.
- **Engine layers:** an orchestrator loops active clients in batches of 5 (fire-and-forget) → a Data Hygiene subflow validates/upserts → "Job Search Runner v6" per client → "Core search" scrapes the Apify Fantastic Jobs actors (LinkedIn/Greenhouse/career-sites) via per-client title×location queries → "LLM Job Matching" (`gpt-5.4-mini`) scores six factors 0/1/2.
- **The match gate:** `final_recommend_apply` is true only if all six factor scores clear that client's per-dimension thresholds (stored in `rapid_match_profile`) AND no deal-breaker fired. The displayed percentage (`total_score/12`) is informational; the gate is the threshold conjunction.
- **Output:** passing matches get a docx resume (Google Drive), are capped at 1/day, and are emailed (Gmail).

Key weaknesses baked in: the daily cap runs at **1** (spec wants up to 5), the per-client thresholds are **set at onboarding and never tuned**, salary is **not weighted as critical** (it's one of six equal factors), and the schedule + 1-hour window miss most postings.

## 3. Why it isn't working
The reported symptoms trace to two structural problems multiplying: the intake funnel is too narrow, and the per-client thresholds never move.

- **Zero matches:** most of the week is never scraped (weekday-daytime + 1h window); a job must clear all six thresholds simultaneously (one strict dimension zeroes a client out); and there is no coverage backstop.
- **Persistent bad matches:** thresholds are created at onboarding and never updated from feedback, so loose defaults keep passing weak jobs forever; salary isn't weighted as critical; and an item-lineage bug in the loop can score a client against another client's criteria.
- **Learning broken:** the per-client "small brain" has a home (the thresholds) but nothing writes feedback back to it.
- **Filter overload:** six simultaneous thresholds + deal-breaker veto + US-only + 1h freshness + 3 default sources + 1/day cap + a 24h guard — each reasonable alone, together they choke the funnel.

**Unifying root cause:** because thresholds are static after onboarding, every client is frozen at their default — too-strict defaults yield zero matches, too-loose defaults yield persistent bad matches, and nothing can move either group. Build the tuning loop, widen the freshness/coverage funnel, and most symptoms collapse. The architecture is sound; the operation around it is incomplete.

*Live example — the "Mesa" client:* 35 jobs surfaced, 0 quality matches over ~8 weeks, while Health Check read "Success." The profile is a senior CPG national-accounts sales exec who is **remote-only + no-relocation** — an extremely narrow target. The six-way AND gate correctly knocked out nearly everything, but nothing flagged the over-narrow config and the health signal measured "pipeline ran," not "client got value."

## 4. Decisions & resolved logic (LOCKED)
*The authoritative spec. Reviewed for internal consistency and signed off.*

**Matching bar & tiers.** A job is emailed only if its weighted fit score is **≥ 70%** (the 70% floor is sacred). Within emailed matches, two honestly-labeled tiers, **both emailed**:
- **Strong fit (≥ 85%)** — "strong match, apply now."
- **Possible fit (70-84%)** — "worth a look, you decide."
- **Below 70% is never emailed.** Coverage for clients with nothing ≥70% is handled by the onboarding feasibility check + a human alert — never by lowering the bar.

**Quality match (one definition, everywhere).** Any match that was **emailed** (≥70%, Strong or Possible). Drives the health metric, the volume throttle, and the 30-day count.

**Schedule & freshness.** Run hourly, ~6am-11pm, weekdays — a fitting job posted in that window is delivered within ~1 hour of going live. The **effective scrape window per cycle is the time since the last successful refresh + a 1-hour overlap** (so an hourly cadence buys ~1-2h of postings, not the same 24h repeatedly), and it auto-widens over any gap — Monday's first run covers the whole weekend, an outage covers itself. Hard freshness cap **≤ 1 week**. DECISION (open, cheap with the shared pool): an optional reduced weekend cadence (e.g. every 3h) to keep weekend postings inside the ~1-hour promise.

**Delivery & limits.** Channels: **email + dashboard** (no SMS). Daily cap: **3 emailed/day**, strong first, topped with possibles. Overflow strong matches queue for the next day (expire on freshness); overflow possibles drop. **Dedup:** never re-send a job to a client; don't re-surface a still-open job.

**Coverage model (no client is ever silently starved).** Quality is never compromised for coverage — the 70% bar never moves. Instead, a five-rung ladder guarantees every zero-match state is prevented, auto-corrected, or diagnosed to a named cause: (1) a **blocking onboarding feasibility gate** — dry-run the criteria against ~30 days of pool jobs; under ~2 projected matches/week the client is **not activated** until a human adjusts criteria with them (widen titles, allow hybrid, revisit the floor); (2) **lenient unknown-handling** in the funnel — a job is filtered only on a *stated* violation; missing arrangement/salary/location data always passes through to scoring; per-factor floors are **clamped at 60** in code so misconfiguration can't recreate the old six-way AND gate; (3) **funnel telemetry every run** — per-stage kill counts (prefilter reason, deal-breakers, confidence suppressions, which floor, sub-70 composites) plus the top near-misses (60-69) logged to `run_log.funnel`, so "zero matches" is always a labeled diagnosis, never a mystery; (4) **automatic query expansion after 5 starved days** — `search_mode` flips to 'expanded' and the scrape adds the client's pre-generated adjacent-title + metro-widened queries (widening the *search*, never the *bar*); (5) **team notification within 5 days, with the WHY** — the client_health view ALERTs at 5 starved days (with a 5-day grace for new clients) carrying the latest funnel diagnosis, AND the pipeline pushes an ops email (Postmark) listing every alerting client and their limiting gate. A client is therefore either receiving matches, or the team has been told they aren't and exactly why, within 5 days.

**Learning (small brain).** Per-client weights/thresholds adjust from feedback with **conservative nudges, a minimum feedback volume before moving, and human review of changes** — never silent auto-tuning.

**Cross-client learning (big brain) — in scope.** A weekly, low-cost Claude pass over aggregate match + feedback data maintains: cold-start defaults by role archetype, source/query effectiveness rankings, and global rubric calibration. Big brain sets priors; small brain personalizes. Initial per-client weights come from the archetype defaults.

**Salary & profile coherence.** Salary is a **hard knockout below the client's true floor, soft/weighted above it.** At onboarding, extract the seniority/comp signal from the **original** resume as ground truth and cross-check it against stated salary and titles; flag incoherent profiles (SVP titles at Director pay) for a human before go-live.

**Resume flow.** The team builds each client a clean **master resume in a consistent format** and places it in the masters Drive folder *before* the client is switched to `rapid_active` — so Rapid never parses a messy document. The engine reads the master once and caches it (`base_resume`); for each emailed match it produces a **tailored copy** into the output Drive folder. Tailoring is truthful repositioning only — surface, translate, and re-altitude real content, never invent claims — and runs fully automated, the single human checkpoint being the master + coherence review at onboarding.

## 5. Scoring methodology
How the 70% is computed so it's accurate and meaningful:

1. **Hard gates first (pass/fail, not scored):** deal-breakers, salary floor, geography (US-only), incompatible work arrangement → fail = excluded entirely, so a gate violation can never appear as a high %.
2. **Score each soft factor 0-100** against an anchored rubric *with required evidence*. Claude scores the four **judgment** factors (seniority, industry, skills, title/adjacency); code scores the **objective** ones (salary above floor, location/distance).
3. **Per-factor floors:** no single critical factor may be near-zero, even if the blend is high.
4. **Weighted composite** (per-client weights) → a true 0-100 fit %. Salary weighted high by default.
5. **Email iff** composite ≥ 70 **and** all per-factor floors pass **and** confidence is high (evidence-backed, not hallucinated). Label Strong (≥85) / Possible (70-84).
6. **Calibrate** the bar against real feedback (big brain) so 70% empirically predicts "worth applying."

The split matters for cost and reliability: the expensive/fuzzy judgment goes to Claude; objective math and all hard gates stay in deterministic code.

## 6. Target architecture, stack & cost model
A **structured Python pipeline** (not an autonomous agent), triggered hourly, hosted in an always-on container (Railway/Render/Fly — not serverless, to avoid timeout limits).

- **Supabase** = single datastore **and** clock (pg_cron). Consolidates the Postgres+Supabase+Airtable sprawl.
- **Claude** is called at exactly two points, each loading a skill as its system prompt: matching (per surviving job) and resume tailoring (per emailed match).
- **Apify** (Fantastic Jobs actors + similar) for scraping.
- **Google Docs / Drive** for resumes: read each client's consistent **master** from the masters folder once and cache it (`base_resume`); per emailed match, copy the master/template, merge the tailored content, and write the tailored resume to the output folder.
- **Email** via **Postmark** (Transactional message stream; verify the sending domain with DKIM + Return-Path) — better deliverability than Gmail.
- The existing **Vercel dashboard** reads Supabase.

**Cost model (designed in, not an afterthought):**
- **Shared job pool:** scrape ONCE per cycle into a global `jobs` pool (the union of all clients' queries, deduped on `content_hash`), then match every client against the pool. A role fitting five clients is pulled from Apify once.
- **Recency window only:** each cycle scrapes new postings in the lookback window, never the whole board.
- **Score each job at most once per client:** a per-client `last_scored_at` cursor means a client only scores pool jobs added since their last run.
- **Provenance scoping (the 100-client keystone):** every scraped job is tagged with the search queries that found it (`jobs.matched_queries`, merged on re-discovery); a client scores ONLY jobs whose tags intersect their own queries. Without this, every client would score every other client's niches and Claude cost would explode with client count; with it, each client's Claude spend tracks their own niche's posting flow (typically a handful of jobs/hour), identical coverage to a per-client scrape at shared-pool cost. At 100+ clients the hourly cycle stays well inside the hour with bounded concurrency (Task 8).
- **Cheap pre-filter before any Claude call:** geography, freshness, salary floor, arrangement, dedup all happen in code; Claude never touches raw scrape volume.
- **Tier the models:** Haiku (or cheapest adequate) for the high-volume matching; a stronger model only for the rare resume step. **Prompt-cache** the skill system prompts.

Net: Apify and Claude spend scale with *genuinely new postings in your clients' niches per cycle* — once — not with client count or the whole board.

## 7. Build plan (phased)
Already done: the Supabase schema (Appendix A, deployed + seeded with the Mesa test client), the migration to the shared pool (Appendix B), the matching and resume skills (Appendices F, G), and the pipeline scaffold with all deterministic logic implemented (Appendix H).

- **Phase 1 — Data foundation.** Run the schema + migration; consolidate existing clients into Supabase. *(Schema/migration done; client migration pending.)*
- **Phase 2 — Scraping.** Implement the shared-pool scrape (Apify Fantastic Jobs, recency window, dedup insert).
- **Phase 3 — Brain.** Skills are written; wire the two Claude calls (already stubbed in the scaffold).
- **Phase 4 — Scoring & delivery.** Implement salary/location scoring, the parsers, the Google Docs merge, and email.
- **Phase 5 — Clock & orchestration.** pg_cron schedule + bounded concurrency.
- **Phase 6 — MVP cutover.** Shadow-run beside n8n, backtest Mesa, tune, migrate clients, retire n8n.
- **Phase 7 (healthy) — Learning + coverage.** Feedback capture → conservative human-reviewed tuning (small brain); weekly big-brain pass; onboarding feasibility meter; honest health signal.

MVP ≈ 2-3 weeks focused; fully healthy ≈ 4-5 weeks.

## 8. Developer scope of work
The deterministic logic is implemented in the scaffold (Appendix H); the items below are the `# TODO[DEV]` integration points. Estimates assume comfort with Python, Supabase, and external APIs.

1. **Supabase data layer** (~0.5-1d) — implement the DB functions; `todays_sent_count` counts only today's `status='sent'`.
2. **Apify scrape into the shared pool** (~1-2d) — `refresh_job_pool()` + `scrape_jobs(query_sets)`: deduped union of (location × title) queries, Fantastic Jobs actors, effective window = since-last-refresh + 1h overlap. **Tag each job with the query that found it**, and make the upsert `ON CONFLICT (content_hash) DO UPDATE` **merging `matched_queries`** (a job re-found by another client's query gains the tag). One scrape per cycle, not per client. Gate pricier sources to run infrequently.
3. **Three parser helpers** (~0.5d) — `_is_us`, `_arrangement_compatible`, `_parse_salary_max`. Pure, testable.
4. **`location_score`** (~0.5-1d) — remote match → high; else distance bands from preferred locations.
5. **Google Docs merge + master read (`render_resume`, `refresh_base_resume`)** (~1-2d) — copy the **global template** Doc and fill its placeholders from `tailored_resume` (the client's facts already live in the cached `base_resume`; do not copy the master at render time). The template holds one experience block with 3 bullet lines — duplicate the block per experience entry and per bullet (variable counts) and remove empty sections rather than leaving headings. Write the result to the **output** Drive folder and return the link. Plus a one-time `refresh_base_resume()` that reads the **master** from Drive (Doc ID/URL stored in `clients.resume_url`) and caches it to `base_resume` on activation/update.
6. **Email send (`send_match_email` + `notify_ops`)** (~0.5-1d) — Postmark (Transactional stream); render `rapid_match_email_template.md` (Strong/Possible variants, field mapping included); respect the cap. Plus the ops alert: end of each cycle, email the team every `client_health` ALERT row with its `latest_funnel` WHY (the 5-day starvation guarantee's push half). Dashboard feed = `matches` WHERE `status='sent'` joined to `jobs`; pending/queued never display.
7. **Harden Claude JSON parsing** (~0.5d) — strip stray text, validate shape, retry once.
8. **Bounded concurrency** (~1d) — parallelize clients within the hour, respect rate limits, keep runs idempotent (no double-sends).
9. **RLS policies** (~0.5d) — before anything client-facing reads the tables.
10. **Deploy + schedule** (~0.5-1d) — container + secrets; ~6am-11pm weekday window; scrape window auto-computed (since-last-refresh + overlap, gap-widening — implemented in `refresh_job_pool`). Note: pg_cron alone cannot run the container — either pg_cron + pg_net HTTP call to a container webhook, or the host platform's own scheduler. The 3/day cap's "today" is evaluated in `CAP_TIMEZONE` (business timezone), not UTC.

**Cost controls (required):** model tiering (cheap for matching, stronger for resume), prompt-cache the skills, keep the deterministic pre-filter strictly before any Claude call, shared pool + recency window + per-client scoring cursor.

**Definition of done (MVP):** (1) the Mesa test client runs end to end and correctly returns few/zero strong matches with the health view flagging it; (2) a normal client gets up to 3 emailed matches/day, strong first, each with a truthfully tailored resume in the configured template format + apply link, nothing below 70% or older than a week, no duplicates; (3) runs hourly on schedule, costs bounded by the controls above, errors logged not fatal.

## 9. Client-provided inputs & configuration
These are supplied by the client; treat them as configuration/secrets and confirm receipt before building the integrations that depend on them.

**Credentials / keys:**
- Anthropic API key; Apify token; Supabase Project URL + service-role key.
- A **Google service account** JSON key — created for this project (not the legacy n8n OAuth client).
- A **Postmark** Server API token, with the sending domain verified (DKIM + Return-Path).

**Google Drive** (each shared with the service account's email):
- Resume **template** Doc — Editor — plus the list of `{{placeholder}}` field names in it.
- **Masters** folder `1YzCQF-V2K1LLbGiZgvsov7KEzoTqX9YT` — Viewer/read — where the consistent master resumes live; read once per client and cache to `base_resume`.
- **Output** folder `1X6PUnQydfjImEwQOisF1NdJwPy6D71ZV` — Editor/write — where tailored resumes are written (prefer a Shared Drive over My Drive to avoid service-account ownership/quota limits).

**Content & data:**
- The **match email copy** is provided — `rapid_match_email_template.md` (built from the current production email; Strong + Possible variants with field mapping). Wire into `send_match_email()`.
- Each client's **expanded_queries** (adjacent titles + metro-widened locations) are generated at onboarding and stored on the match profile — used automatically when `search_mode='expanded'`.
- Existing client records to migrate into Supabase (the Appendix B migration and all three patches are applied to the project).

## 10. Launch sequence
- Shadow-run beside the existing n8n system; backtest the Mesa test client (Definition of Done #1).
- Tune weights/thresholds against real feedback; migrate clients gradually; retire n8n.
- Stand up the dashboard feasibility meter + feedback capture; add RLS before anything client-facing reads the tables.

---

# Appendices — runnable artifacts
*Each appendix is the current, verbatim content of the corresponding file.*

## Appendix A — Supabase schema (rapid_supabase_schema.sql)
```sql
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
```

## Appendix B — Migration: shared job pool (migration_shared_job_pool.sql)
```sql
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
```

## Appendix C — Patch: location weights (patch_location_weights.sql)
```sql
-- ============================================================
-- PATCH: add 'location' to weights + factor floors
-- Run this in your existing Supabase project (SQL Editor -> Run).
-- Why: the engine computes a location score, but the original
-- weights had no 'location' key, so it counted for nothing.
-- ============================================================

alter table match_profiles alter column weights set default '{
    "seniority": 0.20, "industry": 0.10, "skills": 0.20,
    "title": 0.15, "salary": 0.25, "location": 0.10 }'::jsonb;

alter table match_profiles alter column factor_thresholds set default '{
    "seniority": 50, "industry": 40, "skills": 50,
    "title": 50, "salary": 50, "location": 40 }'::jsonb;

-- Bring existing rows (e.g. the Mesa test client) up to the new shape:
update match_profiles set
  weights = '{
    "seniority": 0.20, "industry": 0.10, "skills": 0.20,
    "title": 0.15, "salary": 0.25, "location": 0.10 }'::jsonb,
  factor_thresholds = '{
    "seniority": 50, "industry": 40, "skills": 50,
    "title": 50, "salary": 50, "location": 40 }'::jsonb,
  updated_at = now();
```

## Appendix D — Patch: coverage ladder + funnel telemetry (patch_coverage_telemetry.sql)
```sql
-- ============================================================
-- PATCH: coverage ladder + funnel telemetry
-- Run this in your existing Supabase project (SQL Editor -> Run).
-- Why: guarantees no client is ever SILENTLY starved of matches.
--   - search_mode/expanded_queries: after 7 days with zero emailed
--     matches, the engine widens the client's SEARCH QUERIES
--     (adjacent titles, metro areas) -- never the 70% bar.
--   - run_log.funnel: per-stage kill counts + near-misses, so any
--     zero-match state is diagnosed to a named cause, not a mystery.
-- ============================================================

alter table match_profiles
  add column if not exists expanded_queries text[],
  add column if not exists search_mode text not null default 'standard';

alter table run_log
  add column if not exists funnel jsonb;
```

## Appendix E — Patch: 100-client scale + 5-day alerts (patch_scale_and_alerts.sql)
```sql
-- ============================================================
-- PATCH: 100-client scale + 5-day starvation alert
-- Run this in your existing Supabase project (SQL Editor -> Run).
-- Why:
--   - jobs.matched_queries (provenance): each client scores ONLY
--     jobs found by THEIR queries. Without this, every client
--     scores every other client's jobs -- Claude cost explodes at
--     100+ clients and the circuit breaker drops real matches.
--     NOTE FOR DEV: the jobs upsert must MERGE this array on
--     conflict (a job re-found by another query gains its tag).
--   - client_health: ALERT within 5 starved days (was 14), with a
--     5-day grace for new clients, and the WHY attached (the
--     latest run's funnel diagnosis).
-- ============================================================

alter table jobs add column if not exists matched_queries text[];

create or replace view client_health as
select
  c.id   as client_id,
  c.name,
  c.status,
  count(m.*) filter (where m.status = 'sent'
                       and m.created_at > now() - interval '7 days')   as quality_last_7d,
  count(m.*) filter (where m.tier = 'strong' and m.status = 'sent'
                       and m.created_at > now() - interval '7 days')   as strong_last_7d,
  max(m.created_at) filter (where m.status = 'sent')                   as last_quality_match,
  lf.funnel                                                            as latest_funnel,  -- the WHY
  case
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
```

## Appendix F — Matching skill (rapid_matching_skill.md)
````markdown
---
name: rapid-job-matching
description: Score how well a single job posting matches a Rapid client's match profile. Use whenever the Rapid pipeline needs to judge a scraped job against a client — produces per-factor fit scores (0-100) with evidence and confidence, plus a deal-breaker assessment. Does NOT decide salary/location fit or the final send decision (those are handled deterministically in code around this skill).
---

# Rapid — Job Matching

You are the matching brain for Rapid, a service that finds freshly-posted jobs for executive job-seekers. Your job: judge how well **one job posting** fits **one client**, and return a structured, evidence-backed assessment. A wrong call wastes the client's time and erodes trust, so be honest and specific — never inflate a score to be helpful.

## What you receive
- **The job posting:** title, company, location, remote flag, salary (may be absent), and full description.
- **The client match profile:** a seniority/scope summary, target industries, target job titles, and skills. (You do NOT receive the resume at this step; judge from the profile.)
- **Deal breakers:** explicit conditions that make a job unacceptable to this client.

You may also be told the results of the deterministic checks (salary-vs-floor, geography, work-arrangement). Use them only as context — do not re-score them.

## What you produce
Strict JSON, nothing else:

```json
{
  "factor_scores": {
    "seniority": { "score": 0, "evidence": "", "confidence": 0.0 },
    "industry":  { "score": 0, "evidence": "", "confidence": 0.0 },
    "skills":    { "score": 0, "evidence": "", "confidence": 0.0 },
    "title":     { "score": 0, "evidence": "", "confidence": 0.0 }
  },
  "deal_breakers": { "triggered": false, "which": [], "evidence": "" },
  "overall_assessment": ""
}
```

You score **only these four judgment factors** (each 0–100) and the deal breakers. Salary fit, location/work-arrangement fit, the weighted composite, the per-factor floors, and the Strong/Possible/drop decision are all computed in code — not by you.

## The 0–100 scale (use the same anchors for every factor)
- **90–100** — clear, strong alignment; meets or exceeds.
- **70–89** — solid alignment; minor, non-blocking gaps.
- **50–69** — partial; real gaps but a defensible stretch.
- **25–49** — weak; major gaps.
- **0–24** — no meaningful alignment.

## How to score each factor
**seniority** (seniority & experience) — Does the candidate's level and scope fit the role? Reward a match *or* being slightly above the role's level (a senior person for a role they'd clearly clear). Penalize being clearly under-qualified, or so over-qualified the role is a step down.

**industry** (industry alignment) — Direct industry match scores highest. **Adjacent or transferable** experience is legitimate and should score in the 50–89 band when the transfer is reasonable and explainable from a hiring manager's view — don't require an exact industry. No meaningful overlap → low.

**skills** (skills alignment) — Overlap between the candidate's demonstrated skills (resume + profile) and the job's requirements. Weight *required* skills more than *nice-to-haves*. Strong overlap on the must-haves → high, even if some secondary skills are missing.

**title** (title adjacency) — How close is the posting's title to the client's target titles? Exact or clear equivalent → high. Common variations, or one level up from current → solid. Reward adjacency (similar function/seniority) rather than demanding a literal string match. Unrelated or a clear step down → low.

## Evidence and confidence (this is how we kill false matches)
- For every factor, **cite specific evidence** from the resume or job description in `evidence`. No vague justifications.
- Set **confidence** (0–1) by how directly the evidence supports the score. Explicit, stated facts → high. Inference or thin signal → low. Low confidence on a high score tells the system to treat the match cautiously, so be truthful here.

## Deal breakers
Evaluate each stated deal breaker against the posting. Set `triggered: true` **only on a clear violation**, list which one(s) in `which`, and quote the triggering evidence. If it's ambiguous, do **not** trigger — note the ambiguity in `overall_assessment` instead. (A triggered deal breaker is a hard knockout, so the bar for triggering is "clearly violated," not "might.")

## Rules
- Never fabricate or assume facts not present in the inputs. If the description is thin, say so and lower confidence.
- Do not score salary or location — code handles those.
- Do not decide whether to send — code applies the gates, weights, floors, and tier.
- Output the JSON only. No preamble, no markdown fences.
````

## Appendix G — Resume-tailoring skill (rapid_resume_tailoring_skill.md)
````markdown
---
name: rapid-resume-tailoring
description: Rewrite a Rapid client's resume to fit one specific job posting, using only truthful repositioning - translate, surface, re-altitude. Use when a job has matched and a tailored resume must be produced for delivery. Runs unattended, so it never invents, inflates, or adds unconfirmed claims; when in doubt it omits.
---

# Rapid — Resume Tailoring

You are three experts working in sequence: an elite resume writer, an ATS specialist, and a senior hiring manager who screens 200+ resumes a week. You are not a cheerleader and do not default to positivity. Your job: rewrite the client's resume so it fits this specific job, using only what is true.

## HARD RULE (read first)
You may only **reposition, reword, reorder, and surface** what is ALREADY TRUE in the client's resume. You may NOT invent, inflate, or imply experience, titles, metrics, dates, tools, certifications, or seniority the client does not have. There are exactly three legitimate moves:
1. **TRANSLATE** - use the job description's vocabulary for things the client genuinely did.
2. **SURFACE** - pull real wins that are buried or understated up to the top.
3. **RE-ALTITUDE** - describe real work at the right level (outcomes, not tasks).

**Critical difference from an interactive session:** there is NO human here to confirm anything. So you may **not** add net-new lines or "suggested additions." If a change would require confirming a fact you can't verify from the resume, **omit it** and record it as a gap instead. When in doubt, leave it out - never assume, never invent. Do not use em dashes anywhere; use a hyphen.

## What you receive
- The client's **master resume** — a clean, consistent resume built at onboarding and read from their Drive (the source of truth for everything you may say).
- The **full job description**.
- The **matching evidence** from the scoring step (the real strengths this job matched on) - emphasize these, since they are the true, relevant wins.

## Process (in order)
**1. Critical themes.** Identify the 5-7 most critical skills/themes in the JD (repeated terms, emphasized language, required vs preferred). Weight required above preferred. This is the lens for everything below.

**2. Map themes to real evidence.** For each theme, find the real line(s) in the resume that support it. A theme is "backed" only if the resume genuinely supports it.

**3. Rewrite, truthfully.** For backed themes, apply the three moves:
- TRANSLATE the client's real work into the JD's vocabulary.
- SURFACE buried/understated real wins toward the top.
- RE-ALTITUDE task-level lines into outcome-level lines (real outcomes only).
Rewrite the summary/headline, the bullets, and the skills section this way. Leave already-strong, well-aligned bullets untouched.

**4. Do not fill unbacked themes.** If a critical theme has no real basis in the resume, do NOT fabricate or imply it. Leave it out of the resume and record it in `honest_gaps`.

**5. Content, not formatting.** Produce tailored **content** only - the final layout comes from the client's Google Docs template, so add no styling or markdown. Keep total content concise enough to fit the template's ~2-page layout; if it would overflow, trim the weakest real bullets first. No em dashes - hyphens only. Preserve all real facts (employers, titles, dates, metrics) exactly.

## Output (strict JSON, nothing else)
You produce tailored **content** only. The final formatting comes from the client's existing **Google Docs template** - a render step merges these fields into it, producing a Google Doc in the client's standard format (the same output produced today). Do not add styling or markdown.

```json
{
  "tailored_resume": {
    "headline": "",
    "summary": "",
    "experience": [
      { "company": "", "title": "", "location": "", "start": "", "end": "", "bullets": ["", ""] }
    ],
    "skills": ["", ""],
    "education": [ { "institution": "", "credential": "", "year": "" } ],
    "certifications": []
  },
  "change_log": [
    { "move": "translate|surface|re-altitude", "theme": "", "before": "", "after": "" }
  ],
  "kept_as_is": ["bullets left untouched because they were already strong"],
  "honest_gaps": ["theme the resume does not genuinely support, and why"]
}
```
`tailored_resume` is merged into the template by code, yielding the final Google Doc (the `resume_link`). `change_log`, `kept_as_is`, and `honest_gaps` are stored for the dashboard and human team - the gaps are valuable signal, not something to paper over.

**Preserve facts exactly:** `company`, `title`, `location`, `start`, `end` are copied verbatim from the source resume and never altered. Only `summary`, `bullets`, and `skills` (wording, ordering, emphasis) are tailored. Omit any `education`/`certifications` entry that isn't genuinely in the source.

## Rules
- Never invent or imply titles, metrics, dates, tools, certifications, or seniority not in the resume.
- Only the three moves. No net-new factual claims. Omit-when-in-doubt.
- Preserve identity, employers, titles, and dates exactly; you reposition wording, bullets, emphasis, and ordering - you do not rewrite history.
- Output content only - formatting is owned by the client's template.
- No em dashes. Output the JSON only - no preamble, no fences.
````

## Appendix H — Pipeline scaffold (rapid_agent.py)
```python
"""
RAPID — pipeline scaffold (shared job pool)
===========================================
Structured pipeline (not an autonomous agent): deterministic orchestration with
TWO Claude calls (matching skill, resume skill).

COST MODEL (the important part):
  - Scrape ONCE per cycle into a shared global pool (refresh_job_pool), NOT per
    client. A job that fits five clients is pulled from Apify once.
  - Only the recency window is scraped each cycle (new postings since last run),
    never the whole board. Window widens automatically after a missed run
    (Monday's first run covers the weekend).
  - Each client scores only pool jobs added since THEIR last run (last_scored_at),
    so no job is re-scored (or re-pulled) for a client who already saw it.
  - Deterministic pre-filter runs before any Claude call; cheap model for the
    high-volume scoring, stronger model only for the rare resume step;
    prompt-cache the skill system prompts.
  - MAX_SCORED_PER_RUN caps Claude calls per client per cycle (circuit breaker).

CONTRACT (keep these aligned -- they are aligned now, keep them that way):
  - Matching-skill factor keys == match_profiles.weights/factor_thresholds keys
    == the keys used here: seniority, industry, skills, title (+ salary,
    location computed in code).
  - Per-client email_floor / strong_threshold come from match_profiles,
    NOT from global constants.

Env: ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, APIFY_TOKEN,
     GOOGLE_SERVICE_ACCOUNT_JSON, RESUME_TEMPLATE_DOC_ID, POSTMARK_SERVER_TOKEN
Install: pip install anthropic supabase apify-client google-api-python-client
Model strings: verify current values at https://docs.claude.com/en/api/overview
"""

import os, json, hashlib, datetime as dt
from anthropic import Anthropic

# ============================ CONFIG (locked decisions) ======================
DAILY_CAP            = 3
DEFAULT_EMAIL_FLOOR  = 70     # fallback only; the per-client value in match_profiles wins
DEFAULT_STRONG       = 85     # fallback only; per-client strong_threshold wins
MIN_SCRAPE_HOURS     = 2      # scrape window floor per cycle (hourly cadence + safety margin)
SCRAPE_OVERLAP_HOURS = 1      # overlap buffer so boundary postings are never missed
# NOTE: the old fixed 24h window re-bought the same day from Apify 24x/day. The
# effective window is now (time since last successful refresh + overlap), so an
# hourly cadence scrapes ~1-2h of postings; any gap (weekend, outage) auto-widens.
FRESHNESS_MAX_DAYS   = 7
MIN_CONFIDENCE       = 0.5    # min per-factor confidence from the matching skill; below -> not emailed
MAX_SCORED_PER_RUN   = 150    # circuit breaker: max Claude scoring calls per client per cycle
SIMILAR_WINDOW_DAYS  = 30     # don't re-send same company+title to a client within this window
FLOOR_CAP            = 60     # per-factor floors are clamped here unless profile sets allow_strict_floors
                              # (prevents misconfigured floors from recreating the old six-way AND gate)
STARVATION_DAYS      = 5      # 0 emailed matches for this long -> auto-widen QUERIES (never the bar)
                              # + the client_health view ALERTs at 5 days with the WHY (funnel),
                              # and notify_ops pushes it to the team -- spec: notified within 5 days
CAP_TIMEZONE         = "America/Phoenix"  # DECISION: "today" for the 3/day cap is business TZ, not UTC
JUDGMENT_FACTORS     = ("seniority", "industry", "skills", "title")  # must match skill output keys
MODEL_MATCH  = "claude-haiku-4-5-20251001"  # high-volume scoring -> cheapest adequate model; verify string
MODEL_RESUME = "claude-sonnet-4-6"          # rare, quality-sensitive step

claude = Anthropic()

def load_skill(path):
    with open(path) as f: return f.read()
MATCHING_SKILL = load_skill("rapid_matching_skill.md")
RESUME_SKILL   = load_skill("rapid_resume_tailoring_skill.md")

# ============================ DB — Supabase ==================================
# TODO[DEV]: wire supabase-py (create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY))
def get_active_clients():            ...  # clients WHERE status='rapid_active'
def get_match_profile(client_id):    ...  # match_profiles WHERE client_id=...
def get_all_active_query_sets():     ...  # deduped union of (title query x preferred_location) over active clients;
                                          # for clients with search_mode='expanded', ALSO include their expanded_queries
def get_sent_count_since(client_id, days): ...  # COUNT matches status='sent' in last `days`
def set_search_mode(client_id, mode):      ...  # UPDATE match_profiles SET search_mode=...
def upsert_jobs(jobs):               ...  # INSERT INTO jobs ... ON CONFLICT (content_hash) DO NOTHING
def get_pool_jobs_after(since_ts, max_days, client_queries=None):
    ...  # jobs WHERE scraped_at > since AND posted_at > now()-max_days
         #      AND matched_queries && client_queries   -- provenance scope IN SQL (cheapest)
         # since_ts None (client's first run) => ALL pool jobs inside the freshness window
def get_matched_job_ids(client_id):  ...  # set of job_id already in matches for this client (any status)
def get_queued_matches(client_id):   ...  # matches WHERE status='queued' ORDER BY composite_score DESC (join jobs for posted_at, title, company, description)
def recently_sent_similar(client_id, company, title, days):
    ...  # bool: a 'sent' match for same (company, normalized title) within `days`
         # guards cross-source duplicates (same role, different board/url/hash)
def set_last_scored_at(client_id, ts): ...  # UPDATE clients SET last_scored_at=ts
def todays_sent_count(client_id):    ...  # COUNT matches status='sent' where sent_at::date = today IN CAP_TIMEZONE
def save_match(match):               ...  # INSERT into matches -> RETURN id
def update_match(match_id, fields):  ...  # UPDATE matches SET ... WHERE id=...
def get_last_pool_refresh_at():      ...  # ts of last successful pool refresh (run_log) -> for gap widening
def get_alerting_clients():          ...  # SELECT * FROM client_health WHERE health LIKE 'ALERT%'
def log_run(record):                 ...  # INSERT into run_log

# ============================ SCRAPE — Apify (Fantastic Jobs + similar) ======
def content_hash(job):
    return hashlib.sha256(f"{job['job_url']}|{job['job_title']}|{job['company']}".encode()).hexdigest()

def refresh_job_pool():
    """Run ONCE per cycle. Build the union of all active clients' title x location
    queries, scrape the recency window via the Fantastic Jobs actors (+ similar),
    and insert only jobs not already in the pool (global dedup on content_hash)."""
    last = get_last_pool_refresh_at()
    now  = dt.datetime.now(dt.timezone.utc)
    # window = time since last successful refresh + overlap; widens itself over any
    # gap (weekend, outage), never re-buys already-scraped hours on a normal cycle
    if last:
        gap_h = int((now - last).total_seconds() // 3600) + SCRAPE_OVERLAP_HOURS
    else:
        gap_h = FRESHNESS_MAX_DAYS * 24            # first ever run: fill the pool
    hours = min(max(gap_h, MIN_SCRAPE_HOURS), FRESHNESS_MAX_DAYS * 24)
    query_sets = get_all_active_query_sets()
    jobs = scrape_jobs(query_sets, hours)         # normalized job dicts + content_hash each
    upsert_jobs(jobs)                             # ON CONFLICT (content_hash) DO NOTHING

def scrape_jobs(query_sets, lookback_hours):
    """Call the Apify Fantastic Jobs actors (and similar) for each query in the
    deduped union, with a lookback_hours window. Return normalized job dicts:
    {source, external_job_id, job_title, company, location, is_remote,
     salary_range, job_url, job_description, posted_at, content_hash,
     matched_queries: [the query string(s) that found it]}."""
    # TODO[DEV]: apify-client; reuse the existing actor IDs + Boolean queries.
    #            Gate pricier sources (Indeed/Glassdoor) to run infrequently.
    #            TAG each job with the query that found it (provenance) and make
    #            upsert_jobs MERGE matched_queries on conflict -- this scoping is
    #            what keeps Claude cost flat per client at 100+ clients.
    return []

# ============================ PRE-FILTER (cheap, pre-Claude) =================
# RULE (coverage-critical): UNKNOWN NEVER KILLS. A job is filtered only on a
# STATED violation. Missing arrangement/salary/location data must pass through
# to scoring -- strict parsers silently starving clients is the old system's
# failure mode and must not be rebuilt here.
def prefilter(job, profile):
    """Return None if the job passes; otherwise the kill reason (for the funnel)."""
    if job.get("posted_at"):
        if (dt.datetime.now(dt.timezone.utc) - job["posted_at"]).days > FRESHNESS_MAX_DAYS:
            return "freshness"
    if profile.get("geography_constraint") == "US_only" and not _is_us(job):
        return "geography"
    if not _arrangement_compatible(job, profile):
        return "arrangement"
    floor = profile.get("salary_floor_hard")
    job_max = _parse_salary_max(job.get("salary_range"))
    if floor and job_max is not None and job_max < floor:
        return "salary_floor"
    return None

def _is_us(job):                 ...  # TODO[DEV] -- unknown/unparseable location => True (lenient)
def _arrangement_compatible(job, profile): ...  # TODO[DEV] -- arrangement not stated => True (lenient)
def _parse_salary_max(s):        ...  # TODO[DEV] "$125,000 to $175,000" -> 175000; unparseable => None

# ============================ CLAUDE CALLS ===================================
def _call_claude(model, skill, payload, max_tokens=1500):
    resp = claude.messages.create(
        model=model, max_tokens=max_tokens, system=skill,   # TODO[DEV]: enable prompt caching on `system`
        messages=[{"role": "user", "content": json.dumps(payload)}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    return json.loads(text)   # TODO[DEV]: harden parse + retry once

def score_job(job, profile):
    return _call_claude(MODEL_MATCH, MATCHING_SKILL, {
        "job": job,
        "client_profile": {k: profile.get(k) for k in
            ("seniority_summary","scope","industries","job_titles","skills")},
        "deal_breakers": profile.get("deal_breakers", []),
    })

def tailor_resume(job, profile, match_evidence, base_resume):
    return _call_claude(MODEL_RESUME, RESUME_SKILL, {
        "current_resume": base_resume,
        "job_description": job["job_description"],
        "matching_evidence": match_evidence,
    }, max_tokens=4000)

# ============================ DETERMINISTIC SCORING ==========================
def salary_score(job, profile):
    job_max = _parse_salary_max(job.get("salary_range"))
    if job_max is None: return 70          # unlisted salary: lenient (hard floor already checked)
    floor = profile.get("salary_min") or 0
    if job_max >= floor: return 100
    if job_max >= floor * 0.8: return 60
    return 20

def location_score(job, profile):
    ...  # TODO[DEV]: remote match -> 100; else distance bands from preferred_locations
    return 70

def composite(scores, weights):
    total_w = sum(weights.values()) or 1
    return round(sum(scores[f] * weights.get(f, 0) for f in scores) / total_w)

def effective_floors(profile):
    """Per-factor floors, clamped at FLOOR_CAP unless explicitly overridden.
    Misconfigured floors must not be able to silently starve a client."""
    floors = profile.get("factor_thresholds") or {}
    if profile.get("allow_strict_floors"):
        return floors
    return {f: min(v, FLOOR_CAP) for f, v in floors.items()}

def failed_floors(scores, floors):
    return [f for f in floors if scores.get(f, 0) < floors.get(f, 0)]

def passes_floors(scores, floors):
    return not failed_floors(scores, floors)

def min_confidence(judgment):
    return min((judgment["factor_scores"][f].get("confidence", 0) for f in JUDGMENT_FACTORS), default=0)

def tier_of(comp, profile):
    floor  = float(profile.get("email_floor")  or DEFAULT_EMAIL_FLOOR)   # per-client knobs from
    strong = float(profile.get("strong_threshold") or DEFAULT_STRONG)    # match_profiles, NOT globals
    if comp >= strong: return "strong"
    if comp >= floor:  return "possible"
    return None

# ============================ RESUME RENDER (Google Docs template) ===========
def render_resume(tailored_content, client):
    # TODO[DEV]: copy the GLOBAL template Doc (RESUME_TEMPLATE_DOC_ID) and fill its
    # {{placeholders}} from tailored_content (which already carries all the client's
    # facts, sourced from base_resume). Do NOT copy the client's master at render
    # time -- the master's content lives in clients.base_resume already.
    # NOTE: the template holds ONE experience block with 3 {{BULLET_n}} lines --
    # duplicate the block per experience entry and per bullet (variable counts),
    # and remove empty sections (e.g. no certifications) rather than leaving headings.
    return "https://docs.google.com/document/d/PLACEHOLDER"

def refresh_base_resume(client):
    # TODO[DEV]: read the client's master resume from Drive ONCE (on activation / when the
    # master changes), normalize to text/structure, and cache it to clients.base_resume.
    # The master's Doc ID/URL is stored in clients.resume_url at onboarding.
    # The per-job tailor step reads this cache -- it never re-fetches/re-parses Drive per job.
    ...

# ============================ EMAIL ==========================================
def send_match_email(client, match):
    # TODO[DEV]: Postmark (Transactional stream); render rapid_match_email_template.md
    #            (Strong/Possible variants). The same 'sent' match row is the
    #            DASHBOARD feed: the Vercel dashboard shows matches WHERE
    #            status='sent' (joined to jobs) -- pending/queued never display.
    ...

def notify_ops(alerting):
    # TODO[DEV]: Postmark email to the ops/team address listing each ALERT client
    #            with its latest_funnel WHY (which gate is starving them).
    #            This is the push half of the 5-day guarantee; the health view is the pull half.
    ...

# ============================ DELIVERY (shared by new + queued) ==============
def deliver(client, profile, job, comp, tier, scores, reason, evidence, conf, now,
            queued_match_id=None):
    """Tailor -> render -> save/send with crash-safe ordering:
    write 'pending' first, send the email, then mark 'sent'. A crash between the
    two leaves a 'pending' row (alertable), never a phantom 'sent'."""
    tailored   = tailor_resume(job, profile, evidence, client.get("base_resume"))
    resume_url = render_resume(tailored["tailored_resume"], client)
    fields = {"client_id": client["id"], "job_id": job["id"],
              "composite_score": comp, "dimension_scores": scores, "tier": tier,
              "match_reason": reason, "confidence": conf,
              "resume_url": resume_url, "status": "pending"}
    if queued_match_id:
        update_match(queued_match_id, fields); match_id = queued_match_id
    else:
        match_id = save_match(fields)
    send_match_email(client, {**fields, "job": job})
    update_match(match_id, {"status": "sent", "sent_at": now.isoformat()})

# ============================ ORCHESTRATOR ===================================
def client_queries(profile):
    """The queries this client is scoped to: their own searches, plus the
    expanded set when the coverage ladder has widened them."""
    q = set(profile.get("title_search_queries") or [])
    if profile.get("search_mode") == "expanded":
        q |= set(profile.get("expanded_queries") or [])
    return q

def in_client_scope(job, queries):
    """Provenance gate: a client scores ONLY jobs found by their own queries.
    (Same coverage as the old per-client scrape, at shared-pool cost.)"""
    return bool(set(job.get("matched_queries") or []) & queries)

def starvation_check(client, profile):
    """The coverage ladder, rung 2: zero emailed matches for STARVATION_DAYS ->
    widen the client's SEARCH QUERIES (adjacent titles, metro areas) by flipping
    search_mode to 'expanded'. The 70% bar and floors are NEVER lowered.
    (Rung 1 is the blocking onboarding feasibility gate; rung 3 is the 14-day
    human alert in the client_health view, now armed with funnel data.)"""
    if profile.get("search_mode") == "standard" and \
       get_sent_count_since(client["id"], STARVATION_DAYS) == 0:
        set_search_mode(client["id"], "expanded")
        log_run({"client_id": client["id"],
                 "error": f"coverage: 0 sent in {STARVATION_DAYS}d -> search_mode=expanded"})

def run_for_client(client):
    profile     = get_match_profile(client["id"])
    matched_ids = set(get_matched_job_ids(client["id"]))
    remaining   = max(DAILY_CAP - todays_sent_count(client["id"]), 0)
    now         = dt.datetime.now(dt.timezone.utc)
    floors      = effective_floors(profile)
    funnel      = {"pool_candidates": 0, "already_matched": 0, "similar_recent": 0,
                   "prefilter": {}, "deal_breaker": 0, "low_confidence": 0,
                   "floor": {}, "below_email_floor": 0, "near_misses": []}

    # ---- 1) DRAIN THE QUEUE first (yesterday's overflow strong matches) ----
    for q in get_queued_matches(client["id"]):
        job = q["job"]
        if job.get("posted_at") and (now - job["posted_at"]).days > FRESHNESS_MAX_DAYS:
            update_match(q["id"], {"status": "dropped"})       # expired on freshness
            continue
        if remaining <= 0:
            break                                              # stays queued for tomorrow
        deliver(client, profile, job, q["composite_score"], q["tier"],
                q.get("dimension_scores"), q.get("match_reason"),
                q.get("dimension_scores"), q.get("confidence"), now,
                queued_match_id=q["id"])
        remaining -= 1

    # ---- 2) SCORE new pool jobs since this client's last run ----
    scope      = client_queries(profile)
    candidates = get_pool_jobs_after(client.get("last_scored_at"), FRESHNESS_MAX_DAYS,
                                     client_queries=list(scope))
    candidates = [j for j in candidates if in_client_scope(j, scope)]  # belt + braces with the SQL scope
    candidates = sorted(candidates, key=lambda j: j.get("posted_at") or now, reverse=True)
    candidates = candidates[:MAX_SCORED_PER_RUN]   # circuit breaker (rarely hit once scoped)
    funnel["pool_candidates"] = len(candidates)

    eligible = []
    for job in candidates:
        if job["id"] in matched_ids:        # already surfaced to this client
            funnel["already_matched"] += 1
            continue
        if recently_sent_similar(client["id"], job["company"], job["job_title"],
                                 SIMILAR_WINDOW_DAYS):
            funnel["similar_recent"] += 1   # same role from another board/url
            continue
        kill = prefilter(job, profile)      # cheap per-client gates
        if kill:
            funnel["prefilter"][kill] = funnel["prefilter"].get(kill, 0) + 1
            continue
        judgment = score_job(job, profile)  # the only volume Claude call
        if judgment["deal_breakers"]["triggered"]:
            funnel["deal_breaker"] += 1
            continue
        conf = min_confidence(judgment)
        fs = {f: judgment["factor_scores"][f]["score"] for f in JUDGMENT_FACTORS}
        fs["salary"], fs["location"] = salary_score(job, profile), location_score(job, profile)
        if conf < MIN_CONFIDENCE:           # locked decision: emailed matches are high-confidence
            funnel["low_confidence"] += 1   # logged, never silent: visible per run to the team
            continue
        ff = failed_floors(fs, floors)
        if ff:
            for f in ff:
                funnel["floor"][f] = funnel["floor"].get(f, 0) + 1
            continue
        comp = composite(fs, profile["weights"])
        tier = tier_of(comp, profile)       # per-client email_floor / strong_threshold
        if tier is None:
            funnel["below_email_floor"] += 1
            if comp >= 60 and len(funnel["near_misses"]) < 3:   # the closest non-sends, for humans
                funnel["near_misses"].append({"job": job["job_title"],
                                              "company": job["company"], "composite": comp})
            continue
        eligible.append({"job": job, "composite": comp, "tier": tier, "scores": fs,
                         "confidence": conf, "judgment": judgment})

    eligible.sort(key=lambda m: (m["tier"] != "strong", -m["composite"]))  # strong first
    to_send = eligible[:remaining]

    for m in to_send:
        deliver(client, profile, m["job"], m["composite"], m["tier"], m["scores"],
                m["judgment"]["overall_assessment"], m["judgment"]["factor_scores"],
                m["confidence"], now)

    for m in eligible[len(to_send):]:       # overflow: strong -> queue (full record), possibles -> drop
        if m["tier"] == "strong":
            save_match({"client_id": client["id"], "job_id": m["job"]["id"],
                        "composite_score": m["composite"], "tier": "strong",
                        "dimension_scores": m["scores"], "confidence": m["confidence"],
                        "match_reason": m["judgment"]["overall_assessment"],
                        "status": "queued"})

    set_last_scored_at(client["id"], now)   # advance cursor: don't re-score these jobs next run
    starvation_check(client, profile)       # coverage ladder: widen queries if starving
    log_run({"client_id": client["id"], "jobs_scored": len(candidates),
             "strong_matches": sum(1 for m in eligible if m["tier"] == "strong"),
             "possible_matches": sum(1 for m in eligible if m["tier"] == "possible"),
             "sent_count": len(to_send), "funnel": funnel,
             "run_finished_at": now.isoformat()})

def main():
    refresh_job_pool()                      # ONE shared scrape for everyone
    for client in get_active_clients():
        try:
            run_for_client(client)
        except Exception as e:
            log_run({"client_id": client.get("id"), "error": str(e)})
    alerting = get_alerting_clients()       # 5-day starvation: push the WHY to the team
    if alerting:
        notify_ops(alerting)
    # TODO[DEV]: bound concurrency across clients to hit the hourly SLA
    # TODO[DEV]: trigger mechanism -- pg_cron alone can't run this container.
    #            Either pg_cron + pg_net HTTP call to a container webhook, or the
    #            host platform's scheduler (Railway/Render cron). Pick one.

if __name__ == "__main__":
    main()
```
