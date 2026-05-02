"""
Tournament Participation Service

Handles skill point calculation, XP rewards, and participation tracking (DATA LAYER).
Separate from visual badge awards.
"""
from datetime import datetime, timezone
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import desc, func, text
from sqlalchemy.exc import IntegrityError
import logging

from app.models.tournament_achievement import (
    TournamentSkillMapping,
    TournamentParticipation,
    SkillPointConversionRate
)
from app.models.semester import Semester
from app.models.tournament_ranking import TournamentRanking
from app.models.football_skill_assessment import FootballSkillAssessment
from app.models.license import UserLicense
from app.models.xp_transaction import XPTransaction
from app.schemas.reward_config import TournamentRewardConfig
from app.config import settings
from app.services.skill_progression import DEFAULT_BASELINE
from app.services.notification_service import create_skill_tier_notification

logger = logging.getLogger(__name__)
# Separate logger for skill-propagation metrics.
# Route this to a metrics sink (CloudWatch, Datadog, ELK) by adding a handler
# for "app.metrics.skill_propagation" without touching the main app logger.
_metrics = logging.getLogger("app.metrics.skill_propagation")

# Placement-based skill point rewards
PLACEMENT_SKILL_POINTS = {
    1: 10,  # 1st place: 10 base points
    2: 7,   # 2nd place: 7 base points
    3: 5,   # 3rd place: 5 base points
    None: 1 # Participation: 1 base point
}


def calculate_skill_points_for_placement(
    db: Session,
    tournament_id: int,
    placement: Optional[int]
) -> Dict[str, float]:
    """
    Calculate skill points based on placement and tournament skill mappings.

    🎁 V2: Uses reward_config.skill_mappings if available, falls back to TournamentSkillMapping table.

    Args:
        db: Database session
        tournament_id: Tournament (semester) ID
        placement: Player placement (1, 2, 3, or None for participation)

    Returns:
        Dictionary of skill_name -> points earned
        Example: {"agility": 4.3, "physical_fitness": 2.2}
    """
    # Get base points for placement
    base_points = PLACEMENT_SKILL_POINTS.get(placement, PLACEMENT_SKILL_POINTS[None])

    # 🎁 V2: Try to load skill mappings from reward_config
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()

    skill_mappings_data = []

    if tournament and tournament.reward_config:
        try:
            # Parse reward_config JSONB to TournamentRewardConfig
            config = TournamentRewardConfig(**tournament.reward_config)

            # 🔒 VALIDATION GUARD: Check that at least 1 skill is enabled
            is_valid, error_message = config.validate_enabled_skills()
            if not is_valid:
                logger.error(f"Tournament {tournament_id} has invalid skill configuration: {error_message}")
                logger.warning(f"Falling back to legacy TournamentSkillMapping table")
                skill_mappings_data = []
            else:
                # Extract enabled skill mappings with weights
                for skill_mapping in config.skill_mappings:
                    if skill_mapping.enabled:  # Only include enabled skills
                        skill_mappings_data.append({
                            'skill_name': skill_mapping.skill,
                            'weight': skill_mapping.weight,
                            'skill_category': skill_mapping.category
                        })

                logger.info(f"Loaded {len(skill_mappings_data)} enabled skill mappings from reward_config for tournament {tournament_id}")

        except Exception as e:
            logger.error(f"Failed to parse skill mappings from reward_config for tournament {tournament_id}: {e}")
            skill_mappings_data = []

    # Priority 2: Game preset skill_weights (when no reward_config skill_mappings set)
    if not skill_mappings_data and tournament and tournament.game_preset:
        preset_weights = tournament.game_preset.skill_weights or {}
        if preset_weights:
            skill_mappings_data = [
                {'skill_name': skill, 'weight': float(weight), 'skill_category': None}
                for skill, weight in preset_weights.items()
            ]
            logger.info(f"Loaded {len(skill_mappings_data)} skill weights from game preset '{tournament.game_preset.name}' for tournament {tournament_id}")

    # Priority 3: Legacy TournamentSkillMapping table
    if not skill_mappings_data:
        logger.info(f"No reward config or game preset found, using TournamentSkillMapping table for tournament {tournament_id}")
        skill_mappings = db.query(TournamentSkillMapping).filter(
            TournamentSkillMapping.semester_id == tournament_id
        ).all()

        if not skill_mappings:
            return {}  # No skills mapped, return empty

        skill_mappings_data = [
            {
                'skill_name': mapping.skill_name,
                'weight': float(mapping.weight),
                'skill_category': mapping.skill_category
            }
            for mapping in skill_mappings
        ]

    if not skill_mappings_data:
        return {}

    # Calculate total weight
    total_weight = sum(mapping['weight'] for mapping in skill_mappings_data)

    if total_weight == 0:
        return {}

    # Distribute base points proportionally by weight
    skill_points = {}
    for mapping in skill_mappings_data:
        weight = mapping['weight']
        points = (weight / total_weight) * base_points
        # Round to 1 decimal place
        skill_points[mapping['skill_name']] = round(points, 1)

    return skill_points


