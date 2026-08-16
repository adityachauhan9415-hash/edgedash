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
    APIFY_TOKEN            Apify API token (required for the apify source)

Secrets are loaded from a .env file in the repo root — the single place
where dotenv is invoked (steering rule 13).  A .env.example is committed
to show which keys are expected.
"""
import sys
# Enable ANSI escape codes on Windows
if sys.platform == "win32":
    import os
    os.system("")          # activates VT100 processing in the current console
    # Ensure stdout can handle UTF-8 box-drawing chars regardless of redirect
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Load .env once, here, before any os.getenv() calls anywhere in the
# codebase.  override=False means existing env vars always win, so CI and
# production can supply secrets without a .env file being present.
from dotenv import load_dotenv
load_dotenv(override=False)

from edgedash.config import Config
from edgedash.orchestrator import run_cycle

if __name__ == "__main__":
    config = Config.from_env()
    run_cycle(config)
