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


def _suggest_aliases() -> None:
    """
    Read-only: collect canonical skills NOT in alias map, ask LLM for proposals.

    Makes exactly ONE call to llm.complete_json.  Prints warning, then
    proposals as ready-to-paste YAML.
    """
    import json
    from collections import Counter
    from edgedash.config import Config
    from edgedash.storage import Storage
    from edgedash.llm import complete_json

    config  = Config.from_env()
    existing_aliases: dict = getattr(config, "skill_aliases", {}) or {}
    storage = Storage(db_path=config.db_path)
    storage.init()

    # Collect all canonical skill strings from DB
    try:
        with storage._connect() as conn:
            rows = conn.execute("SELECT result_json FROM extraction_cache").fetchall()
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
                canon = canonical(skill.strip(), existing_aliases)
                if canon:
                    counter[canon] += 1
        for skill in data.get("nice_to_have", []):
            if skill and isinstance(skill, str):
                canon = canonical(skill.strip(), existing_aliases)
                if canon:
                    counter[canon] += 1

    # Filter: only keep skills NOT already covered by aliases
    # A skill is "covered" if it appears as a KEY OR as a VALUE in the alias map
    covered = set(existing_aliases.keys()) | set(existing_aliases.values())
    uncovered = {
        skill: cnt
        for skill, cnt in counter.items()
        if skill not in covered
    }

    if not uncovered:
        print("All canonical skills in the DB are already covered by skill_aliases.")
        return

    # Prepare top 15 uncovered for LLM
    top_uncovered = sorted(uncovered.items(), key=lambda x: -x[1])[:15]
    skill_list = "\n".join(f"- {s} (count: {c})" for s, c in top_uncovered)

    # Build prompt
    prompt = f"""You are a career data expert helping merge duplicate skill names.

We have the following skill strings from job listings (already canonicalised, sorted by frequency):
{skill_list}

These are NOT yet covered by our alias map:
{json.dumps(list(uncovered.keys())[:20], indent=2)}

Your task: propose groups of these skills that clearly represent the same thing.
For each group, provide:
- canonical: the preferred canonical name
- variants: list of other strings that should map to this canonical
- confidence: "high" if you're certain, "low" if uncertain

Return a JSON list like:
[
  {{"canonical": "python", "variants": ["py", "python3"], "confidence": "high"}},
  {{"canonical": "aws", "variants": ["amazon web services"], "confidence": "low"}}
]

Rules:
- Only propose merges you are confident about
- Do NOT merge Node.js and JavaScript (they are different)
- Do NOT merge different skills just because they share some characters
- If you cannot find any good groupings, return an empty list []
- Each variant should appear in only ONE proposal
"""

    schema = {
        "required": [],
        "types": {"list": list},
    }

    try:
        proposals = complete_json(prompt, schema)
    except Exception as exc:
        print(f"ERROR calling LLM: {exc}")
        return

    if not proposals:
        print("No alias suggestions found.")
        return

    # Print warning
    print("\n" + "=" * 70)
    print("⚠️  WARNING: Suggestions require human review")
    print("⚠️  Merging distinct skills is worse than leaving them separate.")
    print("=" * 70 + "\n")

    # Check for CONFLICT with existing aliases
    existing_keys = set(existing_aliases.keys())
    existing_vals = set(existing_aliases.values())

    print("--- Proposed aliases (ready to paste into config.yaml) ---\n")
    print("skill_aliases:")

    for prop in proposals:
        canon = prop.get("canonical", "")
        variants = prop.get("variants", [])
        confidence = prop.get("confidence", "low")

        # Flag conflicts
        conflicts = []
        if canon in existing_keys:
            conflicts.append(f"canonical '{canon}' already a key in aliases")
        if canon in existing_vals:
            conflicts.append(f"canonical '{canon}' already a value in aliases")
        for v in variants:
            if v in existing_keys:
                conflicts.append(f"variant '{v}' already a key in aliases")
            if v in existing_vals:
                conflicts.append(f"variant '{v}' already a value in aliases")

        if conflicts:
            print(f"  # ⚠️  CONFLICT: {'; '.join(conflicts)}")

        conf_mark = "  # confidence: high" if confidence == "high" else "  # confidence: low (review carefully)"
        print(f"  {canon}: {canon}")
        for v in variants:
            print(f"  {v}: {canon}")
        print(f"{conf_mark}\n")

    print("--- End of proposals ---\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(prog="python -m edgedash.skills")
    parser.add_argument(
        "--audit",
        action="store_true",
        help="Print a read-only audit of raw skill strings in the extraction cache.",
    )
    parser.add_argument(
        "--suggest-aliases",
        action="store_true",
        help="Query LLM for alias suggestions for uncovered skills.",
    )
    args = parser.parse_args()
    if args.audit:
        _audit()
    elif args.suggest_aliases:
        _suggest_aliases()
    else:
        parser.print_help()
