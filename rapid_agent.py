"""
RAPID — pipeline scaffold (shared job pool)
===========================================
Structured pipeline (not an autonomous agent): deterministic orchestration with
TWO Claude calls (matching skill, resume skill).

COST MODEL (the important part):
  - Scrape ONCE per cycle into a shared global pool (refresh_job_pool), NOT per
    client. A job that fits five clients is pulled from Apify once.
  - Only the recency window is scraped each cycle (new postings since last run),
    never the whole board. Window widens automatically after a missed run
    (Monday's first run covers the weekend).
  - Each client scores only pool jobs added since THEIR last run (last_scored_at),
    so no job is re-scored (or re-pulled) for a client who already saw it.
  - Deterministic pre-filter runs before any Claude call; cheap model for the
    high-volume scoring, stronger model only for the rare resume step;
    prompt-cache the skill system prompts.
  - MAX_SCORED_PER_RUN caps Claude calls per client per cycle (circuit breaker).

CONTRACT (keep these aligned -- they are aligned now, keep them that way):
  - Matching-skill factor keys == match_profiles.weights/factor_thresholds keys
    == the keys used here: seniority, industry, skills, title (+ salary,
    location computed in code).
  - Per-client email_floor / strong_threshold come from match_profiles,
    NOT from global constants.

Env: ANTHROPIC_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY, APIFY_TOKEN,
     GOOGLE_SERVICE_ACCOUNT_JSON, RESUME_TEMPLATE_DOC_ID, POSTMARK_SERVER_TOKEN
Install: pip install anthropic supabase apify-client google-api-python-client
Model strings: verify current values at https://docs.claude.com/en/api/overview
"""

import os, json, hashlib, re, datetime as dt

try:                       # load .env if present (no-op when python-dotenv absent)
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ============================ CONFIG (locked decisions) ======================
DAILY_CAP            = 3
DEFAULT_EMAIL_FLOOR  = 70     # fallback only; the per-client value in match_profiles wins
DEFAULT_STRONG       = 85     # fallback only; per-client strong_threshold wins
MIN_SCRAPE_HOURS     = 2      # scrape window floor per cycle (hourly cadence + safety margin)
SCRAPE_OVERLAP_HOURS = 1      # overlap buffer so boundary postings are never missed
# NOTE: the old fixed 24h window re-bought the same day from Apify 24x/day. The
# effective window is now (time since last successful refresh + overlap), so an
# hourly cadence scrapes ~1-2h of postings; any gap (weekend, outage) auto-widens.
FRESHNESS_MAX_DAYS   = 7
MIN_CONFIDENCE       = 0.5    # min per-factor confidence from the matching skill; below -> not emailed
MAX_SCORED_PER_RUN   = 150    # circuit breaker: max Claude scoring calls per client per cycle
SIMILAR_WINDOW_DAYS  = 30     # don't re-send same company+title to a client within this window
FLOOR_CAP            = 60     # per-factor floors are clamped here unless profile sets allow_strict_floors
                              # (prevents misconfigured floors from recreating the old six-way AND gate)
STARVATION_DAYS      = 5      # 0 emailed matches for this long -> auto-widen QUERIES (never the bar)
                              # + the client_health view ALERTs at 5 days with the WHY (funnel),
                              # and notify_ops pushes it to the team -- spec: notified within 5 days
CAP_TIMEZONE         = "America/Phoenix"  # DECISION: "today" for the 3/day cap is business TZ, not UTC
JUDGMENT_FACTORS     = ("seniority", "industry", "skills", "title")  # must match skill output keys
MODEL_MATCH  = "claude-haiku-4-5-20251001"  # high-volume scoring -> cheapest adequate model; verify string
MODEL_RESUME = "claude-sonnet-4-6"          # rare, quality-sensitive step

_claude = None
def _get_claude():
    """Lazily construct the Anthropic client so the module imports (and the pure
    helpers stay unit-testable) without the SDK installed or a key in the env."""
    global _claude
    if _claude is None:
        from anthropic import Anthropic
        _claude = Anthropic()
    return _claude

_HERE = os.path.dirname(os.path.abspath(__file__))
def load_skill(path):
    with open(os.path.join(_HERE, path)) as f: return f.read()
MATCHING_SKILL = load_skill("rapid_matching_skill.md")
RESUME_SKILL   = load_skill("rapid_resume_tailoring_skill.md")

# ============================ DB — Supabase ==================================
_sb = None
def _get_sb():
    """Lazily build the Supabase client (service role) so the module imports without
    creds. Server-side only; the service key bypasses RLS."""
    global _sb
    if _sb is None:
        from supabase import create_client
        _sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    return _sb

def _parse_ts(s):
    """Postgres timestamptz string -> aware UTC datetime (the code does datetime math
    on posted_at/scraped_at). Pass through datetimes; None stays None."""
    if not s:
        return None
    if isinstance(s, dt.datetime):
        return s if s.tzinfo else s.replace(tzinfo=dt.timezone.utc)
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)

def _normalize_job(j):
    """Coerce a job row's timestamp columns into aware datetimes."""
    if j:
        for k in ("posted_at", "scraped_at"):
            if k in j:
                j[k] = _parse_ts(j[k])
    return j

def get_active_clients():
    return _get_sb().table("clients").select("*").eq("status", "rapid_active").execute().data

def get_match_profile(client_id):
    rows = _get_sb().table("match_profiles").select("*").eq("client_id", client_id).limit(1).execute().data
    return rows[0] if rows else None

