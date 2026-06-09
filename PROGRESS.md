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
| 1 | Supabase data layer (DB stubs in rapid_agent.py) | 🟡 | all 18 fns implemented; pure helpers (_parse_ts/_normalize_job) tested (115 total); query fns need live DB verification |
| 2 | Apify scrape → shared pool (tag matched_queries; upsert MERGES) | ⬜ | needs APIFY_TOKEN |
| 3 | Parser helpers (_is_us, _arrangement_compatible, _parse_salary_max) + unit tests | ✅ | 48 tests pass (`python3 -m pytest -q`) |
| 4 | location_score | ✅ | 66 tests pass total (`python3 -m pytest -q`) |
| 5 | Google Docs merge (render_resume) + refresh_base_resume | 🟡 | pure core tested (build_resume_fields, replace-requests, _drive_id, inline-JSON creds); LIVE Drive/Docs + multi-experience block-duplication + empty-section pruning need Google creds+network |
| 6 | Postmark send + notify_ops | ✅ | rendering + test-mode safety tested (105 total); live send pending open network |
| 7 | Harden Claude JSON parsing (strip stray text, validate, retry once) | ✅ | 81 tests pass; prompt caching on skill system prompt too |
| 8 | Bounded concurrency (idempotent, no double-sends) | ⬜ | |
| 9 | RLS policies | ⬜ | |
| 10 | Deploy + schedule | ⬜ | |

## Safety state
- 🔒 TEST MODE. Every email sends ONLY to TEST_RECIPIENT_OVERRIDE until shadow-run passes.
- 🔒 Mesa (00000000-0000-0000-0000-000000000001) is the only client until shadow-running.
- Spend caps: start minimal (1 Apify query, ≤10 jobs, ≤10 Claude calls). Ask before any run >~$5.

## Live testing
- Preflight result (this session): **Anthropic OK** (key valid, model reachable).
  Supabase / Apify / Postmark returned **403 from the egress proxy even with no auth** —
  i.e. blocked by the environment's restrictive network policy, NOT bad keys. Those
  three keys remain UNTESTED until the network opens.
- DECISION: open **full network access** on the environment, then restart. Network +
  secret changes only apply to a freshly built session.
- `.env` and `.venv` are gitignored, so they do NOT carry into a new session. Next
  session: re-provide keys (re-upload keys.txt OR use env secrets), recreate venv,
  `pip install`, rerun `scripts/preflight.py`.
- `scripts/preflight.py` — read-only credential checker (Anthropic, Supabase, Apify,
  Postmark; Google deferred). No email, near-zero cost.
- New sessions need deps: `pip install -r requirements.txt` (container is ephemeral).

## Notes
- Offline-testable tasks (3, 4, 7) can proceed without credentials. Tasks needing live
  APIs (1, 2, 5, 6, 9, 10) are blocked until the corresponding secret is provided.