def convert_skill_points_to_xp(
    db: Session,
    skill_points: Dict[str, float]
) -> int:
    """
    Convert skill points to bonus XP based on conversion rates.

    Args:
        db: Database session
        skill_points: Dictionary of skill_name -> points

    Returns:
        Total bonus XP to award
    """
    if not skill_points:
        return 0

    total_xp = 0

    # Get all conversion rates (cached in memory for performance)
    conversion_rates = {
        rate.skill_category: rate.xp_per_point
        for rate in db.query(SkillPointConversionRate).all()
    }

    # Get skill mappings to determine category for each skill
    skill_categories = {}
    for skill_name in skill_points.keys():
        mapping = db.query(TournamentSkillMapping).filter(
            TournamentSkillMapping.skill_name == skill_name
        ).first()
        if mapping and mapping.skill_category:
            skill_categories[skill_name] = mapping.skill_category

    # Calculate XP for each skill
    for skill_name, points in skill_points.items():
        category = skill_categories.get(skill_name, "football_skill")  # Default to football_skill
        xp_per_point = conversion_rates.get(category, 10)  # Default to 10 if not found
        total_xp += int(points * xp_per_point)

    return total_xp


def update_skill_assessments(
    db: Session,
    user_id: int,
    skill_points: Dict[str, float],
    assessed_by_id: Optional[int] = None,
    skill_rating_delta: Optional[Dict[str, float]] = None,
    tournament_id: Optional[int] = None,
) -> None:
    """
    Propagate tournament EMA skill deltas into FootballSkillAssessment rows.

    Uses ``skill_rating_delta`` (V3 EMA per-skill delta dict stored on
    TournamentParticipation) to update the player's live FootballSkillAssessment
    records.  ``skill_points`` is retained for XP/bonus accounting (handled
    upstream) and is otherwise unused here.

    Guard conditions — writes are skipped when:
    - ENABLE_TOURNAMENT_SKILL_PROPAGATION flag is False
    - No active LFA_FOOTBALL_PLAYER license found for the user
    - skill_rating_delta is empty or None

    License selection: most-recently-created active LFA_FOOTBALL_PLAYER license
    (order by id DESC, LIMIT 1).  This is deterministic even if — by some
    operational edge-case — two active licenses exist for the same user.

    Per-skill write pattern:
    1. Find the current active assessment (ASSESSED or VALIDATED, most recent).
    2. Compute new_pct = clamp(current_pct + delta, 40.0, 99.0).
    3. Archive the existing assessment (idempotency: skip if already ARCHIVED).
    4. Insert a new assessment row (audit trail — never mutate existing rows).

    Write-once safety: caller (record_tournament_participation) only calls this
    when TournamentParticipation.skill_rating_delta is None, so the propagation
    is performed at most once per participation record.

    Args:
        db:                 Database session
        user_id:            Player's user ID
        skill_points:       skill_name -> points (used upstream for XP; not used here)
        assessed_by_id:     Assessor user ID; falls back to user_id if None
        skill_rating_delta: V3 EMA per-skill delta dict e.g. {"dribbling": +1.2}
    """
    if not settings.ENABLE_TOURNAMENT_SKILL_PROPAGATION:
        logger.debug(
            "update_skill_assessments: propagation disabled by flag (user=%d)", user_id
        )
        return

    if not skill_rating_delta:
        return

    # ── Resolve the player's active LFA Football Player license ──────────────
    # Order by id DESC so if (in an edge case) multiple active licenses exist
    # we always pick the most recent one — deterministic behaviour.
    license = (
        db.query(UserLicense)
        .filter(
            UserLicense.user_id == user_id,
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
            UserLicense.is_active == True,
        )
        .order_by(UserLicense.id.desc())
        .first()
    )

    if not license:
        logger.debug(
            "update_skill_assessments: no active LFA_FOOTBALL_PLAYER license "
            "for user=%d — skipping", user_id
        )
        return

    assessor_id = assessed_by_id if assessed_by_id is not None else user_id
    now = datetime.now(timezone.utc)
    skills_written = 0

    for skill_key, delta in skill_rating_delta.items():
        if delta == 0.0:
            continue

        # ── Find the current active assessment for this skill ─────────────
        existing = (
            db.query(FootballSkillAssessment)
            .filter(
                FootballSkillAssessment.user_license_id == license.id,
                FootballSkillAssessment.skill_name == skill_key,
                FootballSkillAssessment.status.in_(["ASSESSED", "VALIDATED"]),
            )
            .order_by(FootballSkillAssessment.id.desc())
            .first()
        )

        current_pct = existing.percentage if existing else DEFAULT_BASELINE
        raw_new = current_pct + delta
        new_pct = round(max(40.0, min(99.0, raw_new)), 1)
        clamped = round(raw_new, 1) != new_pct

        # ── Per-skill structured log ───────────────────────────────────────
        # Readable in plain logs; parseable by structured log aggregators.
        logger.info(
            "skill_propagation skill=%s user=%d license=%d "
            "old_pct=%.1f delta=%+.1f new_pct=%.1f clamped=%s",
            skill_key, user_id, license.id,
            current_pct, delta, new_pct, clamped,
        )

        # ── Idempotency guard: skip if this exact delta was already written ──
        expected_notes = f"Auto-assessed from tournament EMA delta ({delta:+.1f})"
        already_propagated = (
            db.query(FootballSkillAssessment)
            .filter(
                FootballSkillAssessment.user_license_id == license.id,
                FootballSkillAssessment.skill_name == skill_key,
                FootballSkillAssessment.status == "ASSESSED",
                FootballSkillAssessment.notes == expected_notes,
            )
            .first()
        )
        if already_propagated:
            logger.debug(
                "update_skill_assessments: idempotency skip skill=%s user=%d "
                "(ASSESSED row with same notes already exists)",
                skill_key, user_id,
            )
            continue

        # ── Archive the superseded assessment ─────────────────────────────
        if existing:
            existing.previous_status = existing.status
            existing.status = "ARCHIVED"
            existing.archived_at = now
            existing.archived_by = assessor_id
            existing.archived_reason = (
                f"tournament_progression_delta={delta:+.1f}"
            )
            existing.status_changed_at = now
            existing.status_changed_by = assessor_id

        # ── Insert new assessment (audit trail) ───────────────────────────
        new_assessment = FootballSkillAssessment(
            user_license_id=license.id,
            skill_name=skill_key,
            points_earned=round(new_pct),
            points_total=100,
            percentage=new_pct,
            assessed_by=assessor_id,
            assessed_at=now,
            status="ASSESSED",
            requires_validation=False,
            notes=f"Auto-assessed from tournament EMA delta ({delta:+.1f})",
        )
        db.add(new_assessment)

        # ── Tier notification (guarded by feature flag) ────────────────────
        if settings.ENABLE_SKILL_TIER_NOTIFICATIONS:
            for threshold, tier_name in sorted(settings.SKILL_TIER_THRESHOLDS.items()):
                if current_pct < threshold <= new_pct:
                    create_skill_tier_notification(
                        db=db,
                        user_id=user_id,
                        skill_name=skill_key,
                        tier_name=tier_name,
                        new_pct=new_pct,
                        tournament_id=tournament_id,
                    )
                    logger.info(
                        "skill_tier_reached user=%d skill=%s tier=%s pct=%.1f",
                        user_id, skill_key, tier_name, new_pct,
                    )
                    break  # at most one tier crossed per update

        skills_written += 1

    # ── Summary log + metrics marker ──────────────────────────────────────
    # _metrics logger uses name "app.metrics.skill_propagation" — add a
    # dedicated handler in logging config to route to a metrics sink.
    logger.info(
        "update_skill_assessments: wrote %d skill(s) for user=%d license=%d",
        skills_written, user_id, license.id,
    )
    _metrics.info(
        "skill_propagation_complete user=%d license=%d skills_written=%d",
        user_id, license.id, skills_written,
    )


