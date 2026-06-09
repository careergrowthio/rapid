"""
Task 7 - hardened Claude JSON parsing.

No network/keys: a fake client returns canned text so we can exercise fence
stripping, prose extraction, shape validation, and the single retry.
"""
import json
import pytest
import rapid_agent
from rapid_agent import _extract_json, _call_claude, score_job


# ------------------------------ fake Anthropic -------------------------------
class _Block:
    def __init__(self, text): self.type, self.text = "text", text

class _Resp:
    def __init__(self, text): self.content = [_Block(text)]

class _Messages:
    def __init__(self, outputs): self.outputs, self.calls = list(outputs), []
    def create(self, **kw):
        self.calls.append(kw)
        return _Resp(self.outputs.pop(0))

class FakeClient:
    def __init__(self, outputs): self.messages = _Messages(outputs)


@pytest.fixture
def fake(monkeypatch):
    def install(outputs):
        client = FakeClient(outputs)
        monkeypatch.setattr(rapid_agent, "_claude", client)
        return client
    return install


VALID_MATCH = {
    "factor_scores": {
        "seniority": {"score": 90, "evidence": "x", "confidence": 0.9},
        "industry":  {"score": 80, "evidence": "x", "confidence": 0.8},
        "skills":    {"score": 85, "evidence": "x", "confidence": 0.8},
        "title":     {"score": 88, "evidence": "x", "confidence": 0.9},
    },
    "deal_breakers": {"triggered": False, "which": [], "evidence": ""},
    "overall_assessment": "You should apply.",
}


# -------------------------------- _extract_json ------------------------------
def test_extract_plain_json():
    assert _extract_json('{"a": 1}') == {"a": 1}

def test_extract_strips_json_fence():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}

def test_extract_strips_bare_fence():
    assert _extract_json('```\n{"a": 1}\n```') == {"a": 1}

def test_extract_handles_surrounding_prose():
    assert _extract_json('Sure! Here you go:\n{"a": 1}\nHope that helps.') == {"a": 1}

def test_extract_nested_object():
    assert _extract_json('prefix {"a": {"b": 2}} suffix') == {"a": {"b": 2}}

@pytest.mark.parametrize("bad", ["", "   ", "no json here", "{not json}"])
def test_extract_raises_on_garbage(bad):
    with pytest.raises(ValueError):
        _extract_json(bad)


# --------------------------------- retry path --------------------------------
def test_retry_recovers_after_one_bad_reply(fake):
    client = fake(["not json at all", json.dumps({"ok": True})])
    assert _call_claude("m", "skill", {"p": 1}) == {"ok": True}
    assert len(client.messages.calls) == 2            # retried exactly once

def test_gives_up_after_retry(fake):
    client = fake(["garbage one", "garbage two"])
    with pytest.raises(ValueError):
        _call_claude("m", "skill", {"p": 1})
    assert len(client.messages.calls) == 2            # initial + one retry, then stop

def test_no_retry_when_first_reply_is_good(fake):
    client = fake([json.dumps({"ok": True})])
    assert _call_claude("m", "skill", {"p": 1}) == {"ok": True}
    assert len(client.messages.calls) == 1


# ----------------------------- prompt caching --------------------------------
def test_system_prompt_is_cache_controlled(fake):
    client = fake([json.dumps({"ok": True})])
    _call_claude("m", "SKILLTEXT", {"p": 1})
    system = client.messages.calls[0]["system"]
    assert system[0]["text"] == "SKILLTEXT"
    assert system[0]["cache_control"] == {"type": "ephemeral"}


# --------------------------- validation drives retry -------------------------
def test_validation_failure_triggers_retry(fake):
    bad = json.dumps({"factor_scores": {}, "deal_breakers": {}})   # parses, wrong shape
    client = fake([bad, json.dumps(VALID_MATCH)])
    result = score_job({"job_title": "SVP Sales"}, {"deal_breakers": []})
    assert result["overall_assessment"] == "You should apply."
    assert len(client.messages.calls) == 2

def test_score_job_parses_fenced_valid_output(fake):
    fake(["```json\n" + json.dumps(VALID_MATCH) + "\n```"])
    result = score_job({"job_title": "SVP Sales"}, {"deal_breakers": []})
    assert result["factor_scores"]["seniority"]["score"] == 90
