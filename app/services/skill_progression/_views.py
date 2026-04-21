"""
View layer for skill progression.

Builds per-user skill profile, timeline, audit, and checkpoint views from
live DB state.  All EMA replay is delegated to Layer 3 (_db_helpers) and
Layer 4 (_ema_engine); this layer only assembles the view dicts.

No formula logic, no config enumeration, no EMA step computation here.

Extracted from skill_progression_service.py (Layer 5).
"""
from typing import Dict, List

from sqlalchemy.orm import Session

from app.models.license import UserLicense
from app.models.football_skill_assessment import FootballSkillAssessment
from app.models.tournament_achievement import TournamentParticipation

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
from ._ema_engine import calculate_tournament_skill_contribution
from app.services.segment_reward_service import (
    get_training_skill_deltas_for_user,
    get_training_session_count_for_user,
)


def get_skill_profile(db: Session, user_id: int) -> Dict[str, any]:
    """
    Get complete skill profile for user (for dashboard display).

    Returns:
        {
            "skills": {
                "ball_control": {
                    "baseline": 70.0,
                    "current_level": 85.0,
                    "total_delta": +15.0,
                    "tournament_delta": +15.0,
                    "assessment_delta": 0.0,  # Future: assessments
                    "tournament_count": 3,
                    "assessment_count": 0,
                    "tier": "ADVANCED",
                    "tier_emoji": "🔥"
                },
                ...
            },
            "average_level": 78.5,
            "total_tournaments": 5,
            "total_assessments": 0
        }
    """
    # Get all skill keys
    all_skill_keys = get_all_skill_keys()

    # Batch-load FootballSkillAssessment rows for the player's active license.
    # One query for all 29 skills (avoids N+1).
    active_license = (
        db.query(UserLicense)
        .filter(
            UserLicense.user_id == user_id,
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
            UserLicense.is_active == True,
        )
        .order_by(UserLicense.id.desc())
        .first()
    )

    assessed_map: Dict[str, float] = {}   # skill_name -> latest ASSESSED/VALIDATED pct
    assessed_count_map: Dict[str, int] = {}  # skill_name -> count of ASSESSED/VALIDATED rows

    if active_license:
        rows = (
            db.query(FootballSkillAssessment)
            .filter(
                FootballSkillAssessment.user_license_id == active_license.id,
                FootballSkillAssessment.status.in_(["ASSESSED", "VALIDATED"]),
            )
            .order_by(
                FootballSkillAssessment.skill_name,
                FootballSkillAssessment.id.desc(),
            )
            .all()
        )
        seen: set = set()
        for row in rows:
            assessed_count_map[row.skill_name] = assessed_count_map.get(row.skill_name, 0) + 1
            if row.skill_name not in seen:
                assessed_map[row.skill_name] = row.percentage
                seen.add(row.skill_name)

    # Calculate tournament contributions for all skills
    skill_data = calculate_tournament_skill_contribution(db, user_id, all_skill_keys)

    # Get total tournament count
    total_tournaments = (
        db.query(TournamentParticipation)
        .filter(TournamentParticipation.user_id == user_id)
        .count()
    )

    # Training contributions (additive on top of EMA; EMA state is not touched)
    training_deltas = get_training_skill_deltas_for_user(db, user_id)
    training_session_count = get_training_session_count_for_user(db, user_id)

    # Build skill profile
    skill_profile = {}
    total_level = 0.0

    for skill_key in all_skill_keys:
        data = skill_data.get(skill_key, {
            "baseline": DEFAULT_BASELINE,
            "current_value": DEFAULT_BASELINE,
            "contribution": 0.0,
            "tournament_count": 0
        })

        tournament_delta = data["contribution"]
        training_delta = round(training_deltas.get(skill_key, 0.0), 1)
        current_level = min(
            MAX_SKILL_CAP,
            max(MIN_SKILL_VALUE, data["current_value"] + training_delta),
        )
        total_delta = round(tournament_delta + training_delta, 1)

        # Determine tier
        tier, tier_emoji = get_skill_tier(current_level)

        skill_profile[skill_key] = {
            "baseline": data["baseline"],
            "current_level": current_level,
            "total_delta": total_delta,
            "tournament_delta": tournament_delta,
            "training_delta": training_delta,
            "assessment_delta": round(assessed_map.get(skill_key, data["baseline"]) - data["baseline"], 1),
            "tournament_count": data["tournament_count"],
            "assessment_count": assessed_count_map.get(skill_key, 0),
            "training_sessions": training_session_count,
            "tier": tier,
            "tier_emoji": tier_emoji
        }

        total_level += current_level

    average_level = total_level / len(all_skill_keys) if all_skill_keys else 0.0

    return {
        "skills": skill_profile,
        "average_level": round(average_level, 1),
        "total_tournaments": total_tournaments,
        "total_assessments": sum(assessed_count_map.values())
    }


