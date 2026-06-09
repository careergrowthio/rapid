# Rapid — Deploy & Schedule (Task 10)

The pipeline is a **batch job**, not a long-running server: `python rapid_agent.py`
runs `main()` once (one shared scrape → fan out across active clients → ops alert) and
exits. A host scheduler re-invokes it hourly. `pg_cron` alone can't do this — it runs
SQL inside Postgres and can't start this container — so scheduling lives on the host.

## Schedule (locked decision)
- **Hourly, ~6am–11pm, weekdays.** The scrape window auto-widens over any gap (a
  Monday 6am run covers the whole weekend; an outage covers itself), so a few missed
  invocations self-heal. Optional reduced weekend cadence (e.g. every 3h) is cheap
  with the shared pool — add a second cron line if desired.
- Cron (host TZ → set to America/Phoenix or convert): `0 6-23 * * 1-5`

## Required environment variables (set in the platform dashboard, never in the image)
ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, APIFY_TOKEN,
POSTMARK_SERVER_TOKEN, POSTMARK_FROM_ADDRESS, OPS_ALERT_EMAIL,
TEST_RECIPIENT_OVERRIDE, RESUME_TEMPLATE_DOC_ID, GOOGLE_SERVICE_ACCOUNT_JSON
(path or full JSON — still deferred), plus optional tuning:
RAPID_MAX_WORKERS (default 5), RAPID_SCRAPE_LIMIT (keep small until shadow-run passes).

> SAFETY: keep `TEST_RECIPIENT_OVERRIDE` set until the shadow-run passes — every email
> is hard-routed there. Do not add real clients (only Mesa is `rapid_active`).

## Option A — Render Cron Job (recommended; simplest)
1. New → **Cron Job**, connect this repo (uses the `Dockerfile`).
2. Schedule: `0 6-23 * * 1-5` (set the service's timezone to America/Phoenix).
3. Add the env vars above. Deploy. Each tick runs one cycle and exits.

## Option B — Railway
1. New project from repo (Dockerfile auto-detected).
2. Add a **Cron** schedule `0 6-23 * * 1-5` on the service; add env vars.
3. Railway starts the container on schedule; it exits when the cycle ends.

## Option C — pg_cron + pg_net → container webhook
Only if you'd rather trigger from Postgres: wrap `main()` in a tiny HTTP endpoint on an
always-on container, then `select cron.schedule('rapid-hourly', '0 6-23 * * 1-5',
$$ select net.http_post('https://<host>/run', '{}') $$);`. More moving parts than A/B.

## Pre-deploy checklist
- [ ] `python -m pytest -q` green.
- [ ] `python scripts/preflight.py` all OK (Google may SKIP while deferred).
- [ ] SQL patches applied in the live project: `04_unique_match_idempotency.sql`,
      `05_rls_policies.sql` (RLS before anything client-facing reads the tables).
- [ ] Task 5 (Google Docs resume render) implemented, OR accept that delivery raises
      and leaves alertable 'pending' rows until it is.
- [ ] `RAPID_SCRAPE_LIMIT` small for the first live cycles; scale up after a clean pass.

## Verify after first scheduled run
- `run_log` has a NULL-client_id pool-refresh row + one row per active client.
- `client_health` shows Mesa (expected: few/zero strong matches, funnel names the gate).
- No email went anywhere except `TEST_RECIPIENT_OVERRIDE`.
