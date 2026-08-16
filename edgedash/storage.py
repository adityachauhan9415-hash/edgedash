"""
edgedash.storage
================
Thin SQLite-backed storage layer.  All pipeline agents write exclusively
through this module; the Dashboard reads exclusively through this module.

Schema (v1)
-----------
listings      — one row per job listing, keyed on listing id
cycle_log     — one row per agent run within a pipeline cycle
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator


# ---------------------------------------------------------------------------
# Stable listing ID
# ---------------------------------------------------------------------------

def make_listing_id(source: str, url: str) -> str:
    """
    Derive a stable, deterministic listing ID from (source, url).

    The same job posting fetched on different days always produces the same
    ID, so upsert_listings treats it as an update rather than a new row.
    This is the single authoritative implementation — no other module should
    reimplement this logic.

    Returns a 16-character lowercase hex string (64-bit prefix of SHA-256).
    """
    payload = f"{source}::{url}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS listings (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    company         TEXT NOT NULL,
    location        TEXT,
    seniority       TEXT,
    description     TEXT,
    skills          TEXT,          -- JSON array
    posted_at       TEXT,
    fetched_at      TEXT NOT NULL,
    score           REAL,
    score_notes     TEXT,
    gap_analysis    TEXT,          -- JSON object
    verified        INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cycle_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id        TEXT NOT NULL,
    agent           TEXT NOT NULL,
    status          TEXT NOT NULL,
    records_touched INTEGER DEFAULT 0,
    notes           TEXT,
    errors          TEXT,          -- JSON array
    ran_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# Storage class
# ---------------------------------------------------------------------------

class Storage:
    """
    Manages all reads and writes to the EdgeDash SQLite database.

    Rules:
    - Agents call upsert_* / log_* methods; they never touch the DB directly.
    - The Dashboard calls read_* methods only; it never calls upsert or log.
    """

    def __init__(self, db_path: str = "edgedash.db") -> None:
        self.db_path = Path(db_path)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def init(self) -> None:
        """Create tables if they don't exist yet."""
        with self._connect() as conn:
            conn.executescript(_DDL)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ------------------------------------------------------------------
    # Listings
    # ------------------------------------------------------------------

    def upsert_listings(self, listings: list[dict[str, Any]]) -> int:
        """
        Insert or update listings by id.

        Returns the number of rows that were *new* (not previously stored).
        Existing rows are updated only if content changed; the score and
        gap_analysis columns are preserved on update.
        """
        now = self._now()
        new_count = 0

        with self._connect() as conn:
            for listing in listings:
                existing = conn.execute(
                    "SELECT id FROM listings WHERE id = ?", (listing["id"],)
                ).fetchone()

                skills_json = json.dumps(listing.get("skills", []))

                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO listings
                            (id, title, company, location, seniority,
                             description, skills, posted_at, fetched_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            listing["id"],
                            listing.get("title", ""),
                            listing.get("company", ""),
                            listing.get("location", ""),
                            listing.get("seniority", ""),
                            listing.get("description", ""),
                            skills_json,
                            listing.get("posted_at", now),
                            now,
                        ),
                    )
                    new_count += 1
                else:
                    # Refresh content fields but leave score / gap_analysis alone
                    conn.execute(
                        """
                        UPDATE listings
                        SET title = ?, company = ?, location = ?, seniority = ?,
                            description = ?, skills = ?, posted_at = ?, fetched_at = ?
                        WHERE id = ?
                        """,
                        (
                            listing.get("title", ""),
                            listing.get("company", ""),
                            listing.get("location", ""),
                            listing.get("seniority", ""),
                            listing.get("description", ""),
                            skills_json,
                            listing.get("posted_at", now),
                            now,
                            listing["id"],
                        ),
                    )

        return new_count

    def count_unscored(self) -> int:
        """Return the number of listings that have no score yet."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM listings WHERE score IS NULL"
            ).fetchone()
            return row["n"]

    def read_listings(self, limit: int = 200) -> list[dict]:
        """Return up to `limit` listings as plain dicts (Dashboard use)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM listings ORDER BY fetched_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Cycle log
    # ------------------------------------------------------------------

    def log_agent_run(
        self,
        cycle_id: str,
        agent: str,
        status: str,
        records_touched: int,
        notes: str,
        errors: list[str] | None = None,
    ) -> None:
        """Append one row to cycle_log for a completed agent run."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cycle_log
                    (cycle_id, agent, status, records_touched, notes, errors, ran_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    agent,
                    status,
                    records_touched,
                    notes,
                    json.dumps(errors or []),
                    self._now(),
                ),
            )

    def read_cycle_log(self, limit: int = 50) -> list[dict]:
        """Return recent cycle log rows (Dashboard use)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM cycle_log ORDER BY ran_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # State key-value store
    # ------------------------------------------------------------------

    def get_state(self, key: str, default: str = "") -> str:
        """Read a named state value (e.g. last_fetch_time)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM state WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set_state(self, key: str, value: str) -> None:
        """Write a named state value."""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
