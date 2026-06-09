"""Task 6 - email rendering + send safety. Postmark is stubbed (no network/keys)."""
import pytest
import rapid_agent
from rapid_agent import render_match_email, render_ops_alert, send_match_email, notify_ops

CLIENT = {"id": "c1", "name": "Mesa Test Client", "email": "real.client@example.com"}

def _match(tier="strong", salary="$150,000 - $180,000", remote=True):
    return {
        "tier": tier, "composite_score": 88.4, "match_reason": "You should apply now.",
        "resume_url": "https://docs.google.com/document/d/RESUME",
        "job": {"job_title": "SVP Sales", "company": "Acme Foods",
                "location": "" if remote else "Chicago, IL", "is_remote": remote,
                "salary_range": salary, "job_url": "https://apply.example.com/123"},
    }


# -------------------------------- rendering ----------------------------------
def test_strong_variant_content():
    subject, html = render_match_email(CLIENT, _match("strong"))
    assert subject == "NEW: RAPID JOB MATCH FOR MESA TEST CLIENT"
    assert "🥇" in html and "STRONG FIT" in html
    assert "Why it matches:" in html
    assert "Strong fits go fast" in html
    assert "Match Score: 88% -" in html            # rounded, no em dash
    assert "—" not in html                          # contract: no em dashes
    assert 'href="https://apply.example.com/123"' in html
    assert 'href="https://docs.google.com/document/d/RESUME"' in html

def test_possible_variant_content():
    _, html = render_match_email(CLIENT, _match("possible"))
    assert "🥈" in html and "POSSIBLE FIT" in html
    assert "Why it's worth a look:" in html
    assert "cleared our 70% quality bar" in html

def test_salary_line_present_when_known():
    _, html = render_match_email(CLIENT, _match(salary="$150,000 - $180,000", remote=False))
    assert "💰 $150,000 - $180,000 PER YEAR" in html
    assert "Chicago, IL" in html

def test_salary_segment_omitted_when_absent():
    _, html = render_match_email(CLIENT, _match(salary=None, remote=True))
    assert "💰" not in html and "PER YEAR" not in html
    assert "null" not in html.lower()

def test_remote_location_label():
    _, html = render_match_email(CLIENT, _match(remote=True, salary=None))
    assert "Remote" in html

def test_match_count_pluralization():
    _, h1 = render_match_email(CLIENT, _match(), match_count=1)
    _, h2 = render_match_email(CLIENT, _match(), match_count=2)
    assert "Found 1 quality match (" in h1
    assert "Found 2 quality matches (" in h2


# ------------------------------ send safety ----------------------------------
def test_send_targets_override_never_client(fake_postmark, monkeypatch):
    monkeypatch.setenv("TEST_RECIPIENT_OVERRIDE", "owner@careergrowth.io")
    monkeypatch.setenv("POSTMARK_SERVER_TOKEN", "x")
    send_match_email(CLIENT, _match("strong"))
    sent = fake_postmark.payloads[-1]
    assert sent["To"] == "owner@careergrowth.io"
    assert sent["To"] != CLIENT["email"]            # never the real client
    assert sent["MessageStream"] == "outbound"

def test_send_refuses_without_override(fake_postmark, monkeypatch):
    monkeypatch.delenv("TEST_RECIPIENT_OVERRIDE", raising=False)
    with pytest.raises(RuntimeError):
        send_match_email(CLIENT, _match("strong"))
    assert fake_postmark.payloads == []             # nothing sent


# ------------------------------- ops alert -----------------------------------
def test_render_ops_alert_lists_clients_and_why():
    alerting = [{"name": "Mesa Test Client", "health": "ALERT: 0 in 5 days",
                 "latest_funnel": {"floor": {"salary": 12}}}]
    subject, html = render_ops_alert(alerting)
    assert "1 client" in subject
    assert "Mesa Test Client" in html
    assert "salary" in html                         # the funnel WHY is included

def test_notify_ops_noop_when_empty(fake_postmark):
    assert notify_ops([]) is None
    assert fake_postmark.payloads == []


@pytest.fixture
def fake_postmark(monkeypatch):
    class Recorder:
        def __init__(self): self.payloads = []
        def __call__(self, payload):
            self.payloads.append(payload)
            return {"ErrorCode": 0, "Message": "OK"}
    rec = Recorder()
    monkeypatch.setattr(rapid_agent, "_post_postmark", rec)
    return rec
