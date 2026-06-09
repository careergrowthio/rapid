"""
Task 3 - unit tests for the three pure parser helpers in rapid_agent.

Coverage-critical invariant under test (Handoff sec 4, coverage ladder rung 2):
UNKNOWN NEVER KILLS. Each parser must stay lenient on missing/ambiguous data and
only ever signal a violation on a CLEARLY STATED one.
"""
import pytest
from rapid_agent import (
    _parse_salary_max, _is_us, _arrangement_compatible,
    _job_arrangement, prefilter,
)


# ----------------------------- _parse_salary_max -----------------------------
@pytest.mark.parametrize("text, expected", [
    ("$125,000 to $175,000", 175000),          # the documented example
    ("$125,000 - $175,000", 175000),
    ("$125,000–$175,000", 175000),             # en-dash separator
    ("Up to $200,000 annually", 200000),
    ("$150,000", 150000),
    ("$150k", 150000),
    ("$120k-$150k", 150000),
    ("150000", 150000),
    ("$1,000,000", 1000000),
    ("$60/hr", 124800),                        # hourly annualized x2080
    ("$50 - $60 per hour", 124800),
    ("$72.50/hour", 150800),
])
def test_parse_salary_max_values(text, expected):
    assert _parse_salary_max(text) == expected


@pytest.mark.parametrize("text", [
    None, "", "Competitive", "DOE", "Salary negotiable",
    "$0", "$500",                              # implausible non-hourly stragglers -> unparseable
])
def test_parse_salary_max_unparseable_is_none(text):
    assert _parse_salary_max(text) is None


# --------------------------------- _is_us ------------------------------------
@pytest.mark.parametrize("loc, is_remote", [
    ("Mesa, Arizona", None),
    ("New York, NY", None),
    ("San Francisco, CA, USA", None),
    ("Remote, US", True),
    ("Austin, Texas, United States", None),
    ("Portland, OR", None),
    ("Washington, DC", None),
    ("", True),                                # empty + remote -> lenient pass
    ("Remote", True),                          # bare remote, no country -> lenient pass
    ("Anywhere", True),
])
def test_is_us_true(loc, is_remote):
    assert _is_us({"location": loc, "is_remote": is_remote}) is True


@pytest.mark.parametrize("loc", [
    "London, UK", "Toronto, ON", "Remote - Canada", "Berlin, Germany",
    "Bangalore, India", "Sydney, Australia", "Remote (EMEA)", "Dublin, Ireland",
])
def test_is_us_false(loc):
    assert _is_us({"location": loc}) is False


# -------------------------- _arrangement_compatible --------------------------
REMOTE_ONLY = {"work_arrangements": ["Remote"]}
REMOTE_HYBRID = {"work_arrangements": ["Remote", "Hybrid"]}
NO_CONSTRAINT = {"work_arrangements": []}

def test_remote_job_ok_for_remote_client():
    assert _arrangement_compatible({"is_remote": True, "location": "Remote"}, REMOTE_ONLY) is True

def test_hybrid_job_filtered_for_remote_only_client():
    assert _arrangement_compatible({"location": "Mesa, AZ (Hybrid)"}, REMOTE_ONLY) is False

def test_onsite_job_filtered_for_remote_only_client():
    assert _arrangement_compatible({"location": "Mesa, AZ", "work_arrangement": "On-site"},
                                   REMOTE_ONLY) is False

def test_hybrid_job_ok_when_client_allows_hybrid():
    assert _arrangement_compatible({"location": "Chicago, IL (Hybrid)"}, REMOTE_HYBRID) is True

def test_unstated_arrangement_passes_through():
    # No arrangement signal at all -> lenient pass (unknown never kills).
    assert _arrangement_compatible({"location": "Chicago, IL"}, REMOTE_ONLY) is True

def test_no_client_constraint_always_ok():
    assert _arrangement_compatible({"location": "Chicago, IL (Onsite)"}, NO_CONSTRAINT) is True

def test_hybrid_beats_remote_word_in_same_text():
    # "Hybrid remote" is hybrid, not remote.
    assert _job_arrangement({"location": "Hybrid remote in Austin"}) == "hybrid"


# ---------------- prefilter integration (lenient gate ordering) --------------
MESA = {
    "geography_constraint": "US_only",
    "work_arrangements": ["Remote"],
    "salary_floor_hard": 125000,
}

def test_prefilter_passes_clean_remote_us_job():
    job = {"location": "Remote, US", "is_remote": True,
           "salary_range": "$150,000 - $180,000", "posted_at": None}
    assert prefilter(job, MESA) is None

def test_prefilter_kills_stated_non_us():
    assert prefilter({"location": "London, UK", "is_remote": True}, MESA) == "geography"

def test_prefilter_kills_below_hard_salary_floor():
    job = {"location": "Remote, US", "is_remote": True, "salary_range": "$90,000"}
    assert prefilter(job, MESA) == "salary_floor"

def test_prefilter_lenient_on_missing_salary():
    # Unlisted salary must NOT trip the hard floor.
    job = {"location": "Remote, US", "is_remote": True, "salary_range": None}
    assert prefilter(job, MESA) is None
