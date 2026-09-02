"""
edgedash.gaps
=============
CLI that prints the latest skill-gap snapshot from Storage.

Usage
-----
    python -m edgedash.gaps

Output format (one line per gap, ranked by opportunity_cost):
    rank  skill                 blocked  opp_cost  mean_score  bar
"""
from __future__ import annotations

import sys


def _bar(value: float, max_value: float, width: int = 20) -> str:
    """Return a simple text bar proportional to value/max_value."""
    if max_value <= 0:
        return ""
    filled = round(width * min(value / max_value, 1.0))
    return "█" * filled + "░" * (width - filled)


def _main() -> None:
    from edgedash.config import Config
    from edgedash.storage import Storage

    config  = Config.from_env()
    storage = Storage(db_path=config.db_path)
    storage.init()

    gaps = storage.read_latest_skill_gaps()

    if not gaps:
        print("No gap snapshots found. Run a full cycle (Fetcher → Scorer → GapAnalyzer) first.")
        sys.exit(0)

    computed_at = gaps[0].get("computed_at", "unknown")
    run_id      = gaps[0].get("run_id", "unknown")
    print(f"Latest snapshot  : {computed_at}  (run {run_id[:8]})")
    print(f"Gap rows         : {len(gaps)}\n")

    max_cost = gaps[0]["opportunity_cost"] if gaps else 1.0

    header = f"  {'#':>2}  {'Skill':<28}  {'Blocked':>7}  {'OppCost':>7}  {'Mean':>5}  {'Top':>3}  Bar"
    print(header)
    print("  " + "─" * (len(header) - 2))

    for i, gap in enumerate(gaps, 1):
        lc    = " *" if gap["low_confidence"] else "  "
        bar   = _bar(gap["opportunity_cost"], max_cost)
        print(
            f"  {i:>2}{lc} {gap['skill']:<28}  "
            f"{gap['listings_blocked']:>7}  "
            f"{gap['opportunity_cost']:>7.2f}  "
            f"{gap['mean_score']:>5.1f}  "
            f"{gap['top_score']:>3}  "
            f"{bar}"
        )

    print("\n  * low confidence (< 3 listings)")
    print("\nSample listing IDs for top gap:")
    if gaps:
        top = gaps[0]
        print(f"  Skill: {top['skill']}")
        for lid in top["example_ids"]:
            print(f"    {lid}")


if __name__ == "__main__":
    _main()
