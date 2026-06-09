# Rapid — Progress Log

Single source of truth for build state so sessions resume cleanly. Update the
status + "verified" notes as each task moves. Build order follows Handoff §8 (1→10).

## Legend
- ✅ done + verified
- 🟡 in progress / partial
- ⬜ not started
- 🔒 blocked (needs a credential or human action)

## Bootstrap
- ✅ Repo seeded with all handoff artifacts (scaffold, skills, email template, SQL, docs, template docx).
- ✅ `.gitignore`, `.env.example`, `requirements.txt` added. `.env` is gitignored; no secrets committed.
- ✅ Module made offline-testable: Anthropic client is lazy (`_get_claude()`), skills load
  relative to the module file, optional `.env` autoload. Importing `rapid_agent` needs no SDK/keys.

## Dev Scope of Work (Handoff §8)
| # | Task | Status | Verified |
|---|------|--------|----------|
| 1 | Supabase data layer (DB stubs in rapid_agent.py) | ✅ | all 17 fns implemented; verified read-only against live Mesa (active client, profile, query sets, counts, pool, health) |
| 2 | Apify scrape → shared pool (tag matched_queries; upsert MERGES) | ✅ | validated LIVE end-to-end: scrape→normalize→upsert(idempotent)→scoped read→prefilter→Claude score. Actors: LinkedIn vIGxjRrHqDTPuE6M4, career-site s3dtSTZSZWFtAVLn5 |
| 3 | Parser helpers (_is_us, _arrangement_compatible, _parse_salary_max) + unit tests | ✅ | tests pass (`python3 -m pytest -q`) |
| 4 | location_score | ✅ | tests pass (`python3 -m pytest -q`) |
| 5 | Google Docs merge (render_resume) + refresh_base_resume | 🔒 | DEFERRED per owner (no GOOGLE_SERVICE_ACCOUNT_JSON). render_resume now raises (no placeholder link can be emailed) |
| 6 | Postmark send + notify_ops | 🟡 | code validated to the API boundary (request shape + auth OK; both variants render). BLOCKED on a human step: confirm sender signature/domain in Postmark (From rapidnotifications@careergrowth.io returns ErrorCode 400 "not a Sender Signature"). |
| 7 | Harden Claude JSON parsing (strip stray text, validate, retry once) | ✅ | prompt caching on skill system prompt too; + json.dumps(default=str) |
| 8 | Bounded concurrency (idempotent, no double-sends) | ✅ | ThreadPoolExecutor(RAPID_MAX_WORKERS=5) fan-out; + sql/04 unique (client_id,job_id) index for DB-level idempotency |
| 9 | RLS policies | 🟡 | sql/05_rls_policies.sql written (default-deny + dashboard sent-only reads). Human step: run in Supabase SQL Editor (no DDL path from the service key). |
| 10 | Deploy + schedule | 🟡 | Dockerfile + docs/DEPLOY.md (Render/Railway hourly cron). Human step: create the host service + cron + env vars. |

Total tests: **103 pass** (`python3 -m pytest -q`).

## HUMAN STEPS REMAINING (cannot be done from the agent session)
1. **Postmark sender** — in Postmark → Sender Signatures, add & confirm
   `rapidnotifications@careergrowth.io` (or verify the `careergrowth.io` domain with
   DKIM/Return-Path). Until then every send 422s. After confirming, the email path is
   done — code already hard-routes To = TEST_RECIPIENT_OVERRIDE.
2. **Run the SQL patches** in Supabase → SQL Editor: `sql/04_unique_match_idempotency.sql`
   (matches uniqueness + jobs.content_hash index) and `sql/05_rls_policies.sql` (RLS,
   before anything client-facing reads the tables). Service key can't run DDL.
3. **Task 5 (Google Docs resume render)** — still deferred; provide
   GOOGLE_SERVICE_ACCOUNT_JSON to unblock. Until then `render_resume` raises and
   delivery leaves an alertable 'pending' row instead of emailing a dead link.
4. **Deploy** — follow `docs/DEPLOY.md` (Render/Railway hourly cron, env vars).

