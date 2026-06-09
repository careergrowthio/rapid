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
| 2 | Apify scrape → shared pool (tag matched_queries; upsert MERGES) | 🟡 | code done + 15 unit tests; actor IDs discovered from account (LinkedIn vIGxjRrHqDTPuE6M4, career-site s3dtSTZSZWFtAVLn5). NOT yet run live (needs minimal-spend go-ahead) |
| 3 | Parser helpers (_is_us, _arrangement_compatible, _parse_salary_max) + unit tests | ✅ | tests pass (`python3 -m pytest -q`) |
| 4 | location_score | ✅ | tests pass (`python3 -m pytest -q`) |
| 5 | Google Docs merge (render_resume) + refresh_base_resume | 🔒 | DEFERRED per owner (no GOOGLE_SERVICE_ACCOUNT_JSON). render_resume now raises (no placeholder link can be emailed) |
| 6 | Postmark send + notify_ops | 🟡 | implemented (HTML render of both variants; hard TEST_RECIPIENT_OVERRIDE). NOT yet sent (needs send go-ahead) |
| 7 | Harden Claude JSON parsing (strip stray text, validate, retry once) | ✅ | prompt caching on skill system prompt too |
| 8 | Bounded concurrency (idempotent, no double-sends) | ✅ | ThreadPoolExecutor(RAPID_MAX_WORKERS=5) fan-out; + sql/04 unique (client_id,job_id) index for DB-level idempotency |
| 9 | RLS policies | 🟡 | sql/05_rls_policies.sql written (default-deny + dashboard sent-only reads). NOT yet applied to live DB |
| 10 | Deploy + schedule | 🟡 | Dockerfile + docs/DEPLOY.md (Render/Railway hourly cron). NOT yet deployed |

Total tests: **96 pass** (`python3 -m pytest -q`).

## Safety state
- 🔒 TEST MODE. Every email sends ONLY to TEST_RECIPIENT_OVERRIDE until shadow-run passes.
- 🔒 Mesa (00000000-0000-0000-0000-000000000001) is the only client until shadow-running.
- Spend caps: start minimal (1 Apify query, ≤10 jobs, ≤10 Claude calls). Ask before any run >~$5.

## Live testing
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
