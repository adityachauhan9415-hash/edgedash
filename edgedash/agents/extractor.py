from __future__ import annotations

import hashlib

from edgedash.config import Config
from edgedash.llm import complete_json
from edgedash.storage import Storage


EXTRACTION_SCHEMA: dict = {
    "required": [
        "required_skills",
        "nice_to_have",
        "seniority",
        "years_required",
        "remote_ok",
    ],
    "types": {
        "required_skills": list,
        "nice_to_have": list,
        "seniority": str,
        "years_required": (int, type(None)),
        "remote_ok": (bool, type(None)),
    },
}

_VALID_SENIORITY = {"junior", "mid", "senior", "lead", "unknown"}


def _description_hash(description: str) -> str:
    return hashlib.sha256(description.encode("utf-8")).hexdigest()


def _prompt(description: str) -> str:
    return f"""
Read the job description below and extract facts only.

Do not infer.
Do not guess.
Do not evaluate any candidate.
Do not score or rank anything.

If the listing does not explicitly state something:
- use an empty list for skill lists
- use null for years_required or remote_ok
- use "unknown" for seniority

Return JSON with EXACTLY these fields:
- required_skills: list of skills explicitly required
- nice_to_have: list of skills explicitly preferred but not required
- seniority: "junior", "mid", "senior", "lead", or "unknown"
- years_required: integer or null
- remote_ok: boolean or null

JOB DESCRIPTION:
{description}
""".strip()


def _lower_skills(values: list) -> list[str]:
    result: list[str] = []
    for value in values:
        skill = str(value).strip().lower()
        if skill and skill not in result:
            result.append(skill)
    return result


def extract(listing: dict) -> dict:
    description = str(listing.get("description") or "").strip()
    if not description:
        raise ValueError("Listing has no job description to extract.")

    description_hash = _description_hash(description)

    config = Config.from_env()
    storage = Storage(config.db_path)
    storage.init()

    cached = storage.get_extraction_cache(description_hash)
    if cached is not None:
        return cached

    raw = complete_json(
        _prompt(description),
        EXTRACTION_SCHEMA,
        max_retries=1,
    )

    seniority = str(raw["seniority"]).strip().lower()
    if seniority not in _VALID_SENIORITY:
        seniority = "unknown"

    result = {
        "required_skills": _lower_skills(raw["required_skills"]),
        "nice_to_have": _lower_skills(raw["nice_to_have"]),
        "seniority": seniority,
        "years_required": raw["years_required"],
        "remote_ok": raw["remote_ok"],
    }

    storage.set_extraction_cache(description_hash, result)
    return result