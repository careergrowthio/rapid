"""Unit tests for the scrape-layer pure helpers (no network):
provenance re-derivation, salary string building, timeRange mapping, job
normalization, and title normalization for cross-source dedup."""
import rapid_agent as r


# ---- _query_matches_title (Boolean ":*" prefix AND semantics) ----------------
def test_query_matches_all_prefix_tokens():
    assert r._query_matches_title("Senior:* Vice:* President:* Sales:*",
                                  "Senior Vice President of Sales")
    assert r._query_matches_title("VP:* Sales:*", "VP, Global Sales")

def test_query_requires_every_token():
    # 'national' prefix not present -> AND fails
    assert not r._query_matches_title("Director:* National:* Account:*",
                                      "Director of Sales")

def test_query_prefix_not_substring():
    # ":*" is a word-PREFIX match, so 'sales' must start a word, not sit mid-word
    assert not r._query_matches_title("Sales:*", "Wholesale Manager")

def test_query_empty_inputs():
    assert not r._query_matches_title("", "Anything")
    assert not r._query_matches_title("VP:* Sales:*", "")


# ---- _salary_str -------------------------------------------------------------
def test_salary_range_from_min_max():
    s = r._salary_str({"ai_salary_min_value": 150000, "ai_salary_max_value": 175000,
                       "ai_salary_unit_text": "YEAR"})
    assert s == "$150,000 to $175,000 per year"

def test_salary_single_value():
    assert r._salary_str({"ai_salary_value": 85000, "ai_salary_unit_text": "YEAR"}) \
        == "$85,000 per year"

def test_salary_hourly_unit():
    assert r._salary_str({"ai_salary_value": 60, "ai_salary_unit_text": "HOUR"}) \
        == "$60 per hour"

def test_salary_zero_is_absent():
    # some sources emit value:0 -> treat as no salary (None), never "$0"
    assert r._salary_str({"ai_salary_value": 0, "ai_salary_unit_text": "YEAR"}) is None
    assert r._salary_str({}) is None

def test_salary_raw_string_wins():
    assert r._salary_str({"salary_raw": "$120k-$140k DOE",
                          "ai_salary_value": 999}) == "$120k-$140k DOE"

def test_salary_equal_min_max_collapses_to_single():
    s = r._salary_str({"ai_salary_min_value": 100000, "ai_salary_max_value": 100000,
                       "ai_salary_unit_text": "YEAR"})
    assert s == "$100,000 per year"


# ---- _timerange --------------------------------------------------------------
def test_timerange_hours_and_days():
    assert r._timerange(1) == "1h"
    assert r._timerange(2) == "2h"
    assert r._timerange(24) == "24h"
    assert r._timerange(25) == "2d"          # ceil to days past 24h
    assert r._timerange(48) == "2d"
    assert r._timerange(10000) == "7d"       # capped at the freshness window
    assert r._timerange(0) == "1h"           # floored at 1h


# ---- _normalize_job ----------------------------------------------------------
_ITEM = {
    "id": 12345, "title": "Senior Vice President of Sales", "organization": "Acme Foods",
    "url": "https://linkedin.com/jobs/view/1", "source": "linkedin",
    "locations_derived": ["Phoenix, Arizona, United States"], "location_type": "TELECOMMUTE",
    "ai_work_arrangement": "Remote OK", "date_posted": "2026-06-09T20:33:33",
    "description_text": "Lead national accounts.", "ai_salary_value": 200000,
    "ai_salary_unit_text": "YEAR",
}

def test_normalize_job_maps_fields():
    j = r._normalize_job(_ITEM, ["Senior:* Vice:* President:* Sales:*", "VP:* Marketing:*"])
    assert j["job_title"] == "Senior Vice President of Sales"
    assert j["company"] == "Acme Foods"
    assert j["job_url"] == "https://linkedin.com/jobs/view/1"
    assert j["location"] == "Phoenix, Arizona, United States"
    assert j["is_remote"] is True
    assert j["source"] == "fantastic_linkedin"
    assert j["external_job_id"] == "12345"
    assert j["salary_range"] == "$200,000 per year"
    assert j["posted_at"].startswith("2026-06-09T20:33:33")
    # provenance: only the matching union query is tagged
    assert j["matched_queries"] == ["Senior:* Vice:* President:* Sales:*"]
    # content_hash is stable + derived from url|title|company
    assert j["content_hash"] == r.content_hash(j)

def test_normalize_job_drops_when_missing_keys():
    assert r._normalize_job({"title": "x", "url": "u"}, []) is None      # no company
    assert r._normalize_job({"title": "x", "organization": "o"}, []) is None  # no url

def test_normalize_job_non_remote_unknown_is_none():
    item = dict(_ITEM, location_type=None, ai_work_arrangement="On-site")
    assert r._normalize_job(item, []).get("is_remote") is None           # unknown never forced to False


# ---- _norm_title (cross-source dedup) ----------------------------------------
def test_norm_title_strips_punctuation_and_case():
    assert r._norm_title("Sr. VP, Sales!") == "sr vp sales"
    assert r._norm_title("  Director   of  Sales ") == "director of sales"
    assert r._norm_title(None) == ""
