"""
edgedash.sources.arbeitnow
==========================
Source connector for the Arbeitnow public job-board API.

  Endpoint : https://www.arbeitnow.com/api/job-board-api
  Auth     : none — no API key required
  Docs     : https://www.arbeitnow.com/api/job-board-api

Behaviour
---------
- Fetches page 1 first, then keeps paging while listings match config keywords,
  up to a hard cap of MAX_PAGES pages.
- Filters results by config.target_role (keyword match) and config.city
  (substring match on location).
- Location filter relaxation: if strict filtering (keyword + city) returns
  fewer than MIN_RESULTS rows, the city filter is dropped and a warning is
  logged.  Keyword matching is always applied.
- Rate-limit: 1 request per second per steering rule 14.
- external_id is the listing's stable `slug`, not a hash.
- All fields map to the normalised contract from edgedash.sources.base.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import TYPE_CHECKING

from edgedash.sources.base import Source, SourceError, register

if TYPE_CHECKING:
    from edgedash.config import Config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_API_URL = "https://www.arbeitnow.com/api/job-board-api"
_MAX_PAGES = 5
_MIN_RESULTS = 5          # below this we relax the city filter
_RATE_LIMIT_SECS = 1.0    # steering rule 14: max 1 req/s


# ---------------------------------------------------------------------------
# HTML stripping helper
# ---------------------------------------------------------------------------

class _HTMLStripper(HTMLParser):
    """Minimal HTML-to-plain-text converter (no external deps)."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return " ".join(self._parts).strip()


def _strip_html(raw: str | None) -> str | None:
    if not raw:
        return None
    stripper = _HTMLStripper()
    stripper.feed(raw)
    text = stripper.get_text()
    # Collapse excess whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def _normalise(item: dict, source_name: str) -> dict:
    """
    Map an Arbeitnow API item onto the EdgeDash normalised row schema.

    Rules (steering rule 10):
    - All keys are present.
    - Missing values are None, never empty string or "N/A".
    """
    # posted_at: API gives Unix timestamp in `created_at`
    created_at = item.get("created_at")
    if created_at:
        try:
            posted_at = datetime.fromtimestamp(
                int(created_at), tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, OSError, OverflowError):
            posted_at = None
    else:
        posted_at = None

    location = item.get("location") or None   # empty string → None
    description = _strip_html(item.get("description"))

    return {
        "source":      source_name,
        "external_id": item["slug"],           # stable identifier
        "title":       item.get("title") or None,
        "company":     item.get("company_name") or None,
        "location":    location,
        "url":         item.get("url") or None,
        "description": description,
        "posted_at":   posted_at,
        "raw":         item,                   # full original payload
    }


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------

def _matches_keywords(row: dict, keywords: list[str]) -> bool:
    """Return True if any keyword appears (case-insensitive) in title or description."""
    if not keywords:
        return True  # no filter configured → keep everything
    haystack = " ".join(filter(None, [
        row.get("title") or "",
        row.get("description") or "",
    ])).lower()
    return any(kw.lower() in haystack for kw in keywords)


def _matches_city(row: dict, city: str) -> bool:
    """Return True if city appears (case-insensitive) in location, or listing is remote."""
    if not city:
        return True
    location = (row.get("location") or "").lower()
    raw = row.get("raw", {})
    is_remote = raw.get("remote", False)
    return is_remote or city.lower() in location


def _build_keywords(config: "Config") -> list[str]:
    """
    Build a keyword list from config.target_role.
    Splits on spaces and commas; drops single-character tokens.
    """
    raw = getattr(config, "target_role", "") or ""
    tokens = re.split(r"[\s,]+", raw)
    return [t for t in tokens if len(t) > 1]


# ---------------------------------------------------------------------------
# Source implementation
# ---------------------------------------------------------------------------

@register
class ArbeitnowSource(Source):
    """
    Fetches job listings from https://www.arbeitnow.com/api/job-board-api.

    Goal       : Populate storage with fresh, keyword-matched listings.
    Stop cond  : All configured sources have been queried (up to MAX_PAGES pages,
                 or earlier if a full page returns no keyword matches).
    """

    @property
    def name(self) -> str:
        return "arbeitnow"

    def fetch(self, config: "Config") -> list[dict]:
        keywords = _build_keywords(config)
        city = getattr(config, "city", "") or ""

        raw_total = 0
        all_matched: list[dict] = []

        print(f"  [{self.name}] Fetching (keywords={keywords}, city={city!r})")

        for page in range(1, _MAX_PAGES + 1):
            if page > 1:
                time.sleep(_RATE_LIMIT_SECS)  # steering rule 14

            try:
                from edgedash.sources.http import get_json
                data = get_json(_API_URL, params={"page": page})
            except SourceError:
                raise  # propagate so the Fetcher can log it per-source

            items = data.get("data", [])
            if not items:
                print(f"  [{self.name}] Page {page}: empty — stopping pagination.")
                break

            raw_total += len(items)
            page_rows = [_normalise(item, self.name) for item in items]

            # Apply keyword filter on every page
            keyword_matched = [r for r in page_rows if _matches_keywords(r, keywords)]

            if not keyword_matched:
                # No matches on this page at all — stop paging
                print(
                    f"  [{self.name}] Page {page}: {len(items)} raw, "
                    f"0 keyword matches — stopping pagination."
                )
                break

            all_matched.extend(keyword_matched)
            print(
                f"  [{self.name}] Page {page}: {len(items)} raw, "
                f"{len(keyword_matched)} keyword matches so far ({len(all_matched)} total)."
            )

        # --- City filter with graceful relaxation (steering rule) ---
        strict = [r for r in all_matched if _matches_city(r, city)]

        if len(strict) >= _MIN_RESULTS or not city:
            final = strict
            if city:
                print(
                    f"  [{self.name}] City filter '{city}': "
                    f"{len(strict)}/{len(all_matched)} listings kept."
                )
        else:
            # Relax city filter — show remote / nearby roles too
            print(
                f"  [{self.name}] City filter '{city}' left only {len(strict)} result(s) "
                f"(threshold: {_MIN_RESULTS}). Relaxing location filter — "
                f"showing keyword-matched results without city restriction."
            )
            final = all_matched

        print(
            f"  [{self.name}] Done. "
            f"{raw_total} raw fetched, {len(all_matched)} keyword-matched, "
            f"{len(final)} survived all filters."
        )

        return final
