"""
skill_progression sub-package — Layers 1 + 2 + 3 + 4 + 5.

Layer 1 (_formulas):    pure math, constants
Layer 2 (_config):      skill key enumeration, baseline lookup, tournament mapping
Layer 3 (_db_helpers):  DB-backed opponent factor + match performance modifier
Layer 4 (_ema_engine):  sequential EMA history-replay loops (ctsc + cstsd)
Layer 5 (_views):       per-user skill profile, timeline, audit, and checkpoint views

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
from ._ema_engine import (
    calculate_tournament_skill_contribution,
    compute_single_tournament_skill_delta,
)
from ._views import (
    get_skill_profile,
    get_skill_timeline,
    get_skill_audit,
    get_avg_skill_level_checkpoints,
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
    "calculate_tournament_skill_contribution",
    "compute_single_tournament_skill_delta",
    "get_skill_profile",
    "get_skill_timeline",
    "get_skill_audit",
    "get_avg_skill_level_checkpoints",
]
