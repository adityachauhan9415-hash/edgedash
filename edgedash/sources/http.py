"""
edgedash.sources.http
=====================
The ONLY place in the project that performs HTTP requests (steering rule 11).

All network calls go through get_json(), which enforces:
  - A 10-second timeout (overridable via EDGEDASH_HTTP_TIMEOUT env var)
  - 2 retry attempts with exponential back-off (1 s, 2 s)
  - A descriptive User-Agent header
  - Raises SourceError with a clear message on failure

No other module may call requests.get(), requests.post(), or any equivalent
directly. If you need HTTP elsewhere, add a helper here and import it.
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

from edgedash.sources.base import SourceError


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_TIMEOUT: int = 10          # seconds
_RETRY_ATTEMPTS: int = 2
_BACKOFF_BASE: float = 1.0          # seconds; doubles on each attempt

_USER_AGENT = (
    "EdgeDash/1.0 (autonomous career intelligence agent; "
    "https://github.com/user/edgedash)"
)


def _timeout() -> int:
    """Read timeout from env, falling back to 10 s."""
    try:
        return int(os.getenv("EDGEDASH_HTTP_TIMEOUT", str(_DEFAULT_TIMEOUT)))
    except ValueError:
        return _DEFAULT_TIMEOUT


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------

def get_json(
    url: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """
    Perform a GET request and return the parsed JSON body.

    Parameters
    ----------
    url:
        The full URL to request.
    params:
        Optional query-string parameters.
    headers:
        Optional additional HTTP headers (merged with defaults).

    Returns
    -------
    Any
        The JSON-decoded response body.

    Raises
    ------
    SourceError
        If all retry attempts fail, or if the response cannot be parsed as JSON.
    """
    merged_headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
    if headers:
        merged_headers.update(headers)

    last_exc: Exception | None = None

    for attempt in range(1, _RETRY_ATTEMPTS + 2):  # attempts: 1, 2, 3
        backoff = _BACKOFF_BASE * (2 ** (attempt - 1))  # 1 s, 2 s, 4 s …

        try:
            response = requests.get(
                url,
                params=params,
                headers=merged_headers,
                timeout=_timeout(),
            )
            response.raise_for_status()
            # Force UTF-8 — the Arbeitnow API returns UTF-8 but requests may
            # auto-detect the wrong encoding from the Content-Type header.
            response.encoding = "utf-8"
            return response.json()

        except requests.exceptions.Timeout as exc:
            last_exc = exc
            msg = f"[attempt {attempt}] GET {url} timed out after {_timeout()} s"

        except requests.exceptions.HTTPError as exc:
            last_exc = exc
            status = exc.response.status_code if exc.response is not None else "?"
            msg = f"[attempt {attempt}] GET {url} returned HTTP {status}"

            # Honour Retry-After if present (steering rule 14)
            if exc.response is not None:
                retry_after = exc.response.headers.get("Retry-After")
                if retry_after:
                    try:
                        backoff = max(backoff, float(retry_after))
                    except ValueError:
                        pass

        except requests.exceptions.RequestException as exc:
            last_exc = exc
            msg = f"[attempt {attempt}] GET {url} failed: {exc}"

        except ValueError as exc:
            # JSON decode error — no point retrying
            raise SourceError(
                f"GET {url} succeeded but response is not valid JSON: {exc}"
            ) from exc

        # Log and wait before retrying (don't sleep after the last attempt)
        if attempt <= _RETRY_ATTEMPTS:
            print(f"  [http] {msg} — retrying in {backoff:.0f} s …")
            time.sleep(backoff)
        else:
            print(f"  [http] {msg} — no more retries.")

    raise SourceError(
        f"GET {url} failed after {_RETRY_ATTEMPTS + 1} attempts. "
        f"Last error: {last_exc}"
    ) from last_exc
