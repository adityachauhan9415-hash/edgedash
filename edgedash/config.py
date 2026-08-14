"""
edgedash.config
===============
Single source of truth for project-wide configuration.
All values are read from environment variables or fall back to safe defaults.
Never hardcode secrets or environment-specific paths here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class Config:
    # --- Job search parameters ---
    target_role: str = "Data Engineer"
    city: str = "San Francisco, CA"

    # --- Pipeline behaviour ---
    fetch_agent: str = "MockFetcher"   # swap to "Fetcher" when real agent is ready
    score_agent: str = "Scorer"        # placeholder
    gap_agent: str = "GapAnalyzer"     # placeholder

    # --- Storage ---
    db_path: str = "edgedash.db"

    # --- Profile ---
    profile_path: str = "profile.yaml"

    # --- Logging ---
    log_level: str = "INFO"

    # --- Extra keys forwarded from env (populated by from_env) ---
    _extras: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config from environment variables, falling back to defaults."""
        return cls(
            target_role=os.getenv("EDGEDASH_TARGET_ROLE", "Data Engineer"),
            city=os.getenv("EDGEDASH_CITY", "San Francisco, CA"),
            fetch_agent=os.getenv("EDGEDASH_FETCH_AGENT", "MockFetcher"),
            score_agent=os.getenv("EDGEDASH_SCORE_AGENT", "Scorer"),
            gap_agent=os.getenv("EDGEDASH_GAP_AGENT", "GapAnalyzer"),
            db_path=os.getenv("EDGEDASH_DB_PATH", "edgedash.db"),
            profile_path=os.getenv("EDGEDASH_PROFILE_PATH", "profile.yaml"),
            log_level=os.getenv("EDGEDASH_LOG_LEVEL", "INFO"),
        )
