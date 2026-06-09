"""Task 1 - pure helpers in the Supabase data layer. The query functions need a live
DB and are verified on connect; the timestamp coercion below is pure and unit-tested
(the orchestrator does datetime math on posted_at/scraped_at, so this must be right)."""
import datetime as dt
import pytest
from rapid_agent import _parse_ts, _normalize_job

UTC = dt.timezone.utc

@pytest.mark.parametrize("inp, expected", [
    ("2026-06-09T22:14:00+00:00", dt.datetime(2026, 6, 9, 22, 14, tzinfo=UTC)),
    ("2026-06-09T22:14:00Z",      dt.datetime(2026, 6, 9, 22, 14, tzinfo=UTC)),
    ("2026-06-09T22:14:00",       dt.datetime(2026, 6, 9, 22, 14, tzinfo=UTC)),   # naive -> UTC
    ("2026-06-09T22:14:00.123456+00:00",
                                  dt.datetime(2026, 6, 9, 22, 14, 0, 123456, tzinfo=UTC)),
])
def test_parse_ts_valid(inp, expected):
    assert _parse_ts(inp) == expected

@pytest.mark.parametrize("inp", [None, "", "not a timestamp"])
def test_parse_ts_none(inp):
    assert _parse_ts(inp) is None

def test_parse_ts_passthrough_datetime():
    aware = dt.datetime(2026, 1, 1, tzinfo=UTC)
    assert _parse_ts(aware) is aware
    naive = dt.datetime(2026, 1, 1)
    assert _parse_ts(naive) == dt.datetime(2026, 1, 1, tzinfo=UTC)

def test_normalize_job_coerces_timestamps():
    j = _normalize_job({"id": "x", "posted_at": "2026-06-09T10:00:00Z",
                        "scraped_at": "2026-06-09T11:00:00Z"})
    assert isinstance(j["posted_at"], dt.datetime) and j["posted_at"].tzinfo is not None
    assert isinstance(j["scraped_at"], dt.datetime)
    assert j["id"] == "x"

def test_normalize_job_empty_and_missing_keys():
    assert _normalize_job({}) == {}
    assert _normalize_job(None) is None
    assert _normalize_job({"id": "x"}) == {"id": "x"}   # no ts keys -> untouched
