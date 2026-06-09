# Rapid — Claude Code Project Instructions

Read `docs/Rapid_Complete_Handoff.md` before doing anything. It is the single source of
truth: every locked decision, the scoring methodology, the dev scope of work, and
the runnable artifacts. Do not re-litigate locked decisions (the 70% bar, tiers,
caps, schedule) — implement them.

## What this is
Rapid scans job boards hourly, matches fresh postings (≤1hr old ideally) against
100+ executive clients, tailors their resume per role, emails the match (Postmark)
and posts it to the dashboard (Vercel reads Supabase). The logic/design is DONE and
triple-audited. The work here is integration: implementing the `# TODO[DEV]` blocks
in `rapid_agent.py`, testing against real APIs, and fixing what reality disagrees with.

## SAFETY RAILS — never violate these
1. **TEST MODE until the shadow-run passes.** Every email sends ONLY to
   `TEST_RECIPIENT_OVERRIDE` (owner's address). Never send to any real client address.
   Hardcode a recipient override in test builds.
2. **Mesa is the only client** (id 00000000-0000-0000-0000-000000000001) until
   shadow-running starts. Never set any other client to 'rapid_active'.
3. **Spend caps:** start every integration with minimal volume — 1 Apify query,
   ≤10 jobs, ≤10 Claude scoring calls. Scale up only after a pass works end to end.
   Ask before any run that could exceed ~$5 in API cost.
4. **Secrets live in `.env` only.** Never print keys to logs/output, never commit
   them, never paste them into code. `.env` is gitignored.
5. **Supabase: additive only.** Never DROP/TRUNCATE tables. The schema + 3 patches
   are already applied to the live project — do not re-run schema files against it.
6. **Ask before:** sending any email, any destructive DB statement, any run against
   the full query union, or deploying.

## Build order — follow the Dev Scope of Work (handoff §8), tasks 1→10
1. Supabase data layer (all DB stubs in rapid_agent.py)
2. Apify scrape → shared pool (tag matched_queries; upsert MERGES tags on conflict)
3. Parser helpers (_is_us, _arrangement_compatible, _parse_salary_max) + unit tests
4. location_score
5. Google Docs merge (render_resume) + refresh_base_resume (Drive master → cache)
6. Postmark send (rapid_match_email_template.md) + notify_ops
7. Harden Claude JSON parsing (strip stray text, validate shape, retry once)
8. Bounded concurrency (idempotent, no double-sends)
9. RLS policies
10. Deploy + schedule
Write tests as you go; run the relevant test before declaring a task done.

## Contracts — keep these aligned (breaking them was the audit's worst bug)
- Matching-skill factor keys == weights/threshold keys == code keys:
  `seniority, industry, skills, title` (+ `salary`, `location` computed in code).
- Per-client `email_floor` / `strong_threshold` come from match_profiles, never globals.
- UNKNOWN NEVER KILLS in the pre-filter: only a *stated* violation filters a job.
- Provenance: a client scores only jobs whose `matched_queries` intersect their queries.
- Send ordering: save 'pending' → send email → mark 'sent'.
- Dashboard feed = matches WHERE status='sent' joined to jobs. pending/queued never display.
- Resume tailoring: truthful repositioning only; facts copied verbatim; no em dashes.

## Environment (.env keys expected)
ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, APIFY_TOKEN,
GOOGLE_SERVICE_ACCOUNT_JSON (path), RESUME_TEMPLATE_DOC_ID, POSTMARK_SERVER_TOKEN,
OPS_ALERT_EMAIL, TEST_RECIPIENT_OVERRIDE

Google Drive (already shared with the service account):
- Masters folder (read):  1YzCQF-V2K1LLbGiZgvsov7KEzoTqX9YT
- Output folder (write):  1X6PUnQydfjImEwQOisF1NdJwPy6D71ZV

## Acceptance gates (definition of done — handoff §8)
1. Mesa runs end to end; correctly yields few/zero strong matches; health view +
   funnel name the limiting gate.
2. A normal test profile gets up to 3 emailed matches/day, strong first, truthfully
   tailored resume + apply link, nothing <70% or >1 week old, no duplicates.
3. Hourly schedule holds; costs bounded; errors logged, not fatal.
Then: shadow-run beside n8n ≥1 week before any real client migrates.

## Working style
- Small steps: implement one function, test it, show the result, move on.
- When an external API fails, show the raw error + diagnosis before fixing.
- If something needs a human click (Google Cloud console, DNS, account creation),
  stop and give exact click-by-click steps.
- Log progress in PROGRESS.md (task, status, what's verified) so sessions resume cleanly.
