"""
EMA engine layer for skill progression.

Implements the sequential EMA history-replay loops that compute per-skill
values from a player's tournament participation history.

No view/response building, no assessment data.  All DB access is via
TournamentParticipation queries + delegated calls to Layer 2 (config) and
Layer 3 (DB helpers).

Extracted from skill_progression_service.py (Layer 4).
"""
from typing import Dict, List

from sqlalchemy.orm import Session

from app.models.tournament_achievement import TournamentParticipation
from ._formulas import (
    DEFAULT_BASELINE,
    calculate_skill_value_from_placement,
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


def calculate_tournament_skill_contribution(
    db: Session,
    user_id: int,
    skill_keys: List[str]
) -> Dict[str, Dict[str, float]]:
    """
    Calculate tournament-based skill contributions for specified skills.

    Args:
        db: Database session
        user_id: User ID
        skill_keys: List of skill keys to calculate (from tournament reward config)

    Returns:
        Dict of skill_key → {
            "contribution": float,  # Net contribution from all tournaments
            "tournament_count": int,  # Number of tournaments affecting this skill
            "current_value": float,  # Current skill value after all tournaments
            "baseline": float  # Original onboarding value
        }

    Logic:
        1. Get user's baseline skills from onboarding
        2. For each tournament participation:
           - Get tournament's selected skills (from reward_config)
           - For each selected skill:
             - Calculate new skill value based on placement
             - Track contribution vs baseline
        3. Return aggregated data per skill
    """
    # Get baseline skills from UserLicense
    baseline_skills = get_baseline_skills(db, user_id)

    # Get all tournament participations for this user (ordered by date).
    # S01: (achieved_at, id) stable sort — prevents non-deterministic EMA replay
    # when concurrent inserts share the same server_default NOW() timestamp.
    participations = (
        db.query(TournamentParticipation)
        .filter(TournamentParticipation.user_id == user_id)
        .order_by(
            TournamentParticipation.achieved_at.asc(),
            TournamentParticipation.id.asc(),
        )
        .all()
    )

    # Player's average baseline (used to compute opponent_factor per tournament)
    all_baseline_vals = list(baseline_skills.values())
    player_baseline_avg = (sum(all_baseline_vals) / len(all_baseline_vals)) if all_baseline_vals else DEFAULT_BASELINE

    # Track skill evolution across tournaments
    skill_data = {}
    skill_tournament_counts = {}  # Track how many tournaments affected each skill
    skill_previous_values: Dict[str, float] = {}  # Running EMA value per skill

    for skill_key in skill_keys:
        baseline = baseline_skills.get(skill_key, DEFAULT_BASELINE)
        skill_data[skill_key] = {
            "baseline": baseline,
            "current_value": baseline,  # Start with baseline
            "contribution": 0.0,
            "tournament_count": 0
        }
        skill_tournament_counts[skill_key] = 0
        skill_previous_values[skill_key] = baseline

    # Process each tournament participation
    for participation in participations:
        tournament = participation.tournament

        if not tournament:
            continue

        tournament_skills_with_weights = _extract_tournament_skills(db, tournament, skill_keys)
        if not tournament_skills_with_weights:
            continue

        # Get placement data
        placement = participation.placement
        if not placement:
            continue

        # Get total players in tournament.
        # For TEAM tournaments: count distinct placements (= number of teams),
        # not total individual rows — prevents last-place teams getting 5th-percentile scores.
        if getattr(tournament, "participant_type", "INDIVIDUAL") == "TEAM":
            total_players = (
                db.query(TournamentParticipation.placement)
                .filter(TournamentParticipation.semester_id == tournament.id)
                .distinct()
                .count()
            )
        else:
            total_players = (
                db.query(TournamentParticipation)
                .filter(TournamentParticipation.semester_id == tournament.id)
                .count()
            )

        if total_players == 0:
            continue

        # Opponent factor for this tournament (ELO-inspired)
        opp_factor = _compute_opponent_factor(
            db, tournament.id, user_id, player_baseline_avg
        )
        # Match-level performance modifier (win rate + score differential)
        match_modifier = _compute_match_performance_modifier(
            db, tournament.id, user_id
        )

        # Update each affected skill with its weight
        for skill_key, skill_weight in tournament_skills_with_weights.items():
            if skill_key not in skill_data:
                continue

            baseline = skill_data[skill_key]["baseline"]
            current_count = skill_tournament_counts[skill_key]
            prev_val = skill_previous_values[skill_key]

            new_value = calculate_skill_value_from_placement(
                baseline=baseline,
                placement=placement,
                total_players=total_players,
                tournament_count=current_count + 1,
                skill_weight=skill_weight,
                prev_value=prev_val,
                opponent_factor=opp_factor,
                match_performance_modifier=match_modifier,
            )

            # Update skill data
            skill_data[skill_key]["current_value"] = new_value
            skill_data[skill_key]["contribution"] = new_value - baseline
            skill_data[skill_key]["tournament_count"] = current_count + 1

            # Advance running state
            skill_tournament_counts[skill_key] += 1
            skill_previous_values[skill_key] = new_value

    return skill_data


def compute_single_tournament_skill_delta(
    db: Session,
    user_id: int,
    tournament_id: int,
) -> Dict[str, float]:
    """
    Compute the isolated V3 EMA skill delta for ONE specific tournament.

    Replays the full EMA history up to (but not including) the target tournament,
    then computes the EMA step for the target tournament alone.

    Returns:
        Dict of skill_key → delta (new_value - prev_value), rounded to 1 decimal.
        Only skills affected by this tournament are included.
        Empty dict if the tournament has no valid placement data.

    This is the authoritative source for TournamentParticipation.skill_rating_delta.
    It is written once at reward-distribution time and never recomputed.
    """
    baseline_skills = get_baseline_skills(db, user_id)
    all_baseline_vals = list(baseline_skills.values())
    player_baseline_avg = (
        sum(all_baseline_vals) / len(all_baseline_vals)
        if all_baseline_vals else DEFAULT_BASELINE
    )

    # All participations in chronological order (includes target tournament).
    # S01: secondary sort by .id ensures deterministic ordering when two participations
    # share the same achieved_at timestamp (e.g., concurrent tournament inserts within
    # the same PostgreSQL clock tick — server_default=NOW() can collide).
    participations = (
        db.query(TournamentParticipation)
        .filter(TournamentParticipation.user_id == user_id)
        .order_by(
            TournamentParticipation.achieved_at.asc(),
            TournamentParticipation.id.asc(),
        )
        .all()
    )

    all_skill_keys = get_all_skill_keys()
    skill_previous_values: Dict[str, float] = {
        sk: baseline_skills.get(sk, DEFAULT_BASELINE) for sk in all_skill_keys
    }
    skill_tournament_counts: Dict[str, int] = {sk: 0 for sk in all_skill_keys}

    for participation in participations:
        tournament = participation.tournament
        if not tournament:
            continue

        placement = participation.placement
        if not placement:
            continue

        tournament_skills_with_weights = _extract_tournament_skills(db, tournament, all_skill_keys)
        if not tournament_skills_with_weights:
            continue

        # For TEAM tournaments: total competitive units = number of distinct team placements
        # (e.g. 3-team tournament → total=3, not 36 individual rows).
        # Using raw row count inflates total_players and collapses the percentile toward 0%,
        # causing even the last-placed team's players to receive large positive deltas.
        if getattr(tournament, "participant_type", "INDIVIDUAL") == "TEAM":
            total_players = (
                db.query(TournamentParticipation.placement)
                .filter(TournamentParticipation.semester_id == tournament.id)
                .distinct()
                .count()
            )
        else:
            total_players = (
                db.query(TournamentParticipation)
                .filter(TournamentParticipation.semester_id == tournament.id)
                .count()
            )
        if total_players == 0:
            continue

        opp_factor = _compute_opponent_factor(db, tournament.id, user_id, player_baseline_avg)
        match_modifier = _compute_match_performance_modifier(db, tournament.id, user_id)

        is_target = (tournament.id == tournament_id)
        target_delta: Dict[str, float] = {}

        for skill_key, skill_weight in tournament_skills_with_weights.items():
            if skill_key not in skill_previous_values:
                continue

            prev_val = skill_previous_values[skill_key]
            current_count = skill_tournament_counts[skill_key]
            baseline = baseline_skills.get(skill_key, DEFAULT_BASELINE)

            new_value = calculate_skill_value_from_placement(
                baseline=baseline,
                placement=placement,
                total_players=total_players,
                tournament_count=current_count + 1,
                skill_weight=skill_weight,
                prev_value=prev_val,
                opponent_factor=opp_factor,
                match_performance_modifier=match_modifier,
            )

            if is_target:
                delta = round(new_value - prev_val, 1)
                if delta != 0.0:
                    target_delta[skill_key] = delta

            skill_previous_values[skill_key] = new_value
            skill_tournament_counts[skill_key] += 1

        if is_target:
            return target_delta

    return {}
