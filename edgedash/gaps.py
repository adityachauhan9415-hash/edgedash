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


def _trend() -> None:
    """Print trend report comparing earliest vs latest snapshot."""
    from edgedash.config import Config
    from edgedash.storage import Storage

    config  = Config.from_env()
    storage = Storage(db_path=config.db_path)
    storage.init()

    snapshots = storage.read_skill_gap_snapshots()

    if not snapshots:
        print("No gap snapshots found. Run a full cycle first.")
        return

    if len(snapshots) < 2:
        earliest = snapshots[0]
        print(f"Only one snapshot exists: {earliest['computed_at']}")
        print("Run a daily cycle for at least 2 days to see a trend.")
        print(f"(Current: {len(snapshots)} snapshot(s), need 2+)")
        return

    earliest = snapshots[0]
    latest   = snapshots[-1]

    print(f"Comparing snapshots:")
    print(f"  Earliest: {earliest['computed_at']}  (run {earliest['run_id'][:8]})")
    print(f"  Latest:   {latest['computed_at']}    (run {latest['run_id'][:8]})")
    print()

    # Build lookup by skill for both
    earliest_by_skill = {g["skill"]: g for g in earliest["gaps"]}
    latest_by_skill   = {g["skill"]: g for g in latest["gaps"]}

    latest_top10 = latest["gaps"][:10]
    latest_top10_skills = {g["skill"] for g in latest_top10}

    # Process each skill in latest top 10
    print("── Trend for latest top 10 skills ──────────────────────────────")
    print(f"  {'Skill':<20}  {'Earliest':>8}  {'Latest':>8}  {'Change':>8}  {'%':>7}")
    print(f"  {'─'*20}  {'─'*8}  {'─'*8}  {'─'*8}  {'─'*7}")

    trend_rows = []
    dropped_skills = set()

    for gap in latest_top10:
        skill = gap["skill"]
        latest_cost = gap["opportunity_cost"]

        if skill in earliest_by_skill:
            earliest_cost = earliest_by_skill[skill]["opportunity_cost"]
            change = latest_cost - earliest_cost
            pct = (change / earliest_cost * 100) if earliest_cost != 0 else 0.0
            trend_rows.append({
                "skill":        skill,
                "earliest":     earliest_cost,
                "latest":       latest_cost,
                "change":       change,
                "pct":          pct,
                "is_new":       False,
            })
        else:
            # NEW skill (not in earliest top 10)
            trend_rows.append({
                "skill":        skill,
                "earliest":     0.0,
                "latest":       latest_cost,
                "change":       latest_cost,
                "pct":          100.0,
                "is_new":       True,
            })

    # Detect dropped skills (in earliest top 10 but not in latest top 10)
    earliest_top10_skills = {g["skill"] for g in earliest["gaps"][:10]}
    for skill in earliest_top10_skills:
        if skill not in latest_top10_skills:
            dropped_skills.add(skill)

    # Print trend rows
    for row in trend_rows:
        if row["is_new"]:
            marker = " [NEW]"
        else:
            marker = ""
        change_str = f"+{row['change']:.2f}" if row["change"] >= 0 else f"{row['change']:.2f}"
        pct_str    = f"+{row['pct']:.1f}%" if row["pct"] >= 0 else f"{row['pct']:.1f}%"
        print(
            f"  {row['skill']:<20}  {row['earliest']:>8.2f}  {row['latest']:>8.2f}  "
            f"{change_str:>8}  {pct_str:>7}{marker}"
        )

    # Print dropped skills
    if dropped_skills:
        print(f"\n── Dropped out of top 10 (were in earliest, not in latest) ───")
        for skill in sorted(dropped_skills):
            print(f"  - {skill}")

    print(f"\nTotal snapshots tracked: {len(snapshots)}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(prog="python -m edgedash.gaps")
    parser.add_argument(
        "--trend",
        action="store_true",
        help="Show trend report comparing earliest vs latest snapshot.",
    )
    args = parser.parse_args()

    if args.trend:
        _trend()
    else:
        _main()
