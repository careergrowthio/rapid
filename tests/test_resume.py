"""Task 5 - pure resume field-mapping, Docs replace-requests, and Drive-id parsing.
(The live Drive/Docs calls in render_resume/refresh_base_resume need Google creds +
network and are verified separately.)"""
import pytest
from rapid_agent import build_resume_fields, _resume_replace_requests, _drive_id

CLIENT = {"name": "Jane Doe", "email": "jane@example.com", "phone": "555-1212",
          "linkedin_url": "https://linkedin.com/in/janedoe", "location": "Mesa, AZ"}

TAILORED = {
    "headline": "SVP, National Accounts",
    "summary": "Senior CPG sales executive.",
    "skills": ["Sales", "P&L", "Negotiation"],
    "experience": [
        {"company": "Acme Foods", "title": "VP Sales", "location": "Remote",
         "start": "2018", "end": "Present", "bullets": ["Grew revenue 30%", "Led 12 reps"]},
        {"company": "Beta Corp", "title": "Director", "location": "Chicago",
         "start": "2012", "end": "2018", "bullets": ["x", "y", "z", "w"]},
    ],
    "education": [{"institution": "State U", "credential": "BBA", "year": 2008}],
    "certifications": [],
}


# ----------------------------- build_resume_fields ---------------------------
def test_maps_identity_and_content():
    f = build_resume_fields(TAILORED, CLIENT)
    assert f["FULL_NAME"] == "Jane Doe"
    assert f["EMAIL"] == "jane@example.com"
    assert f["LINKEDIN"].endswith("janedoe")
    assert f["HEADLINE"] == "SVP, National Accounts"
    assert f["SUMMARY"].startswith("Senior CPG")

def test_skills_joined():
    f = build_resume_fields(TAILORED, CLIENT)
    assert f["SKILLS"] == "Sales • P&L • Negotiation"

def test_first_experience_block_and_bullet_padding():
    f = build_resume_fields(TAILORED, CLIENT)
    assert f["COMPANY"] == "Acme Foods" and f["JOB_TITLE"] == "VP Sales"
    assert f["START"] == "2018" and f["END"] == "Present"
    assert f["BULLET_1"] == "Grew revenue 30%"
    assert f["BULLET_2"] == "Led 12 reps"
    assert f["BULLET_3"] == ""                    # only 2 bullets -> third padded empty

def test_education_mapped():
    f = build_resume_fields(TAILORED, CLIENT)
    assert f["DEGREE"] == "BBA"
    assert f["INSTITUTION"] == "State U"
    assert f["GRAD_YEAR"] == "2008"               # coerced to str

def test_empty_certifications_blank():
    f = build_resume_fields(TAILORED, CLIENT)
    assert f["CERTIFICATIONS"] == ""

def test_missing_sections_render_empty_not_crash():
    f = build_resume_fields({}, {})
    for k in ("FULL_NAME", "SUMMARY", "JOB_TITLE", "BULLET_1", "DEGREE", "SKILLS"):
        assert f[k] == ""

def test_all_template_placeholders_present():
    expected = {"FULL_NAME","HEADLINE","EMAIL","PHONE","LOCATION","LINKEDIN","SUMMARY",
                "SKILLS","JOB_TITLE","COMPANY","JOB_LOCATION","START","END",
                "BULLET_1","BULLET_2","BULLET_3","DEGREE","INSTITUTION","GRAD_YEAR",
                "CERTIFICATIONS"}
    assert set(build_resume_fields(TAILORED, CLIENT)) == expected


# --------------------------- _resume_replace_requests ------------------------
def test_replace_requests_wrap_placeholders():
    reqs = _resume_replace_requests({"FULL_NAME": "Jane Doe"})
    r = reqs[0]["replaceAllText"]
    assert r["containsText"]["text"] == "{{FULL_NAME}}"
    assert r["containsText"]["matchCase"] is True
    assert r["replaceText"] == "Jane Doe"

def test_replace_requests_one_per_field():
    fields = build_resume_fields(TAILORED, CLIENT)
    assert len(_resume_replace_requests(fields)) == len(fields)


# -------------------------------- _drive_id ----------------------------------
@pytest.mark.parametrize("inp, expected", [
    ("https://docs.google.com/document/d/1AbC_def-123/edit", "1AbC_def-123"),
    ("https://drive.google.com/file/d/1AbC_def-123/view?usp=sharing", "1AbC_def-123"),
    ("1AbC_def-123", "1AbC_def-123"),
    ("  1AbC_def-123  ", "1AbC_def-123"),
    (None, ""),
])
def test_drive_id(inp, expected):
    assert _drive_id(inp) == expected