def get_skill_timeline(
    db: Session,
    user_id: int,
    skill_key: str
) -> Dict:
    """
    Build a per-tournament timeline for a single skill showing how it evolved
    across all tournaments the player participated in.

    The timeline replays the same sequential weighted-average formula used by
    calculate_tournament_skill_contribution(), but captures the intermediate
    value after every tournament instead of only the final aggregated result.

    No schema changes required: all data lives in TournamentParticipation +
    TournamentSkillMapping / reward_config.

    Returns:
        {
            "skill": "passing",
            "baseline": 80.0,
            "current_level": 97.5,
            "total_delta": 17.5,
            "timeline": [
                {
                    "tournament_id":   10,
                    "tournament_name": "League Cup 2026",
                    "achieved_at":     "2026-02-11T15:33:11+00:00",
                    "placement":       1,
                    "total_players":   4,
                    "placement_skill": 100.0,
                    "skill_weight":    1.0,
                    "skill_value_after": 88.5,
                    "delta_from_baseline": 8.5,
                    "delta_from_previous": 8.5,
                },
                ...
            ]
        }
        Returns None if the player has no participation data or the skill is unknown.
    """
    all_skill_keys = get_all_skill_keys()
    if skill_key not in all_skill_keys:
        return None

    # --- baseline --------------------------------------------------------
    baseline_skills = get_baseline_skills(db, user_id)
    baseline = baseline_skills.get(skill_key, DEFAULT_BASELINE)

    # Player average baseline for opponent_factor computation
    all_baseline_vals = list(baseline_skills.values())
    player_baseline_avg = (sum(all_baseline_vals) / len(all_baseline_vals)) if all_baseline_vals else DEFAULT_BASELINE

    # --- participations in chronological order ---------------------------
    # S01: (achieved_at, id) stable sort for deterministic timeline replay
    participations = (
        db.query(TournamentParticipation)
        .filter(TournamentParticipation.user_id == user_id)
        .order_by(
            TournamentParticipation.achieved_at.asc(),
            TournamentParticipation.id.asc(),
        )
        .all()
    )

    timeline = []
    tournament_count = 0      # How many tournaments have already affected this skill
    previous_value = baseline

    for participation in participations:
        tournament = participation.tournament
        if not tournament or not participation.placement:
            continue

        tournament_skills_with_weights = _extract_tournament_skills(db, tournament, all_skill_keys)
        # This tournament does not affect the requested skill → skip
        if skill_key not in tournament_skills_with_weights:
            continue

        skill_weight = tournament_skills_with_weights[skill_key]

        # Total players in this tournament.
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

        # Placement → placement_skill (100 for 1st, 40 for last)
        if total_players == 1:
            percentile = 0.0
        else:
            percentile = (participation.placement - 1) / (total_players - 1)
        placement_skill = MAX_SKILL_VALUE - (percentile * (MAX_SKILL_VALUE - MIN_SKILL_VALUE))

        opp_factor = _compute_opponent_factor(
            db, tournament.id, user_id, player_baseline_avg
        )

        tournament_count += 1
        skill_value_after = calculate_skill_value_from_placement(
            baseline=baseline,
            placement=participation.placement,
            total_players=total_players,
            tournament_count=tournament_count,
            skill_weight=skill_weight,
            prev_value=previous_value,
            opponent_factor=opp_factor,
        )

        timeline.append({
            # Event-model fields (canonical)
            "event_type":           "tournament",
            "event_name":           tournament.name,
            # Legacy fields kept for backwards compatibility
            "tournament_id":        tournament.id,
            "tournament_name":      tournament.name,
            "achieved_at":          participation.achieved_at.isoformat() if participation.achieved_at else None,
            "placement":            participation.placement,
            "total_players":        total_players,
            "placement_skill":      round(placement_skill, 1),
            "skill_weight":         skill_weight,
            "skill_value_after":    skill_value_after,
            "delta_from_baseline":  round(skill_value_after - baseline, 1),
            "delta_from_previous":  round(skill_value_after - previous_value, 1),
        })
        previous_value = skill_value_after

    if not timeline:
        return {
            "skill": skill_key,
            "baseline": baseline,
            "current_level": baseline,
            "total_delta": 0.0,
            "timeline": []
        }

    current_level = timeline[-1]["skill_value_after"]
    return {
        "skill":         skill_key,
        "baseline":      baseline,
        "current_level": current_level,
        "total_delta":   round(current_level - baseline, 1),
        "timeline":      timeline,
    }


