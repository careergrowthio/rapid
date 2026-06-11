# Rapid — Handover Checklist (AS-BUILT status)

*Originally a fill-in template for the developer. Updated during integration to show
current status. ✅ = done/verified this session, ⏳ = waiting on a human step.
Live build state is in `PROGRESS.md`; the durable summary is handoff §8.1.*

---

## 1. Credentials & keys  (keep secret — share via a password manager, not email)

```
Anthropic API key:                 ✅ provided + verified (preflight OK)
Apify token:                       ✅ provided + verified (auth as 'careergrowth')
Supabase Project URL:              ✅ provided + verified
Supabase service_role key:         ✅ provided + verified (server-side only)
Supabase anon/public key:          ⏳ needed by the DASHBOARD (not the agent) — get from Supabase API settings
Google service account JSON:       ⏳ NOT yet provided — blocks Task 5 (resume render)
Email provider API key:            ✅ provided + verified (Postmark Server token)
Sending from-address / domain:     rapidnotifications@careergrowth.io
                                   ⏳ BLOCKER: not yet a confirmed Postmark Sender Signature (live ErrorCode 400)
```

## 2. Decisions to set

```
Email provider:                    [x] Postmark  (Transactional / "outbound" stream — wired)
   Verify sending domain in Postmark (DKIM + Return-Path) early -- DNS can take time   [x] started   [ ] verified  <-- ⏳ DO THIS NEXT (unblocks all email)
Agent hosting platform:            [ ] Railway  [ ] Render  [ ] Fly  [ ] other: __________  <-- ⏳ pick one; Dockerfile + docs/DEPLOY.md ready
Schedule timezone (for 6am-11pm):  America/Phoenix   (CAP_TIMEZONE in code; cron: 0 6-23 * * 1-5)
Weekend run cadence (optional):    [ ] none (Mon covers weekend)  [ ] every 3h  [ ] hourly
   (shared pool makes weekend runs cheap; keeps weekend postings inside the ~1hr promise)
Will you build Task 3 (the 3 parser functions) yourself?   [ ] Yes   [x] No — ✅ done + unit-tested
```
*(Everything else — the 70% bar, Strong/Possible tiers, 3/day cap, 24h lookback, freshness, shared pool — is already locked in the handoff doc; nothing to decide.)*

## 3. Google Docs resume template + master resumes

```
Resume template Google Doc ID:     1GZB9vI2wJmf5vRqrB0lACpiSm8sb2uZS   (RESUME_TEMPLATE_DOC_ID, in .env)
Template placeholder field names:  ⏳ confirm the {{placeholders}} in the template Doc when wiring Task 5
   (list every {{placeholder}} in the template, e.g. {{FULL_NAME}}, {{SUMMARY}},
    {{JOB_TITLE}}, {{SKILLS}} — the dev maps the skill output to these)
Template shared with the Google service account?   [ ] Yes (editor access)   <-- ⏳ needed once the service account exists

Drive folder where client MASTER resumes live:  1YzCQF-V2K1LLbGiZgvsov7KEzoTqX9YT   (read access)
   (link: https://drive.google.com/drive/folders/1YzCQF-V2K1LLbGiZgvsov7KEzoTqX9YT)
Drive folder where TAILORED output resumes go:  1X6PUnQydfjImEwQOisF1NdJwPy6D71ZV   (EDITOR/write access -- engine creates Docs here)
   (link: https://drive.google.com/drive/folders/1X6PUnQydfjImEwQOisF1NdJwPy6D71ZV ; a Shared Drive is more robust than My Drive -- ask dev)
Both folders shared with the service account?   [ ] Yes
```
*(Masters are built by your team in a consistent format and placed in Drive BEFORE a client
is set to rapid_active. The engine reads each master once, caches it (base_resume), and tailors
copies per role — it never re-parses Drive on every job.)*

## 4. Match email copy  — PROVIDED
The email copy is done: `rapid_match_email_template.md`, built from your current
production email (Strong + Possible variants, placeholder mapping, dev rules).
Edit wording there if you want changes; the dev wires it into send_match_email().

Merge fields the system fills: `{{client_name}} {{job_title}} {{company}} {{location}} {{fit_label}} {{match_reason}} {{apply_link}} {{resume_link}}`

```
SUBJECT (Strong fit):   ____________________________________________________
   starter: New strong match: {{job_title}} at {{company}}

SUBJECT (Possible fit): ____________________________________________________
   starter: A role worth a look: {{job_title}} at {{company}}

BODY (edit freely):
-------------------------------------------------------------------
Hi {{client_name}},

{{fit_label}} — {{job_title}} at {{company}} ({{location}}).

   Strong-fit line starter: "This is a strong match. I'd apply now."
   Possible-fit line starter: "This one's a possible fit - take a look and decide."

Why it fits: {{match_reason}}

Your tailored resume:  {{resume_link}}
Apply here:            {{apply_link}}

[ your sign-off ]
-------------------------------------------------------------------
```

## 5. Existing clients to migrate

```
Where do current clients live now?     ____________________________________
Approx. number of active clients:      __________
Who validates the migrated data?       __________  (you / dev)
```

## 6. Repo layout (as-built — the package is now the repo)

```
docs/Rapid_Complete_Handoff.md       — master doc (see §8.1 for as-built status)
sql/01_rapid_supabase_schema.sql     — database (run once) [applied]
sql/02_migration_shared_job_pool.sql — shared-pool migration [applied — but content_hash index missing in live, see 04]
sql/03_seed_mesa_test_client.sql     — Mesa test client [applied]
sql/04_unique_match_idempotency.sql  — ⏳ RUN: match uniqueness + missing jobs.content_hash index
sql/05_rls_policies.sql              — ⏳ RUN: RLS before the dashboard reads the tables
rapid_matching_skill.md              — SKILL: how Claude scores a job
rapid_resume_tailoring_skill.md      — SKILL: how Claude tailors a resume
rapid_agent.py                       — the pipeline (TODO[DEV] done except Task 5 / Google)
rapid_match_email_template.md        — email copy (wired into send_match_email)
scripts/preflight.py                 — read-only credential checker
tests/                               — 103 passing unit tests
Dockerfile, docs/DEPLOY.md           — deploy (hourly cron on Render/Railway)
PROGRESS.md                          — live build state (source of truth)
```

## 7. What's left to go live (the short list)
```
[ ] Confirm Postmark Sender Signature for rapidnotifications@careergrowth.io (or verify domain DKIM)
[ ] Run sql/04 + sql/05 in the Supabase SQL Editor
[ ] Provide Google service account JSON  -> then build Task 5 (resume render)
[ ] Pick a host + deploy per docs/DEPLOY.md (hourly cron, env vars)
[ ] Full end-to-end shadow run on Mesa, then >=1 week shadow before any real client
```

*The two skill files (matching + resume) are the "brain" — the scaffold loads them by
filename, so they must sit beside `rapid_agent.py`.*