def get_all_active_query_sets():
    """Deduped union of every active client's title_search_queries (+ expanded_queries
    when search_mode='expanded'). These strings are the provenance tags written to
    jobs.matched_queries; location is applied in scrape_jobs (Task 2)."""
    sb = _get_sb()
    active = sb.table("clients").select("id").eq("status", "rapid_active").execute().data
    ids = [c["id"] for c in active]
    if not ids:
        return []
    profs = sb.table("match_profiles").select(
        "title_search_queries, expanded_queries, search_mode").in_("client_id", ids).execute().data
    out = set()
    for p in profs:
        out.update(p.get("title_search_queries") or [])
        if p.get("search_mode") == "expanded":
            out.update(p.get("expanded_queries") or [])
    return sorted(out)

def get_sent_count_since(client_id, days):
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    res = _get_sb().table("matches").select("id", count="exact").eq("client_id", client_id)\
        .eq("status", "sent").gte("sent_at", cutoff).execute()
    return res.count or 0

def set_search_mode(client_id, mode):
    _get_sb().table("match_profiles").update({"search_mode": mode})\
        .eq("client_id", client_id).execute()

def upsert_jobs(jobs):
    """Insert new pool jobs; on content_hash conflict MERGE matched_queries (a job
    re-found by another client's query gains that tag) rather than overwriting.
    Read-modify-write per job -- volumes per cycle are small and it needs no DB function."""
    sb = _get_sb()
    for job in jobs or []:
        ch = job.get("content_hash")
        existing = sb.table("jobs").select("id, matched_queries")\
            .eq("content_hash", ch).limit(1).execute().data if ch else []
        if existing:
            cur = existing[0].get("matched_queries") or []
            merged = sorted(set(cur) | set(job.get("matched_queries") or []))
            if merged != sorted(cur):
                sb.table("jobs").update({"matched_queries": merged})\
                    .eq("id", existing[0]["id"]).execute()
        else:
            sb.table("jobs").insert(job).execute()

def get_pool_jobs_after(since_ts, max_days, client_queries=None):
    """Pool jobs within the freshness window, scoped by provenance. since_ts None
    (client's first run) => all pool jobs in the window. Unknown posted_at is kept
    (unknown never kills); the prefilter applies the hard freshness cap."""
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max_days)).isoformat()
    q = _get_sb().table("jobs").select("*").or_(f"posted_at.gte.{cutoff},posted_at.is.null")
    if since_ts:
        q = q.gt("scraped_at", since_ts.isoformat() if isinstance(since_ts, dt.datetime) else since_ts)
    if client_queries:
        q = q.overlaps("matched_queries", list(client_queries))
    return [_normalize_job(j) for j in q.execute().data]

def get_matched_job_ids(client_id):
    rows = _get_sb().table("matches").select("job_id").eq("client_id", client_id).execute().data
    return {r["job_id"] for r in rows if r.get("job_id")}

def get_queued_matches(client_id):
    rows = _get_sb().table("matches").select("*, jobs(*)").eq("client_id", client_id)\
        .eq("status", "queued").order("composite_score", desc=True).execute().data
    out = []
    for r in rows:
        r["job"] = _normalize_job(r.pop("jobs", None) or {})
        out.append(r)
    return out

def recently_sent_similar(client_id, company, title, days):
    """True if a 'sent' match for the same (company, normalized title) exists within
    `days` -- guards cross-source duplicates (same role, different board/url/hash)."""
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()
    rows = _get_sb().table("matches").select("id, jobs(company, job_title)")\
        .eq("client_id", client_id).eq("status", "sent").gte("sent_at", cutoff).execute().data
    co, ti = (company or "").strip().lower(), (title or "").strip().lower()
    for r in rows:
        j = r.get("jobs") or {}
        if (j.get("company") or "").strip().lower() == co and \
           (j.get("job_title") or "").strip().lower() == ti:
            return True
    return False

def set_last_scored_at(client_id, ts):
    _get_sb().table("clients").update(
        {"last_scored_at": ts.isoformat() if isinstance(ts, dt.datetime) else ts})\
        .eq("id", client_id).execute()

def todays_sent_count(client_id):
    """Count today's 'sent' matches, where 'today' is the business day in CAP_TIMEZONE
    (not UTC) -- the 3/day cap is evaluated in business time."""
    from zoneinfo import ZoneInfo
    now_local = dt.datetime.now(ZoneInfo(CAP_TIMEZONE))
    start_utc = now_local.replace(hour=0, minute=0, second=0, microsecond=0)\
        .astimezone(dt.timezone.utc)
    res = _get_sb().table("matches").select("id", count="exact").eq("client_id", client_id)\
        .eq("status", "sent").gte("sent_at", start_utc.isoformat()).execute()
    return res.count or 0

def save_match(match):
    return _get_sb().table("matches").insert(match).execute().data[0]["id"]

def update_match(match_id, fields):
    _get_sb().table("matches").update(fields).eq("id", match_id).execute()

def get_last_pool_refresh_at():
    """Approximate last successful refresh as the newest scraped_at in the pool (drives
    the gap-widening scrape window). No new jobs => window simply overlaps a bit more."""
    rows = _get_sb().table("jobs").select("scraped_at")\
        .order("scraped_at", desc=True).limit(1).execute().data
    return _parse_ts(rows[0]["scraped_at"]) if rows else None

def get_alerting_clients():
    return _get_sb().table("client_health").select("*").like("health", "ALERT%").execute().data

def log_run(record):
    _get_sb().table("run_log").insert(record).execute()

# ============================ SCRAPE — Apify (Fantastic Jobs + similar) ======
def content_hash(job):
    return hashlib.sha256(f"{job['job_url']}|{job['job_title']}|{job['company']}".encode()).hexdigest()

