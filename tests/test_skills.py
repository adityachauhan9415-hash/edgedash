"""
tests/test_skills.py
====================
Tests for edgedash.skills canonicalisation and alias suggestion.
"""
import pytest


def test_canonical_lowercase():
    from edgedash.skills import canonical
    result = canonical("PYTHON", {})
    assert result == "python"


def test_canonical_strips_whitespace():
    from edgedash.skills import canonical
    result = canonical("  python  ", {})
    assert result == "python"


def test_canonical_removes_parentheses():
    from edgedash.skills import canonical
    result = canonical("kubernetes (eks)", {})
    assert result == "kubernetes"


def test_canonical_applies_alias():
    from edgedash.skills import canonical
    result = canonical("k8s", {"k8s": "kubernetes"})
    assert result == "kubernetes"


def test_canonical_no_alias():
    from edgedash.skills import canonical
    result = canonical("python", {"k8s": "kubernetes"})
    assert result == "python"


def test_canonical_empty_string():
    from edgedash.skills import canonical
    result = canonical("", {})
    assert result == ""


# ---------------------------------------------------------------------------
# Alias suggestion tests (C4-P5) - mocks to avoid real API calls
# ---------------------------------------------------------------------------

class FakeRow:
    """Fake row for extraction_cache results."""
    def __init__(self, data):
        self._data = data
    def __getitem__(self, key):
        return self._data


# Sample row used by most tests (python, docker required; sql nice-to-have)
SAMPLE_ROWS = [
    FakeRow('{"required_skills": ["python", "docker"], "nice_to_have": ["sql"]}')
]


class MockConn:
    """Returns sample extraction_cache data."""
    def __init__(self, rows=None):
        self._rows = rows or []
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def execute(self, q):
        return self
    def fetchall(self):
        return self._rows


class MockStorage:
    """Simple mock storage class."""
    def __init__(self, rows=None):
        self._rows = rows or []
    def init(self): pass
    def _connect(self):
        return MockConn(self._rows)


def make_mock_storage(rows=None):
    """Create a mock storage object."""
    return MockStorage(rows)


def test_suggest_aliases_one_llm_call(tmp_path, monkeypatch):
    """--suggest-aliases makes exactly one llm.complete_json call."""
    import edgedash.skills as skills_mod
    from unittest.mock import MagicMock

    call_count = 0

    def mock_complete_json(prompt, schema, *, max_retries=1):
        nonlocal call_count
        call_count += 1
        return []

    monkeypatch.setattr("edgedash.llm.complete_json", mock_complete_json)
    monkeypatch.setattr("edgedash.storage.Storage", lambda db_path: make_mock_storage(SAMPLE_ROWS))
    monkeypatch.setattr("edgedash.config.Config.from_env", lambda: MagicMock(
        skill_aliases={"k8s": "kubernetes"},
        db_path=":memory:",
    ))

    skills_mod._suggest_aliases()
    assert call_count == 1, f"Expected 1 LLM call, got {call_count}"


def test_suggest_aliases_excludes_existing(tmp_path, monkeypatch):
    """Skills already in alias map are excluded from LLM prompt."""
    import edgedash.skills as skills_mod
    from unittest.mock import MagicMock

    # Use row with k8s to verify it's excluded
    rows = [FakeRow('{"required_skills": ["python", "k8s"], "nice_to_have": ["sql"]}')]

    captured_prompt = []
    def mock_complete_json(prompt, schema, *, max_retries=1):
        captured_prompt.append(prompt)
        return []

    monkeypatch.setattr("edgedash.llm.complete_json", mock_complete_json)
    monkeypatch.setattr("edgedash.storage.Storage", lambda db_path: make_mock_storage(rows))
    monkeypatch.setattr("edgedash.config.Config.from_env", lambda: MagicMock(
        skill_aliases={"k8s": "kubernetes"},
        db_path=":memory:",
    ))

    skills_mod._suggest_aliases()
    assert len(captured_prompt) == 1
    assert "k8s" not in captured_prompt[0]


def test_suggest_aliases_warning_printed(tmp_path, monkeypatch, capsys):
    """Warning is printed before suggestions."""
    import edgedash.skills as skills_mod
    from unittest.mock import MagicMock

    monkeypatch.setattr("edgedash.llm.complete_json", lambda p, s, **k: [
        {"canonical": "python", "variants": ["py"], "confidence": "high"}
    ])
    monkeypatch.setattr("edgedash.storage.Storage", lambda db_path: make_mock_storage(SAMPLE_ROWS))
    monkeypatch.setattr("edgedash.config.Config.from_env", lambda: MagicMock(
        skill_aliases={},
        db_path=":memory:",
    ))

    skills_mod._suggest_aliases()
    captured = capsys.readouterr().out
    assert "WARNING" in captured
    assert "human review" in captured


def test_suggest_aliases_yaml_output(tmp_path, monkeypatch, capsys):
    """Proposals printed as ready-to-paste YAML."""
    import edgedash.skills as skills_mod
    from unittest.mock import MagicMock

    proposals = [
        {"canonical": "python", "variants": ["py"], "confidence": "high"},
        {"canonical": "sql", "variants": ["mysql"], "confidence": "low"},
    ]

    monkeypatch.setattr("edgedash.llm.complete_json", lambda p, s, **k: proposals)
    monkeypatch.setattr("edgedash.storage.Storage", lambda db_path: make_mock_storage(SAMPLE_ROWS))
    monkeypatch.setattr("edgedash.config.Config.from_env", lambda: MagicMock(
        skill_aliases={},
        db_path=":memory:",
    ))

    skills_mod._suggest_aliases()
    captured = capsys.readouterr().out
    assert "skill_aliases:" in captured
    assert "python: python" in captured
    assert "py: python" in captured


def test_suggest_aliases_conflict_flagging(tmp_path, monkeypatch, capsys):
    """Conflicts with existing aliases are flagged loudly."""
    import edgedash.skills as skills_mod
    from unittest.mock import MagicMock

    proposals = [
        {"canonical": "python", "variants": ["py"], "confidence": "high"},
    ]

    monkeypatch.setattr("edgedash.llm.complete_json", lambda p, s, **k: proposals)
    monkeypatch.setattr("edgedash.storage.Storage", lambda db_path: make_mock_storage(SAMPLE_ROWS))
    monkeypatch.setattr("edgedash.config.Config.from_env", lambda: MagicMock(
        skill_aliases={"python": "python"},
        db_path=":memory:",
    ))

    skills_mod._suggest_aliases()
    captured = capsys.readouterr().out
    assert "CONFLICT" in captured
    assert "already a key in aliases" in captured


def test_suggest_aliases_confidence_handling(tmp_path, monkeypatch, capsys):
    """Confidence levels are printed as comments."""
    import edgedash.skills as skills_mod
    from unittest.mock import MagicMock

    proposals = [
        {"canonical": "python", "variants": ["py"], "confidence": "high"},
        {"canonical": "sql", "variants": ["mysql"], "confidence": "low"},
    ]

    monkeypatch.setattr("edgedash.llm.complete_json", lambda p, s, **k: proposals)
    monkeypatch.setattr("edgedash.storage.Storage", lambda db_path: make_mock_storage(SAMPLE_ROWS))
    monkeypatch.setattr("edgedash.config.Config.from_env", lambda: MagicMock(
        skill_aliases={},
        db_path=":memory:",
    ))

    skills_mod._suggest_aliases()
    captured = capsys.readouterr().out
    assert "confidence: high" in captured
    assert "confidence: low" in captured
    assert "review carefully" in captured
