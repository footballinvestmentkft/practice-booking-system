"""
Pure formula layer for skill progression calculations.

No DB access, no model imports.  All functions are pure mathematical
transformations that can be tested without a database connection.

Extracted from skill_progression_service.py (Layer 1).
"""
import math
from typing import Optional


# ─── Constants ───────────────────────────────────────────────────────────────

MIN_SKILL_VALUE  = 40.0   # Worst possible skill value (last place)
MAX_SKILL_VALUE  = 100.0  # Best possible skill value (1st place) — used for percentile mapping
MAX_SKILL_CAP    = 99.0   # Hard cap for final skill values (business rule)
SYSTEM_BASELINE  = 60.0   # Fixed visible starting level for every new LFA Football Player
DEFAULT_BASELINE = 60.0   # Fallback when football_skills is absent (equals SYSTEM_BASELINE)


# ─── Formula functions ────────────────────────────────────────────────────────

def calculate_skill_value_from_placement(
    baseline: float,
    placement: int,
    total_players: int,
    tournament_count: int,
    skill_weight: float = 1.0,
    prev_value: Optional[float] = None,
    learning_rate: float = 0.20,
    opponent_factor: float = 1.0,
    match_performance_modifier: float = 0.0,
) -> float:
    """
    Calculate new skill value from tournament placement.

    V3 EMA path (prev_value is provided):
        Uses Exponential Moving Average (online learning) with:
        - Log-normalised step: step = lr × log(1+w) / log(2)
          → weight=1.0 anchors at lr=0.20; high weights grow sub-linearly (no hard cap needed)
        - Asymmetric ELO opponent factor:
          win  (delta≥0): delta × opponent_factor  (bonus for beating stronger field)
          loss (delta<0): delta / opponent_factor  (reduced penalty for losing to stronger field)
        → Mathematical guarantee: norm_delta ratio = log(1+w_dom)/log(1+w_sup) = constant
        - Match performance multiplier (sign-symmetric delta scaling):
          delta>0: raw × (1+m) — good perf amplifies gain,  bad perf softens gain
          delta<0: raw × (1-m) — good perf softens loss,    bad perf amplifies loss
          modifier ∈ [-1, +1]; confidence-weighted so sparse data → 0 naturally.

    V2 legacy path (prev_value=None):
        Original weighted-average convergence formula (unchanged for backward compat).

    Args:
        baseline:                   Onboarding skill value (used as fallback / legacy anchor)
        placement:                  Tournament placement (1 = best)
        total_players:              Field size
        tournament_count:           Number of prior tournaments for this skill (legacy path only)
        skill_weight:               Reactivity multiplier (0.1–5.0). 1.0 = neutral.
        prev_value:                 Current running skill level (EMA path). None → legacy path.
        learning_rate:              EMA base learning rate (default 0.20, calibrated at weight=1.0)
        opponent_factor:            avg_opponent_baseline / player_baseline, clamped [0.5, 2.0].
                                    1.0 = equal field (no adjustment).
        match_performance_modifier: Confidence-weighted signal in [-1, +1] from
                                    _compute_match_performance_modifier(). 0.0 = no data.

    Returns:
        New skill value, capped to [40.0, 99.0].
    """
    # Shared: placement → placement_skill (unchanged in both paths)
    if total_players == 1:
        percentile = 0.0
    else:
        percentile = (placement - 1) / (total_players - 1)
    placement_skill = MAX_SKILL_VALUE - (percentile * (MAX_SKILL_VALUE - MIN_SKILL_VALUE))

    if prev_value is not None:
        # ── V3 EMA PATH ────────────────────────────────────────────────────────
        # Log-normalised step: anchored at lr when weight=1.0
        step = learning_rate * math.log(1.0 + skill_weight) / math.log(2.0)

        # Raw delta toward placement evidence
        raw_delta = step * (placement_skill - prev_value)

        # Match performance: sign-symmetric delta scaling
        #   raw_delta > 0 (placement says: go up)   → good perf amplifies gain,  bad perf softens gain
        #   raw_delta < 0 (placement says: go down)  → good perf softens loss,   bad perf amplifies loss
        # Formula: coeff = (1 + m) for gain, (1 - m) for loss
        #   m=+1: gain×2, loss×0  →  great perf always helps, never hurts
        #   m=-1: gain×0, loss×2  →  poor perf always hurts, never helps
        if match_performance_modifier != 0.0:
            if raw_delta >= 0:
                raw_delta = raw_delta * (1.0 + match_performance_modifier)
            else:
                raw_delta = raw_delta * (1.0 - match_performance_modifier)

        # Asymmetric opponent factor
        f = max(0.5, min(2.0, opponent_factor))
        if raw_delta >= 0:
            adjusted_delta = raw_delta * f      # win vs strong → bigger reward
        else:
            adjusted_delta = raw_delta / f      # loss vs strong → smaller penalty

        new_val = max(MIN_SKILL_VALUE, min(MAX_SKILL_CAP, prev_value + adjusted_delta))
        return round(new_val, 1)

    # ── V2 LEGACY PATH (unchanged) ─────────────────────────────────────────────
    baseline_weight = 1.0 / (tournament_count + 1)
    placement_weight = tournament_count / (tournament_count + 1)
    new_skill_base = (baseline * baseline_weight) + (placement_skill * placement_weight)
    delta = new_skill_base - baseline
    weighted_delta = delta * skill_weight
    new_skill = baseline + weighted_delta
    return round(max(MIN_SKILL_VALUE, min(MAX_SKILL_CAP, new_skill)), 1)


def get_skill_tier(level: float) -> tuple[str, str]:
    """
    Get skill tier name and emoji based on level.

    Args:
        level: Skill level (0-100)

    Returns:
        (tier_name, tier_emoji)
    """
    if level >= 95:
        return ("MASTER", "💎")
    elif level >= 85:
        return ("ADVANCED", "🔥")
    elif level >= 70:
        return ("INTERMEDIATE", "⚡")
    elif level >= 50:
        return ("DEVELOPING", "📈")
    else:
        return ("BEGINNER", "🌱")
