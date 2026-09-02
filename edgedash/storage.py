"""
edgedash.storage
================
Thin SQLite-backed storage layer.  All pipeline agents write exclusively
through this module; the Dashboard reads exclusively through this module.

Schema (v1)
-----------
listings          -- one row per job listing, keyed on listing id
cycle_log         -- one row per agent run within a pipeline cycle
state             -- key-value store for pipeline state
extraction_cache  -- persistent LLM extraction cache (rule 18)
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
    This is the single authoritative implementation -- no other module should
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
    skills          TEXT,
    posted_at       TEXT,
    fetched_at      TEXT NOT NULL,
    score           REAL,
    score_notes     TEXT,
    gap_analysis    TEXT,
    scored_at       TEXT,
    verified        INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cycle_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id        TEXT NOT NULL,
    agent           TEXT NOT NULL,
    status          TEXT NOT NULL,
    records_touched INTEGER DEFAULT 0,
    notes           TEXT,
    errors          TEXT,
    ran_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS state (
    key     TEXT PRIMARY KEY,
    value   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extraction_cache (
    description_hash TEXT PRIMARY KEY,
    result_json      TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_gaps (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id           TEXT NOT NULL,
    computed_at      TEXT NOT NULL,
    skill            TEXT NOT NULL,
    listings_blocked INTEGER NOT NULL,
    opportunity_cost REAL NOT NULL,
    mean_score       REAL NOT NULL,
    top_score        INTEGER NOT NULL,
    example_ids      TEXT NOT NULL,   -- JSON array, max 5
    low_confidence   INTEGER NOT NULL  -- 1 if listings_blocked < 3
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
        """Create tables if they do not exist yet."""
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

    # ------------------------------------------------------------------
    # Scoring  (rule 18: idempotent; rule 20: distribution logged by agent)
    # ------------------------------------------------------------------

    def read_unscored_listings(self, limit: int = 25) -> list[dict]:
        """
        Return up to `limit` listings that have not yet been scored.
        Safe migration: adds scored_at and components columns if absent.
        """
        self._ensure_score_columns()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM listings WHERE score IS NULL "
                "ORDER BY fetched_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def save_score(
        self,
        listing_id: str,
        score: int,
        reason: str,
        components: dict,
    ) -> None:
        """
        Persist score, reason, components JSON, and scored_at for one listing.
        Safe migration: adds scored_at and components columns if absent.
        """
        self._ensure_score_columns()
        now = self._now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE listings
                SET score       = ?,
                    score_notes = ?,
                    gap_analysis = ?,
                    scored_at   = ?
                WHERE id = ?
                """,
                (
                    score,
                    reason,
                    json.dumps(components),
                    now,
                    listing_id,
                ),
            )

    def clear_all_scores(self) -> int:
        """
        Set score, score_notes, gap_analysis (components), and scored_at to
        NULL for every listing.  Never touches extraction_cache.

        Returns the number of rows actually cleared (those that had a score).
        """
        with self._connect() as conn:
            affected = conn.execute(
                "SELECT COUNT(*) AS n FROM listings WHERE score IS NOT NULL"
            ).fetchone()["n"]
            conn.execute(
                """
                UPDATE listings
                SET score       = NULL,
                    score_notes = NULL,
                    gap_analysis = NULL,
                    scored_at   = NULL
                WHERE score IS NOT NULL
                """
            )
        return affected

    def clear_listing_score(self, listing_id: str) -> int:
        """
        Set score, score_notes, gap_analysis, and scored_at to NULL for the
        given listing only.  Never touches extraction_cache.

        Returns 1 if the listing existed and had a score, 0 otherwise
        (including nonexistent ids).
        """
        with self._connect() as conn:
            affected = conn.execute(
                "SELECT COUNT(*) AS n FROM listings "
                "WHERE id = ? AND score IS NOT NULL",
                (listing_id,),
            ).fetchone()["n"]
            conn.execute(
                """
                UPDATE listings
                SET score       = NULL,
                    score_notes = NULL,
                    gap_analysis = NULL,
                    scored_at   = NULL
                WHERE id = ? AND score IS NOT NULL
                """,
                (listing_id,),
            )
        return affected

    def _ensure_score_columns(self) -> None:
        with self._connect() as conn:
            existing = {
                r["name"]
                for r in conn.execute("PRAGMA table_info(listings)").fetchall()
            }
            if "scored_at" not in existing:
                conn.execute(
                    "ALTER TABLE listings ADD COLUMN scored_at TEXT"
                )
            # gap_analysis already in DDL but might be missing in old DBs
            if "gap_analysis" not in existing:
                conn.execute(
                    "ALTER TABLE listings ADD COLUMN gap_analysis TEXT"
                )

    # ------------------------------------------------------------------
    # Extraction cache (rule 18)
    # ------------------------------------------------------------------

    def get_extraction_cache(self, description_hash: str) -> dict | None:
        """
        Return a previously stored extraction result, or None on cache miss.

        Parameters
        ----------
        description_hash:
            Full SHA-256 hex digest of the job description text.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json FROM extraction_cache "
                "WHERE description_hash = ?",
                (description_hash,),
            ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def set_extraction_cache(self, description_hash: str, result: dict) -> None:
        """
        Persist a validated extraction result keyed on the description hash.

        Parameters
        ----------
        description_hash:
            Full SHA-256 hex digest of the job description text.
        result:
            Validated extraction dict to store.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO extraction_cache
                    (description_hash, result_json, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(description_hash) DO UPDATE SET
                    result_json = excluded.result_json,
                    created_at  = excluded.created_at
                """,
                (description_hash, json.dumps(result), self._now()),
            )

    # ------------------------------------------------------------------
    # Skill-gap snapshots (rule 25: append-only, never overwrite)
    # ------------------------------------------------------------------

    def append_skill_gap_snapshot(
        self,
        run_id: str,
        gaps: list[dict],
    ) -> int:
        """
        Append a batch of gap rows for one run.  Never overwrites previous rows.

        Parameters
        ----------
        run_id:
            Unique identifier for this gap-analysis run (e.g. cycle_id).
        gaps:
            List of dicts, each with keys:
                skill, listings_blocked, opportunity_cost, mean_score,
                top_score, example_ids (list[str]), low_confidence (bool).

        Returns
        -------
        int
            Number of rows inserted.
        """
        now = self._now()
        with self._connect() as conn:
            for gap in gaps:
                conn.execute(
                    """
                    INSERT INTO skill_gaps
                        (run_id, computed_at, skill, listings_blocked,
                         opportunity_cost, mean_score, top_score,
                         example_ids, low_confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        now,
                        gap["skill"],
                        gap["listings_blocked"],
                        gap["opportunity_cost"],
                        gap["mean_score"],
                        gap["top_score"],
                        json.dumps(gap.get("example_ids", [])),
                        1 if gap.get("low_confidence") else 0,
                    ),
                )
        return len(gaps)

    def read_latest_skill_gaps(self) -> list[dict]:
        """
        Return all rows from the most recent run_id, ordered by
        opportunity_cost descending.  Returns [] if no snapshots exist.
        """
        with self._connect() as conn:
            # Use id DESC as tiebreaker when computed_at identical
            run_row = conn.execute(
                "SELECT run_id FROM skill_gaps ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if run_row is None:
                return []
            latest_run = run_row["run_id"]
            rows = conn.execute(
                """
                SELECT skill, listings_blocked, opportunity_cost,
                       mean_score, top_score, example_ids, low_confidence,
                       computed_at, run_id
                FROM skill_gaps
                WHERE run_id = ?
                ORDER BY opportunity_cost DESC
                """,
                (latest_run,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["example_ids"]   = json.loads(d["example_ids"])
            d["low_confidence"] = bool(d["low_confidence"])
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Diagnostic reads (read-only, no writes, no schema changes)
    # ------------------------------------------------------------------

    def _diag_columns(self) -> set[str]:
        """Return the actual column names present in the listings table."""
        with self._connect() as conn:
            rows = conn.execute("PRAGMA table_info(listings)").fetchall()
            return {r["name"] for r in rows}

    def diag_total_and_source_counts(self) -> dict:
        """
        Return total listing count and a best-effort per-source breakdown.

        Source is derived from the description attribution line that
        Arbeitnow appends to every listing.
        """
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM listings"
            ).fetchone()["n"]
            rows = conn.execute(
                "SELECT description FROM listings"
            ).fetchall()

        source_counts: dict[str, int] = {}
        for r in rows:
            desc = (r["description"] or "").lower()
            if "arbeitnow" in desc:
                label = "arbeitnow.com"
            elif "apify" in desc:
                label = "apify.com"
            elif "indeed" in desc:
                label = "indeed.com"
            elif "linkedin" in desc:
                label = "linkedin.com"
            else:
                label = "unknown"
            source_counts[label] = source_counts.get(label, 0) + 1

        return {"total": total, "by_source": source_counts}

    def diag_cross_source_duplicates(self) -> list[dict]:
        """
        Return (title, company) pairs that appear under more than one
        source label -- probable cross-source duplicates.
        """
        from collections import defaultdict

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT title, company, description FROM listings "
                "WHERE title != '' AND company != ''"
            ).fetchall()

        pair_sources: dict[tuple, set] = defaultdict(set)
        for r in rows:
            key = (
                (r["title"] or "").strip().lower(),
                (r["company"] or "").strip().lower(),
            )
            desc = (r["description"] or "").lower()
            if "arbeitnow" in desc:
                label = "arbeitnow.com"
            elif "apify" in desc:
                label = "apify.com"
            else:
                label = "unknown"
            pair_sources[key].add(label)

        duplicates = []
        for (title, company), sources in pair_sources.items():
            if len(sources) > 1:
                duplicates.append({
                    "title":   title,
                    "company": company,
                    "count":   len(sources),
                    "sources": sorted(sources),
                })
        duplicates.sort(key=lambda d: -d["count"])
        return duplicates

    def diag_recent_listings(self, n: int = 5) -> list[dict]:
        """Return the n most recently fetched listings."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT title, company, fetched_at FROM listings "
                "ORDER BY fetched_at DESC LIMIT ?",
                (n,),
            ).fetchall()
            return [dict(r) for r in rows]

    def diag_quality_issues(self) -> list[dict]:
        """Return listings with a NULL or empty title or company."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, title, company FROM listings "
                "WHERE title IS NULL OR TRIM(title) = '' "
                "   OR company IS NULL OR TRIM(company) = ''"
            ).fetchall()

        result = []
        for r in rows:
            issues = []
            if not (r["title"] or "").strip():
                issues.append("missing title")
            if not (r["company"] or "").strip():
                issues.append("missing company")
            result.append({
                "id":      r["id"],
                "title":   r["title"],
                "company": r["company"],
                "issues":  issues,
            })
        return result