## Design decision implemented this session
- **Remote clients search nationally.** `get_all_active_query_sets` now returns query
  BUCKETS: remote-accepting clients → national `locationSearch=["United States"]`
  (validated: 10/10 US jobs, vs ~0 when filtered by home city); strictly on-site/hybrid
  clients → their preferred_locations. Provenance still re-derived from titleSearch.
  This unblocks acceptance gate #2 for normal (remote) clients.

## Safety state
- 🔒 TEST MODE. Every email sends ONLY to TEST_RECIPIENT_OVERRIDE until shadow-run passes.
- 🔒 Mesa (00000000-0000-0000-0000-000000000001) is the only client until shadow-running.
- Spend caps: start minimal (1 Apify query, ≤10 jobs, ≤10 Claude calls). Ask before any run >~$5.

## Live testing
- **Minimal Apify scrape validated end-to-end (2026-06-09).** One LinkedIn actor run,
  limit=10, Mesa's 34 title queries. Path proven: scrape → `_normalize_job` →
  `upsert_jobs` (10 new, then 0 on re-run = idempotent) → `get_pool_jobs_after`
  (scoped to Mesa queries) → `prefilter` (killed 5 non-US jobs BEFORE any Claude call —
  cost control works) → `score_job` (Claude). CPG roles scored highest (industry 95,
  skills 88, salary 100). Mesa correctly yields 0 matches = acceptance gate #1.
- **3 real bugs found + fixed via live testing:**
  1. `apify-client` 2.20 returns a pydantic `Run` (not a dict) → added `_run_dataset_id`.
  2. Live `jobs` table has NO unique constraint on content_hash (migration 02 not fully
     applied) → rewrote `upsert_jobs` as select-then-insert/merge (no ON CONFLICT dep).
  3. DB-hydrated jobs carry `datetime` → `_call_claude` now `json.dumps(..., default=str)`.
- **Deal-breaker engine verified CORRECT:** a "remote"-flagged Tropicana NAM role was
  knocked out because the JD body said "must be located in Cincinnati/Charlotte or
  willing to relocate." Skill read the description and cited it verbatim. Deep reading
  beats the surface remote flag — working as designed.
- **2 findings to decide on (not blocking):**
  - (a) For REMOTE-only clients, scraping with `locationSearch=[home city]` returns ~0
    (Mesa, AZ → 0 jobs); titles-only returned 10. Remote clients likely need national
    search, not home-city filtering. Design question for `get_all_active_query_sets`.
  - (b) Consider applying a unique index on `jobs(content_hash)` (additive) to harden
    dedup; the code no longer requires it but it's good hygiene.
- Preflight result (2026-06-09, open-network session): **ALL FOUR LIVE KEYS OK** via
  `scripts/preflight.py` (read-only, no email, near-zero cost):
  - Anthropic — model `claude-haiku-4-5` reachable.
  - Supabase — `clients` table reachable (count=1, i.e. Mesa only ✓).
  - Apify — authenticated as `careergrowth`.
  - Postmark — server 'My First Server' reachable (no email sent).
  - Google Docs/Drive — SKIPPED (GOOGLE_SERVICE_ACCOUNT_JSON deferred by request).
- Prior session note (superseded): Supabase/Apify/Postmark had returned 403 from the
  egress proxy under the restrictive network policy — that was the network, not the keys.
- Env quirk this session: system `PyJWT`/`cryptography` were Debian-managed and broke the
  supabase import chain. Fix: `pip install --ignore-installed PyJWT cryptography` before
  `pip install -r requirements.txt`.
- `.env` and `.venv` are gitignored, so they do NOT carry into a new session. Next
  session: re-provide keys (re-upload keys.txt OR use env secrets), recreate venv,
  `pip install`, rerun `scripts/preflight.py`.
- `scripts/preflight.py` — read-only credential checker (Anthropic, Supabase, Apify,
  Postmark; Google deferred). No email, near-zero cost.
- New sessions need deps: `pip install -r requirements.txt` (container is ephemeral).

## Notes
- Offline-testable tasks (3, 4, 7) can proceed without credentials. Tasks needing live
  APIs (1, 2, 5, 6, 9, 10) are blocked until the corresponding secret is provided.
