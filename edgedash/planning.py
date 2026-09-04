"""
edgedash.planning
=================
Deterministic planning module.

Given current system state and config, decides which agents should run.
Pure function with no I/O.

Rules:
- build_plan is a pure function; no I/O inside.
- Skipped agents still appear in the Plan with a reason.
- Each Task includes: agent_name, goal, stop_conditions, reason, status.
"""
from dataclasses import dataclass, field
from typing import Any

from edgedash.config import Config
from edgedash.state import SystemState


@dataclass
class Task:
    """A single task in the execution plan."""
    agent_name: str
    goal: str
    stop_conditions: dict[str, Any]
    reason: str
    status: str  # "run" or "skip"

    def render_line(self) -> str:
        """Render one readable line for this task."""
        status_icon = "▶" if self.status == "run" else "○"
        limits = ", ".join(f"{k}={v}" for k, v in self.stop_conditions.items())
        return f"  {status_icon} {self.agent_name}: {self.goal} [{limits}] — {self.reason}"


@dataclass
class Plan:
    """A plan containing ordered tasks to execute."""
    tasks: list[Task] = field(default_factory=list)

    def render(self) -> str:
        """Render the entire plan as readable lines."""
        if not self.tasks:
            return "  (empty plan)"
        return "\n".join(task.render_line() for task in self.tasks)

    def tasks_to_run(self) -> list[Task]:
        """Return only tasks that have status='run'."""
        return [t for t in self.tasks if t.status == "run"]

    def tasks_to_skip(self) -> list[Task]:
        """Return only tasks that have status='skip'."""
        return [t for t in self.tasks if t.status == "skip"]


def build_plan(state: SystemState, config: Config) -> Plan:
    """
    Build an execution plan based on current state and config.

    This is a PURE FUNCTION — no I/O, no side effects.

    Parameters
    ----------
    state:
        Current SystemState from read_state().
    config:
        Config instance with agent names and limits.

    Returns
    -------
    Plan
        Ordered list of Tasks, each with run/skip decision and reason.
    """
    tasks: list[Task] = []

    # Determine fetch_agent name (considering mock flag)
    fetch_agent_name = "MockFetcher" if config.use_mock_fetcher else config.fetch_agent

    # --- FETCHER ---
    fetch_needed = (
        state.hours_since_fetch is None or
        state.hours_since_fetch >= config.fetch_interval_hours
    )
    if fetch_needed:
        tasks.append(Task(
            agent_name=fetch_agent_name,
            goal="Fetch latest job listings from configured sources",
            stop_conditions={
                "max_pages": getattr(config, "max_pages", 10),
                "max_listings": getattr(config, "max_listings", 100),
            },
            reason=f"Last fetch was {state.hours_since_fetch:.1f}h ago "
                   f"(threshold: {config.fetch_interval_hours}h)"
                   if state.hours_since_fetch is not None
                   else "Never fetched before",
            status="run",
        ))
    else:
        tasks.append(Task(
            agent_name=fetch_agent_name,
            goal="Fetch latest job listings from configured sources",
            stop_conditions={
                "max_pages": getattr(config, "max_pages", 10),
                "max_listings": getattr(config, "max_listings", 100),
            },
            reason=f"Last fetch was only {state.hours_since_fetch:.1f}h ago "
                   f"(threshold: {config.fetch_interval_hours}h)",
            status="skip",
        ))

    # --- SCORER ---
    if state.unscored_count > 0:
        tasks.append(Task(
            agent_name=config.score_agent,
            goal="Score unscored listings for fit against user profile",
            stop_conditions={
                "max_items": config.scoring_batch_size,
                "max_seconds": getattr(config, "score_max_seconds", 300),
            },
            reason=f"{state.unscored_count} unscored listing(s) need scoring",
            status="run",
        ))
    else:
        tasks.append(Task(
            agent_name=config.score_agent,
            goal="Score unscored listings for fit against user profile",
            stop_conditions={
                "max_items": config.scoring_batch_size,
                "max_seconds": getattr(config, "score_max_seconds", 300),
            },
            reason="No unscored listings — nothing to score",
            status="skip",
        ))

    # --- GAP ANALYZER ---
    # Run if gaps are stale (new scores since last snapshot) or no snapshot exists
    analyse_needed = state.gaps_stale or state.gaps_computed_at is None
    if analyse_needed:
        tasks.append(Task(
            agent_name=config.gap_agent,
            goal="Analyze skill gaps between user profile and scored listings",
            stop_conditions={
                "max_seconds": getattr(config, "gap_max_seconds", 120),
            },
            reason="Gap snapshot is stale or missing"
                   if state.gaps_computed_at is None
                   else f"New scores since last snapshot ({state.gaps_computed_at})",
            status="run",
        ))
    else:
        tasks.append(Task(
            agent_name=config.gap_agent,
            goal="Analyze skill gaps between user profile and scored listings",
            stop_conditions={
                "max_seconds": getattr(config, "gap_max_seconds", 120),
            },
            reason=f"Gap snapshot up-to-date ({state.gaps_computed_at})",
            status="skip",
        ))

    return Plan(tasks=tasks)