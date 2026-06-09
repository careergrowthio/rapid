#!/usr/bin/env python3
"""
Rapid - credential preflight ("knock on each door").

Read-only liveness probes for each external service so we can confirm every
credential works BEFORE running the real pipeline. SAFE BY DESIGN:
  - No email is sent (Postmark probe only reads server info).
  - No rows are written anywhere (Supabase probe only counts).
  - The Claude probe asks for a single token (negligible cost).
Run:  python3 scripts/preflight.py
Each line prints OK / FAIL / SKIP with a short, secret-free reason.
"""
import os, sys

try:                                  # secrets normally come from the environment;
    from dotenv import load_dotenv    # also honor a local .env if one exists
    load_dotenv()
except Exception:
    pass

OK, FAIL, SKIP = "\033[92mOK  \033[0m", "\033[91mFAIL\033[0m", "\033[93mSKIP\033[0m"
results = []

def record(name, status, detail=""):
    results.append((name, status))
    print(f"[{status}] {name:<24} {detail}")

def have(*names):
    return all(os.environ.get(n) for n in names)


# ------------------------------- Anthropic -----------------------------------
def check_anthropic():
    if not have("ANTHROPIC_API_KEY"):
        return record("Anthropic (Claude)", SKIP, "ANTHROPIC_API_KEY not set")
    try:
        from anthropic import Anthropic
        from rapid_agent import MODEL_MATCH
        client = Anthropic()
        r = client.messages.create(model=MODEL_MATCH, max_tokens=1,
                                    messages=[{"role": "user", "content": "ping"}])
        record("Anthropic (Claude)", OK, f"model {MODEL_MATCH} reachable")
    except Exception as e:
        record("Anthropic (Claude)", FAIL, f"{type(e).__name__}: {str(e)[:120]}")


# ------------------------------- Supabase ------------------------------------
def check_supabase():
    if not have("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        return record("Supabase", SKIP, "SUPABASE_URL / SUPABASE_SERVICE_KEY not set")
    try:
        from supabase import create_client
        sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        res = sb.table("clients").select("id", count="exact").limit(1).execute()
        record("Supabase", OK, f"clients table reachable (count={res.count})")
    except Exception as e:
        record("Supabase", FAIL, f"{type(e).__name__}: {str(e)[:120]}")


# -------------------------------- Apify --------------------------------------
def check_apify():
    if not have("APIFY_TOKEN"):
        return record("Apify", SKIP, "APIFY_TOKEN not set")
    try:
        import requests
        r = requests.get("https://api.apify.com/v2/users/me",
                         params={"token": os.environ["APIFY_TOKEN"]}, timeout=20)
        r.raise_for_status()
        user = r.json().get("data", {}).get("username", "?")
        record("Apify", OK, f"authenticated as '{user}'")
    except Exception as e:
        record("Apify", FAIL, f"{type(e).__name__}: {str(e)[:120]}")


# ------------------------------- Postmark ------------------------------------
def check_postmark():
    if not have("POSTMARK_SERVER_TOKEN"):
        return record("Postmark", SKIP, "POSTMARK_SERVER_TOKEN not set")
    try:
        import requests
        r = requests.get("https://api.postmarkapp.com/server",
                         headers={"X-Postmark-Server-Token": os.environ["POSTMARK_SERVER_TOKEN"],
                                  "Accept": "application/json"}, timeout=20)
        r.raise_for_status()
        name = r.json().get("Name", "?")
        override = os.environ.get("TEST_RECIPIENT_OVERRIDE")
        extra = "" if override else "  (WARNING: TEST_RECIPIENT_OVERRIDE unset - sends would have no safe target)"
        record("Postmark", OK, f"server '{name}' reachable (no email sent){extra}")
    except Exception as e:
        record("Postmark", FAIL, f"{type(e).__name__}: {str(e)[:120]}")


# -------------------------------- Google -------------------------------------
def check_google():
    if not have("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return record("Google Docs/Drive", SKIP, "GOOGLE_SERVICE_ACCOUNT_JSON not set (deferred)")
    record("Google Docs/Drive", SKIP, "configured but probe not yet implemented")


def main():
    print("Rapid preflight - read-only credential checks (no email, near-zero cost)\n")
    check_anthropic()
    check_supabase()
    check_apify()
    check_postmark()
    check_google()
    failed = [n for n, s in results if s == FAIL]
    print()
    if failed:
        print(f"{len(failed)} check(s) FAILED: {', '.join(failed)}")
        sys.exit(1)
    print("No failures. (SKIP = credential intentionally not set yet.)")

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main()