def record_tournament_participation(
    db: Session,
    user_id: int,
    tournament_id: int,
    placement: Optional[int],
    skill_points: Dict[str, float],
    base_xp: int,
    credits: int,
    assessed_by_id: Optional[int] = None,
    team_id: Optional[int] = None,
    foot_context: str = "neutral",
) -> TournamentParticipation:
    """
    Record tournament participation and update player skill assessments.

    This is the DATA layer - records numerical rewards only.
    Visual badges are handled separately by tournament_badge_service.py

    Args:
        db: Database session
        user_id: Player user ID
        tournament_id: Tournament (semester) ID
        placement: Player placement (1, 2, 3, or None)
        skill_points: Dictionary of skill_name -> points
        base_xp: Base XP from placement
        credits: Credits from placement
        assessed_by_id: ID of admin/instructor who triggered the reward

    Returns:
        Created TournamentParticipation record
    """
    # Calculate bonus XP from skill points
    bonus_xp = convert_skill_points_to_xp(db, skill_points)
    total_xp = base_xp + bonus_xp

    # ── Phase 1: upsert placement + rewards (no skill_rating_delta yet) ──────────
    # skill_rating_delta requires placement to be visible in DB before computing.
    existing_participation = db.query(TournamentParticipation).filter(
        TournamentParticipation.user_id == user_id,
        TournamentParticipation.semester_id == tournament_id
    ).first()

    # foot_context is resolved by the caller from the tournament's game preset (F4a).
    # Default "neutral" covers callers that don't supply laterality context.
    _foot_ctx = foot_context if foot_context in ("right", "left", "neutral") else "neutral"

    if existing_participation:
        existing_participation.placement = placement
        existing_participation.skill_points_awarded = skill_points if skill_points else None
        existing_participation.xp_awarded = total_xp
        existing_participation.credits_awarded = credits
        existing_participation.foot_context = _foot_ctx
        participation = existing_participation
    else:
        participation = TournamentParticipation(
            user_id=user_id,
            semester_id=tournament_id,
            team_id=team_id,
            placement=placement,
            skill_points_awarded=skill_points if skill_points else None,
            xp_awarded=total_xp,
            credits_awarded=credits,
            foot_context=_foot_ctx,
        )
        db.add(participation)

    # Flush so placement is visible to the skill delta query (autoflush may handle
    # this, but an explicit flush guarantees it regardless of session config)
    db.flush()

    # ── Phase 2: compute isolated per-tournament EMA delta and write back ────────
    # S05: write-once guard — if skill_rating_delta is already set (e.g., on a retry
    # after a transient failure), do not recompute.  Recomputing on retry is unsafe
    # because new tournaments may have committed since the first run, causing the delta
    # to change even though the underlying placement did not change.
    if participation.skill_rating_delta is None:
        from app.services.skill_progression_service import compute_single_tournament_skill_delta
        rating_delta = compute_single_tournament_skill_delta(db, user_id, tournament_id) or None
        participation.skill_rating_delta = rating_delta
    else:
        rating_delta = participation.skill_rating_delta

    # ── Phase 3: propagate EMA delta → FootballSkillAssessment rows ──────────
    # Called after Phase 2 so that rating_delta is available.
    # Guarded internally by ENABLE_TOURNAMENT_SKILL_PROPAGATION flag.
    update_skill_assessments(
        db,
        user_id,
        skill_points,
        assessed_by_id,
        skill_rating_delta=rating_delta,
        tournament_id=tournament_id,
    )

    # Create XP transaction for bonus XP (if any)
    if bonus_xp > 0:
        from app.models.user import User
        tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
        tournament_name = tournament.name if tournament else f"Tournament #{tournament_id}"

        # R07: Atomic XP balance increment — prevents lost-update race when two concurrent
        # distributions for different tournaments both read the same stale xp_balance.
        new_balance = db.execute(
            text(
                "UPDATE users SET xp_balance = xp_balance + :delta "
                "WHERE id = :uid RETURNING xp_balance"
            ),
            {"delta": bonus_xp, "uid": user_id},
        ).scalar() or 0

        # R06: Idempotency key prevents duplicate XP grants on concurrent distribution retry.
        xp_idempotency_key = f"reward_xp_{tournament_id}_{user_id}"
        xp_transaction = XPTransaction(
            user_id=user_id,
            transaction_type="TOURNAMENT_SKILL_BONUS",
            amount=bonus_xp,
            balance_after=new_balance,
            description=f"Skill point bonus from {tournament_name}",
            idempotency_key=xp_idempotency_key,
            semester_id=tournament_id,
        )
        sp_xp = db.begin_nested()
        db.add(xp_transaction)
        try:
            sp_xp.commit()
        except IntegrityError:
            sp_xp.rollback()
            logger.debug(
                "record_tournament_participation: XP transaction idempotency key collision "
                "for user=%d tournament=%d — skipping duplicate insert.",
                user_id, tournament_id,
            )

    # Create credit transaction and update credit balance (if credits awarded)
    if credits > 0:
        from app.models.user import User
        from app.models.credit_transaction import CreditTransaction

        tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
        tournament_name = tournament.name if tournament else f"Tournament #{tournament_id}"

        # Determine rank display
        if placement == 1:
            rank_display = "#1"
        elif placement == 2:
            rank_display = "#2"
        elif placement == 3:
            rank_display = "#3"
        else:
            rank_display = f"#{placement}" if placement else "participation"

        # R07: Atomic credit balance increment — prevents lost-update race.
        new_credit_balance = db.execute(
            text(
                "UPDATE users SET credit_balance = credit_balance + :delta "
                "WHERE id = :uid RETURNING credit_balance"
            ),
            {"delta": credits, "uid": user_id},
        ).scalar() or 0

        # Generate idempotency key for credit transaction (R06)
        idempotency_key = f"tournament_reward_{tournament_id}_{user_id}_{placement}"

        credit_transaction = CreditTransaction(
            user_id=user_id,
            transaction_type="TOURNAMENT_REWARD",
            amount=credits,
            balance_after=new_credit_balance,
            description=f"Tournament '{tournament_name}' - Rank {rank_display} reward",
            idempotency_key=idempotency_key,
            semester_id=tournament_id,
        )
        sp_cr = db.begin_nested()
        db.add(credit_transaction)
        try:
            sp_cr.commit()
        except IntegrityError:
            sp_cr.rollback()
            logger.debug(
                "record_tournament_participation: credit transaction idempotency key collision "
                "for user=%d tournament=%d — skipping duplicate insert.",
                user_id, tournament_id,
            )

    return participation