def get_skill_audit(db: Session, user_id: int) -> List[Dict]:
    """
    Build a per-tournament audit log showing which skills were expected to change
    and whether they actually changed.

    For each tournament the player participated in, returns one row per mapped skill:
        {
          "tournament_id":   17,
          "tournament_name": "E2E Phase 4d",
          "achieved_at":     "2026-02-11T...",
          "placement":       1,
          "total_players":   4,
          "skill":           "finishing",
          "skill_weight":    1.50,
          "avg_weight":      1.00,        # average weight of all skills in this tournament
          "is_dominant":     True,        # weight > avg_weight
          "expected_change": True,        # skill was in the tournament's skill_mappings
          "placement_skill": 100.0,       # raw score from placement (100=1st, 40=last)
          "delta_this_tournament": +2.0,  # actual change produced by THIS tournament
                                          # (cumulative formula, so derived from timeline)
          "actual_changed":  True,        # abs(delta_this_tournament) > 0.0
          "fairness_ok":     True,        # dominant skill had |delta| >= balanced peers
        }

    Sorted: tournament chronological ASC, then skill name ASC within each tournament.
    """
    all_skill_keys = get_all_skill_keys()
    baseline_skills = get_baseline_skills(db, user_id)

    # Player average baseline for opponent_factor computation
    all_baseline_vals = list(baseline_skills.values())
    player_baseline_avg = (sum(all_baseline_vals) / len(all_baseline_vals)) if all_baseline_vals else DEFAULT_BASELINE

    # S01: (achieved_at, id) stable sort for deterministic audit replay
    participations = (
        db.query(TournamentParticipation)
        .filter(TournamentParticipation.user_id == user_id)
        .order_by(
            TournamentParticipation.achieved_at.asc(),
            TournamentParticipation.id.asc(),
        )
        .all()
    )

    # We need to replay the sequential formula per skill (same as get_skill_timeline),
    # tracking previous_value per skill to compute delta_this_tournament.
    skill_tournament_counts: Dict[str, int] = {}    # cumulative count per skill
    skill_previous_values: Dict[str, float] = {}    # last computed value per skill

    for sk in all_skill_keys:
        skill_tournament_counts[sk] = 0
        skill_previous_values[sk] = baseline_skills.get(sk, DEFAULT_BASELINE)

    audit_rows: List[Dict] = []

    for participation in participations:
        tournament = participation.tournament
        if not tournament or not participation.placement:
            continue

        tournament_skills_with_weights = _extract_tournament_skills(db, tournament, all_skill_keys)
        if not tournament_skills_with_weights:
            continue

        # Stats for this tournament.
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

        # Placement → placement_skill
        if total_players == 1:
            percentile = 0.0
        else:
            percentile = (participation.placement - 1) / (total_players - 1)
        placement_skill = round(
            MAX_SKILL_VALUE - (percentile * (MAX_SKILL_VALUE - MIN_SKILL_VALUE)), 1
        )

        achieved_at_str = (
            participation.achieved_at.isoformat() if participation.achieved_at else None
        )

        # Compute avg weight for this tournament (to flag dominant skills)
        avg_weight = (
            sum(tournament_skills_with_weights.values()) / len(tournament_skills_with_weights)
        )

        # Opponent factor for this tournament (ELO-inspired)
        opp_factor = _compute_opponent_factor(
            db, tournament.id, user_id, player_baseline_avg
        )

        # Calculate delta and normalised delta for each mapped skill.
        #
        # Normalised delta = delta / headroom
        #   where headroom = max_cap - prev_val  (when improving)
        #                  = prev_val - min_floor (when declining)
        #
        # This is the correct fairness metric: it measures what fraction of the
        # available development range was consumed, independent of the skill's
        # absolute position.  A skill capped at 99 has headroom=0 → norm_delta=0,
        # which is a physical limit, NOT an unfairness signal.
        skill_deltas: Dict[str, float] = {}
        skill_norm_deltas: Dict[str, float] = {}

        for skill_key, skill_weight in tournament_skills_with_weights.items():
            prev = skill_previous_values[skill_key]
            count = skill_tournament_counts[skill_key] + 1
            new_val = calculate_skill_value_from_placement(
                baseline=baseline_skills.get(skill_key, DEFAULT_BASELINE),
                placement=participation.placement,
                total_players=total_players,
                tournament_count=count,
                skill_weight=skill_weight,
                prev_value=prev,
                opponent_factor=opp_factor,
            )
            delta = round(new_val - prev, 2)
            skill_deltas[skill_key] = delta

            # Normalised delta: fraction of available headroom consumed
            if delta > 0:
                headroom = max(0.001, MAX_SKILL_CAP - prev)
            elif delta < 0:
                headroom = max(0.001, prev - MIN_SKILL_VALUE)
            else:
                headroom = 0.0   # no movement at all
            skill_norm_deltas[skill_key] = (
                round(delta / headroom, 6) if headroom > 0 else 0.0
            )

        # Fairness check (normalised):
        #   A dominant skill (weight > avg * 1.05) should have
        #   |norm_delta| >= |norm_delta| of every lower-weight peer.
        #
        #   Exception: if the dominant skill is at the hard cap (headroom=0),
        #   its norm_delta is 0 by definition — that is a physical limit,
        #   NOT an unfair outcome.  We skip the check in that case.
        for skill_key, skill_weight in tournament_skills_with_weights.items():
            delta = skill_deltas.get(skill_key, 0.0)
            my_norm = abs(skill_norm_deltas.get(skill_key, 0.0))
            is_dominant = skill_weight > avg_weight * 1.05

            fairness_ok = True
            if is_dominant and skill_norm_deltas.get(skill_key, 0.0) != 0.0:
                # Only check when dominant skill actually had room to move
                for peer_key, peer_weight in tournament_skills_with_weights.items():
                    if peer_key == skill_key:
                        continue
                    if peer_weight < skill_weight:
                        peer_norm = abs(skill_norm_deltas.get(peer_key, 0.0))
                        # Flag if peer consumed more headroom than dominant skill
                        # (0.005 tolerance for floating-point imprecision)
                        if my_norm + 0.005 < peer_norm:
                            fairness_ok = False

            audit_rows.append({
                "tournament_id":          tournament.id,
                "tournament_name":        tournament.name,
                "achieved_at":            achieved_at_str,
                "placement":              participation.placement,
                "total_players":          total_players,
                "skill":                  skill_key,
                "skill_weight":           round(skill_weight, 2),
                "avg_weight":             round(avg_weight, 2),
                "is_dominant":            is_dominant,
                "expected_change":        True,   # by definition: it's in the mapping
                "placement_skill":        placement_skill,
                "delta_this_tournament":  delta,
                "norm_delta":             skill_norm_deltas.get(skill_key, 0.0),
                "actual_changed":         abs(delta) > 0.001,
                "fairness_ok":            fairness_ok,
                "opponent_factor":        opp_factor,
                "ema_path":               skill_tournament_counts.get(skill_key, 0) > 0,
            })

        # Advance running state
        for skill_key, skill_weight in tournament_skills_with_weights.items():
            skill_tournament_counts[skill_key] += 1
            prev = skill_previous_values[skill_key]
            count = skill_tournament_counts[skill_key]
            new_val = calculate_skill_value_from_placement(
                baseline=baseline_skills.get(skill_key, DEFAULT_BASELINE),
                placement=participation.placement,
                total_players=total_players,
                tournament_count=count,
                skill_weight=skill_weight,
                prev_value=prev,
                opponent_factor=opp_factor,
            )
            skill_previous_values[skill_key] = new_val

    return audit_rows


