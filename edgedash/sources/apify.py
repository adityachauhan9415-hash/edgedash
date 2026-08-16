"""
edgedash.sources.apify
======================
Source connector for Apify's job-scraper actors via the run-sync-get-dataset
endpoint.

Auth
----
Requires APIFY_TOKEN in the environment (loaded from .env by run_cycle.py).
Per steering rule 13: if the token is absent, the source logs a clear message
and returns an empty list — it does NOT raise and does NOT crash the cycle.

API used
--------
POST https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items
    ?token=<APIFY_TOKEN>
    &format=json
    &limit=<cap>

The actor ID is configurable via APIFY_ACTOR_ID (default: "misceres/indeed-scraper",
a commonly used free-tier job scraper on Apify's marketplace).

Result mapping
--------------
Apify actors vary in their field names.  This file is the only place that
knows the actor's schema and maps it onto our normalised keys.  The mapping
is deliberately defensive: every field falls back to None rather than raising
on an unexpected payload shape.

Cap
---
MAX_RESULTS = 100 per run to protect free-tier credits.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from edgedash.sources.base import Source, SourceError, register
from edgedash.sources.http import get_json

if TYPE_CHECKING:
    from edgedash.config import Config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_ACTOR = "misceres/indeed-scraper"
MAX_RESULTS = 100
_BASE_URL = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"


# ---------------------------------------------------------------------------
# Field mapping helpers
# ---------------------------------------------------------------------------

def _coerce_date(raw: object) -> str | None:
    """
    Try to normalise a date value to ISO-8601.
    Accepts Unix timestamps (int/float) or ISO strings.
    Returns None if the value cannot be parsed.
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(raw, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        except (OSError, OverflowError, ValueError):
            return None
    s = str(raw).strip()
    return s if s else None


def _nonempty(value: object) -> str | None:
    """Return str(value) if truthy, else None.  Never returns empty string."""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _normalise(item: dict, source_name: str) -> dict:
    """
    Map an Apify actor item onto the EdgeDash normalised row schema.

    The indeed-scraper actor returns fields like:
        positionName, company, location, jobUrl, description, datePosted, ...

    We map those here.  All fallbacks are None (steering rule 10).
    """
    # Primary field candidates in priority order — different actor versions
    # use slightly different names, so we try several.
    title = (
        _nonempty(item.get("positionName"))
        or _nonempty(item.get("title"))
        or _nonempty(item.get("jobTitle"))
    )
    company = (
        _nonempty(item.get("company"))
        or _nonempty(item.get("companyName"))
    )
    location = (
        _nonempty(item.get("location"))
        or _nonempty(item.get("jobLocation"))
    )
    url = (
        _nonempty(item.get("jobUrl"))
        or _nonempty(item.get("url"))
        or _nonempty(item.get("applyUrl"))
    )
    description = (
        _nonempty(item.get("description"))
        or _nonempty(item.get("jobDescription"))
        or _nonempty(item.get("summary"))
    )
    posted_at = _coerce_date(
        item.get("datePosted")
        or item.get("postedAt")
        or item.get("publishedAt")
    )

    # external_id: prefer a source-provided stable id, fall back to url
    external_id = (
        _nonempty(item.get("id"))
        or _nonempty(item.get("jobId"))
        or _nonempty(item.get("externalId"))
        or url  # last resort — the Fetcher will hash (source, url) for storage.id
    )

    return {
        "source":      source_name,
        "external_id": external_id,
        "title":       title,
        "company":     company,
        "location":    location,
        "url":         url,
        "description": description,
        "posted_at":   posted_at,
        "raw":         item,
    }


# ---------------------------------------------------------------------------
# Source implementation
# ---------------------------------------------------------------------------

@register
class ApifySource(Source):
    """
    Fetches job listings from an Apify actor via run-sync-get-dataset-items.

    Goal       : Return up to MAX_RESULTS normalised rows from the configured actor.
    Stop cond  : The API call has completed (sync endpoint — one request, one response).
    """

    @property
    def name(self) -> str:
        return "apify"

    def fetch(self, config: "Config") -> list[dict]:
        # ── Secret check (steering rule 13) ───────────────────────────
        token = os.getenv("APIFY_TOKEN", "").strip()
        if not token:
            print("  [apify] apify: no APIFY_TOKEN, skipping")
            return []

        actor = os.getenv("APIFY_ACTOR_ID", _DEFAULT_ACTOR).strip()
        url = _BASE_URL.format(actor=actor.replace("/", "~"))

        role = getattr(config, "target_role", "Data Engineer") or "Data Engineer"
        city = getattr(config, "city", "") or ""

        print(
            f"  [apify] Fetching actor={actor!r} "
            f"role={role!r} city={city!r} cap={MAX_RESULTS}"
        )

        # ── Single HTTP call via the shared helper (steering rule 11) ─
        try:
            raw_list = get_json(
                url,
                params={
                    "token":  token,
                    "format": "json",
                    "limit":  MAX_RESULTS,
                    # Actor-specific input fields — most Indeed-type scrapers
                    # accept these as query params on the sync endpoint.
                    "position": role,
                    "location": city,
                    "maxItems": MAX_RESULTS,
                },
            )
        except SourceError:
            raise  # Fetcher's per-source try/except will log and continue

        if not isinstance(raw_list, list):
            raise SourceError(
                f"apify: expected a JSON array from actor, got {type(raw_list).__name__}"
            )

        rows = [_normalise(item, self.name) for item in raw_list]

        # Drop rows with no URL — we can't compute a stable ID without one
        usable = [r for r in rows if r.get("url")]

        print(
            f"  [apify] Done. {len(raw_list)} raw items, "
            f"{len(usable)} with a usable URL."
        )
        return usable