def get_player_tournament_history(
    db: Session,
    user_id: int,
    limit: int = 50,
    offset: int = 0
) -> Tuple[List[Dict], int]:
    """
    Get comprehensive tournament history for a player.

    Args:
        db: Database session
        user_id: Player user ID
        limit: Maximum number of results
        offset: Pagination offset

    Returns:
        Tuple of (list of tournament dicts, total count)
    """
    # Query participations with tournament info
    query = db.query(
        TournamentParticipation,
        Semester
    ).join(
        Semester, TournamentParticipation.semester_id == Semester.id
    ).filter(
        TournamentParticipation.user_id == user_id
    ).order_by(
        desc(TournamentParticipation.achieved_at)
    )

    total_count = query.count()
    participations = query.limit(limit).offset(offset).all()

    results = []
    for participation, semester in participations:
        results.append({
            "tournament_id": semester.id,
            "tournament_name": semester.name,
            "tournament_format": semester.format,
            "specialization": semester.specialization_type,
            "start_date": semester.start_date.isoformat() if semester.start_date else None,
            "end_date": semester.end_date.isoformat() if semester.end_date else None,
            "placement": participation.placement,
            "skill_points_awarded": participation.skill_points_awarded,
            "xp_awarded": participation.xp_awarded,
            "credits_awarded": participation.credits_awarded,
            "achieved_at": participation.achieved_at.isoformat()
        })

    return results, total_count


