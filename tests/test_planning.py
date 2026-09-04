"""
tests/test_planning.py
======================
Tests for edgedash.state and edgedash.planning modules.
"""
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


# Sample config for testing
def make_config(**overrides):
    from edgedash.config import Config
    defaults = {
        "target_role": "Data Engineer",
        "city": "San Francisco, CA",
        "fetch_agent": "Fetcher",
        "score_agent": "Scorer",
        "gap_agent": "GapAnalyzer",
        "use_mock_fetcher": False,
        "db_path": ":memory:",
        "fetch_interval_hours": 6,
        "scoring_batch_size": 25,
    }
    defaults.update(overrides)
    return Config(**defaults)


# ---------------------------------------------------------------------------
# read_state tests
# ---------------------------------------------------------------------------

class FakeRow:
    """Fake row for DB results."""
    def __init__(self, data):
        self._data = data
    def __getitem__(self, key):
        return self._data[key]


class MockConn:
    """Mock DB connection."""
    def __init__(self, results=None):
        self._results = results or {}
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def execute(self, q):
        return self
    def fetchone(self):
        # Return first result for each unique query pattern
        return self._results.get(id(self), None)
    def fetchall(self):
        return self._results.get(id(self), [])


class MockStorage:
    """Mock storage for testing."""
    def __init__(self, state_data=None, gaps_data=None, cycle_data=None):
        self._state = state_data or {}
        self._gaps = gaps_data or []
        self._cycle = cycle_data or []
        self._unscored_count = 0
        
    def init(self): pass
    
    def get_state(self, key, default=""):
        return self._state.get(key, default)
    
    def count_unscored(self):
        return self._unscored_count
    
    def set_unscored_count(self, n):
        self._unscored_count = n
    
    def read_latest_skill_gaps(self):
        return self._gaps
    
    def _connect(self):
        # Return mock connection with results based on what was queried
        results = {}
        
        # Latest scored_at query
        def make_fetchone(key):
            def inner():
                return self._results.get(key)
            return inner
        
        conn = MagicMock()
        
        # For scored_at query
        conn.execute.return_value.fetchone.return_value = self._results.get("scored_at")
        
        # For cycle_log query  
        conn.execute.return_value.fetchone.return_value = self._cycle[0] if self._cycle else None
        
        return conn


def test_read_state_never_fetched():
    """State when never fetched before."""
    from edgedash.state import read_state
    
    now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
    config = make_config()
    
    # Mock storage that returns empty state
    mock_storage = MagicMock()
    mock_storage.get_state.return_value = ""
    mock_storage.count_unscored.return_value = 5
    mock_storage.read_latest_skill_gaps.return_value = []
    
    # Mock connection for scored_at and cycle_log
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = None
    mock_storage._connect.return_value = mock_conn
    
    with patch("edgedash.state.Storage", return_value=mock_storage):
        state = read_state(config, now)
    
    assert state.last_fetch_at is None
    assert state.hours_since_fetch is None
    assert state.unscored_count == 5
    assert state.gaps_computed_at is None
    assert state.gaps_stale is True  # No snapshot, so stale


