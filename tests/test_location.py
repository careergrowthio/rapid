"""Task 4 - unit tests for location_score and its parsing helpers."""
import pytest
from rapid_agent import location_score, _parse_location, _location_band

REMOTE_ONLY = {"work_arrangements": ["Remote"], "preferred_locations": ["Mesa, Arizona"]}
ONSITE      = {"work_arrangements": ["Onsite"], "preferred_locations": ["Mesa, Arizona"]}
NO_PREF     = {"work_arrangements": ["Onsite"], "preferred_locations": []}


# ------------------------------ _parse_location ------------------------------
@pytest.mark.parametrize("loc, expected", [
    ("Mesa, Arizona", ("mesa", "az")),
    ("New York, NY", ("new york", "ny")),
    ("San Francisco, CA, USA", ("san francisco", "ca")),
    ("Austin, Texas (Hybrid)", ("austin", "tx")),
    ("Mesa", ("mesa", None)),
    ("Remote, US", (None, None)),
    ("", (None, None)),
    (None, (None, None)),
])
def test_parse_location(loc, expected):
    assert _parse_location(loc) == expected


# ------------------------------- location_score ------------------------------
def test_remote_job_remote_client_is_100():
    assert location_score({"is_remote": True, "location": "Remote"}, REMOTE_ONLY) == 100

def test_remote_in_text_remote_client_is_100():
    assert location_score({"location": "Remote (US)"}, REMOTE_ONLY) == 100

def test_exact_city_state_match_is_100():
    assert location_score({"is_remote": False, "location": "Mesa, AZ"}, REMOTE_ONLY) == 100

def test_same_state_different_city_is_75():
    assert location_score({"location": "Phoenix, AZ"}, REMOTE_ONLY) == 75

def test_same_city_unknown_state_is_80():
    assert location_score({"location": "Mesa"}, REMOTE_ONLY) == 80

def test_different_state_is_40():
    assert location_score({"location": "Denver, CO"}, REMOTE_ONLY) == 40

def test_unparseable_location_is_lenient_70():
    assert location_score({"location": ""}, REMOTE_ONLY) == 70

def test_no_preferred_locations_is_lenient_70():
    assert location_score({"location": "Denver, CO"}, NO_PREF) == 70

def test_remote_job_but_onsite_only_client_falls_to_distance():
    # Client does not accept remote -> the remote shortcut does not apply; the bare
    # "Remote" string carries no city/state, so it lands on the lenient default.
    assert location_score({"is_remote": True, "location": "Remote"}, ONSITE) == 70


# -------------------------------- _location_band -----------------------------
def test_location_band_tiers():
    assert _location_band(("mesa", "az"), ("mesa", "az")) == 100
    assert _location_band(("phoenix", "az"), ("mesa", "az")) == 75
    assert _location_band(("mesa", None), ("mesa", "az")) == 80
    assert _location_band(("denver", "co"), ("mesa", "az")) == 40