def refresh_job_pool():
    """Run ONCE per cycle. Build the union of all active clients' title x location
    queries, scrape the recency window via the Fantastic Jobs actors (+ similar),
    and insert only jobs not already in the pool (global dedup on content_hash)."""
    last = get_last_pool_refresh_at()
    now  = dt.datetime.now(dt.timezone.utc)
    # window = time since last successful refresh + overlap; widens itself over any
    # gap (weekend, outage), never re-buys already-scraped hours on a normal cycle
    if last:
        gap_h = int((now - last).total_seconds() // 3600) + SCRAPE_OVERLAP_HOURS
    else:
        gap_h = FRESHNESS_MAX_DAYS * 24            # first ever run: fill the pool
    hours = min(max(gap_h, MIN_SCRAPE_HOURS), FRESHNESS_MAX_DAYS * 24)
    query_sets = get_all_active_query_sets()
    jobs = scrape_jobs(query_sets, hours)         # normalized job dicts + content_hash each
    upsert_jobs(jobs)                             # ON CONFLICT (content_hash) DO NOTHING

def scrape_jobs(query_sets, lookback_hours):
    """Call the Apify Fantastic Jobs actors (and similar) for each query in the
    deduped union, with a lookback_hours window. Return normalized job dicts:
    {source, external_job_id, job_title, company, location, is_remote,
     salary_range, job_url, job_description, posted_at, content_hash,
     matched_queries: [the query string(s) that found it]}."""
    # TODO[DEV]: apify-client; reuse the existing actor IDs + Boolean queries.
    #            Gate pricier sources (Indeed/Glassdoor) to run infrequently.
    #            TAG each job with the query that found it (provenance) and make
    #            upsert_jobs MERGE matched_queries on conflict -- this scoping is
    #            what keeps Claude cost flat per client at 100+ clients.
    return []

# ============================ PRE-FILTER (cheap, pre-Claude) =================
# RULE (coverage-critical): UNKNOWN NEVER KILLS. A job is filtered only on a
# STATED violation. Missing arrangement/salary/location data must pass through
# to scoring -- strict parsers silently starving clients is the old system's
# failure mode and must not be rebuilt here.
def prefilter(job, profile):
    """Return None if the job passes; otherwise the kill reason (for the funnel)."""
    if job.get("posted_at"):
        if (dt.datetime.now(dt.timezone.utc) - job["posted_at"]).days > FRESHNESS_MAX_DAYS:
            return "freshness"
    if profile.get("geography_constraint") == "US_only" and not _is_us(job):
        return "geography"
    if not _arrangement_compatible(job, profile):
        return "arrangement"
    floor = profile.get("salary_floor_hard")
    job_max = _parse_salary_max(job.get("salary_range"))
    if floor and job_max is not None and job_max < floor:
        return "salary_floor"
    return None

# --- US geography signals (heuristic, US-biased: only a CLEARLY non-US location
#     is filtered; empty/ambiguous stays in -- "unknown never kills"). -----------
_US_STATE_ABBR = {
    "al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il","in","ia",
    "ks","ky","la","me","md","ma","mi","mn","ms","mo","mt","ne","nv","nh","nj",
    "nm","ny","nc","nd","oh","ok","or","pa","ri","sc","sd","tn","tx","ut","vt",
    "va","wa","wv","wi","wy","dc",
}
_US_STATE_NAMES = {
    "alabama","alaska","arizona","arkansas","california","colorado","connecticut",
    "delaware","florida","georgia","hawaii","idaho","illinois","indiana","iowa",
    "kansas","kentucky","louisiana","maine","maryland","massachusetts","michigan",
    "minnesota","mississippi","missouri","montana","nebraska","nevada",
    "new hampshire","new jersey","new mexico","new york","north carolina",
    "north dakota","ohio","oklahoma","oregon","pennsylvania","rhode island",
    "south carolina","south dakota","tennessee","texas","utah","vermont",
    "virginia","washington","west virginia","wisconsin","wyoming",
    "district of columbia",
}
_NON_US_TOKENS = {
    "canada","mexico","united kingdom","england","scotland","wales","ireland",
    "london","manchester","dublin","germany","berlin","munich","france","paris",
    "spain","madrid","barcelona","italy","rome","milan","netherlands","amsterdam",
    "belgium","switzerland","zurich","sweden","stockholm","denmark","copenhagen",
    "norway","oslo","finland","poland","warsaw","portugal","lisbon","austria",
    "vienna","czech","romania","greece","ukraine","india","bangalore","bengaluru",
    "mumbai","delhi","hyderabad","pune","chennai","gurgaon","china","beijing",
    "shanghai","shenzhen","hong kong","taiwan","singapore","japan","tokyo",
    "south korea","seoul","australia","sydney","melbourne","brisbane","perth",
    "new zealand","auckland","brazil","sao paulo","argentina","buenos aires",
    "chile","colombia","peru","toronto","vancouver","montreal","ottawa","calgary",
    "edmonton","philippines","manila","pakistan","karachi","indonesia","jakarta",
    "malaysia","kuala lumpur","vietnam","thailand","bangkok","south africa",
    "nigeria","lagos","kenya","nairobi","egypt","cairo","uae","dubai","abu dhabi",
    "qatar","israel","tel aviv","turkey","istanbul","emea","apac","latam",
}
_STATE_NAME_TO_ABBR = {
    "alabama":"al","alaska":"ak","arizona":"az","arkansas":"ar","california":"ca",
    "colorado":"co","connecticut":"ct","delaware":"de","florida":"fl","georgia":"ga",
    "hawaii":"hi","idaho":"id","illinois":"il","indiana":"in","iowa":"ia",
    "kansas":"ks","kentucky":"ky","louisiana":"la","maine":"me","maryland":"md",
    "massachusetts":"ma","michigan":"mi","minnesota":"mn","mississippi":"ms",
    "missouri":"mo","montana":"mt","nebraska":"ne","nevada":"nv",
    "new hampshire":"nh","new jersey":"nj","new mexico":"nm","new york":"ny",
    "north carolina":"nc","north dakota":"nd","ohio":"oh","oklahoma":"ok",
    "oregon":"or","pennsylvania":"pa","rhode island":"ri","south carolina":"sc",
    "south dakota":"sd","tennessee":"tn","texas":"tx","utah":"ut","vermont":"vt",
    "virginia":"va","washington":"wa","west virginia":"wv","wisconsin":"wi",
    "wyoming":"wy","district of columbia":"dc",
}

def _is_us(job):
    """True unless the location clearly names a non-US place. Empty/unknown -> True
    (lenient: unknown never kills; Claude + location_score handle the gray area)."""
    loc = (job.get("location") or "").strip()
    if not loc:
        return True
    low = loc.lower()
    if any(s in low for s in ("united states", "usa", "u.s.a", "u.s.")):
        return True
    if re.search(r'(^|[,\s])us($|[,\s])', low):                 # ", US" / "US," etc.
        return True
    if any(re.search(r'\b' + re.escape(n) + r'\b', low) for n in _US_STATE_NAMES):
        return True
    if any(ab in _US_STATE_ABBR for ab in re.findall(r',\s*([a-z]{2})\b', low)):
        return True
    if any(re.search(r'\b' + re.escape(t) + r'\b', low) for t in _NON_US_TOKENS):
        return False                                            # stated non-US
    return True                                                 # ambiguous -> lenient

# --- Work arrangement -----------------------------------------------------------
_REMOTE_WORDS = ("remote", "work from home", "wfh", "telecommute", "anywhere")
_ONSITE_WORDS = ("on-site", "onsite", "on site", "in-office", "in office",
                 "in-person", "in person")

def _norm_arrangement(a):
    a = (a or "").strip().lower()
    if a in ("remote", "fully remote", "wfh", "work from home", "telecommute"):
        return "remote"
    if a in ("onsite", "on-site", "on site", "in-office", "in office",
             "in person", "in-person"):
        return "onsite"
    return a                                                    # 'hybrid' and any custom value pass through

def _job_arrangement(job):
    """The job's STATED arrangement ('remote'|'hybrid'|'onsite'), or None when the
    posting doesn't say. Inferred only from explicit signals, never guessed."""
    text = " ".join(str(job.get(k) or "") for k in
                    ("location", "arrangement", "work_arrangement")).lower()
    if job.get("is_remote") is True and "hybrid" not in text:
        return "remote"
    if "hybrid" in text:
        return "hybrid"
    if any(w in text for w in _REMOTE_WORDS):
        return "remote"
    if any(w in text for w in _ONSITE_WORDS):
        return "onsite"
    return None

def _arrangement_compatible(job, profile):
    """True unless the job's STATED arrangement matches none the client accepts.
    No client constraint, or no stated arrangement -> True (unknown never kills)."""
    allowed = {_norm_arrangement(a) for a in (profile.get("work_arrangements") or [])}
    if not allowed:
        return True
    stated = _job_arrangement(job)
    if stated is None:
        return True
    return stated in allowed

def _parse_salary_max(s):
    """Largest annual dollar figure in a salary string. '$125,000 to $175,000' ->
    175000; '$150k' -> 150000; hourly rates annualized (x2080). Unparseable -> None
    (lenient: an unreadable salary never filters a job; the hard floor only fires on
    a parsed value below it)."""
    if not s:
        return None
    low = str(s).lower()
    hourly = bool(re.search(r'(/\s*h(r|our)|per\s*hour|hourly|an\s*hour)', low))
    vals = []
    for num, suf in re.findall(r'\$?\s*(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*([kK])?', str(s)):
        v = float(num.replace(",", ""))
        if suf:
            v *= 1000
        if hourly and v < 1000:
            v *= 2080                                           # annualize an hourly rate
        if hourly or v >= 1000:                                # drop implausible non-hourly stragglers
            vals.append(v)
    return int(round(max(vals))) if vals else None

# --- Location parsing (city/state) for location_score --------------------------
_LOC_NOISE = {"usa", "us", "united states", "u.s.", "u.s.a", "remote", "anywhere",
              "hybrid", "onsite", "on-site", "on site", "in-office", "in office"}

def _parse_location(loc):
    """'Mesa, Arizona' -> ('mesa','az'); 'New York, NY' -> ('new york','ny').
    Country/remote noise is dropped. Returns (city, state_abbr); either may be None.
    A 2-letter abbreviation wins over a name match so a city that shares a state's
    name (e.g. New York) is still read as the city."""
    if not loc:
        return (None, None)
    parts = [re.sub(r'\(.*?\)', '', p).strip().lower() for p in re.split(r'[,/]', str(loc))]
    parts = [p for p in parts if p and p not in _LOC_NOISE]
    if not parts:
        return (None, None)
    state = state_idx = None
    for i, p in enumerate(parts):                 # prefer an explicit abbreviation
        if p in _US_STATE_ABBR:
            state, state_idx = p, i; break
    if state is None:                             # else fall back to a full state name
        for i, p in enumerate(parts):
            if p in _US_STATE_NAMES:
                state, state_idx = _STATE_NAME_TO_ABBR[p], i; break
    city = next((p for i, p in enumerate(parts) if i != state_idx), None)
    return (city, state)

def _location_band(job_loc, pref_loc):
    """Coarse distance band between a job and a preferred location (no geocoding):
    exact city+state -> 100, same state -> 75, same city only -> 80, else US -> 40."""
    jc, js = job_loc
    pc, ps = pref_loc
    if jc and pc and jc == pc and js and ps and js == ps:
        return 100
    if js and ps and js == ps:
        return 75
    if jc and pc and jc == pc:
        return 80
    return 40

# ============================ CLAUDE CALLS ===================================
def _extract_json(text):
    """Pull a JSON object out of model text: strip code fences and any surrounding
    prose, then parse. Raises ValueError when nothing parseable is present."""
    if not text or not text.strip():
        raise ValueError("empty model response")
    t = text.strip()
    fenced = re.search(r'```(?:json)?\s*(.*?)```', t, re.DOTALL)   # ```json ... ```
    if fenced:
        t = fenced.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        s, e = t.find('{'), t.rfind('}')                          # outermost {...}
        if 0 <= s < e:
            return json.loads(t[s:e + 1])
        raise ValueError("no JSON object found in model response")

def _call_claude(model, skill, payload, max_tokens=1500, validate=None):
    """Call Claude with the skill as a (prompt-cached) system prompt and return the
    parsed JSON. Hardened: strip stray text, validate the shape, and retry ONCE with
    a corrective nudge before giving up."""
    last_text = last_err = None
    messages = [{"role": "user", "content": json.dumps(payload)}]
    for attempt in range(2):                                      # initial try + one retry
        if attempt:                                               # nudge with the bad reply in context
            messages = [
                {"role": "user", "content": json.dumps(payload)},
                {"role": "assistant", "content": last_text or "{}"},
                {"role": "user", "content": "That was not valid JSON for the schema. "
                 "Reply with ONLY the JSON object, no prose and no code fences."},
            ]
        resp = _get_claude().messages.create(
            model=model, max_tokens=max_tokens,
            system=[{"type": "text", "text": skill,               # prompt-cache the skill
                     "cache_control": {"type": "ephemeral"}}],
            messages=messages,
        )
        last_text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            data = _extract_json(last_text)
            if validate:
                validate(data)                                    # raises on bad shape
            return data
        except (ValueError, KeyError, TypeError) as e:
            last_err = e
    raise ValueError(f"Claude returned unparseable/invalid JSON after retry: {last_err}")

def _validate_match(d):
    """Shape check for the matching skill output."""
    fs = d["factor_scores"]
    for f in JUDGMENT_FACTORS:
        if "score" not in fs[f]:
            raise KeyError(f"factor_scores.{f}.score missing")
    if "triggered" not in d["deal_breakers"]:
        raise KeyError("deal_breakers.triggered missing")
    if "overall_assessment" not in d:
        raise KeyError("overall_assessment missing")

def _validate_resume(d):
    """Shape check for the resume-tailoring skill output."""
    tr = d["tailored_resume"]
    for k in ("headline", "summary", "experience", "skills"):
        if k not in tr:
            raise KeyError(f"tailored_resume.{k} missing")

def score_job(job, profile):
    return _call_claude(MODEL_MATCH, MATCHING_SKILL, {
        "job": job,
        "client_profile": {k: profile.get(k) for k in
            ("seniority_summary","scope","industries","job_titles","skills")},
        "deal_breakers": profile.get("deal_breakers", []),
    }, validate=_validate_match)

def tailor_resume(job, profile, match_evidence, base_resume):
    return _call_claude(MODEL_RESUME, RESUME_SKILL, {
        "current_resume": base_resume,
        "job_description": job["job_description"],
        "matching_evidence": match_evidence,
    }, max_tokens=4000, validate=_validate_resume)

# ============================ DETERMINISTIC SCORING ==========================
def salary_score(job, profile):
    job_max = _parse_salary_max(job.get("salary_range"))
    if job_max is None: return 70          # unlisted salary: lenient (hard floor already checked)
    floor = profile.get("salary_min") or 0
    if job_max >= floor: return 100
    if job_max >= floor * 0.8: return 60
    return 20

def location_score(job, profile):
    """Remote job + remote-accepting client -> 100. Otherwise the best distance band
    against the client's preferred_locations. Unknown/unlistable location -> 70
    (lenient default, matching salary_score: missing data never tanks the composite)."""
    arrangements = {_norm_arrangement(a) for a in (profile.get("work_arrangements") or [])}
    if _job_arrangement(job) == "remote" and ("remote" in arrangements or not arrangements):
        return 100
    job_loc = _parse_location(job.get("location"))
    if job_loc == (None, None):
        return 70                              # unparseable location -> lenient
    prefs = [_parse_location(p) for p in (profile.get("preferred_locations") or [])]
    if not prefs:
        return 70                              # no stated preference -> lenient
    return max(_location_band(job_loc, p) for p in prefs)

def composite(scores, weights):
    total_w = sum(weights.values()) or 1
    return round(sum(scores[f] * weights.get(f, 0) for f in scores) / total_w)

def effective_floors(profile):
    """Per-factor floors, clamped at FLOOR_CAP unless explicitly overridden.
    Misconfigured floors must not be able to silently starve a client."""
    floors = profile.get("factor_thresholds") or {}
    if profile.get("allow_strict_floors"):
        return floors
    return {f: min(v, FLOOR_CAP) for f, v in floors.items()}

def failed_floors(scores, floors):
    return [f for f in floors if scores.get(f, 0) < floors.get(f, 0)]

def passes_floors(scores, floors):
    return not failed_floors(scores, floors)

def min_confidence(judgment):
    return min((judgment["factor_scores"][f].get("confidence", 0) for f in JUDGMENT_FACTORS), default=0)

def tier_of(comp, profile):
    floor  = float(profile.get("email_floor")  or DEFAULT_EMAIL_FLOOR)   # per-client knobs from
    strong = float(profile.get("strong_threshold") or DEFAULT_STRONG)    # match_profiles, NOT globals
    if comp >= strong: return "strong"
    if comp >= floor:  return "possible"
    return None

# ============================ RESUME RENDER (Google Docs template) ===========
# Template {{placeholders}} (discovered from Rapid_Resume_Template.docx):
#   header  : FULL_NAME HEADLINE EMAIL PHONE LOCATION LINKEDIN
#   body    : SUMMARY SKILLS
#   exp blk : JOB_TITLE COMPANY JOB_LOCATION START END BULLET_1 BULLET_2 BULLET_3
#   edu     : DEGREE INSTITUTION GRAD_YEAR
#   other   : CERTIFICATIONS
SKILL_SEP = " • "

def build_resume_fields(tailored, client):
    """Map the resume-skill output + client identity onto the template's single-occurrence
    {{placeholders}}. Pure + testable. Uses the first experience/education for the
    template's single block (extra entries need live block-duplication -- see render_resume).
    Missing values render as empty strings; truly-empty sections are pruned at render time."""
    tailored = tailored or {}
    exp = tailored.get("experience") or []
    edu = tailored.get("education") or []
    e0  = exp[0] if exp else {}
    ed0 = edu[0] if edu else {}
    bullets = e0.get("bullets") or []
    def bullet(i): return bullets[i] if i < len(bullets) else ""
    return {
        "FULL_NAME":      client.get("name") or "",
        "HEADLINE":       tailored.get("headline") or "",
        "EMAIL":          client.get("email") or "",
        "PHONE":          client.get("phone") or "",
        "LOCATION":       client.get("location") or "",
        "LINKEDIN":       client.get("linkedin_url") or "",
        "SUMMARY":        tailored.get("summary") or "",
        "SKILLS":         SKILL_SEP.join(tailored.get("skills") or []),
        "JOB_TITLE":      e0.get("title") or "",
        "COMPANY":        e0.get("company") or "",
        "JOB_LOCATION":   e0.get("location") or "",
        "START":          e0.get("start") or "",
        "END":            e0.get("end") or "",
        "BULLET_1":       bullet(0),
        "BULLET_2":       bullet(1),
        "BULLET_3":       bullet(2),
        "DEGREE":         ed0.get("credential") or "",
        "INSTITUTION":    ed0.get("institution") or "",
        "GRAD_YEAR":      str(ed0.get("year") or ""),
        "CERTIFICATIONS": SKILL_SEP.join(tailored.get("certifications") or []),
    }

def _resume_replace_requests(fields):
    """Google Docs batchUpdate replaceAllText requests, one per {{placeholder}}. Pure."""
    return [{"replaceAllText": {"containsText": {"text": f"{{{{{k}}}}}", "matchCase": True},
                                "replaceText": v}}
            for k, v in fields.items()]

def _drive_id(url_or_id):
    """Extract a Google Doc/Drive file id from a share URL, or pass through a bare id. Pure."""
    m = re.search(r"/d/([A-Za-z0-9_-]+)", url_or_id or "")
    return m.group(1) if m else (url_or_id or "").strip()

def _google_creds():
    """Service-account credentials. GOOGLE_SERVICE_ACCOUNT_JSON may be a file PATH or the
    inline JSON contents (handy for cloud envs)."""
    from google.oauth2 import service_account
    raw = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(raw) if raw.lstrip().startswith("{") else json.load(open(raw))
    return service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/documents",
                      "https://www.googleapis.com/auth/drive"])

