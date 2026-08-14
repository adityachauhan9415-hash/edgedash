"""
edgedash.agents.base
====================
Defines the Agent protocol and the AgentResult dataclass that every
pipeline agent must conform to.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from edgedash.config import Config
    from edgedash.storage import Storage


# ---------------------------------------------------------------------------
# Result contract
# ---------------------------------------------------------------------------

Status = Literal["ok", "failed"]


@dataclass
class AgentResult:
    """Returned by every agent after a run."""

    agent: str
    """Name of the agent that produced this result."""

    status: Status
    """'ok' if the agent completed its goal; 'failed' otherwise."""

    records_touched: int = 0
    """Number of records created or updated in Storage during this run."""

    notes: str = ""
    """Free-form human-readable summary — shown in the cycle log."""

    errors: list[str] = field(default_factory=list)
    """Any non-fatal errors encountered (fatal errors should set status='failed')."""


# ---------------------------------------------------------------------------
# Agent contract
# ---------------------------------------------------------------------------

class Agent(abc.ABC):
    """
    Abstract base class for all EdgeDash pipeline agents.

    Rules (enforced by the architecture):
    - One goal, one stop condition.
    - Reads config for its own settings; writes only through `storage`.
    - Never imports from another sub-agent module.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Stable identifier used in logs and the cycle registry."""
        ...

    @abc.abstractmethod
    def run(self, config: "Config", storage: "Storage") -> AgentResult:
        """
        Execute the agent's single goal.

        Parameters
        ----------
        config:
            Project-wide configuration object.
        storage:
            The shared storage layer. Agents *only* interact with
            Storage; they never call each other or the Orchestrator.

        Returns
        -------
        AgentResult
            Outcome of the run, including records touched and notes.
        """
        ...
