"""
skill_progression sub-package — Layer 1 (pure formulas) only.

Future layers (config resolution, DB helpers, EMA engine, views) will be
added to this package in subsequent PRs.  The canonical import surface
remains app.services.skill_progression_service (thin shim).
"""
from ._formulas import (
    MIN_SKILL_VALUE,
    MAX_SKILL_VALUE,
    MAX_SKILL_CAP,
    DEFAULT_BASELINE,
    calculate_skill_value_from_placement,
    get_skill_tier,
)

__all__ = [
    "MIN_SKILL_VALUE",
    "MAX_SKILL_VALUE",
    "MAX_SKILL_CAP",
    "DEFAULT_BASELINE",
    "calculate_skill_value_from_placement",
    "get_skill_tier",
]
