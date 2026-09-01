"""
edgedash.llm
============
The single door to any language model (steering rule 15).

Public interface
----------------
    complete_json(prompt, schema, *, max_retries=1) -> dict

Rules enforced here
-------------------
Rule 15: Only this file imports any LLM SDK or makes LLM HTTP calls.
         Provider and model come from Config only — never hardcoded.
         Calling code must never name a model or provider directly.
         Rate-limited: default 1 req/s floor, 15 RPM cap (free-tier safe).

Rule 16: complete_json returns a validated dict. It has no knowledge of
         scores, weights, or rankings. That arithmetic lives in the Scorer.

Rule 17: Every response is validated against the caller-supplied schema
         before being returned. On failure the prompt is retried once with
         the exact validation error appended. After the retry, LLMError
         is raised for that listing only — the cycle is not affected.

Supported providers
-------------------
  "gemini"  — Google Gemini REST API (free tier: 15 RPM on Flash models).
              Requires GEMINI_API_KEY in environment.
              Default model: gemini-2.0-flash

  "ollama"  — Local Ollama server (http://localhost:11434 by default).
              No API key required. Set OLLAMA_BASE_URL to override host.
              Default model: llama3

Adding a new provider: implement _call_<name>(prompt, config) -> str and
add it to _PROVIDERS. complete_json does not need to change (rule 4).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import TYPE_CHECKING, Any

import requests as _requests

if TYPE_CHECKING:
    from edgedash.config import Config


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class LLMError(RuntimeError):
    """Raised when a model call fails after all retries."""


# ---------------------------------------------------------------------------
# Rate-limit state  (module-level, thread-safe)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_last_call_at: float = 0.0
_calls_this_minute: list[float] = []


def _wait_for_rate_limit(config: "Config") -> None:
    """
    Block until both constraints are satisfied:
      - At least config.llm_rps seconds since the previous call.
      - Fewer than config.llm_rpm calls in the last 60 seconds.
    """
    with _lock:
        global _last_call_at, _calls_this_minute
        now = time.monotonic()

        # per-second floor
        elapsed = now - _last_call_at
        if elapsed < config.llm_rps:
            time.sleep(config.llm_rps - elapsed)
            now = time.monotonic()

        # per-minute cap
        cutoff = now - 60.0
        _calls_this_minute = [t for t in _calls_this_minute if t >= cutoff]
        if len(_calls_this_minute) >= config.llm_rpm:
            wait = (_calls_this_minute[0] + 60.0) - now + 0.05
            if wait > 0:
                time.sleep(wait)
            now = time.monotonic()
            _calls_this_minute = [t for t in _calls_this_minute if t >= now - 60.0]

        _last_call_at = time.monotonic()
        _calls_this_minute.append(_last_call_at)


# ---------------------------------------------------------------------------
# Response cleaning
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _strip_fences(text: str) -> str:
    """
    Remove markdown code fences and surrounding prose so json.loads
    has a clean string to parse.

    Strategy (in order):
      1. If a ```json ... ``` or ``` ... ``` block exists, extract its body.
      2. Otherwise find the first '{' and last '}' and slice to that range.
    """
    text = text.strip()
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    # Fall back: find outermost braces
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def _validate(data: Any, schema: dict) -> list[str]:
    """
    Validate `data` against a lightweight schema dict.

    Schema format (intentionally simple — no jsonschema dependency needed):
        {
            "required": ["field1", "field2"],
            "types":    {"field1": str, "field2": (int, float)},
        }

    Returns a list of error strings (empty = valid).
    """
    errors: list[str] = []

    if not isinstance(data, dict):
        return [f"expected a JSON object, got {type(data).__name__}"]

    for field in schema.get("required", []):
        if field not in data:
            errors.append(f"missing required field '{field}'")

    for field, expected_types in schema.get("types", {}).items():
        if field in data and not isinstance(data[field], expected_types):
            actual = type(data[field]).__name__
            if isinstance(expected_types, tuple):
                expected_str = " | ".join(t.__name__ for t in expected_types)
            else:
                expected_str = expected_types.__name__
            errors.append(
                f"field '{field}' must be {expected_str}, got {actual}"
            )

    return errors


# ---------------------------------------------------------------------------
# Provider: Gemini
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str, config: "Config") -> str:
    """
    POST to the Gemini generateContent REST endpoint and return raw text.
    Handles 429 / quota errors with exponential back-off (3 attempts).
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise LLMError(
            "GEMINI_API_KEY is not set. "
            "Add it to .env to use the gemini provider."
        )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{config.llm_model}:generateContent"
    )
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }
    timeout = int(os.getenv("EDGEDASH_HTTP_TIMEOUT", "30"))

    last_err: Exception | None = None
    for attempt in range(1, 4):                    # 3 attempts
        resp = _requests.post(url, json=body, headers=headers, timeout=timeout)

        if resp.status_code == 429:
            # Honour Retry-After if present, otherwise exponential back-off
            retry_after = resp.headers.get("Retry-After")
            wait = float(retry_after) if retry_after else (2 ** attempt)
            print(f"  [llm] Gemini 429 — backing off {wait:.0f}s (attempt {attempt}/3)")
            time.sleep(wait)
            last_err = LLMError(f"Gemini 429 after attempt {attempt}")
            continue

        if not resp.ok:
            raise LLMError(
                f"Gemini HTTP {resp.status_code}: {resp.text[:300]}"
            )

        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as exc:
            raise LLMError(
                f"Unexpected Gemini response shape: {str(data)[:300]}"
            ) from exc

    raise LLMError(
        f"Gemini quota exceeded after 3 attempts"
    ) from last_err