def render_resume(tailored_content, client):
    """Copy the GLOBAL template Doc, merge tailored_content into its {{placeholders}}, write
    to the output Drive folder, return the shareable link. The client's facts already live in
    tailored_content/base_resume, so the master is NOT copied here.
    LIVE-VERIFY (deferred until Google creds + network): rendering >1 experience or >3 bullets
    needs block-duplication, and empty-section heading removal needs a structural pass; the
    single-block flat merge below is what the unit tests cover."""
    from googleapiclient.discovery import build
    creds = _google_creds()
    drive = build("drive", "v3", credentials=creds)
    docs  = build("docs", "v1", credentials=creds)
    out_folder = os.environ.get("DRIVE_OUTPUT_FOLDER_ID")
    first_co = (tailored_content.get("experience") or [{}])[0].get("company", "")
    title = f"{client.get('name','Resume')} - {first_co}".strip(" -")
    copy = drive.files().copy(
        fileId=os.environ["RESUME_TEMPLATE_DOC_ID"],
        body={"name": title, **({"parents": [out_folder]} if out_folder else {})},
        supportsAllDrives=True).execute()
    doc_id = copy["id"]
    fields = build_resume_fields(tailored_content, client)
    docs.documents().batchUpdate(
        documentId=doc_id, body={"requests": _resume_replace_requests(fields)}).execute()
    return f"https://docs.google.com/document/d/{doc_id}/edit"

