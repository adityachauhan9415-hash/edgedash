"""
edgedash.agents.scorer
======================
Scorer agent — runs deterministic scoring over unscored listings.

Goal:       Score every unscored listing up to config.scoring_batch_size.
Stop cond:  All unscored listings have been processed or batch limit reached.

Architecture rules enforced
---------------------------
- No LLM calls here (rule 16).  scoring.py contains pure arithmetic.
- Per-listing try/except: one failure is logged; remaining listings continue (rule 17).
- Idempotent: SELECT WHERE score IS NULL only (rule 18).
- Batch cap via config.scoring_batch_size (rule 21).
- Score distribution logged with suspect-run detection (rule 20).
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from edgedash.agents.base import Agent, AgentResult
from edgedash.agents.extractor import extract
from edgedash.scoring import score_listing

if TYPE_CHECKING:
    from edgedash.config import Config
    from edgedash.storage import Storage


class Scorer(Agent):
    """
    Scores unscored listings deterministically using extracted facts.

    Goal       : All unscored listings within the batch have a score.
    Stop cond  : Batch exhausted or no more unscored listings remain.
    """

    @property
    def name(self) -> str:
        return "Scorer"

    def run(self, config: "Config", storage: "Storage") -> AgentResult:
        batch_size: int = getattr(config, "scoring_batch_size", 25)

        # --- Select only unscored listings (rule 18: idempotent) ---
        listings = storage.read_unscored_listings(limit=batch_size)

        if not listings:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="No unscored listings in this batch.",
            )

        scores: list[int] = []
        failed_ids: list[str] = []
        failed_count = 0

        for listing in listings:
            listing_id = listing["id"]
            try:
                # Step 1: extract facts (hits persistent cache if already done)
                facts = extract(listing)

                # Step 2: deterministic scoring (no LLM, no network)
                result = score_listing(listing, facts, config)

                # Step 3: persist via storage only (no direct sqlite3)
                storage.save_score(
                    listing_id=listing_id,
                    score=result["score"],
                    reason=result["reason"],
                    components=result["components"],
                )

                scores.append(result["score"])

            except Exception as exc:  # noqa: BLE001
                failed_count += 1
                failed_ids.append(listing_id)
                # Log per-listing failure; remaining listings continue (rule 17)
                storage.log_agent_run(
                    cycle_id=str(uuid.uuid4()),
                    agent=f"{self.name}/{listing_id[:8]}",
                    status="failed",
                    records_touched=0,
                    notes=f"Scoring failed for listing {listing_id[:8]}: {exc}",
                    errors=[str(exc)],
                )

        scored_count = len(scores)

        # --- Compute distribution (rule 20) ---
        if scored_count == 0:
            notes = f"scored 0 · {failed_count} failed · no distribution"
            status = "ok"
        else:
            mn   = min(scores)
            mx   = max(scores)
            mean = round(sum(scores) / scored_count)
            spread = mx - mn

            # Rule 20: spread < 10 with enough data is suspect
            suspect = scored_count >= 3 and spread < 10
            spread_label = "SUSPECT (spread < 10)" if suspect else "OK"

            fail_part = f" · {failed_count} failed" if failed_count else ""
            notes = (
                f"scored {scored_count} · range {mn}-{mx} · "
                f"mean {mean}{fail_part} · spread {spread_label}"
            )
            status = "ok"

        return AgentResult(
            agent=self.name,
            status=status,
            records_touched=scored_count,
            notes=notes,
            errors=[f"failed listing {lid[:8]}" for lid in failed_ids],
        )