# ---------------------------------------------------------------------------
# Provider: Ollama  (local, no key required)
# ---------------------------------------------------------------------------

def _call_ollama(prompt: str, config: "Config") -> str:
    """
    POST to a local Ollama server's /api/generate endpoint.

    Default base URL: http://localhost:11434
    Override: OLLAMA_BASE_URL env var.
    Handles 429 / overload with exponential back-off (3 attempts).
    """
    base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
    url = f"{base_url}/api/generate"
    body = {
        "model":  config.llm_model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
    }
    timeout = int(os.getenv("EDGEDASH_HTTP_TIMEOUT", "60"))

    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            resp = _requests.post(url, json=body, timeout=timeout)
        except _requests.exceptions.ConnectionError as exc:
            raise LLMError(
                f"Cannot connect to Ollama at {base_url}. "
                "Is `ollama serve` running?"
            ) from exc

        if resp.status_code == 429:
            wait = 2 ** attempt
            print(f"  [llm] Ollama 429 — backing off {wait}s (attempt {attempt}/3)")
            time.sleep(wait)
            last_err = LLMError(f"Ollama 429 after attempt {attempt}")
            continue

        if not resp.ok:
            raise LLMError(f"Ollama HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        try:
            return data["response"]
        except KeyError as exc:
            raise LLMError(
                f"Unexpected Ollama response shape: {str(data)[:300]}"
            ) from exc

    raise LLMError("Ollama overloaded after 3 attempts") from last_err


# ---------------------------------------------------------------------------
# Provider dispatch table
# ---------------------------------------------------------------------------
# To add a provider: implement _call_<name>(prompt, config) -> str
# and add one entry here. complete_json does not change.

_PROVIDERS: dict[str, Any] = {
    "gemini": _call_gemini,
    "ollama": _call_ollama,
}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def complete_json(
    prompt: str,
    schema: dict,
    *,
    max_retries: int = 1,
) -> dict:
    """
    Send `prompt` to the configured LLM, parse the response as JSON,
    validate it against `schema`, and return a validated dict.

    This is the ONLY function the rest of the codebase may call for LLM
    access (steering rule 15). No other module may import an LLM SDK or
    make LLM HTTP calls.

    Parameters
    ----------
    prompt:
        The full user prompt.  The caller is responsible for including any
        system context, job description, and extraction instructions.
    schema:
        Validation schema passed to _validate(). Keys:
          "required": list of field names that must be present.
          "types":    dict mapping field name -> type or tuple of types.
    max_retries:
        How many times to retry after a parse/validation failure.
        Default 1 (one retry = two total attempts) — satisfies rule 17.

    Returns
    -------
    dict
        A validated JSON object extracted from the model response.

    Raises
    ------
    LLMError
        If the provider is unknown, the API key is absent, the HTTP call
        fails, or parse/validation still fails after all retries.
    """
    # Resolve config lazily so this module doesn't need a global config ref
    from edgedash.config import Config
    config = Config.from_env()

    provider_name = (config.llm_provider or "gemini").lower().strip()
    provider_fn = _PROVIDERS.get(provider_name)
    if provider_fn is None:
        known = ", ".join(sorted(_PROVIDERS))
        raise LLMError(
            f"Unknown LLM provider '{provider_name}'. "
            f"Set EDGEDASH_LLM_PROVIDER to one of: {known}"
        )

    current_prompt = prompt
    last_error: str = ""

    for attempt in range(max_retries + 1):          # attempt 0, then retries
        # Enforce rate limits before every transport call (rule 15)
        _wait_for_rate_limit(config)

        # --- Transport ---
        raw_text = provider_fn(current_prompt, config)

        # --- Strip fences / surrounding prose (rule 17) ---
        cleaned = _strip_fences(raw_text)

        # --- Parse JSON (rule 17: never bare json.loads) ---
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            last_error = f"JSON parse error: {exc} — raw: {cleaned[:200]}"
            if attempt < max_retries:
                current_prompt = (
                    f"{prompt}\n\n"
                    f"Your previous response could not be parsed as JSON.\n"
                    f"Error: {last_error}\n"
                    f"Respond with valid JSON only, no markdown fences."
                )
                continue
            raise LLMError(
                f"JSON parse failed after {attempt + 1} attempt(s): {last_error}"
            )

        # --- Schema validation (rule 17) ---
        errors = _validate(data, schema)
        if errors:
            last_error = "; ".join(errors)
            if attempt < max_retries:
                current_prompt = (
                    f"{prompt}\n\n"
                    f"Your previous response failed schema validation.\n"
                    f"Errors: {last_error}\n"
                    f"Respond with valid JSON only, correcting the errors above."
                )
                continue
            raise LLMError(
                f"Schema validation failed after {attempt + 1} attempt(s): {last_error}"
            )

        return data

    # Should not be reached, but satisfies type checker
    raise LLMError(f"complete_json exhausted all attempts. Last error: {last_error}")


# ---------------------------------------------------------------------------
# CLI: python -m edgedash.llm --check
# ---------------------------------------------------------------------------

def _cli_check() -> None:
    """
    Run a minimal smoke-test against the configured provider and model.
    Prints provider, model, and whether the check succeeded.
    Exits 0 on success, 1 on failure.
    """
    import sys
    from edgedash.config import Config

    config = Config.from_env()
    print(f"Provider : {config.llm_provider}")
    print(f"Model    : {config.llm_model}")

    test_prompt = (
        'Respond with exactly this JSON object and nothing else: {"ok": true}'
    )
    test_schema = {"required": ["ok"], "types": {"ok": bool}}

    try:
        result = complete_json(test_prompt, test_schema)
        if result.get("ok") is True:
            print("Check    : OK")
            sys.exit(0)
        else:
            print(f"Check    : FAILED (unexpected response: {result})")
            sys.exit(1)
    except LLMError as exc:
        print(f"Check    : FAILED ({exc})")
        sys.exit(1)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(prog="python -m edgedash.llm")
    parser.add_argument("--check", action="store_true",
                        help="Smoke-test the configured LLM provider.")
    args = parser.parse_args()
    if args.check:
        _cli_check()
    else:
        parser.print_help()
