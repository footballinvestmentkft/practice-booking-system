"""
Skill Progression Service V3 - EMA-Based Placement Assessment

Core principle: Incremental online learning (EMA) replaces the static convergence model.

V3 Formula:
    step  = lr × log(1 + weight) / log(2)         [log-normalised, lr=0.20 at weight=1.0]
    delta = step × (placement_skill - prev_value)  [toward placement evidence]
    adjusted_delta:
        win  (delta≥0): delta × opponent_factor    [boost when beating stronger field]
        loss (delta<0): delta / opponent_factor     [soften when losing to stronger field]
    new_value = clamp(prev_value + adjusted_delta, 40, 99)

V3 Properties vs V2:
    - No "dead baseline anchor": prev_value is the running level, not the onboarding score
    - No volatility amplification: EMA step is constant, oscillation stays bounded (±4–5 pt)
    - No drastic T1 jumps: max step ≈ 0.264 (w=1.5) vs V2 0.300
    - Dominant skills always have norm_delta ≥ supporting peers (mathematical guarantee)
    - ELO-inspired opponent_factor: beating strong opponents rewards more

V2 legacy path: call with prev_value=None for backward-compatible behaviour.
"""

import math
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from datetime import datetime

from ..models.user import User
from ..models.license import UserLicense
from ..models.football_skill_assessment import FootballSkillAssessment
from ..models.tournament_achievement import TournamentParticipation, TournamentSkillMapping
from ..skills_config import SKILL_CATEGORIES
from .skill_progression import (
    MIN_SKILL_VALUE,
    MAX_SKILL_VALUE,
    MAX_SKILL_CAP,
    DEFAULT_BASELINE,
    calculate_skill_value_from_placement,
    get_skill_tier,
    get_all_skill_keys,
    get_baseline_skills,
    _extract_tournament_skills,
    _compute_opponent_factor,
    _compute_match_performance_modifier,
    calculate_tournament_skill_contribution,
    compute_single_tournament_skill_delta,
    get_skill_profile,
    get_skill_timeline,
    get_skill_audit,
    get_avg_skill_level_checkpoints,
)