def refresh_base_resume(client):
    """Read the client's master resume from Drive ONCE and return its plain text, for the data
    layer to cache in clients.base_resume. resume_url holds the master Doc id/url. The per-job
    tailor step reads that cache -- it never re-fetches Drive per job."""
    from googleapiclient.discovery import build
    drive = build("drive", "v3", credentials=_google_creds())
    data = drive.files().export(
        fileId=_drive_id(client.get("resume_url")), mimeType="text/plain").execute()
    return data.decode("utf-8") if isinstance(data, bytes) else data

# ============================ EMAIL ==========================================
def _email_location(job):
    return job.get("location") or ("Remote" if job.get("is_remote") else "")

def render_match_email(client, match, match_count=1):
    """Render the (subject, html) for one match per rapid_match_email_template.md.
    Pure + testable. Strong vs Possible differ only in the medal, the tier label on
    the score line, and the closing line; everything else is identical."""
    job   = match["job"]
    name  = (client.get("name") or "").upper()
    score = int(round(float(match.get("composite_score") or 0)))
    tier  = match.get("tier")
    strong = tier == "strong"
    medal  = "🥇" if strong else "🥈"
    label  = "STRONG FIT" if strong else "POSSIBLE FIT"
    why_lead = "Why it matches:" if strong else "Why it's worth a look:"
    closing = ("Strong fits go fast - apply within 24 hours for best results!"
               if strong else
               "This one cleared our 70% quality bar but isn't a slam dunk - you "
               "decide. Your tailored resume is ready either way.")
    word = "match" if match_count == 1 else "matches"

    loc = _email_location(job)
    salary = job.get("salary_range")
    line2 = loc + (f" | 💰 {salary} PER YEAR" if salary else "")   # omit salary segment when absent

    subject = f"NEW: RAPID JOB MATCH FOR {name}"
    html = (
        f"<p>🎯 JOB MATCHES FOR {name} 🎯</p>"
        f"<p>{medal} <b>{job.get('job_title','')}</b> at {job.get('company','')}<br>"
        f"{line2}<br>"
        f"Match Score: {score}% - {label}<br>"
        f"📄 Tailored Resume: <a href=\"{match.get('resume_url','')}\">View Resume</a><br>"
        f"<a href=\"{job.get('job_url','')}\">Apply Here</a></p>"
        f"<p><b>{why_lead}</b> {match.get('match_reason','')}</p>"
        f"<p>💡 Found {match_count} quality {word} (70%+ fit). {closing}</p>"
    )
    return subject, html

