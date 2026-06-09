"""Unit tests for email rendering (pure, no network). Covers both variants, the
salary-unit fix (no 'per year PER YEAR'), salary omission, and HTML escaping."""
import rapid_agent as r

_CLIENT = {"name": "Jane Doe"}
_JOB = {"job_title": "VP, Sales", "company": "Acme & Co", "location": "Ohio, United States",
        "salary_range": "$120,000 to $150,000 per year", "job_url": "https://x/apply",
        "is_remote": False}


def _match(tier="strong", **kw):
    m = {"job": dict(_JOB), "tier": tier, "composite_score": 88,
         "resume_url": "https://docs/r", "match_reason": "Great fit.", "match_count": 1}
    m.update(kw)
    return m


def test_strong_variant():
    subj, body = r._render_match_email(_CLIENT, _match("strong"))
    assert subj == "NEW: RAPID JOB MATCH FOR JANE DOE"
    assert "🥇" in body and "STRONG FIT" in body
    assert "Why it matches:" in body
    assert "apply within 24 hours" in body


def test_possible_variant():
    subj, body = r._render_match_email(_CLIENT, _match("possible", composite_score=76))
    assert "🥈" in body and "POSSIBLE FIT" in body
    assert "Why it's worth a look:" in body
    assert "cleared our 70% quality bar" in body


def test_salary_unit_not_doubled():
    _, body = r._render_match_email(_CLIENT, _match())
    assert "$120,000 to $150,000 per year" in body
    assert "per year PER YEAR" not in body
    assert "PER YEAR" not in body          # template's hardcoded unit is gone


def test_salary_line_omitted_when_absent():
    job = dict(_JOB, salary_range=None)
    _, body = r._render_match_email(_CLIENT, _match(job=job))
    assert "💰" not in body
    assert "null" not in body.lower()
    assert "Ohio, United States<br>" in body  # location still shown, no salary


def test_remote_location_fallback():
    job = dict(_JOB, location=None, is_remote=True, salary_range=None)
    _, body = r._render_match_email(_CLIENT, _match(job=job))
    assert "Remote" in body


def test_html_escaped():
    _, body = r._render_match_email(_CLIENT, _match())
    assert "Acme &amp; Co" in body          # & escaped, not raw


def test_pluralization():
    _, body = r._render_match_email(_CLIENT, _match("strong", match_count=3))
    assert "Found 3 quality matches" in body
    _, body1 = r._render_match_email(_CLIENT, _match("strong", match_count=1))
    assert "Found 1 quality match " in body1 or "Found 1 quality match." in body1
