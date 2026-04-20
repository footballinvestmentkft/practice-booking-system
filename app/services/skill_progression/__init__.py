"""
skill_progression sub-package — Layers 1 + 2 + 3.

Layer 1 (_formulas):    pure math, constants
Layer 2 (_config):      skill key enumeration, baseline lookup, tournament mapping
Layer 3 (_db_helpers):  DB-backed opponent factor + match performance modifier

Future layers (EMA engine, views) will be added in subsequent PRs.
The canonical import surface remains app.services.skill_progression_service (thin shim).
"""
from ._formulas import (
    MIN_SKILL_VALUE,
    MAX_SKILL_VALUE,
    MAX_SKILL_CAP,
    DEFAULT_BASELINE,
    calculate_skill_value_from_placement,
    get_skill_tier,
)
from ._config import (
    get_all_skill_keys,
    get_baseline_skills,
    _extract_tournament_skills,
)
from ._db_helpers import (
    _compute_opponent_factor,
    _compute_match_performance_modifier,
)

__all__ = [
    "MIN_SKILL_VALUE",
    "MAX_SKILL_VALUE",
    "MAX_SKILL_CAP",
    "DEFAULT_BASELINE",
    "calculate_skill_value_from_placement",
    "get_skill_tier",
    "get_all_skill_keys",
    "get_baseline_skills",
    "_extract_tournament_skills",
    "_compute_opponent_factor",
    "_compute_match_performance_modifier",
]
