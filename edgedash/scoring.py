"""
edgedash.scoring
================
Pure deterministic scoring arithmetic.  NO LLM calls, NO network, NO imports
from edgedash.llm.  Every function here is a side-effect-free computation.

Public API
----------
    score_listing(listing, facts, config) -> dict
    build_reason(components, facts, config) -> str

The Scorer agent calls these after extract() returns facts.  The model never
sees the weights (rule 16); this file never calls the model (rule 16).

Scoring formula
---------------
Four components, each normalised to [0.0, 1.0]:

1. skill_match  (default weight 0.45)
   ----------------------------------------
   Let R = required_skills from facts,  P = config.skills (user profile),
       N = nice_to_have from facts.

   All comparisons are lower-cased.

   matched_req  = count(r in R if r in P)
   matched_nice = count(n in N if n in P)
   req_count    = len(R)
   nice_count   = len(N)

   When req_count > 0:
       req_frac  = matched_req / req_count
       nice_frac = matched_nice / nice_count  if nice_count > 0 else 0.0
       component = min(1.0, req_frac + nice_frac / 3.0)

   When req_count == 0  (no required skills stated):
       -- We cannot penalise for unstated requirements, but we also
       -- cannot fully reward a listing that specifies nothing.
       -- Neutral base of 0.5; nice-to-have can push upward.
       nice_frac = matched_nice / nice_count  if nice_count > 0 else 0.0
       component = min(1.0, 0.5 + nice_frac / 3.0)

2. seniority_fit  (default weight 0.25)
   ----------------------------------------
   Ordered bands: junior(0) < mid(1) < senior(2) < lead(3)

   exact match  -> 1.0
   1 band away  -> 0.6
   2 bands away -> 0.25
   3+ bands away -> 0.0
   "unknown" or target not in bands -> 0.5 (neutral)

3. location_fit  (default weight 0.15)
   ----------------------------------------
   facts["remote_ok"] is True              -> 1.0
   listing location contains config.city   -> 1.0  (case-insensitive)
   both remote_ok null/False AND location
     is empty / None / "remote"            -> 0.5
   clearly elsewhere, remote_ok not true   -> 0.1

4. recency  (default weight 0.15)
   ----------------------------------------
   days_old = (utcnow - posted_at).days
   component = max(0.0, 1.0 - days_old / 30.0)
   posted_at null or unparseable            -> 0.5

Final score:
   weighted_sum = sum(component_i * weight_i)
   score = int(round(clamp(weighted_sum * 100, 0, 100)))
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from edgedash.config import Config

# ---------------------------------------------------------------------------
# Seniority band ordering
# ---------------------------------------------------------------------------

_SENIORITY_BANDS: dict[str, int] = {
    "junior": 0,
    "mid":    1,
    "senior": 2,
    "lead":   3,
}

# ---------------------------------------------------------------------------
# Internal component calculators
# ---------------------------------------------------------------------------

def _skill_match(facts: dict, profile_skills: list[str]) -> tuple[float, dict]:
    """
    Compute the skill_match component and return metadata for reason building.

    Returns (component_value, metadata_dict).
    metadata_dict keys:
        matched_req, total_req, matched_nice, total_nice, missing_required
    """
    required: list[str] = [s.lower() for s in (facts.get("required_skills") or [])]
    nice:     list[str] = [s.lower() for s in (facts.get("nice_to_have")     or [])]
    profile:  set[str]  = {s.lower() for s in profile_skills}

    req_count  = len(required)
    nice_count = len(nice)

    matched_req  = sum(1 for r in required if r in profile)
    matched_nice = sum(1 for n in nice     if n in profile)

    missing_required = [r for r in required if r not in profile]

    if req_count > 0:
        req_frac  = matched_req / req_count
        nice_frac = matched_nice / nice_count if nice_count > 0 else 0.0
        component = min(1.0, req_frac + nice_frac / 3.0)
    else:
        # No required skills stated — neutral base, nice-to-have can lift it.
        nice_frac = matched_nice / nice_count if nice_count > 0 else 0.0
        component = min(1.0, 0.5 + nice_frac / 3.0)

    meta = {
        "matched_req":      matched_req,
        "total_req":        req_count,
        "matched_nice":     matched_nice,
        "total_nice":       nice_count,
        "missing_required": missing_required,
    }
    return component, meta


def _seniority_fit(facts: dict, target_seniority: str) -> float:
    """
    Compute seniority_fit component.

    "unknown" job seniority or unrecognised target -> neutral 0.5.
    """
    job_seniority    = (facts.get("seniority") or "").lower().strip()
    target_lower     = (target_seniority or "").lower().strip()

    job_idx    = _SENIORITY_BANDS.get(job_seniority)
    target_idx = _SENIORITY_BANDS.get(target_lower)

    if job_idx is None or target_idx is None:
        # "unknown" or unrecognised value on either side
        return 0.5

    distance = abs(job_idx - target_idx)
    if distance == 0:
        return 1.0
    elif distance == 1:
        return 0.6
    elif distance == 2:
        return 0.25
    else:
        return 0.0


def _location_fit(listing: dict, facts: dict, target_city: str) -> float:
    """
    Compute location_fit component.

    Priority:
    1. remote_ok True -> 1.0
    2. listing location matches target_city -> 1.0
    3. null/empty/ambiguous -> 0.5
    4. clearly elsewhere and not remote -> 0.1
    """
    remote_ok = facts.get("remote_ok")

    if remote_ok is True:
        return 1.0

    location = (listing.get("location") or "").strip().lower()
    city_lower = (target_city or "").strip().lower()

    if city_lower and location and city_lower in location:
        return 1.0

    # Empty / null / just "remote" text with remote_ok=None
    if not location or location in {"remote", "n/a", "unknown"}:
        return 0.5

    # Has a location but doesn't match and remote_ok is not True
    # (includes remote_ok=False and remote_ok=None with a real location)
    return 0.1


def _recency(listing: dict) -> float:
    """
    Compute recency component.  posted_at null or unparseable -> 0.5.
    """
    posted_at_str = (listing.get("posted_at") or "").strip()
    if not posted_at_str:
        return 0.5

    try:
        # Accept ISO-8601 with or without timezone
        if posted_at_str.endswith("Z"):
            posted_at_str = posted_at_str[:-1] + "+00:00"
        posted_at = datetime.fromisoformat(posted_at_str)
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        days_old = (now - posted_at).days

        return max(0.0, 1.0 - days_old / 30.0)
    except (ValueError, OverflowError, OSError):
        return 0.5


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def score_listing(
    listing: dict,
    facts: dict,
    config: "Config",
) -> dict:
    """
    Compute a deterministic score for a listing given extracted facts.

    Parameters
    ----------
    listing:
        Raw listing row from Storage (needs 'location', 'posted_at').
    facts:
        Validated extraction result from edgedash.agents.extractor.extract().
    config:
        Project config -- weights and profile skills come from here.

    Returns
    -------
    dict with keys:
        "score"      : int  0-100
        "reason"     : str  human-readable, code-generated (never LLM text)
        "components" : dict with individual component values and weights
    """
    profile_skills: list[str] = list(getattr(config, "skills", []) or [])
    target_seniority: str     = getattr(config, "target_seniority", "senior") or "senior"
    target_city: str          = getattr(config, "city", "") or ""

    # Weights from config with documented defaults (rule 16: never hardcoded
    # without a config path)
    weights = _get_weights(config)

    # --- Compute components ---
    sm_value, sm_meta = _skill_match(facts, profile_skills)
    sf_value          = _seniority_fit(facts, target_seniority)
    lf_value          = _location_fit(listing, facts, target_city)
    rc_value          = _recency(listing)

    components = {
        "skill_match":    {"value": sm_value, "weight": weights["skill_match"],    **sm_meta},
        "seniority_fit":  {"value": sf_value, "weight": weights["seniority_fit"]},
        "location_fit":   {"value": lf_value, "weight": weights["location_fit"]},
        "recency":        {
            "value": rc_value,
            "weight": weights["recency"],
            "_posted_at_raw": (listing.get("posted_at") or "").strip() or None,
        },
    }

    # --- Weighted sum -> 0-100 int ---
    weighted_sum = (
        sm_value * weights["skill_match"]
        + sf_value * weights["seniority_fit"]
        + lf_value * weights["location_fit"]
        + rc_value * weights["recency"]
    )
    score = int(round(max(0.0, min(100.0, weighted_sum * 100))))

    reason = build_reason(components, facts, config)

    return {
        "score":      score,
        "reason":     reason,
        "components": components,
    }


def build_reason(
    components: dict,
    facts: dict,
    config: "Config",
) -> str:
    """
    Generate a compact, human-readable reason string FROM score components.

    This function is pure Python arithmetic and string formatting.
    It never calls an LLM (rule 19).

    Example output:
        "4/6 required skills · seniority fits · remote · posted 2d ago · gap: kubernetes, spark"
    """
    parts: list[str] = []

    # --- Skill summary ---
    sm = components.get("skill_match", {})
    total_req   = sm.get("total_req", 0)
    matched_req = sm.get("matched_req", 0)
    missing     = sm.get("missing_required", [])

    if total_req == 0:
        parts.append("no required skills stated")
    else:
        parts.append(f"{matched_req}/{total_req} required skills")

    # --- Seniority ---
    sf_value = components.get("seniority_fit", {}).get("value", 0.5)
    job_seniority = (facts.get("seniority") or "unknown").lower()
    target_seniority = (getattr(config, "target_seniority", "senior") or "senior").lower()

    if job_seniority == "unknown":
        parts.append("seniority unknown")
    elif sf_value >= 1.0:
        parts.append("seniority fits")
    elif sf_value >= 0.6:
        parts.append(f"seniority close ({job_seniority})")
    else:
        parts.append(f"seniority mismatch ({job_seniority} vs {target_seniority})")

    # --- Location ---
    lf_value = components.get("location_fit", {}).get("value", 0.5)
    remote_ok = facts.get("remote_ok")
    if remote_ok is True:
        parts.append("remote")
    elif lf_value >= 1.0:
        parts.append("location match")
    elif lf_value >= 0.5:
        parts.append("location unclear")
    else:
        parts.append("not remote/local")

    # --- Recency ---
    rc_value = components.get("recency", {}).get("value", 0.5)
    # Use the listing's posted_at directly to distinguish "unknown" from real 15d
    # (rc_value=0.5 is both the sentinel for unknown AND the value for ~15 days old)
    # We check the source listing which is not available here, so we use a small
    # epsilon: if the value is exactly 0.5 and comes from rounding we can't tell.
    # Instead, build_reason receives facts which has no posted_at; use rc_value
    # sentinel convention: scoring._recency returns exactly 0.5 for null/unparseable.
    # Real 15-day posts also yield 0.5, so we disambiguate via the listing dict
    # passed through components metadata if present, else fall back to label.
    _listing_posted = components.get("recency", {}).get("_posted_at_raw")
    if _listing_posted is None and rc_value == 0.5:
        # Could be null posted_at OR exactly 15 days old; label conservatively
        parts.append("posted date unknown")
    elif rc_value >= 1.0:
        parts.append("posted today")
    elif rc_value > 0.0:
        days = round((1.0 - rc_value) * 30)
        parts.append(f"posted {max(1, days)}d ago")
    else:
        parts.append("posted 30+ d ago")

    # --- Skill gap ---
    if missing:
        gap_str = ", ".join(missing[:5])  # cap at 5 for readability
        if len(missing) > 5:
            gap_str += f" (+{len(missing)-5} more)"
        parts.append(f"gap: {gap_str}")
    else:
        parts.append("gap: none")

    return " · ".join(parts)


# ---------------------------------------------------------------------------
# Weight helper
# ---------------------------------------------------------------------------

def _get_weights(config: "Config") -> dict[str, float]:
    """
    Read scoring weights from config, falling back to spec defaults.

    Config fields (all optional, all have defaults):
        score_weight_skill_match   default 0.45
        score_weight_seniority_fit default 0.25
        score_weight_location_fit  default 0.15
        score_weight_recency       default 0.15
    """
    w = {
        "skill_match":   float(getattr(config, "score_weight_skill_match",   0.45)),
        "seniority_fit": float(getattr(config, "score_weight_seniority_fit", 0.25)),
        "location_fit":  float(getattr(config, "score_weight_location_fit",  0.15)),
        "recency":       float(getattr(config, "score_weight_recency",       0.15)),
    }
    # Normalise in case weights were changed and no longer sum to 1.0
    total = sum(w.values())
    if total > 0 and not math.isclose(total, 1.0, rel_tol=1e-6):
        w = {k: v / total for k, v in w.items()}
    return w
