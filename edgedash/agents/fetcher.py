"""
edgedash.agents.fetcher
=======================
The real Fetcher agent.  Iterates over every enabled source in
config.sources, calls fetch(config) on each, stamps a stable listing ID
onto every row, then bulk-upserts into Storage.

Architecture rules
------------------
- The Fetcher never contains source-specific logic.  All source knowledge
  lives in edgedash/sources/*.
- Per-source failures are caught here (steering rule 12): the failed source
  is logged to cycle_log with status "failed" and the cycle continues.
- The stable listing ID is computed via storage.make_listing_id — the single
  authoritative implementation.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from edgedash.agents.base import Agent, AgentResult
from edgedash.sources.base import SOURCES, SourceError, SourceSkipped
from edgedash.storage import make_listing_id

if TYPE_CHECKING:
    from edgedash.config import Config
    from edgedash.storage import Storage


class Fetcher(Agent):
    """
    Fetches live job listings from all configured sources.

    Goal       : Populate storage with fresh listings from every enabled source.
    Stop cond  : All sources in config.sources have been queried (or failed).
    """

    @property
    def name(self) -> str:
        return "Fetcher"

    def run(self, config: "Config", storage: "Storage") -> AgentResult:
        enabled_sources: list[str] = list(config.sources or ["arbeitnow"])

        source_summaries: list[str] = []
        all_rows: list[dict] = []
        per_source_errors: list[str] = []

        for source_name in enabled_sources:
            # ── Resolve source from registry ──────────────────────────
            source_cls = SOURCES.get(source_name)
            if source_cls is None:
                msg = f"{source_name}: NOT FOUND in SOURCES registry — skipping."
                print(f"  [Fetcher] WARNING: {msg}")
                source_summaries.append(f"{source_name}: SKIPPED (not registered)")
                per_source_errors.append(msg)
                continue

            source = source_cls()

            # ── Fetch — catch per-source failures (steering rule 12) ──
            try:
                rows = source.fetch(config)
            except SourceSkipped as exc:
                msg = f"{source_name}: SKIPPED — {exc}"
                print(f"  [Fetcher] {msg}")
                source_summaries.append(f"{source_name}: SKIPPED ({exc})")
                storage.log_agent_run(
                    cycle_id=_current_cycle_id(),
                    agent=f"Fetcher/{source_name}",
                    status="skipped",
                    records_touched=0,
                    notes=msg,
                )
                continue
            except (SourceError, Exception) as exc:  # noqa: BLE001
                msg = f"{source_name}: FAILED — {exc}"
                print(f"  [Fetcher] WARNING: {msg}")
                source_summaries.append(
                    f"{source_name}: FAILED ({type(exc).__name__})"
                )
                per_source_errors.append(str(exc))
                storage.log_agent_run(
                    cycle_id=_current_cycle_id(),
                    agent=f"Fetcher/{source_name}",
                    status="failed",
                    records_touched=0,
                    notes=msg,
                    errors=[str(exc)],
                )
                continue

            # ── Stamp stable IDs ───────────────────────────────────────
            stamped: list[dict] = []
            for row in rows:
                url = row.get("url") or ""
                if not url:
                    continue
                row_with_id = dict(row)
                row_with_id["id"] = make_listing_id(source_name, url)
                stamped.append(row_with_id)

            all_rows.extend(stamped)
            source_summaries.append(f"{source_name}: {len(stamped)} rows fetched")
            print(
                f"  [Fetcher] {source_name}: "
                f"{len(rows)} raw → {len(stamped)} with stable IDs"
            )

        # ── Upsert all rows and tally new ones ────────────────────────
        new_count = 0
        if all_rows:
            new_count = storage.upsert_listings(all_rows)
            source_summaries.append(
                f"total: {len(all_rows)} rows, {new_count} new"
            )

        notes = " | ".join(source_summaries)

        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=new_count,
            notes=notes,
            errors=per_source_errors,
        )


# ---------------------------------------------------------------------------
# Cycle-ID helper
# ---------------------------------------------------------------------------
# The Fetcher logs per-source sub-results to cycle_log.  It doesn't receive
# the top-level cycle_id through Agent.run(), so the Orchestrator calls
# set_cycle_id() before invoking run().

_CURRENT_CYCLE_ID: str = "unknown"


def set_cycle_id(cycle_id: str) -> None:
    """Called by the Orchestrator before running the Fetcher."""
    global _CURRENT_CYCLE_ID
    _CURRENT_CYCLE_ID = cycle_id


def _current_cycle_id() -> str:
    return _CURRENT_CYCLE_ID
