"""
edgedash.state
==============
Deterministic system state inspection.

Reads current pipeline state from Storage, returning a SystemState dataclass
containing the values needed by the planning module to decide which agents run.

Rules:
- `now` must be passed in; do NOT call datetime.now() inside.
- DB access only through Storage.
- Use cheap count/max queries only; no full table loads.
- No LLM/network.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from edgedash.config import Config
from edgedash.storage import Storage


@dataclass
class SystemState:
    """Current pipeline state needed for planning decisions."""
    last_fetch_at: str | None  # ISO timestamp string or None if never
    hours_since_fetch: float | None  # None if never fetched
    unscored_count: int
    gaps_computed_at: str | None  # ISO timestamp or None if no snapshots
    gaps_stale: bool  # True if any score is newer than latest gap snapshot
    last_cycle_verdict: str | None  # "ok", "partial", or None
    last_cycle_at: str | None  # ISO timestamp or None if no cycles


def _parse_iso(ts: str | None) -> datetime | None:
    """Parse ISO timestamp string to datetime, or return None."""
    if ts is None or ts == "":
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _hours_between(start: datetime, end: datetime) -> float:
    """Compute hours between two datetimes (both-aware)."""
    delta = end - start
    return delta.total_seconds() / 3600.0


def read_state(config: Config, now: datetime) -> SystemState:
    """
    Read current system state from Storage.

    Parameters
    ----------
    config:
        Config instance (provides db_path).
    now:
        Current datetime (timezone-aware). Must be passed in by caller.

    Returns
    -------
    SystemState
        Dataclass with all values needed for planning decisions.
    """
    storage = Storage(db_path=config.db_path)

    # last_fetch_at from state table
    last_fetch_str = storage.get_state("last_fetch_time", default="")
    last_fetch_at = _parse_iso(last_fetch_str) if last_fetch_str else None

    # hours_since_fetch
    if last_fetch_at is None:
        hours_since_fetch = None
    else:
        hours_since_fetch = _hours_between(last_fetch_at, now)

    # unscored_count
    unscored_count = storage.count_unscored()

    # gaps_computed_at - latest snapshot timestamp
    latest_gaps = storage.read_latest_skill_gaps()
    if latest_gaps:
        # read_latest_skill_gaps returns rows with computed_at field
        gaps_computed_at = latest_gaps[0].get("computed_at")
    else:
        gaps_computed_at = None

    # gaps_stale: check if any listing was scored after the latest gap snapshot
    # Get max scored_at from listings
    with storage._connect() as conn:
        latest_score_row = conn.execute(
            "SELECT MAX(scored_at) AS max_scored_at FROM listings WHERE scored_at IS NOT NULL"
        ).fetchone()
        latest_scored_at = latest_score_row["max_scored_at"] if latest_score_row else None

    if latest_scored_at and gaps_computed_at:
        latest_scored_dt = _parse_iso(latest_scored_at)
        gaps_computed_dt = _parse_iso(gaps_computed_at)
        gaps_stale = latest_scored_dt is not None and gaps_computed_dt is not None and latest_scored_dt > gaps_computed_dt
    else:
        # If either is missing, gaps are stale (or we can't determine)
        gaps_stale = latest_scored_at is not None and gaps_computed_at is None

    # last_cycle_verdict and last_cycle_at from most recent cycle_log
    with storage._connect() as conn:
        cycle_row = conn.execute(
            "SELECT status, ran_at FROM cycle_log ORDER BY ran_at DESC LIMIT 1"
        ).fetchone()
        if cycle_row:
            last_cycle_verdict = cycle_row["status"]  # "ok", "partial", "failed"
            last_cycle_at = cycle_row["ran_at"]
        else:
            last_cycle_verdict = None
            last_cycle_at = None

    return SystemState(
        last_fetch_at=last_fetch_str or None,
        hours_since_fetch=hours_since_fetch,
        unscored_count=unscored_count,
        gaps_computed_at=gaps_computed_at,
        gaps_stale=gaps_stale,
        last_cycle_verdict=last_cycle_verdict,
        last_cycle_at=last_cycle_at,
    )