def _post_postmark(payload):
    """POST one message to Postmark. Isolated so tests can stub it (no network)."""
    import requests
    token = os.environ["POSTMARK_SERVER_TOKEN"]
    r = requests.post("https://api.postmarkapp.com/email",
                      headers={"X-Postmark-Server-Token": token,
                               "Accept": "application/json",
                               "Content-Type": "application/json"},
                      json=payload, timeout=30)
    r.raise_for_status()
    return r.json()

def send_match_email(client, match):
    """Render + send one match email via Postmark (Transactional stream).
    SAFETY RAIL: in test mode every email goes ONLY to TEST_RECIPIENT_OVERRIDE; we
    NEVER fall back to the real client address. No override set -> refuse to send."""
    to = os.environ.get("TEST_RECIPIENT_OVERRIDE")
    if not to:
        raise RuntimeError("TEST_RECIPIENT_OVERRIDE not set - refusing to send "
                           "(test-mode safety rail). No email sent.")
    subject, html = render_match_email(client, match)
    sender = os.environ.get("POSTMARK_FROM_ADDRESS", "rapidnotifications@careergrowth.io")
    return _post_postmark({
        "From": f"RAPID MATCH <{sender}>",
        "To": to,
        "Subject": subject,
        "HtmlBody": html,
        "MessageStream": "outbound",       # Postmark Transactional stream
    })

