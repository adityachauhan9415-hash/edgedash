"""
tests/test_gap_analyzer.py
==========================
Tests for edgedash.agents.gap_analyzer._compute_gaps (pure function)
and the storage snapshot append behavior.
No LLM, no network.
"""
from __future__ import annotations

import json
import pytest

from edgedash.agents.gap_analyzer import _compute_gaps


# ---------------------------------------------------------------------------
# Test data helpers
# ---------------------------------------------------------------------------

def _listing(score: int, listing_id: str = "lid-1") -> dict:
    return {"id": listing_id, "score": score, "description": "some text"}


def _facts(required: list = None, nice: list = None) -> dict:
    return {
        "required_skills": required or [],
        "nice_to_have":    nice or [],
    }


# ---------------------------------------------------------------------------
# 1. Basic arithmetic: opportunity_cost and ranking
# ---------------------------------------------------------------------------

def test_opportunity_cost_weighted_by_score():
    """
    A high-scoring listing contributes more than low-scoring ones.
    kubernetes (85 + 40 = 1.25) should rank above spark (40 + 30 = 0.70).
    """
    pairs = [
        (_listing(85, "A"), _facts(required=["kubernetes", "python"])),
        (_listing(40, "B"), _facts(required=["kubernetes", "spark"])),
        (_listing(30, "C"), _facts(required=["spark"])),
    ]
    profile = {"python"}  # user has python, lacks kubernetes and spark
    aliases = {}

    gaps, nice_counts = _compute_gaps(pairs, profile, aliases)

    # Find each skill
    k8s = next(g for g in gaps if g["skill"] == "kubernetes")
    spr = next(g for g in gaps if g["skill"] == "spark")

    assert abs(k8s["opportunity_cost"] - 1.25) < 0.001
    assert abs(spr["opportunity_cost"] - 0.70) < 0.001
    assert k8s["opportunity_cost"] > spr["opportunity_cost"]


def test_ranking_by_opportunity_cost_desc():
    """_compute_gaps returns gaps sorted by opportunity_cost descending."""
    pairs = [
        (_listing(30, "a"), _facts(required=["a"])),
        (_listing(90, "b"), _facts(required=["b"])),
        (_listing(50, "c"), _facts(required=["c"])),
    ]
    # No profile skills → all are gaps
    gaps, _ = _compute_gaps(pairs, set(), {})

    # Order should be b (0.90), c (0.50), a (0.30)
    assert gaps[0]["skill"] == "b"
    assert gaps[1]["skill"] == "c"
    assert gaps[2]["skill"] == "a"


# ---------------------------------------------------------------------------
# 2. Canonicalization via alias map
# ---------------------------------------------------------------------------

def test_aliases_applied():
    """k8s -> kubernetes via alias map, then compared to profile."""
    pairs = [
        (_listing(80, "X"), _facts(required=["k8s"])),
    ]
    # User has kubernetes (canonical form), so k8s should NOT be a gap
    profile = {"kubernetes"}  # canonical already
    aliases = {"k8s": "kubernetes"}

    gaps, _ = _compute_gaps(pairs, profile, aliases)

    assert len(gaps) == 0, "k8s should map to kubernetes, which user has"


def test_canonical_lowercase_and_strip():
    """Input is normalised before lookup."""
    pairs = [
        (_listing(80, "Y"), _facts(required=["  KAFKA  "])),
    ]
    profile = {"kafka"}
    aliases = {}

    gaps, _ = _compute_gaps(pairs, profile, aliases)

    assert len(gaps) == 0  # canonicalised to "kafka" which user has


def test_parenthetical_removed():
    """'spark (pyspark)' -> 'spark' before alias lookup."""
    pairs = [
        (_listing(75, "Z"), _facts(required=["spark (pyspark)"])),
    ]
    profile = {"spark"}
    aliases = {}

    gaps, _ = _compute_gaps(pairs, profile, aliases)

    assert len(gaps) == 0


# ---------------------------------------------------------------------------
# 3. nice_to_have separation
# ---------------------------------------------------------------------------

def test_nice_to_have_separate_counts():
    """nice_to_have should only produce counts, no opportunity_cost."""
    pairs = [
        (_listing(80, "L"), _facts(required=["sql"], nice=["aws", "docker"])),
        (_listing(60, "M"), _facts(required=["sql"], nice=["docker"])),
    ]
    profile = {"sql", "aws"}  # has sql and aws, lacks docker

    gaps, nice_counts = _compute_gaps(pairs, profile, {})

    # sql is in profile → not a gap
    assert len(gaps) == 0

    # docker appears twice in nice_to_have across listings
    assert nice_counts.get("docker", 0) == 2
    # aws appears only in nice_to_have, count = 1
    assert nice_counts.get("aws", 0) == 1


def test_nice_to_have_not_in_gap_ranking():
    """nice_to_have should never appear in required_gaps list."""
    pairs = [
        (_listing(50, "N"), _facts(required=[], nice=["ruby"])),
    ]
    profile = set()
    aliases = {}

    gaps, nice_counts = _compute_gaps(pairs, profile, aliases)

    # No required skills → no gaps
    assert len(gaps) == 0
    # But ruby is in nice_to_have
    assert "ruby" in nice_counts


# ---------------------------------------------------------------------------
# 4. Low confidence
# ---------------------------------------------------------------------------

def test_low_confidence_threshold():
    """listings_blocked < 3 -> low_confidence = True."""
    # 2 listings blocked → low
    pairs = [
        (_listing(80, "p"), _facts(required=["go"])),
        (_listing(70, "q"), _facts(required=["go"])),
    ]
    gaps, _ = _compute_gaps(pairs, set(), {})

    assert len(gaps) == 1
    assert gaps[0]["low_confidence"] is True
    assert gaps[0]["listings_blocked"] == 2


