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

## Dev Scope of Work (Handoff §8)
| # | Task | Status | Verified |
|---|------|--------|----------|
| 1 | Supabase data layer (DB stubs in rapid_agent.py) | ⬜ | needs SUPABASE_URL + SERVICE_KEY |
| 2 | Apify scrape → shared pool (tag matched_queries; upsert MERGES) | ⬜ | needs APIFY_TOKEN |
| 3 | Parser helpers (_is_us, _arrangement_compatible, _parse_salary_max) + unit tests | ⬜ | pure/offline — testable now |
| 4 | location_score | ⬜ | pure/offline — testable now |
| 5 | Google Docs merge (render_resume) + refresh_base_resume | ⬜ | needs GOOGLE_SERVICE_ACCOUNT_JSON + template |
| 6 | Postmark send + notify_ops | ⬜ | needs POSTMARK_SERVER_TOKEN |
| 7 | Harden Claude JSON parsing (strip stray text, validate, retry once) | ⬜ | testable offline (mock) |
| 8 | Bounded concurrency (idempotent, no double-sends) | ⬜ | |
| 9 | RLS policies | ⬜ | |
| 10 | Deploy + schedule | ⬜ | |

## Safety state
- 🔒 TEST MODE. Every email sends ONLY to TEST_RECIPIENT_OVERRIDE until shadow-run passes.
- 🔒 Mesa (00000000-0000-0000-0000-000000000001) is the only client until shadow-running.
- Spend caps: start minimal (1 Apify query, ≤10 jobs, ≤10 Claude calls). Ask before any run >~$5.

## Notes
- Offline-testable tasks (3, 4, 7) can proceed without credentials. Tasks needing live
  APIs (1, 2, 5, 6, 9, 10) are blocked until the corresponding secret is provided.
