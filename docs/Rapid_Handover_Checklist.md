# Rapid — Handover Checklist (fill in the blanks)

*Complete this, then send it to your developer along with the files listed in section 6. Anything you can't fill, flag so the dev can help.*

---

## 1. Credentials & keys  (keep secret — share via a password manager, not email)

```
Anthropic API key:                 ____________________________________
Apify token:                       ____________________________________
Supabase Project URL:              ____________________________________
Supabase service_role key:         ____________________________________   (server-side only)
Supabase anon/public key:          ____________________________________   (dashboard)
Google service account JSON:       [ ] attached as a file
Email provider API key:            ____________________________________   (Postmark Server API token)
Sending from-address / domain:     ____________________________________   (e.g. jobs@careergrowth.io)
```

## 2. Decisions to set

```
Email provider:                    [x] Postmark  (use the Transactional stream)
   Verify sending domain in Postmark (DKIM + Return-Path) early -- DNS can take time   [ ] started   [ ] verified
Agent hosting platform:            [ ] Railway  [ ] Render  [ ] Fly  [ ] other: __________
Schedule timezone (for 6am-11pm):  ____________________   (e.g. America/Phoenix)
Weekend run cadence (optional):    [ ] none (Mon covers weekend)  [ ] every 3h  [ ] hourly
   (shared pool makes weekend runs cheap; keeps weekend postings inside the ~1hr promise)
Will you build Task 3 (the 3 parser functions) yourself?   [ ] Yes   [ ] No, dev does it
```
*(Everything else — the 70% bar, Strong/Possible tiers, 3/day cap, 24h lookback, freshness, shared pool — is already locked in the handoff doc; nothing to decide.)*

## 3. Google Docs resume template + master resumes

```
Resume template Google Doc ID:     ____________________________________
Template placeholder field names:  ____________________________________
   (list every {{placeholder}} in the template, e.g. {{FULL_NAME}}, {{SUMMARY}},
    {{JOB_TITLE}}, {{SKILLS}} — the dev maps the skill output to these)
Template shared with the Google service account?   [ ] Yes (editor access)

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

## 6. Files to send the developer  (the package)

```
[ ] Rapid_Complete_Handoff.md        — the master doc (read this first; contains all of the below in appendices)
[ ] rapid_supabase_schema.sql        — database (run once)
[ ] migration_shared_job_pool.sql    — shared-pool migration (run once, after schema)
[ ] seed_mesa_test_client.sql        — test client for the acceptance test
[ ] rapid_matching_skill.md          — SKILL: how Claude scores a job
[ ] rapid_resume_tailoring_skill.md  — SKILL: how Claude tailors a resume
[ ] rapid_agent.py                   — the pipeline scaffold (the dev finishes the TODO[DEV] items)
```

*The two skill files (matching + resume) are the "brain" — make sure both go with the scaffold, since the scaffold loads them by filename.*
