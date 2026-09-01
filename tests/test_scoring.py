"""
tests/test_scoring.py
=====================
Pytest tests for edgedash.scoring.  NO network, NO LLM, NO API key needed.
All six required cases are covered.
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from edgedash.scoring import score_listing, build_reason, _skill_match, _seniority_fit


# ---------------------------------------------------------------------------
# Minimal Config stub — no env required
# ---------------------------------------------------------------------------

@dataclass
class _Config:
    skills: list = field(default_factory=list)
    target_seniority: str = "senior"
    city: str = "San Francisco, CA"
    score_weight_skill_match: float = 0.45
    score_weight_seniority_fit: float = 0.25
    score_weight_location_fit: float = 0.15
    score_weight_recency: float = 0.15


def _cfg(**kwargs) -> _Config:
    return _Config(**kwargs)


# ---------------------------------------------------------------------------
# Helper: build minimal listing / facts dicts
# ---------------------------------------------------------------------------

def _listing(posted_at: str | None = "2026-08-01T00:00:00Z",
             location: str | None = "San Francisco, CA") -> dict:
    return {"id": "test-001", "posted_at": posted_at, "location": location}


def _facts(
    required_skills: list | None = None,
    nice_to_have: list | None = None,
    seniority: str = "senior",
    remote_ok: bool | None = None,
    years_required: int | None = None,
) -> dict:
    return {
        "required_skills": required_skills or [],
        "nice_to_have":    nice_to_have or [],
        "seniority":       seniority,
        "remote_ok":       remote_ok,
        "years_required":  years_required,
    }


# ---------------------------------------------------------------------------
# Case 1: Perfect match
# ---------------------------------------------------------------------------

def test_perfect_match():
    """All required skills present, exact seniority, city match, recent."""
    cfg = _cfg(
        skills=["python", "sql", "spark"],
        target_seniority="senior",
        city="San Francisco, CA",
    )
    listing = _listing(posted_at="2026-08-15T00:00:00Z",
                       location="San Francisco, CA")
    facts = _facts(
        required_skills=["python", "sql", "spark"],
        nice_to_have=["airflow"],
        seniority="senior",
        remote_ok=None,
    )
    result = score_listing(listing, facts, cfg)

    assert isinstance(result["score"], int)
    assert 0 <= result["score"] <= 100
    # With all required skills matched, exact seniority, city match, recent:
    # expect a high score
    assert result["score"] >= 75, f"Expected high score, got {result['score']}"
    assert "score" in result
    assert "reason" in result
    assert "components" in result
    # reason must be code-generated, not empty
    assert len(result["reason"]) > 10
    # components must not include a 'score' key at top level (rule 16)
    assert "score" not in result["components"]


# ---------------------------------------------------------------------------
# Case 2: Zero match
# ---------------------------------------------------------------------------

def test_zero_match():
    """No profile skills match any required skills."""
    cfg = _cfg(
        skills=["java", "kotlin"],
        target_seniority="senior",
        city="New York, NY",
    )
    listing = _listing(posted_at="2026-08-15T00:00:00Z",
                       location="London, UK")
    facts = _facts(
        required_skills=["python", "spark", "airflow"],
        seniority="senior",
        remote_ok=False,
    )
    result = score_listing(listing, facts, cfg)

    assert 0 <= result["score"] <= 100
    # skill_match = 0, location mismatch, no remote: should be low
    assert result["score"] <= 50, f"Expected low score for zero match, got {result['score']}"
    assert "gap:" in result["reason"]


# ---------------------------------------------------------------------------
# Case 3: Empty required_skills
# ---------------------------------------------------------------------------

def test_empty_required_skills():
    """
    When required_skills == [], skill_match uses the neutral 0.5 base.
    Must not raise ZeroDivisionError.
    """
    cfg = _cfg(skills=["python", "sql"])
    listing = _listing()
    facts = _facts(required_skills=[], nice_to_have=[], seniority="unknown")

    result = score_listing(listing, facts, cfg)

    assert isinstance(result["score"], int)
    assert 0 <= result["score"] <= 100
    # skill_match component value should be 0.5 (neutral base, no nice-to-have)
    sm = result["components"]["skill_match"]["value"]
    assert abs(sm - 0.5) < 1e-9, f"Expected 0.5 neutral, got {sm}"
    assert "no required skills stated" in result["reason"]


# ---------------------------------------------------------------------------
# Case 4: Null posted_at
# ---------------------------------------------------------------------------

def test_null_posted_at():
    """posted_at = None must return recency component of 0.5, not crash."""
    cfg = _cfg()
    listing = _listing(posted_at=None)
    facts = _facts()

    result = score_listing(listing, facts, cfg)

    assert isinstance(result["score"], int)
    rc_value = result["components"]["recency"]["value"]
    assert abs(rc_value - 0.5) < 1e-9, f"Expected 0.5 for null posted_at, got {rc_value}"
    assert "unknown" in result["reason"]


# ---------------------------------------------------------------------------
# Case 5: Null remote_ok
# ---------------------------------------------------------------------------

def test_null_remote_ok():
    """remote_ok = None with a real non-matching location -> 0.1 (elsewhere)."""
    cfg = _cfg(city="San Francisco, CA")
    listing = _listing(location="Berlin, Germany")
    facts = _facts(remote_ok=None)

    result = score_listing(listing, facts, cfg)

    lf = result["components"]["location_fit"]["value"]
    # Berlin != San Francisco, remote_ok is None -> 0.1
    assert abs(lf - 0.1) < 1e-9, f"Expected 0.1 for non-matching + null remote, got {lf}"


def test_null_remote_ok_empty_location():
    """remote_ok = None with empty location -> neutral 0.5."""
    cfg = _cfg(city="San Francisco, CA")
    listing = _listing(location=None)
    facts = _facts(remote_ok=None)

    result = score_listing(listing, facts, cfg)

    lf = result["components"]["location_fit"]["value"]
    assert abs(lf - 0.5) < 1e-9, f"Expected 0.5 for null location, got {lf}"


# ---------------------------------------------------------------------------
# Case 6: Seniority three bands off (junior vs lead)
# ---------------------------------------------------------------------------

def test_seniority_three_bands_off():
    """junior job listing vs lead target -> seniority_fit = 0.0."""
    cfg = _cfg(target_seniority="lead")
    listing = _listing()
    facts = _facts(seniority="junior")

    result = score_listing(listing, facts, cfg)

    sf = result["components"]["seniority_fit"]["value"]
    assert abs(sf - 0.0) < 1e-9, f"Expected 0.0 for 3-band gap, got {sf}"
    assert "mismatch" in result["reason"]


# ---------------------------------------------------------------------------
# Additional: seniority band distances
# ---------------------------------------------------------------------------

def test_seniority_exact():
    assert abs(_seniority_fit({"seniority": "senior"}, "senior") - 1.0) < 1e-9

def test_seniority_one_away():
    assert abs(_seniority_fit({"seniority": "mid"}, "senior") - 0.6) < 1e-9

def test_seniority_two_away():
    assert abs(_seniority_fit({"seniority": "junior"}, "senior") - 0.25) < 1e-9

def test_seniority_unknown():
    # "unknown" job seniority -> neutral 0.5
    v = _seniority_fit({"seniority": "unknown"}, "senior")
    assert abs(v - 0.5) < 1e-9

def test_skill_match_no_divide_by_zero():
    """_skill_match must not raise even when both lists are empty."""
    v, meta = _skill_match({"required_skills": [], "nice_to_have": []}, [])
    assert isinstance(v, float)
    assert 0.0 <= v <= 1.0

def test_score_is_int_in_range():
    cfg = _cfg(skills=["python"])
    listing = _listing()
    facts = _facts(required_skills=["python"])
    result = score_listing(listing, facts, cfg)
    assert isinstance(result["score"], int)
    assert 0 <= result["score"] <= 100

def test_no_llm_imports_in_scoring():
    """scoring.py must not import edgedash.llm or requests."""
    import ast, pathlib
    src = pathlib.Path("edgedash/scoring.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden = {"edgedash.llm", "requests", "urllib"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
            else:
                mod = ", ".join(alias.name for alias in node.names)
            for f in forbidden:
                assert f not in mod, f"scoring.py must not import {f!r}"