def get_player_participation_stats(
    db: Session,
    user_id: int
) -> Dict:
    """
    Get aggregate participation statistics for a player.

    Args:
        db: Database session
        user_id: Player user ID

    Returns:
        Dictionary with aggregate stats
    """
    # Count total tournaments
    total_tournaments = db.query(func.count(TournamentParticipation.id)).filter(
        TournamentParticipation.user_id == user_id
    ).scalar() or 0

    # Count placements
    first_places = db.query(func.count(TournamentParticipation.id)).filter(
        TournamentParticipation.user_id == user_id,
        TournamentParticipation.placement == 1
    ).scalar() or 0

    second_places = db.query(func.count(TournamentParticipation.id)).filter(
        TournamentParticipation.user_id == user_id,
        TournamentParticipation.placement == 2
    ).scalar() or 0

    third_places = db.query(func.count(TournamentParticipation.id)).filter(
        TournamentParticipation.user_id == user_id,
        TournamentParticipation.placement == 3
    ).scalar() or 0

    # Sum XP and credits
    xp_sum = db.query(func.sum(TournamentParticipation.xp_awarded)).filter(
        TournamentParticipation.user_id == user_id
    ).scalar() or 0

    credits_sum = db.query(func.sum(TournamentParticipation.credits_awarded)).filter(
        TournamentParticipation.user_id == user_id
    ).scalar() or 0

    # Calculate total skill points per skill
    participations = db.query(TournamentParticipation).filter(
        TournamentParticipation.user_id == user_id,
        TournamentParticipation.skill_points_awarded.isnot(None)
    ).all()

    skill_totals = {}
    for participation in participations:
        if participation.skill_points_awarded:
            for skill_name, points in participation.skill_points_awarded.items():
                skill_totals[skill_name] = skill_totals.get(skill_name, 0) + points

    # Find top skill
    top_skill = None
    top_skill_points = 0
    if skill_totals:
        top_skill = max(skill_totals, key=skill_totals.get)
        top_skill_points = skill_totals[top_skill]

    return {
        "total_tournaments": total_tournaments,
        "first_places": first_places,
        "second_places": second_places,
        "third_places": third_places,
        "total_xp_earned": int(xp_sum),
        "total_credits_earned": int(credits_sum),
        "skill_totals": skill_totals,
        "top_skill": top_skill,
        "top_skill_points": top_skill_points
    }
