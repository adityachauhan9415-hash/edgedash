"""
edgedash.skills
===============
Deterministic skill-name canonicalisation.  No LLM, no network.

Public API
----------
    canonical(raw: str, aliases: dict) -> str

CLI
---
    python -m edgedash.skills --audit

The audit reads the existing extraction_cache via Storage and prints the
40 most common raw skill strings with their canonical forms, plus all
singletons.
"""
from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

_PARENS_RE = re.compile(r"\s*\([^)]*\)")   # matches " (eks)" etc.
_PUNCT_RE  = re.compile(r"^[\s\-_/.,;:]+|[\s\-_/.,;:]+$")  # leading/trailing
_WS_RE     = re.compile(r"\s+")


def canonical(raw: str, aliases: dict) -> str:
    """
    Return the canonical form of a skill name.

    Steps (in order):
    1. Lowercase.
    2. Strip surrounding whitespace and punctuation.
    3. Remove parenthetical qualifiers: "kubernetes (eks)" -> "kubernetes".
    4. Collapse internal whitespace.
    5. Apply alias map (exact match on the normalised form).
    6. Empty input at any step -> return "".

    Parameters
    ----------
    raw:
        The raw skill string, e.g. "  Kubernetes (EKS) ".
    aliases:
        Dict mapping normalised raw -> canonical name.
        Keys must already be lowercase and stripped (config.yaml values are).

    Returns
    -------
    str
        Canonical skill name, or "" for empty/whitespace-only input.
    """
    if not raw:
        return ""

    s = raw.lower()
    s = _PARENS_RE.sub("", s)          # remove parentheticals
    s = _PUNCT_RE.sub("", s)           # strip leading/trailing punctuation
    s = _WS_RE.sub(" ", s).strip()     # collapse internal whitespace

    if not s:
        return ""

    return aliases.get(s, s)


# ---------------------------------------------------------------------------
# CLI: --audit
# ---------------------------------------------------------------------------

def _audit() -> None:
    """
    Read-only audit of raw skill strings in the extraction cache.

    Prints:
    - 40 most common raw skill strings with count and canonical form.
    - All raw strings that appear exactly once.
    """
    import os, json
    from collections import Counter
    from edgedash.config import Config
    from edgedash.storage import Storage

    config  = Config.from_env()
    aliases: dict = getattr(config, "skill_aliases", {}) or {}
    storage = Storage(db_path=config.db_path)
    storage.init()

    # Read all extraction_cache rows via storage._connect() (rule: never
    # direct sqlite3 outside storage.py — we use the internal context manager
    # since Storage exposes no public cache-scan method).
    try:
        with storage._connect() as conn:
            rows = conn.execute(
                "SELECT result_json FROM extraction_cache"
            ).fetchall()
    except Exception as exc:
        print(f"ERROR reading extraction_cache: {exc}")
        return

    if not rows:
        print("extraction_cache is empty — run at least one scoring cycle first.")
        return

    counter: Counter = Counter()
    for row in rows:
        try:
            data = json.loads(row["result_json"])
        except (ValueError, KeyError):
            continue
        for skill in data.get("required_skills", []):
            if skill and isinstance(skill, str):
                counter[skill.strip()] += 1
        for skill in data.get("nice_to_have", []):
            if skill and isinstance(skill, str):
                counter[skill.strip()] += 1

    if not counter:
        print("No skill strings found in extraction_cache.")
        return

    total_unique = len(counter)
    total_occurrences = sum(counter.values())
    print(f"Unique raw skill strings : {total_unique}")
    print(f"Total occurrences        : {total_occurrences}")
    print(f"Cache rows scanned       : {len(rows)}\n")

    top40 = counter.most_common(40)
    print("── Top 40 raw skill strings ──────────────────────────────────")
    print(f"  {'Count':>5}  {'Canonical':<30}  Raw")
    print(f"  {'─'*5}  {'─'*30}  {'─'*40}")
    for raw_skill, count in top40:
        canon = canonical(raw_skill, aliases)
        print(f"  {count:>5}  {canon:<30}  {raw_skill}")

    singletons = [s for s, c in counter.items() if c == 1]
    print(f"\n── Singletons ({len(singletons)} raw strings appearing exactly once) ──")
    for s in sorted(singletons)[:80]:   # cap display at 80
        canon = canonical(s, aliases)
        marker = "  (alias)" if canon != s else ""
        print(f"  {s}{marker}")
    if len(singletons) > 80:
        print(f"  ... and {len(singletons) - 80} more")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(prog="python -m edgedash.skills")
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Print a read-only audit of raw skill strings in the extraction cache.",
    )
    args = parser.parse_args()
    if args.audit:
        _audit()
    else:
        parser.print_help()