def test_read_state_with_fetch_and_scores():
    """State with recent fetch and scores."""
    from edgedash.state import read_state
    
    # 3 hours ago
    now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
    fetch_time = datetime(2024, 1, 15, 9, 0, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    score_time = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    gap_time = datetime(2024, 1, 15, 9, 30, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    config = make_config()
    
    mock_storage = MagicMock()
    mock_storage.get_state.return_value = fetch_time
    mock_storage.count_unscored.return_value = 0
    mock_storage.read_latest_skill_gaps.return_value = [{"computed_at": gap_time}]

    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.fetchone.side_effect = [
        {"max_scored_at": score_time},
        {"status": "ok", "ran_at": "2024-01-15T11:00:00Z"},
    ]
    mock_storage._connect.return_value = mock_conn

    with patch("edgedash.state.Storage", return_value=mock_storage):
        state = read_state(config, now)
    
    assert state.last_fetch_at == fetch_time
    assert state.hours_since_fetch == 3.0
    assert state.unscored_count == 0
    assert state.gaps_computed_at == gap_time
    # Score is newer than gap snapshot, so stale
    assert state.gaps_stale is True


def test_read_state_gaps_up_to_date():
    """State when gaps are up-to-date."""
    from edgedash.state import read_state
    
    now = datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)
    fetch_time = datetime(2024, 1, 15, 9, 0, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Gap computed AFTER latest score
    gap_time = datetime(2024, 1, 15, 11, 0, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    score_time = datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    config = make_config()
    
    mock_storage = MagicMock()
    mock_storage.get_state.return_value = fetch_time
    mock_storage.count_unscored.return_value = 0
    mock_storage.read_latest_skill_gaps.return_value = [{"computed_at": gap_time}]
    
    mock_conn = MagicMock()
    mock_conn.__enter__.return_value = mock_conn
    mock_conn.execute.return_value.fetchone.side_effect = [
    {"max_scored_at": score_time},
    {"status": "ok", "ran_at": "2024-01-15T11:00:00Z"},
    ]
    mock_storage._connect.return_value = mock_conn
    
    with patch("edgedash.state.Storage", return_value=mock_storage):
        state = read_state(config, now)
    
    assert state.gaps_stale is False  # Gap is newer than score


# ---------------------------------------------------------------------------
# build_plan tests
# ---------------------------------------------------------------------------

def test_all_three_run():
    """All three agents should run when conditions are met."""
    from edgedash.planning import build_plan
    from edgedash.state import SystemState
    
    config = make_config(fetch_interval_hours=6)
    
    # Fetch 7 hours ago, 10 unscored, gaps stale
    state = SystemState(
        last_fetch_at="2024-01-15T05:00:00Z",
        hours_since_fetch=7.0,
        unscored_count=10,
        gaps_computed_at="2024-01-15T10:00:00Z",
        gaps_stale=True,
        last_cycle_verdict="ok",
        last_cycle_at="2024-01-15T11:00:00Z",
    )
    
    plan = build_plan(state, config)
    
    # All should run
    run_tasks = plan.tasks_to_run()
    skip_tasks = plan.tasks_to_skip()
    
    assert len(run_tasks) == 3
    assert len(skip_tasks) == 0
    
    assert run_tasks[0].agent_name == "Fetcher"
    assert run_tasks[1].agent_name == "Scorer"
    assert run_tasks[2].agent_name == "GapAnalyzer"


def test_nothing_to_do():
    """Nothing should run when everything is up to date."""
    from edgedash.planning import build_plan
    from edgedash.state import SystemState
    
    config = make_config(fetch_interval_hours=6)
    
    # Fetch 2 hours ago, 0 unscored, gaps NOT stale
    state = SystemState(
        last_fetch_at="2024-01-15T10:00:00Z",
        hours_since_fetch=2.0,
        unscored_count=0,
        gaps_computed_at="2024-01-15T10:30:00Z",
        gaps_stale=False,
        last_cycle_verdict="ok",
        last_cycle_at="2024-01-15T11:00:00Z",
    )
    
    plan = build_plan(state, config)
    
    # All should be skipped
    run_tasks = plan.tasks_to_run()
    skip_tasks = plan.tasks_to_skip()
    
    assert len(run_tasks) == 0
    assert len(skip_tasks) == 3
    
    # Verify reasons
    for task in skip_tasks:
        assert task.status == "skip"


def test_only_unscored():
    """Only scorer should run when fetch is fresh but unscored exist."""
    from edgedash.planning import build_plan
    from edgedash.state import SystemState
    
    config = make_config(fetch_interval_hours=6)
    
    # Fetch 1 hour ago, 5 unscored, gaps NOT stale
    state = SystemState(
        last_fetch_at="2024-01-15T11:00:00Z",
        hours_since_fetch=1.0,
        unscored_count=5,
        gaps_computed_at="2024-01-15T10:30:00Z",
        gaps_stale=False,
        last_cycle_verdict="ok",
        last_cycle_at="2024-01-15T11:00:00Z",
    )
    
    plan = build_plan(state, config)
    
    run_tasks = plan.tasks_to_run()
    skip_tasks = plan.tasks_to_skip()
    
    # Fetcher and GapAnalyzer should skip, Scorer should run
    assert len(run_tasks) == 1
    assert run_tasks[0].agent_name == "Scorer"
    
    assert len(skip_tasks) == 2
    skip_names = {t.agent_name for t in skip_tasks}
    assert "Fetcher" in skip_names
    assert "GapAnalyzer" in skip_names


def test_gaps_stale_but_no_unscored():
    """GapAnalyzer should run even when no unscored, if gaps are stale."""
    from edgedash.planning import build_plan
    from edgedash.state import SystemState
    
    config = make_config(fetch_interval_hours=6)
    
    # Fetch 3 hours ago, 0 unscored, gaps STALE
    state = SystemState(
        last_fetch_at="2024-01-15T09:00:00Z",
        hours_since_fetch=3.0,
        unscored_count=0,
        gaps_computed_at="2024-01-15T08:00:00Z",  # Old gap snapshot
        gaps_stale=True,  # New scores since then
        last_cycle_verdict="ok",
        last_cycle_at="2024-01-15T11:00:00Z",
    )
    
    plan = build_plan(state, config)
    
    run_tasks = plan.tasks_to_run()
    skip_tasks = plan.tasks_to_skip()
    run_names = {t.agent_name for t in run_tasks}
    skip_names = {t.agent_name for t in skip_tasks}
    
    # Fetcher and GapAnalyzer should run, Scorer should skip
    assert len(run_tasks) == 1
    assert "GapAnalyzer" in run_names

    skip_names = {t.agent_name for t in skip_tasks}
    assert "Fetcher" in skip_names
    assert "Scorer" in skip_names


def test_fetch_interval_not_met():
    """Fetcher should skip when fetch interval not met."""
    from edgedash.planning import build_plan
    from edgedash.state import SystemState
    
    config = make_config(fetch_interval_hours=6)
    
    # Fetch 4 hours ago (less than 6h threshold)
    state = SystemState(
        last_fetch_at="2024-01-15T08:00:00Z",
        hours_since_fetch=4.0,
        unscored_count=0,
        gaps_computed_at="2024-01-15T08:30:00Z",
        gaps_stale=False,
        last_cycle_verdict="ok",
        last_cycle_at="2024-01-15T12:00:00Z",
    )
    
    plan = build_plan(state, config)
    
    fetcher_task = plan.tasks[0]
    assert fetcher_task.status == "skip"
    assert "4.0h ago" in fetcher_task.reason


def test_mock_fetcher_used():
    """When use_mock_fetcher is True, use MockFetcher."""
    from edgedash.planning import build_plan
    from edgedash.state import SystemState
    
    config = make_config(use_mock_fetcher=True, fetch_interval_hours=6)
    
    state = SystemState(
        last_fetch_at="2024-01-15T05:00:00Z",
        hours_since_fetch=7.0,
        unscored_count=10,
        gaps_computed_at="2024-01-15T10:00:00Z",
        gaps_stale=True,
        last_cycle_verdict="ok",
        last_cycle_at="2024-01-15T11:00:00Z",
    )
    
    plan = build_plan(state, config)
    
    fetcher_task = plan.tasks[0]
    assert fetcher_task.agent_name == "MockFetcher"


def test_plan_render():
    """Plan.render() produces readable output."""
    from edgedash.planning import build_plan
    from edgedash.state import SystemState
    
    config = make_config(fetch_interval_hours=6)
    
    # Nothing to do - all skipped
    state = SystemState(
        last_fetch_at="2024-01-15T10:00:00Z",
        hours_since_fetch=2.0,
        unscored_count=0,
        gaps_computed_at="2024-01-15T10:30:00Z",
        gaps_stale=False,
        last_cycle_verdict="ok",
        last_cycle_at="2024-01-15T11:00:00Z",
    )
    
    plan = build_plan(state, config)
    rendered = plan.render()
    
    # Check readable format
    assert "Fetcher" in rendered
    assert "Scorer" in rendered
    assert "GapAnalyzer" in rendered
    assert "○" in rendered  # skipped icon
    assert "run" not in rendered.lower() or "skip" in rendered.lower()


def test_stop_conditions_present():
    """Each task has correct stop_conditions."""
    from edgedash.planning import build_plan
    from edgedash.state import SystemState
    
    config = make_config(
        fetch_interval_hours=6,
        scoring_batch_size=25,
    )
    
    state = SystemState(
        last_fetch_at="2024-01-15T05:00:00Z",
        hours_since_fetch=7.0,
        unscored_count=10,
        gaps_computed_at="2024-01-15T10:00:00Z",
        gaps_stale=True,
        last_cycle_verdict="ok",
        last_cycle_at="2024-01-15T11:00:00Z",
    )
    
    plan = build_plan(state, config)
    
    fetcher = plan.tasks[0]
    assert "max_pages" in fetcher.stop_conditions
    assert "max_listings" in fetcher.stop_conditions
    
    scorer = plan.tasks[1]
    assert scorer.stop_conditions["max_items"] == 25
    
    analyzer = plan.tasks[2]
    assert "max_seconds" in analyzer.stop_conditions


# ---------------------------------------------------------------------------
# Integration-style tests with real Storage mock
# ---------------------------------------------------------------------------

def test_build_plan_never_fetched():
    """When never fetched, fetch should run."""
    from edgedash.planning import build_plan
    from edgedash.state import SystemState
    
    config = make_config(fetch_interval_hours=6)
    
    # Never fetched
    state = SystemState(
        last_fetch_at=None,
        hours_since_fetch=None,
        unscored_count=0,
        gaps_computed_at=None,
        gaps_stale=True,
        last_cycle_verdict=None,
        last_cycle_at=None,
    )
    
    plan = build_plan(state, config)
    
    # Fetcher should run (never fetched)
    assert plan.tasks[0].status == "run"
    assert "Never fetched" in plan.tasks[0].reason


def test_no_gaps_snapshot():
    """When no gaps snapshot exists, GapAnalyzer should run."""
    from edgedash.planning import build_plan
    from edgedash.state import SystemState
    
    config = make_config(fetch_interval_hours=6)
    
    state = SystemState(
        last_fetch_at="2024-01-15T10:00:00Z",
        hours_since_fetch=2.0,
        unscored_count=0,
        gaps_computed_at=None,  # No snapshot
        gaps_stale=True,  # Should be True when no snapshot
        last_cycle_verdict="ok",
        last_cycle_at="2024-01-15T11:00:00Z",
    )
    
    plan = build_plan(state, config)
    
    gap_task = plan.tasks[2]
    assert gap_task.status == "run"
    assert "missing" in gap_task.reason.lower()