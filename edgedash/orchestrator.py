"""
edgedash.orchestrator
=====================
The Orchestrator reads pipeline state, decides which agents to run, delegates
to them one by one, logs every result, and prints a readable cycle summary.

Architecture rules enforced here
---------------------------------
- Orchestrator NEVER fetches, scores, or analyses directly.
- It only reads state and calls agent.run().
- The agent registry is the single place to swap a real agent in for a mock.
"""
from __future__ import annotations

import textwrap
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from edgedash.agents.base import Agent, AgentResult
from edgedash.agents.fetcher import Fetcher, set_cycle_id as _set_fetcher_cycle_id
from edgedash.agents.gap_analyzer import GapAnalyzer
from edgedash.agents.mock_fetcher import MockFetcher
from edgedash.agents.scorer import Scorer
from edgedash.config import Config
from edgedash.storage import Storage

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# ANSI colour helpers (degrade gracefully on Windows without ANSI support)
# ---------------------------------------------------------------------------

_RESET  = "\033[0m"
_BOLD   = "\033[1m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_RED    = "\033[31m"
_CYAN   = "\033[36m"
_DIM    = "\033[2m"


def _c(text: str, *codes: str) -> str:
    return "".join(codes) + text + _RESET


def _divider(char: str = "─", width: int = 62) -> str:
    return char * width


# ---------------------------------------------------------------------------
# Placeholder agents
# ---------------------------------------------------------------------------

class _PlaceholderAgent(Agent):
    """
    Registered but not yet implemented.
    Logs a clear 'not implemented' message and skips without error.
    """

    def __init__(self, agent_name: str) -> None:
        self._name = agent_name

    @property
    def name(self) -> str:
        return self._name

    def run(self, config: Config, storage: Storage) -> AgentResult:
        msg = f"{self._name} is not implemented yet — skipping this cycle."
        return AgentResult(
            agent=self._name,
            status="ok",
            records_touched=0,
            notes=msg,
        )


# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------
# To swap in a new agent: replace the value in this dict.
# MockFetcher remains available for offline development; the Orchestrator
# picks it automatically when config.use_mock_fetcher is True.

_AGENT_REGISTRY: dict[str, Agent] = {
    "Fetcher":      Fetcher(),
    "MockFetcher":  MockFetcher(),
    "Scorer":       Scorer(),
    "GapAnalyzer":  GapAnalyzer(),   # ← registered; replaces placeholder
}


def _resolve_agent(name: str) -> Agent:
    agent = _AGENT_REGISTRY.get(name)
    if agent is None:
        raise KeyError(
            f"Agent '{name}' is not registered. "
            f"Available: {list(_AGENT_REGISTRY.keys())}"
        )
    return agent


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def _read_state(storage: Storage) -> dict:
    last_fetch = storage.get_state("last_fetch_time", default="never")
    unscored   = storage.count_unscored()
    # Count all listings directly from the table — reliable even on first run.
    total_stored = len(storage.read_listings(limit=100_000))
    return {
        "last_fetch_time": last_fetch,
        "total_listings":  total_stored,
        "count_unscored":  unscored,
    }


# ---------------------------------------------------------------------------
# Console formatting
# ---------------------------------------------------------------------------

def _print_header(cycle_id: str, started_at: str) -> None:
    print()
    print(_c(_divider("═"), _BOLD, _CYAN))
    print(_c(f"  EdgeDash  ·  Cycle {cycle_id[:8]}  ·  {started_at}", _BOLD, _CYAN))
    print(_c(_divider("═"), _BOLD, _CYAN))


def _print_state(state: dict) -> None:
    print()
    print(_c("  PIPELINE STATE", _BOLD))
    print(_c(_divider(), _DIM))
    print(f"  Last fetch      : {_c(state['last_fetch_time'], _YELLOW)}")
    print(f"  Listings stored : {_c(str(state['total_listings']), _YELLOW)}")
    print(f"  Unscored        : {_c(str(state['count_unscored']), _YELLOW)}")
    print(_c(_divider(), _DIM))


def _print_plan(plan: list[tuple[str, str]]) -> None:
    """plan is a list of (agent_name, reason_string)."""
    print()
    print(_c("  CYCLE PLAN", _BOLD))
    print(_c(_divider(), _DIM))
    for agent_name, reason in plan:
        print(f"  {_c('▶', _GREEN)} {_c(agent_name, _BOLD)}  —  {reason}")
    print(_c(_divider(), _DIM))


def _print_agent_result(result: AgentResult, elapsed_ms: int) -> None:
    icon  = _c("✓", _GREEN) if result.status == "ok" else _c("✗", _RED)
    label = _c(result.agent, _BOLD)
    touch = _c(str(result.records_touched), _CYAN)
    ms    = _c(f"{elapsed_ms} ms", _DIM)
    print(f"  {icon}  {label}  ·  {touch} records touched  ·  {ms}")
    if result.notes:
        lines = textwrap.wrap(result.notes, width=56)
        for line in lines:
            print(_c(f"       {line}", _DIM))
    if result.errors:
        for err in result.errors:
            print(_c(f"       ! {err}", _RED))


def _print_summary(results: list[AgentResult], total_ms: int, state_after: dict) -> None:
    print()
    print(_c("  CYCLE SUMMARY", _BOLD))
    print(_c(_divider(), _DIM))

    ok_count     = sum(1 for r in results if r.status == "ok")
    fail_count   = len(results) - ok_count
    total_touched = sum(r.records_touched for r in results)

    col_w = 18
    print(f"  {'Agent':<{col_w}} {'Status':<8} {'Touched':>7}")
    print(_c(f"  {'-'*col_w} {'--------':<8} {'-------':>7}", _DIM))
    for r in results:
        # Build the plain and coloured status; pad based on the plain length
        # so columns stay aligned regardless of ANSI escape bytes.
        plain_status = "ok" if r.status == "ok" else "FAILED"
        coloured_status = _c(plain_status, _GREEN) if r.status == "ok" else _c(plain_status, _RED)
        # Right-pad the coloured string to match the column width of the plain one
        padding = " " * (8 - len(plain_status))
        print(f"  {r.agent:<{col_w}} {coloured_status}{padding} {r.records_touched:>7}")

    print(_c(_divider(), _DIM))
    print(
        f"  Agents run : {len(results)}  "
        f"({_c(str(ok_count), _GREEN)} ok, {_c(str(fail_count), _RED)} failed)"
    )
    print(f"  Records    : {_c(str(total_touched), _CYAN)} touched this cycle")
    print(f"  Listings   : {_c(str(state_after['total_listings']), _CYAN)} in storage  "
          f"({_c(str(state_after['count_unscored']), _YELLOW)} unscored)")
    print(f"  Wall time  : {_c(str(total_ms)+' ms', _DIM)}")
    print(_c(_divider("═"), _BOLD, _CYAN))
    print()


# ---------------------------------------------------------------------------
# Plan builder
# ---------------------------------------------------------------------------

def _build_plan(config: Config, state: dict) -> list[tuple[str, str]]:
    """
    Decide which agents to run and record a human-readable reason for each.

    Rules:
    - Always run the Fetcher to check for new listings.
    - Run Scorer if there are unscored listings.
    - Run GapAnalyzer after Scorer (placeholder; will check scored count later).
    """
    plan: list[tuple[str, str]] = []

    # Honour the offline-dev flag: EDGEDASH_USE_MOCK=1 swaps in MockFetcher
    # regardless of what fetch_agent says.
    fetch_agent_name = "MockFetcher" if config.use_mock_fetcher else config.fetch_agent

    plan.append((
        fetch_agent_name,
        "Always fetch — listings may have changed since last run."
        if state["last_fetch_time"] == "never"
        else f"Last fetch was {state['last_fetch_time']}; refresh listings.",
    ))

    plan.append((
        config.score_agent,
        f"{state['count_unscored']} unscored listing(s) need scoring."
        if state["count_unscored"] > 0
        else "No unscored listings — Scorer will be a no-op this cycle.",
    ))

    plan.append((
        config.gap_agent,
        "Run gap analysis after scoring (placeholder this cycle).",
    ))

    return plan


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_cycle(config: Config | None = None) -> None:
    """
    Execute one full pipeline cycle.

    Steps
    -----
    a. Initialise the DB.
    b. Read state: last_fetch_time, count_unscored, total_listings.
    c. Print a short plan with the rationale for each decision.
    d. Run the agents decided upon.
    e. Log every agent run to cycle_log.
    f. Print a cycle summary table.
    """
    if config is None:
        config = Config.from_env()

    storage = Storage(db_path=config.db_path)

    # a. Init DB
    storage.init()

    # Generate a unique ID for this cycle
    cycle_id  = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    cycle_start_ns = _monotonic_ns()

    # b. Read state
    state = _read_state(storage)

    # Print header + state
    _print_header(cycle_id, started_at)
    _print_state(state)

    # c. Build and print plan
    plan = _build_plan(config, state)
    _print_plan(plan)

    # d. Run agents
    print(_c("  RUNNING AGENTS", _BOLD))
    print(_c(_divider(), _DIM))

    results: list[AgentResult] = []

    for agent_name, _ in plan:
        agent = _resolve_agent(agent_name)
        t_start = _monotonic_ns()
        try:
            result = agent.run(config, storage)
        except Exception as exc:  # noqa: BLE001
            result = AgentResult(
                agent=agent_name,
                status="failed",
                records_touched=0,
                notes="Unhandled exception — see errors field.",
                errors=[str(exc)],
            )
        elapsed_ms = (_monotonic_ns() - t_start) // 1_000_000

        _print_agent_result(result, elapsed_ms)

        # e. Log to cycle_log
        storage.log_agent_run(
            cycle_id=cycle_id,
            agent=result.agent,
            status=result.status,
            records_touched=result.records_touched,
            notes=result.notes,
            errors=result.errors,
        )

        results.append(result)

    # Update state after cycle
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    storage.set_state("last_fetch_time", now_str)

    # Read final state for summary (total_listings comes from live table count)
    state_after = _read_state(storage)

    total_ms = (_monotonic_ns() - cycle_start_ns) // 1_000_000

    # f. Print summary
    _print_summary(results, total_ms, state_after)


# ---------------------------------------------------------------------------
# Monotonic clock helper (avoids importing time at module level)
# ---------------------------------------------------------------------------

def _monotonic_ns() -> int:
    import time
    return time.monotonic_ns()