def render_ops_alert(alerting):
    """Render (subject, html) for the ops starvation alert listing each ALERT client
    and its latest funnel WHY. Pure + testable."""
    n = len(alerting)
    subject = f"RAPID ALERT: {n} client{'s' if n != 1 else ''} with 0 matches in 5 days"
    rows = []
    for c in alerting:
        why = c.get("latest_funnel")
        why = json.dumps(why) if not isinstance(why, str) else why
        rows.append(f"<li><b>{c.get('name','?')}</b> ({c.get('health','')})<br>"
                    f"<small>limiting gate: {why or 'no funnel logged'}</small></li>")
    html = (f"<p>{n} active client{'s' if n != 1 else ''} have received 0 emailed "
            f"matches in 5 days. Each row shows the latest funnel diagnosis (the WHY):</p>"
            f"<ul>{''.join(rows)}</ul>")
    return subject, html

def notify_ops(alerting):
    """Email the ops/team address the list of ALERT clients + their funnel WHY (the
    push half of the 5-day starvation guarantee). No-op when there's nothing to send."""
    if not alerting:
        return None
    to = os.environ.get("OPS_ALERT_EMAIL")
    if not to:
        raise RuntimeError("OPS_ALERT_EMAIL not set - cannot send ops alert.")
    subject, html = render_ops_alert(alerting)
    sender = os.environ.get("POSTMARK_FROM_ADDRESS", "rapidnotifications@careergrowth.io")
    return _post_postmark({
        "From": f"RAPID OPS <{sender}>",
        "To": to,
        "Subject": subject,
        "HtmlBody": html,
        "MessageStream": "outbound",
    })

# ============================ DELIVERY (shared by new + queued) ==============
def deliver(client, profile, job, comp, tier, scores, reason, evidence, conf, now,
            queued_match_id=None):
    """Tailor -> render -> save/send with crash-safe ordering:
    write 'pending' first, send the email, then mark 'sent'. A crash between the
    two leaves a 'pending' row (alertable), never a phantom 'sent'."""
    tailored   = tailor_resume(job, profile, evidence, client.get("base_resume"))
    resume_url = render_resume(tailored["tailored_resume"], client)
    fields = {"client_id": client["id"], "job_id": job["id"],
              "composite_score": comp, "dimension_scores": scores, "tier": tier,
              "match_reason": reason, "confidence": conf,
              "resume_url": resume_url, "status": "pending"}
    if queued_match_id:
        update_match(queued_match_id, fields); match_id = queued_match_id
    else:
        match_id = save_match(fields)
    send_match_email(client, {**fields, "job": job})
    update_match(match_id, {"status": "sent", "sent_at": now.isoformat()})

# ============================ ORCHESTRATOR ===================================
def client_queries(profile):
    """The queries this client is scoped to: their own searches, plus the
    expanded set when the coverage ladder has widened them."""
    q = set(profile.get("title_search_queries") or [])
    if profile.get("search_mode") == "expanded":
        q |= set(profile.get("expanded_queries") or [])
    return q

def in_client_scope(job, queries):
    """Provenance gate: a client scores ONLY jobs found by their own queries.
    (Same coverage as the old per-client scrape, at shared-pool cost.)"""
    return bool(set(job.get("matched_queries") or []) & queries)

def starvation_check(client, profile):
    """The coverage ladder, rung 2: zero emailed matches for STARVATION_DAYS ->
    widen the client's SEARCH QUERIES (adjacent titles, metro areas) by flipping
    search_mode to 'expanded'. The 70% bar and floors are NEVER lowered.
    (Rung 1 is the blocking onboarding feasibility gate; rung 3 is the 14-day
    human alert in the client_health view, now armed with funnel data.)"""
    if profile.get("search_mode") == "standard" and \
       get_sent_count_since(client["id"], STARVATION_DAYS) == 0:
        set_search_mode(client["id"], "expanded")
        log_run({"client_id": client["id"],
                 "error": f"coverage: 0 sent in {STARVATION_DAYS}d -> search_mode=expanded"})

