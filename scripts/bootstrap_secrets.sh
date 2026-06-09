#!/usr/bin/env bash
# Rebuild file-based secrets from single-line env vars, and report which
# required keys are present. Safe to run in both cloud and local sessions.
#
# WHY THIS EXISTS:
#   Claude Code on the web stores environment variables in ".env format, one
#   KEY=value per line". A multi-line value (like the Google service-account
#   JSON) corrupts that field and the platform silently drops the ENTIRE save
#   ("I save it and reopen and see nothing"). So the JSON must travel as a
#   single base64 line and be rebuilt into a file here, at session start.
#
# USAGE: invoked by the SessionStart hook (.claude/settings.json). It writes
#   the decoded credential to .secrets/service_account.json and, when run as a
#   hook, exports GOOGLE_SERVICE_ACCOUNT_JSON (the path) via $CLAUDE_ENV_FILE.
#
# NEVER prints secret values. Only prints key NAMES and SET/MISSING status.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SECRETS_DIR="$REPO_ROOT/.secrets"
SA_PATH="$SECRETS_DIR/service_account.json"

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR" 2>/dev/null || true

# --- 1. Materialize the Google service-account JSON from base64, if provided ---
if [ -n "${GOOGLE_SERVICE_ACCOUNT_JSON_B64:-}" ]; then
  if printf '%s' "$GOOGLE_SERVICE_ACCOUNT_JSON_B64" | base64 -d > "$SA_PATH" 2>/dev/null \
       && python3 -c "import json,sys; json.load(open('$SA_PATH'))" 2>/dev/null; then
    chmod 600 "$SA_PATH" 2>/dev/null || true
    echo "[bootstrap] decoded service account -> $SA_PATH (valid JSON)"
    # Point the loader at the file unless an explicit path is already set.
    if [ -z "${GOOGLE_SERVICE_ACCOUNT_JSON:-}" ] || [ "${GOOGLE_SERVICE_ACCOUNT_JSON}" = "./service_account.json" ]; then
      export GOOGLE_SERVICE_ACCOUNT_JSON="$SA_PATH"
    fi
    # Persist the path for subsequent Bash commands when run as a SessionStart hook.
    if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
      echo "GOOGLE_SERVICE_ACCOUNT_JSON=$SA_PATH" >> "$CLAUDE_ENV_FILE"
    fi
  else
    echo "[bootstrap] ERROR: GOOGLE_SERVICE_ACCOUNT_JSON_B64 did not decode to valid JSON" >&2
    rm -f "$SA_PATH"
  fi
fi

# --- 2. Report required keys (names + status only; never values) ---
REQUIRED="ANTHROPIC_API_KEY SUPABASE_URL SUPABASE_SERVICE_KEY APIFY_TOKEN POSTMARK_SERVER_TOKEN OPS_ALERT_EMAIL TEST_RECIPIENT_OVERRIDE RESUME_TEMPLATE_DOC_ID"
missing=""
echo "[bootstrap] environment check:"
for k in $REQUIRED; do
  if [ -n "${!k:-}" ]; then echo "  ✓ $k"; else echo "  ✗ $k (MISSING)"; missing="$missing $k"; fi
done
# Google creds satisfied by either the decoded file or an explicit path.
if [ -n "${GOOGLE_SERVICE_ACCOUNT_JSON:-}" ] && [ -f "${GOOGLE_SERVICE_ACCOUNT_JSON}" ]; then
  echo "  ✓ GOOGLE_SERVICE_ACCOUNT_JSON (file present)"
elif [ -n "${GOOGLE_SERVICE_ACCOUNT_JSON_B64:-}" ]; then
  echo "  ✗ GOOGLE_SERVICE_ACCOUNT_JSON (B64 set but decode failed)"; missing="$missing GOOGLE_SERVICE_ACCOUNT_JSON"
else
  echo "  ✗ GOOGLE_SERVICE_ACCOUNT_JSON (set GOOGLE_SERVICE_ACCOUNT_JSON_B64)"; missing="$missing GOOGLE_SERVICE_ACCOUNT_JSON"
fi

if [ -n "$missing" ]; then
  echo "[bootstrap] WARNING: missing keys —$missing" >&2
  echo "[bootstrap] Set these in the environment's 'Environment variables' field (one KEY=value per line, NO quotes, single-line only)." >&2
fi

# Non-fatal: never block a session on missing secrets.
exit 0
