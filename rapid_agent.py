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
# TODO[DEV]: wire supabase-py (create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY))
def get_active_clients():            ...  # clients WHERE status='rapid_active'
def get_match_profile(client_id):    ...  # match_profiles WHERE client_id=...
def get_all_active_query_sets():     ...  # deduped union of (title query x preferred_location) over active clients;
                                          # for clients with search_mode='expanded', ALSO include their expanded_queries
def get_sent_count_since(client_id, days): ...  # COUNT matches status='sent' in last `days`
def set_search_mode(client_id, mode):      ...  # UPDATE match_profiles SET search_mode=...
def upsert_jobs(jobs):               ...  # INSERT INTO jobs ... ON CONFLICT (content_hash) DO NOTHING
def get_pool_jobs_after(since_ts, max_days, client_queries=None):
    ...  # jobs WHERE scraped_at > since AND posted_at > now()-max_days
         #      AND matched_queries && client_queries   -- provenance scope IN SQL (cheapest)
         # since_ts None (client's first run) => ALL pool jobs inside the freshness window
def get_matched_job_ids(client_id):  ...  # set of job_id already in matches for this client (any status)
def get_queued_matches(client_id):   ...  # matches WHERE status='queued' ORDER BY composite_score DESC (join jobs for posted_at, title, company, description)
def recently_sent_similar(client_id, company, title, days):
    ...  # bool: a 'sent' match for same (company, normalized title) within `days`
         # guards cross-source duplicates (same role, different board/url/hash)
def set_last_scored_at(client_id, ts): ...  # UPDATE clients SET last_scored_at=ts
def todays_sent_count(client_id):    ...  # COUNT matches status='sent' where sent_at::date = today IN CAP_TIMEZONE
def save_match(match):               ...  # INSERT into matches -> RETURN id
def update_match(match_id, fields):  ...  # UPDATE matches SET ... WHERE id=...
def get_last_pool_refresh_at():      ...  # ts of last successful pool refresh (run_log) -> for gap widening
def get_alerting_clients():          ...  # SELECT * FROM client_health WHERE health LIKE 'ALERT%'
def log_run(record):                 ...  # INSERT into run_log

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
def render_resume(tailored_content, client):
    # TODO[DEV]: copy the GLOBAL template Doc (RESUME_TEMPLATE_DOC_ID) and fill its
    # {{placeholders}} from tailored_content (which already carries all the client's
    # facts, sourced from base_resume). Do NOT copy the client's master at render
    # time -- the master's content lives in clients.base_resume already.
    # NOTE: the template holds ONE experience block with 3 {{BULLET_n}} lines --
    # duplicate the block per experience entry and per bullet (variable counts),
    # and remove empty sections (e.g. no certifications) rather than leaving headings.
    return "https://docs.google.com/document/d/PLACEHOLDER"

def refresh_base_resume(client):
    # TODO[DEV]: read the client's master resume from Drive ONCE (on activation / when the
    # master changes), normalize to text/structure, and cache it to clients.base_resume.
    # The master's Doc ID/URL is stored in clients.resume_url at onboarding.
    # The per-job tailor step reads this cache -- it never re-fetches/re-parses Drive per job.
    ...

# ============================ EMAIL ==========================================
def send_match_email(client, match):
    # TODO[DEV]: Postmark (Transactional stream); render rapid_match_email_template.md
    #            (Strong/Possible variants). The same 'sent' match row is the
    #            DASHBOARD feed: the Vercel dashboard shows matches WHERE
    #            status='sent' (joined to jobs) -- pending/queued never display.
    ...

def notify_ops(alerting):
    # TODO[DEV]: Postmark email to the ops/team address listing each ALERT client
    #            with its latest_funnel WHY (which gate is starving them).
    #            This is the push half of the 5-day guarantee; the health view is the pull half.
    ...

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
