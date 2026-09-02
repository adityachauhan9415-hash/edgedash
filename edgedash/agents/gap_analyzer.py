"""
edgedash.agents.gap_analyzer
=============================
Deterministic gap analysis.  No LLM, no network.

Goal:       Compute weighted skill gaps from all scored+extracted listings
            and append a timestamped snapshot to skill_gaps.
Stop cond:  All scored listings with extraction cache hits have been processed.

Arithmetic (approved)
----------------------
For each required skill s that the user LACKS:

    L(s) = scored listings where s in required_skills (canonical)
           AND s not in user profile (canonical)

    opportunity_cost   = sum(listing.score / 100.0  for l in L(s))
    listings_blocked   = len(L(s))
    mean_score         = round(mean of scores in L(s), 1)
    top_score          = max score in L(s)
    example_ids        = up to 5 listing IDs ordered by score desc
    low_confidence     = listings_blocked < 3

Ranking: by opportunity_cost desc, top 10.

nice_to_have: tracked as a plain count-per-canonical-skill only.
Never ranked with required gaps, never given opportunity_cost.
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from typing import TYPE_CHECKING

from edgedash.agents.base import Agent, AgentResult
from edgedash.skills import canonical
import os


def _load_profile_skills(profile_path: str) -> list[str]:
    """
    Load skills from the profile YAML file.
    Returns list of raw skill strings, or [] if file missing/unreadable.
    Parses manually to avoid dependency on PyYAML.
    """
    if not profile_path:
        return []
    full_path = os.path.abspath(profile_path)
    if not os.path.isfile(full_path):
        return []

    try:
        with open(full_path, encoding="utf-8") as f:
            lines = f.readlines()

        in_skills = False
        skills = []
        for line in lines:
            stripped = line.strip()
            if stripped == "skills:":
                in_skills = True
                continue
            if in_skills:
                if stripped.startswith("- "):
                    skill = stripped[2:].strip().strip('"').strip("'")
                    if skill:
                        skills.append(skill)
                elif stripped and not stripped.startswith("#"):
                    # End of skills section (next top-level key)
                    if not stripped.startswith(" "):
                        break
        return skills
    except Exception:
        return []

if TYPE_CHECKING:
    from edgedash.config import Config
    from edgedash.storage import Storage

# Fewer than this many listings → low_confidence flag
_LOW_CONFIDENCE_THRESHOLD = 3
# Maximum example IDs stored per gap
_MAX_EXAMPLE_IDS = 5
# Maximum gap rows written per snapshot
_TOP_N = 10


def _compute_gaps(
    listings_with_facts: list[tuple[dict, dict]],
    profile_canonical: set[str],
    aliases: dict,
) -> tuple[list[dict], dict[str, int]]:
    """
    Core arithmetic.  Pure function — no I/O.

    Parameters
    ----------
    listings_with_facts:
        List of (listing_row, facts_dict) pairs.
        listing_row must have 'id' and 'score'.
        facts_dict must have 'required_skills' and 'nice_to_have'.
    profile_canonical:
        Set of canonical skill names the user already has.
    aliases:
        Alias map from config, forwarded to canonical().

    Returns
    -------
    (required_gaps, nice_counts)
        required_gaps: list of gap dicts (unsorted, untruncated)
        nice_counts:   {canonical_skill: total_count_across_listings}
    """
    # Accumulate per required-skill data
    # skill -> list of (score, listing_id)
    skill_hits: dict[str, list[tuple[int, str]]] = defaultdict(list)

    # nice_to_have: skill -> total appearance count (no opportunity_cost)
    nice_counts: dict[str, int] = defaultdict(int)

    for listing, facts in listings_with_facts:
        score       = int(listing.get("score") or 0)
        listing_id  = listing["id"]

        # Required skills
        for raw in (facts.get("required_skills") or []):
            canon = canonical(raw, aliases)
            if not canon:
                continue
            if canon not in profile_canonical:
                skill_hits[canon].append((score, listing_id))

        # Nice-to-have: count only, never mixed with required
        for raw in (facts.get("nice_to_have") or []):
            canon = canonical(raw, aliases)
            if canon:
                nice_counts[canon] += 1

    # Build gap records
    required_gaps: list[dict] = []
    for skill, hits in skill_hits.items():
        scores     = [s for s, _ in hits]
        blocked    = len(hits)
        opp_cost   = sum(s / 100.0 for s in scores)
        mean_s     = round(sum(scores) / blocked, 1)
        top_s      = max(scores)
        # example_ids: top 5 by score desc
        ordered    = sorted(hits, key=lambda t: -t[0])
        example_ids = [lid for _, lid in ordered[:_MAX_EXAMPLE_IDS]]

        required_gaps.append({
            "skill":            skill,
            "listings_blocked": blocked,
            "opportunity_cost": round(opp_cost, 4),
            "mean_score":       mean_s,
            "top_score":        top_s,
            "example_ids":      example_ids,
            "low_confidence":   blocked < _LOW_CONFIDENCE_THRESHOLD,
        })

    # Sort by opportunity_cost desc for deterministic output
    required_gaps.sort(key=lambda g: -g["opportunity_cost"])

    return required_gaps, dict(nice_counts)


class GapAnalyzer(Agent):
    """
    Deterministic skill-gap analyzer.

    Goal       : Produce a ranked, weighted gap snapshot from all
                 scored listings that have extraction cache data.
    Stop cond  : All eligible listings processed; snapshot appended.
    """

    @property
    def name(self) -> str:
        return "GapAnalyzer"

    def run(self, config: "Config", storage: "Storage") -> AgentResult:
        aliases: dict         = getattr(config, "skill_aliases", {}) or {}
        profile_path: str     = getattr(config, "profile_path", "profile.yaml") or "profile.yaml"
        raw_skills            = _load_profile_skills(profile_path)
        profile_canonical     = {canonical(s, aliases) for s in raw_skills if s}

        # Collect all scored listings
        all_listings = storage.read_listings(limit=10_000)
        scored       = [r for r in all_listings if r.get("score") is not None]

        if not scored:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes="No scored listings available for gap analysis.",
            )

        # Pair each listing with its extraction facts (from cache)
        pairs: list[tuple[dict, dict]] = []
        missing_facts = 0

        for listing in scored:
            desc = (listing.get("description") or "").strip()
            if not desc:
                missing_facts += 1
                continue

            import hashlib
            desc_hash = hashlib.sha256(desc.encode("utf-8")).hexdigest()
            facts = storage.get_extraction_cache(desc_hash)
            if facts is None:
                missing_facts += 1
                continue
            pairs.append((listing, facts))

        if not pairs:
            return AgentResult(
                agent=self.name,
                status="ok",
                records_touched=0,
                notes=(
                    f"No extraction facts found for {len(scored)} scored listing(s). "
                    "Run a scoring cycle first."
                ),
            )

        # Compute gaps
        required_gaps, nice_counts = _compute_gaps(pairs, profile_canonical, aliases)

        # Rank by opportunity_cost desc, keep top N
        required_gaps.sort(key=lambda g: -g["opportunity_cost"])
        top_gaps = required_gaps[:_TOP_N]

        # Append snapshot (rule 25: append-only)
        run_id = str(uuid.uuid4())
        rows_written = storage.append_skill_gap_snapshot(run_id, top_gaps)

        # Build notes
        nice_summary = f"{len(nice_counts)} nice-to-have skills tracked"
        lc_count     = sum(1 for g in top_gaps if g["low_confidence"])
        lc_note      = f" · {lc_count} low-confidence" if lc_count else ""
        skip_note    = f" · {missing_facts} skipped (no facts)" if missing_facts else ""

        notes = (
            f"{rows_written} gap(s) in snapshot · "
            f"{len(pairs)} listings analysed{skip_note} · "
            f"{nice_summary}{lc_note}"
        )

        return AgentResult(
            agent=self.name,
            status="ok",
            records_touched=rows_written,
            notes=notes,
        )