def test_not_low_confidence():
    """3 or more listings blocked -> low_confidence = False."""
    pairs = [
        (_listing(80, "r"), _facts(required=["go"])),
        (_listing(70, "s"), _facts(required=["go"])),
        (_listing(60, "t"), _facts(required=["go"])),
    ]
    gaps, _ = _compute_gaps(pairs, set(), {})

    assert gaps[0]["low_confidence"] is False


# ---------------------------------------------------------------------------
# 5. Example IDs
# ---------------------------------------------------------------------------

def test_example_ids_top_5_by_score():
    """example_ids should be up to 5 listing IDs sorted by score desc."""
    pairs = [
        (_listing(30, "low"),   _facts(required=["go"])),
        (_listing(90, "high1"), _facts(required=["go"])),
        (_listing(80, "high2"), _facts(required=["go"])),
        (_listing(70, "high3"), _facts(required=["go"])),
        (_listing(60, "high4"), _facts(required=["go"])),
        (_listing(50, "high5"), _facts(required=["go"])),
        (_listing(40, "high6"), _facts(required=["go"])),
    ]
    gaps, _ = _compute_gaps(pairs, set(), {})

    example_ids = gaps[0]["example_ids"]
    assert len(example_ids) == 5
    # Must be ordered by score descending
    assert example_ids[0] == "high1"  # 90
    assert example_ids[1] == "high2"  # 80
    assert example_ids[2] == "high3"  # 70
    # The order beyond top 5 doesn't matter (they're trimmed)


# ---------------------------------------------------------------------------
# 6. Snapshot append behavior (storage layer)
# ---------------------------------------------------------------------------

def test_snapshot_never_overwrites(tmp_path):
    """Appends to skill_gaps without clearing previous rows."""
    from edgedash.storage import Storage

    db = tmp_path / "gaps.db"
    storage = Storage(db_path=str(db))
    storage.init()

    run1 = "run-111"
    run2 = "run-222"

    # First snapshot
    gaps1 = [
        {"skill": "python", "listings_blocked": 5,
         "opportunity_cost": 3.2, "mean_score": 64.0,
         "top_score": 85, "example_ids": ["x"], "low_confidence": False},
    ]
    storage.append_skill_gap_snapshot(run1, gaps1)

    # Second snapshot
    gaps2 = [
        {"skill": "scala", "listings_blocked": 2,
         "opportunity_cost": 0.9, "mean_score": 45.0,
         "top_score": 50, "example_ids": ["y"], "low_confidence": True},
    ]
    storage.append_skill_gap_snapshot(run2, gaps2)

    # Read latest -> should be run2 only
    latest = storage.read_latest_skill_gaps()
    assert len(latest) == 1
    assert latest[0]["skill"] == "scala"

    # Verify both rows exist in DB (we can query directly)
    with storage._connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM skill_gaps").fetchone()[0]
    assert total == 2, "Previous snapshot should not be overwritten"


def test_read_latest_skill_gaps_empty(tmp_path):
    """read_latest_skill_gaps returns [] when table is empty."""
    from edgedash.storage import Storage

    db = tmp_path / "empty.db"
    storage = Storage(db_path=str(db))
    storage.init()

    result = storage.read_latest_skill_gaps()
    assert result == []
# ---------------------------------------------------------------------------
# 7. Profile file loading (C4-P3 fix)
# ---------------------------------------------------------------------------

def test_profile_skills_excluded_from_gaps(tmp_path):
    """Skills in profile.yaml must NOT appear as gaps."""
    # Create a temporary profile.yaml
    profile_file = tmp_path / "my_profile.yaml"
    profile_file.write_text(
        "role: Data Engineer\n"
        "skills:\n"
        "  - Python\n"
        "  - SQL\n"
        "  - Pandas\n",
        encoding="utf-8"
    )

    # Import directly — no monkeypatch recursion
    from edgedash.agents.gap_analyzer import _load_profile_skills, _compute_gaps, canonical

    # Load from the temp profile (Python, SQL, Pandas)
    raw_skills = _load_profile_skills(str(profile_file))
    assert raw_skills == ["Python", "SQL", "Pandas"]

    # Canonicalize using empty alias map (same as GapAnalyzer does)
    aliases = {}
    profile_canonical = {canonical(s, aliases) for s in raw_skills}
    assert "python" in profile_canonical
    assert "sql" in profile_canonical
    assert "pandas" in profile_canonical
    assert "kafka" not in profile_canonical

    # Listings requiring Python and SQL (skills user HAS) and Kafka (skill user lacks)
    pairs = [
        (_listing(80, "A"), {"required_skills": ["python", "sql"], "nice_to_have": []}),
        (_listing(70, "B"), {"required_skills": ["pandas", "kafka"], "nice_to_have": []}),
    ]

    # Compute gaps
    gaps, _ = _compute_gaps(pairs, profile_canonical, aliases)

    # Should have ONLY kafka as a gap (python, sql, pandas are in profile)
    skills_gapped = {g["skill"] for g in gaps}
    assert "python" not in skills_gapped, "Python is in profile, should not be a gap"
    assert "sql" not in skills_gapped, "SQL is in profile, should not be a gap"
    assert "pandas" not in skills_gapped, "Pandas is in profile, should not be a gap"
    assert "kafka" in skills_gapped, "Kafka is not in profile, must be a gap"