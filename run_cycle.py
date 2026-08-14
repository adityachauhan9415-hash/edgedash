#!/usr/bin/env python3
"""
run_cycle.py
============
Entry point for one EdgeDash pipeline cycle.

Usage
-----
    python run_cycle.py

Environment variables (all optional — see edgedash/config.py for defaults)
---------------------------------------------------------------------------
    EDGEDASH_TARGET_ROLE   e.g. "Machine Learning Engineer"
    EDGEDASH_CITY          e.g. "New York, NY"
    EDGEDASH_FETCH_AGENT   e.g. "MockFetcher" (default) or "Fetcher"
    EDGEDASH_DB_PATH       path to the SQLite file (default: edgedash.db)
    EDGEDASH_PROFILE_PATH  path to the YAML profile (default: profile.yaml)
"""
import sys
# Enable ANSI escape codes on Windows
if sys.platform == "win32":
    import os
    os.system("")          # activates VT100 processing in the current console

from edgedash.config import Config
from edgedash.orchestrator import run_cycle

if __name__ == "__main__":
    config = Config.from_env()
    run_cycle(config)