def get_avg_skill_level_checkpoints(
    db: Session,
    user_id: int,
) -> Dict[int, float]:
    """
    Single-pass EMA replay returning the average skill level AFTER each tournament.

    Returns:
        {tournament_id: avg_level_after}  — one entry per participated tournament.
        Uses the same algorithm as calculate_tournament_skill_contribution()
        but captures intermediate averages at each step instead of only the final state.
    """
    baseline_skills = get_baseline_skills(db, user_id)
    all_skill_keys = get_all_skill_keys()
    all_baseline_vals = list(baseline_skills.values())
    player_baseline_avg = (
        sum(all_baseline_vals) / len(all_baseline_vals)
        if all_baseline_vals else DEFAULT_BASELINE
    )
    participations = (
        db.query(TournamentParticipation)
        .filter(TournamentParticipation.user_id == user_id)
        .order_by(
            TournamentParticipation.achieved_at.asc(),
            TournamentParticipation.id.asc(),
        )
        .all()
    )
    skill_prev: Dict[str, float] = {
        sk: baseline_skills.get(sk, DEFAULT_BASELINE) for sk in all_skill_keys
    }
    skill_counts: Dict[str, int] = {sk: 0 for sk in all_skill_keys}
    checkpoints: Dict[int, float] = {}

    for participation in participations:
        tournament = participation.tournament
        if not tournament or not participation.placement:
            continue
        skills_w = _extract_tournament_skills(db, tournament, all_skill_keys)
        if not skills_w:
            continue
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
        if not total_players:
            continue
        opp = _compute_opponent_factor(db, tournament.id, user_id, player_baseline_avg)
        mod = _compute_match_performance_modifier(db, tournament.id, user_id)
        for sk, w in skills_w.items():
            if sk not in skill_prev:
                continue
            new_val = calculate_skill_value_from_placement(
                baseline=baseline_skills.get(sk, DEFAULT_BASELINE),
                placement=participation.placement,
                total_players=total_players,
                tournament_count=skill_counts[sk] + 1,
                skill_weight=w,
                prev_value=skill_prev[sk],
                opponent_factor=opp,
                match_performance_modifier=mod,
            )
            skill_prev[sk] = new_val
            skill_counts[sk] += 1
        checkpoints[tournament.id] = round(
            sum(skill_prev.values()) / len(skill_prev), 1
        )
    return checkpoints
