"""
Laterality-Aware Skill Aggregation
====================================
Layer 2 — gameplay aggregation only.

This module is intentionally isolated:
  - Does NOT import from _ema_engine  (Layer 3)
  - Does NOT import from dominant_foot (Layer 1 / display)
  - Called exclusively from the tournament reward orchestrator

Public API
----------
aggregate_lateral_components(skill_entry, right_foot_score, left_foot_score) -> float
update_lateral_component(skill_entry, foot_context, delta) -> dict

Aggregation formula (MVP — neutral_weight = 1.0)
-------------------------------------------------
R  = right_foot_score / (right_foot_score + left_foot_score)
L  = 1 - R
(If both scores are 0 or None → R = L = 0.5, balanced fallback)

weights:
  "right"   → R   (only if component exists)
  "left"    → L   (only if component exists)
  "neutral" → 1.0 (fixed; see NOTE below)

NOTE: neutral_weight is hard-coded at 1.0 for MVP.
If per-preset configurability is needed in the future, promote
neutral_weight to a parameter and wire it from GamePreset.foot_context.

final = Σ(weight_k * level_k) / Σ(weight_k)
        clamped to [MIN_SKILL_VALUE, MAX_SKILL_CAP]

If no lateral_components key exists → no-op, returns existing current_level.
If lateral_components is empty      → no-op, returns existing current_level.
"""

from __future__ import annotations

from typing import Optional

# Reuse the same cap/floor as the EMA engine so aggregated values
# never leave the system-wide valid range.
from app.services.skill_progression._formulas import MIN_SKILL_VALUE, MAX_SKILL_CAP

# MVP constant — see module docstring for upgrade path.
_NEUTRAL_WEIGHT: float = 1.0

_VALID_CONTEXTS = frozenset({"right", "left", "neutral"})


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def update_lateral_component(
    skill_entry: dict,
    foot_context: str,
    delta: float,
) -> dict:
    """Apply an EMA delta to the appropriate lateral component.

    Creates the component bucket on first contact, initialising
    ``level`` from the existing global ``current_level`` so that players
    who accumulated history before this feature was introduced start
    from their actual current level rather than the system baseline.

    Args:
        skill_entry:  One entry from UserLicense.football_skills, e.g.
                      {"current_level": 65.3, "baseline": 60.0, ...}
        foot_context: "right" | "left" | "neutral"
        delta:        EMA delta from compute_single_tournament_skill_delta

    Returns:
        A copy of skill_entry with lateral_components updated.
        The caller is responsible for writing this back to the JSONB
        and for re-running aggregate_lateral_components to refresh
        current_level.
    """
    if foot_context not in _VALID_CONTEXTS:
        foot_context = "neutral"

    entry = dict(skill_entry)
    components: dict = dict(entry.get("lateral_components") or {})

    current_global = float(entry.get("current_level", 60.0))

    if foot_context not in components:
        # First tournament in this context — seed from current global level.
        new_level = _clamp(current_global + delta)
        components[foot_context] = {
            "level":           new_level,
            "total_delta":     delta,
            "tournament_count": 1,
            "last_delta":      delta,
        }
    else:
        bucket = dict(components[foot_context])
        new_level = _clamp(float(bucket["level"]) + delta)
        bucket["level"]            = new_level
        bucket["total_delta"]      = round(float(bucket.get("total_delta", 0.0)) + delta, 4)
        bucket["tournament_count"] = int(bucket.get("tournament_count", 0)) + 1
        bucket["last_delta"]       = delta
        components[foot_context]   = bucket

    entry["lateral_components"] = components
    return entry


def aggregate_lateral_components(
    skill_entry: dict,
    right_foot_score: Optional[float],
    left_foot_score: Optional[float],
) -> float:
    """Compute the aggregated current_level from lateral components.

    Handles all partial-availability cases:
      - Only neutral           → neutral.level
      - Only right             → right.level
      - Only left              → left.level
      - right + neutral        → (R * right + 1.0 * neutral) / (R + 1.0)
      - left  + neutral        → (L * left  + 1.0 * neutral) / (L + 1.0)
      - right + left           → (R * right + L * left) / (R + L)
      - All three              → full formula
      - No / empty components  → existing current_level unchanged

    Args:
        skill_entry:      One entry from UserLicense.football_skills
        right_foot_score: UserLicense.right_foot_score (may be None)
        left_foot_score:  UserLicense.left_foot_score  (may be None)

    Returns:
        Aggregated level, clamped to [MIN_SKILL_VALUE, MAX_SKILL_CAP].
        Returns the existing current_level if no components are present.
    """
    components = skill_entry.get("lateral_components") or {}
    if not components:
        return float(skill_entry.get("current_level", 60.0))

    R, L = _foot_ratios(right_foot_score, left_foot_score)

    weights: dict[str, float] = {}
    if "right"   in components: weights["right"]   = R
    if "left"    in components: weights["left"]    = L
    if "neutral" in components: weights["neutral"] = _NEUTRAL_WEIGHT

    denominator = sum(weights.values())
    if denominator == 0.0:
        return float(skill_entry.get("current_level", 60.0))

    numerator = sum(
        weights[ctx] * float(components[ctx]["level"])
        for ctx in weights
    )
    return _clamp(numerator / denominator)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _foot_ratios(
    right: Optional[float],
    left: Optional[float],
) -> tuple[float, float]:
    """Return (R, L) dominance ratios in [0, 1], summing to 1.0.

    Falls back to (0.5, 0.5) when both scores are absent or zero.
    """
    r = max(float(right or 0.0), 0.0)
    l = max(float(left  or 0.0), 0.0)
    total = r + l
    if total == 0.0:
        return 0.5, 0.5
    return r / total, l / total


def _clamp(value: float) -> float:
    """Clamp to [MIN_SKILL_VALUE, MAX_SKILL_CAP] and round to 1 decimal."""
    return round(max(MIN_SKILL_VALUE, min(MAX_SKILL_CAP, value)), 1)