def run_for_client(client):
    profile     = get_match_profile(client["id"])
    matched_ids = set(get_matched_job_ids(client["id"]))
    remaining   = max(DAILY_CAP - todays_sent_count(client["id"]), 0)
    now         = dt.datetime.now(dt.timezone.utc)
    floors      = effective_floors(profile)
    funnel      = {"pool_candidates": 0, "already_matched": 0, "similar_recent": 0,
                   "prefilter": {}, "deal_breaker": 0, "low_confidence": 0,
                   "floor": {}, "below_email_floor": 0, "near_misses": []}

    # ---- 1) DRAIN THE QUEUE first (yesterday's overflow strong matches) ----
    for q in get_queued_matches(client["id"]):
        job = q["job"]
        if job.get("posted_at") and (now - job["posted_at"]).days > FRESHNESS_MAX_DAYS:
            update_match(q["id"], {"status": "dropped"})       # expired on freshness
            continue
        if remaining <= 0:
            break                                              # stays queued for tomorrow
        deliver(client, profile, job, q["composite_score"], q["tier"],
                q.get("dimension_scores"), q.get("match_reason"),
                q.get("dimension_scores"), q.get("confidence"), now,
                queued_match_id=q["id"])
        remaining -= 1

    # ---- 2) SCORE new pool jobs since this client's last run ----
    scope      = client_queries(profile)
    candidates = get_pool_jobs_after(client.get("last_scored_at"), FRESHNESS_MAX_DAYS,
                                     client_queries=list(scope))
    candidates = [j for j in candidates if in_client_scope(j, scope)]  # belt + braces with the SQL scope
    candidates = sorted(candidates, key=lambda j: j.get("posted_at") or now, reverse=True)
    candidates = candidates[:MAX_SCORED_PER_RUN]   # circuit breaker (rarely hit once scoped)
    funnel["pool_candidates"] = len(candidates)

    eligible = []
    for job in candidates:
        if job["id"] in matched_ids:        # already surfaced to this client
            funnel["already_matched"] += 1
            continue
        if recently_sent_similar(client["id"], job["company"], job["job_title"],
                                 SIMILAR_WINDOW_DAYS):
            funnel["similar_recent"] += 1   # same role from another board/url
            continue
        kill = prefilter(job, profile)      # cheap per-client gates
        if kill:
            funnel["prefilter"][kill] = funnel["prefilter"].get(kill, 0) + 1
            continue
        judgment = score_job(job, profile)  # the only volume Claude call
        if judgment["deal_breakers"]["triggered"]:
            funnel["deal_breaker"] += 1
            continue
        conf = min_confidence(judgment)
        fs = {f: judgment["factor_scores"][f]["score"] for f in JUDGMENT_FACTORS}
        fs["salary"], fs["location"] = salary_score(job, profile), location_score(job, profile)
        if conf < MIN_CONFIDENCE:           # locked decision: emailed matches are high-confidence
            funnel["low_confidence"] += 1   # logged, never silent: visible per run to the team
            continue
        ff = failed_floors(fs, floors)
        if ff:
            for f in ff:
                funnel["floor"][f] = funnel["floor"].get(f, 0) + 1
            continue
        comp = composite(fs, profile["weights"])
        tier = tier_of(comp, profile)       # per-client email_floor / strong_threshold
        if tier is None:
            funnel["below_email_floor"] += 1
            if comp >= 60 and len(funnel["near_misses"]) < 3:   # the closest non-sends, for humans
                funnel["near_misses"].append({"job": job["job_title"],
                                              "company": job["company"], "composite": comp})
            continue
        eligible.append({"job": job, "composite": comp, "tier": tier, "scores": fs,
                         "confidence": conf, "judgment": judgment})

    eligible.sort(key=lambda m: (m["tier"] != "strong", -m["composite"]))  # strong first
    to_send = eligible[:remaining]

    for m in to_send:
        deliver(client, profile, m["job"], m["composite"], m["tier"], m["scores"],
                m["judgment"]["overall_assessment"], m["judgment"]["factor_scores"],
                m["confidence"], now)

    for m in eligible[len(to_send):]:       # overflow: strong -> queue (full record), possibles -> drop
        if m["tier"] == "strong":
            save_match({"client_id": client["id"], "job_id": m["job"]["id"],
                        "composite_score": m["composite"], "tier": "strong",
                        "dimension_scores": m["scores"], "confidence": m["confidence"],
                        "match_reason": m["judgment"]["overall_assessment"],
                        "status": "queued"})

    set_last_scored_at(client["id"], now)   # advance cursor: don't re-score these jobs next run
    starvation_check(client, profile)       # coverage ladder: widen queries if starving
    log_run({"client_id": client["id"], "jobs_scored": len(candidates),
             "strong_matches": sum(1 for m in eligible if m["tier"] == "strong"),
             "possible_matches": sum(1 for m in eligible if m["tier"] == "possible"),
             "sent_count": len(to_send), "funnel": funnel,
             "run_finished_at": now.isoformat()})

def main():
    refresh_job_pool()                      # ONE shared scrape for everyone
    for client in get_active_clients():
        try:
            run_for_client(client)
        except Exception as e:
            log_run({"client_id": client.get("id"), "error": str(e)})
    alerting = get_alerting_clients()       # 5-day starvation: push the WHY to the team
    if alerting:
        notify_ops(alerting)
    # TODO[DEV]: bound concurrency across clients to hit the hourly SLA
    # TODO[DEV]: trigger mechanism -- pg_cron alone can't run this container.
    #            Either pg_cron + pg_net HTTP call to a container webhook, or the
    #            host platform's scheduler (Railway/Render cron). Pick one.

if __name__ == "__main__":
    main()
