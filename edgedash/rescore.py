"""
edgedash.rescore
================
CLI tool to clear scoring data so listings can be re-scored on the next cycle.

Usage
-----
    python -m edgedash.rescore --all
    python -m edgedash.rescore --id <listing_id>

--all requires interactive confirmation.
--id clears exactly one listing; nonexistent ids are reported as 0 cleared.

Never touches extraction_cache.
"""
from __future__ import annotations

import argparse
import sys


def _main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m edgedash.rescore",
        description="Clear listing scores so they are re-scored on the next cycle.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--all",
        action="store_true",
        help="Clear scores for ALL listings (requires confirmation).",
    )
    group.add_argument(
        "--id",
        metavar="LISTING_ID",
        help="Clear the score for a single listing by its ID.",
    )
    args = parser.parse_args()

    from edgedash.config import Config
    from edgedash.storage import Storage

    config = Config.from_env()
    storage = Storage(db_path=config.db_path)
    storage.init()

    if args.all:
        answer = input("Clear all scores? [y/N]: ").strip().lower()
        if answer not in ("y", "yes"):
            print("Cancelled. Nothing was changed.")
            sys.exit(0)
        cleared = storage.clear_all_scores()
    else:
        cleared = storage.clear_listing_score(args.id)

    print(f"Cleared {cleared} score(s). Run the cycle to re-score.")


if __name__ == "__main__":
    _main()
