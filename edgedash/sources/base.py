"""
edgedash.sources.base
=====================
Defines the Source protocol that every job-board connector must implement,
plus the module-level registry used to discover and instantiate sources.

Normalised row contract (steering rule 10)
------------------------------------------
Every Source.fetch() call must return a list of dicts with exactly these keys:

    source        str   – canonical source name, e.g. "arbeitnow"
    external_id   str   – the source's own stable slug or ID
    title         str   – job title
    company       str   – company name
    location      str   – location string, or None
    url           str   – canonical URL to the listing
    description   str   – full description text, or None
    posted_at     str   – ISO-8601 datetime string, or None
    raw           dict  – the full original response payload for this row

Missing values MUST be None — never empty string, never "N/A".
"""
from __future__ import annotations

import abc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from edgedash.config import Config


# ---------------------------------------------------------------------------
# Normalised row keys
# ---------------------------------------------------------------------------

NORMALISED_KEYS: tuple[str, ...] = (
    "source",
    "external_id",
    "title",
    "company",
    "location",
    "url",
    "description",
    "posted_at",
    "raw",
)


# ---------------------------------------------------------------------------
# Source ABC
# ---------------------------------------------------------------------------

class Source(abc.ABC):
    """
    Abstract base class for all EdgeDash job-board source connectors.

    Rules (steering rules 9–14):
    - The Fetcher iterates over Sources; it never contains source-specific logic.
    - Adding a new source = adding a new Source subclass + @register decoration.
    - All HTTP goes through edgedash.sources.http.get_json — never requests.get.
    - A source failure must be caught by the caller (Fetcher); sources raise
      SourceError on unrecoverable failures.
    - If a required secret is missing, the source should raise SourceSkipped.
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Stable lowercase identifier for this source, e.g. 'arbeitnow'."""
        ...

    @abc.abstractmethod
    def fetch(self, config: "Config") -> list[dict]:
        """
        Fetch listings from this source and return normalised rows.

        Each row must have exactly the keys listed in NORMALISED_KEYS.
        Missing values must be None, not empty string or "N/A".

        Raises
        ------
        SourceError
            On any unrecoverable HTTP or parsing failure.
        SourceSkipped
            When a required secret is absent.
        """
        ...


# ---------------------------------------------------------------------------
# Registry + decorator
# ---------------------------------------------------------------------------

SOURCES: dict[str, type[Source]] = {}
"""
Module-level registry mapping source name → Source class.

To add a source, decorate its class with @register:

    from edgedash.sources.base import register

    @register
    class MySource(Source):
        ...
"""


def register(cls: type[Source]) -> type[Source]:
    """
    Class decorator that registers a Source subclass in SOURCES.

    Usage::

        @register
        class ArbeitnowSource(Source):
            ...

    The source's `name` property is read from a temporary instance; it must
    be a stable lowercase string with no spaces.
    """
    # Instantiate temporarily just to read the name
    try:
        instance = cls.__new__(cls)
        source_name = instance.name  # calls the property without __init__
    except Exception as exc:  # noqa: BLE001
        # Fall back: parse name from class name if the property needs __init__
        source_name = cls.__name__.lower().replace("source", "")

    SOURCES[source_name] = cls
    return cls


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class SourceError(RuntimeError):
    """Raised when a source encounters an unrecoverable fetch or parse error."""


class SourceSkipped(RuntimeError):
    """Raised when a source skips itself due to a missing configuration key."""
