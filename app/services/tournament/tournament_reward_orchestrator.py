"""
Tournament Reward Orchestrator

Coordinates both participation rewards (skill/XP) and visual badges.
Provides unified interface for reward distribution.
"""
from typing import List, Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime
import logging

from app.models.semester import Semester
from app.models.tournament_ranking import TournamentRanking
from app.models.user import User
from app.utils.lock_logger import lock_timer
from app.schemas.tournament_rewards import (
    TournamentRewardResult,
    ParticipationReward,
    BadgeReward,
    SkillPointsAwarded,
    BadgeAwarded,
    BulkRewardDistributionResult,
    RewardPolicy,
    BadgeEvaluationContext
)
from app.schemas.reward_config import TournamentRewardConfig

# Import both service modules
from app.services.tournament import tournament_participation_service as participation_service
from app.services.tournament import tournament_badge_service as badge_service


# ─── S03: football_skills format normalisation ───────────────────────────────

def _normalise_skill_entry(entry) -> dict:
    """
    Ensure a football_skills entry is in the V2 dict format before deep-merge.

    The assessment path (FootballSkillService.recalculate_skill_average) may
    write bare float values for V1 / assessment-only users.  The orchestrator's
    deep-merge loop requires dict format to update sub-keys.

    Rules:
    - dict  → returned as-is (may be mutated by caller, but structure is preserved)
    - float / int / any scalar → promoted to dict with baseline=current_level=float(entry)

    This function is module-level so it can be unit-tested independently.
    """
    if isinstance(entry, dict):
        return entry
    try:
        val = float(entry)
    except (TypeError, ValueError):
        val = 50.0  # DEFAULT_BASELINE fallback
    return {
        "baseline":        val,
        "current_level":   val,
        "tournament_delta": 0.0,
        "total_delta":     0.0,
        "tournament_count": 0,
    }
from app.services import skill_progression_service

logger = logging.getLogger(__name__)

# Default reward policy (fallback)
DEFAULT_REWARD_POLICY = RewardPolicy()


def _extract_tier(config: dict, *keys) -> dict:
    """
    Extract a placement tier from a reward config dict.

    Supports two stored formats:
      - create.py / admin UI: {"first_place": {"xp": 100, "credits": 50}, ...}
      - OPS wizard:           {"1": {"xp": 2000, "credits": 1000}, ...}
      - Legacy schema:        {"first_place": {"xp_multiplier": 1.5, "credits": 500}, ...}

    Returns a dict with "xp" and "credits" keys, or empty dict if not found.
    """
    for key in keys:
        tier = config.get(key)
        if tier and isinstance(tier, dict):
            xp = tier.get("xp") or tier.get("xp_reward") or 0
            # Handle xp_multiplier format (old PlacementRewardConfig schema)
            if not xp and "xp_multiplier" in tier:
                base_xp = {"first_place": 500, "1": 500, "second_place": 300, "2": 300,
                           "third_place": 200, "3": 200}.get(key, 50)
                xp = int(base_xp * tier["xp_multiplier"])
            credits = tier.get("credits") or tier.get("credits_reward") or 0
            return {"xp": int(xp), "credits": int(credits)}
    return {}


def load_reward_policy_from_config(
    db: Session,
    tournament_id: int,
    tournament=None,
) -> RewardPolicy:
    """
    Load reward policy from tournament's reward_config JSONB field.

    Supports multiple stored formats:
      - create.py format: {"first_place": {"xp": 100, "credits": 50}, ...}
      - OPS wizard format: {"1": {"xp": 2000, "credits": 1000}, "2": {...}, "3": {...}}
      - Legacy schema:     {"first_place": {"xp_multiplier": 1.5, "credits": 500}}

    Falls back to DEFAULT_REWARD_POLICY if no config found or parsing fails.

    Pass `tournament` to skip the Semester re-fetch (avoids a redundant query when the
    caller already holds the loaded Semester object).
    """
    if tournament is None:
        tournament = db.query(Semester).filter(Semester.id == tournament_id).first()

    if not tournament or not tournament.reward_config:
        logger.info(f"No reward config found for tournament {tournament_id}, using default policy")
        return DEFAULT_REWARD_POLICY

    try:
        cfg = tournament.reward_config  # dict from JSONB

        first  = _extract_tier(cfg, "first_place",  "1")
        second = _extract_tier(cfg, "second_place", "2")
        third  = _extract_tier(cfg, "third_place",  "3")
        part   = _extract_tier(cfg, "participation", "participant", "4")

        policy = RewardPolicy(
            tournament_type="custom",
            first_place_xp=first.get("xp", 500),
            first_place_credits=first.get("credits", 100),
            second_place_xp=second.get("xp", 300),
            second_place_credits=second.get("credits", 50),
            third_place_xp=third.get("xp", 200),
            third_place_credits=third.get("credits", 25),
            participant_xp=part.get("xp", 50),
            participant_credits=part.get("credits", 0),
        )

        logger.info(
            f"Loaded reward policy for tournament {tournament_id}: "
            f"1st={policy.first_place_xp}xp/{policy.first_place_credits}cr, "
            f"2nd={policy.second_place_xp}xp/{policy.second_place_credits}cr, "
            f"3rd={policy.third_place_xp}xp/{policy.third_place_credits}cr"
        )
        return policy

    except Exception as e:
        logger.error(f"Failed to parse reward config for tournament {tournament_id}: {e}")
        return DEFAULT_REWARD_POLICY


def get_placement_rewards(placement: Optional[int], policy: RewardPolicy = DEFAULT_REWARD_POLICY) -> Dict:
    """
    Get XP and credits for a placement based on reward policy.

    Args:
        placement: Player placement (1, 2, 3, or None)
        policy: Reward policy to use

    Returns:
        Dictionary with xp and credits
    """
    if placement == 1:
        return {"xp": policy.first_place_xp, "credits": policy.first_place_credits}
    elif placement == 2:
        return {"xp": policy.second_place_xp, "credits": policy.second_place_credits}
    elif placement == 3:
        return {"xp": policy.third_place_xp, "credits": policy.third_place_credits}
    else:
        return {"xp": policy.participant_xp, "credits": policy.participant_credits}


def build_badge_evaluation_context(
    db: Session,
    user_id: int,
    tournament_id: int,
    placement: Optional[int],
    total_participants: int
) -> BadgeEvaluationContext:
    """
    Build evaluation context for badge awarding decisions.

    Gathers all relevant data for determining which badges to award.
    """
    # Get tournament info
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()

    # Get user's previous tournament history
    from app.models.tournament_achievement import TournamentParticipation
    previous_participations = db.query(TournamentParticipation).filter(
        TournamentParticipation.user_id == user_id,
        TournamentParticipation.semester_id != tournament_id
    ).all()

    previous_count = len(previous_participations)
    previous_placements = [p.placement for p in previous_participations if p.placement]

    # Calculate consecutive wins
    consecutive_wins = 0
    for p in sorted(previous_participations, key=lambda x: x.achieved_at, reverse=True):
        if p.placement == 1:
            consecutive_wins += 1
        else:
            break

    return BadgeEvaluationContext(
        user_id=user_id,
        tournament_id=tournament_id,
        placement=placement,
        total_participants=total_participants,
        previous_tournaments_count=previous_count,
        previous_placements=previous_placements,
        consecutive_wins=consecutive_wins,
        tournament_format=tournament.format if tournament else "",
        measurement_unit=tournament.measurement_unit if tournament else None
    )


def distribute_rewards_for_user(
    db: Session,
    user_id: int,
    tournament_id: int,
    placement: Optional[int],
    total_participants: int,
    reward_policy: RewardPolicy = DEFAULT_REWARD_POLICY,
    distributed_by: Optional[int] = None,
    force_redistribution: bool = False,
    is_sandbox_mode: bool = False,
    team_id: Optional[int] = None,
) -> TournamentRewardResult:
    """
    Distribute both participation rewards and badges for a single user.

    🔒 IDEMPOTENT: Checks if rewards already distributed for (user_id, tournament_id).
    Will not double-award unless force_redistribution=True.

    This is the main orchestration function that coordinates:
    1. Skill point calculation and participation recording (DATA layer)
    2. Badge awarding based on achievements (UI layer)

    Args:
        db: Database session
        user_id: Player user ID
        tournament_id: Tournament ID
        placement: Final placement (1, 2, 3, or None)
        total_participants: Total number of participants
        reward_policy: Custom reward policy (optional)
        distributed_by: Admin/instructor who triggered distribution
        force_redistribution: If True, allows re-distribution (updates existing records)
        is_sandbox_mode: If True, skip skill profile persistence (sandbox isolation)

    Returns:
        TournamentRewardResult with both participation and badge data

    Raises:
        ValueError: If rewards already distributed and force_redistribution=False
    """
    from app.models.tournament_achievement import TournamentParticipation

    # ========================================================================
    # 🔒 IDEMPOTENCY GUARD: Check if already distributed
    # R02: FOR UPDATE serialises concurrent distribute_rewards_for_user() calls
    # for the same (user_id, tournament_id) pair.  Thread B blocks here until
    # Thread A commits, then sees the committed row and returns early.
    # ========================================================================
    with lock_timer("reward", "TournamentParticipation", user_id, logger,
                    caller="distribute_rewards_for_user.idempotency_guard"):
        existing_participation = db.query(TournamentParticipation).filter(
            TournamentParticipation.user_id == user_id,
            TournamentParticipation.semester_id == tournament_id
        ).with_for_update().first()

    if existing_participation and not force_redistribution:
        # Already distributed - return existing summary
        return get_user_reward_summary(db, user_id, tournament_id)

    # Get tournament info
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    tournament_name = tournament.name if tournament else f"Tournament #{tournament_id}"

    # ========================================================================
    # STEP 1: PARTICIPATION REWARDS (Skill Points + XP + Credits)
    # ========================================================================

    # Get base rewards for placement
    placement_rewards = get_placement_rewards(placement, reward_policy)
    base_xp = placement_rewards["xp"]
    credits = placement_rewards["credits"]

    # Calculate skill points
    skill_points = participation_service.calculate_skill_points_for_placement(
        db, tournament_id, placement
    )

    # Convert skill points to bonus XP
    bonus_xp = participation_service.convert_skill_points_to_xp(db, skill_points)
    total_xp = base_xp + bonus_xp

    # Resolve foot_context from the already-loaded tournament preset (F4a — laterality).
    _preset    = getattr(tournament, "game_preset", None) if tournament else None
    _foot_ctx  = getattr(_preset, "foot_context", "neutral") if _preset is not None else "neutral"

    # Record participation
    participation_record = participation_service.record_tournament_participation(
        db, user_id, tournament_id, placement, skill_points, base_xp, credits, distributed_by,
        team_id=team_id,
        foot_context=_foot_ctx,
    )

    # ── Monitoring: log dominant/minor delta ratio (sampled: podium placements only) ──
    _skill_delta = participation_record.skill_rating_delta if participation_record else None
    if _skill_delta and isinstance(_skill_delta, dict) and len(_skill_delta) >= 2 and placement in (1, 2, 3):
        _sorted_deltas = sorted(_skill_delta.values(), reverse=True)
        _dom, _min = _sorted_deltas[0], _sorted_deltas[-1]
        _ratio = round(_dom / _min, 3) if _min and _min != 0 else None
        logger.debug(
            "skill_delta_ratio  user=%d  tournament=%d  placement=%d  "
            "dom=%.1f  min=%.1f  ratio=%s  deltas=%s",
            user_id, tournament_id, placement,
            _dom, _min, _ratio, _skill_delta,
        )

    # ========================================================================
    # STEP 1.5: APPLY SKILL DELTAS TO PLAYER PROFILE (Dynamic Progression)
    # ========================================================================
    # 🧪 SANDBOX MODE GUARD: Skip skill persistence in sandbox to maintain isolation
    if is_sandbox_mode:
        logger.info(
            f"🧪 SANDBOX MODE: Skipping skill profile persistence for user {user_id} "
            f"(skills calculated in-memory only for verdict)"
        )
    else:
        # Persist computed skill deltas back into UserLicense.football_skills JSONB.
        # skill_progression_service.get_skill_profile() calculates current_level /
        # tournament_delta from all TournamentParticipation rows (idempotent read).
        # We write the result back so the dashboard/performance-card always has
        # up-to-date values without re-computing on every page load.
        try:
            from app.models.license import UserLicense
            from sqlalchemy.orm.attributes import flag_modified
            from datetime import timezone
            from app.services.skill_progression import (
                update_lateral_component,
                aggregate_lateral_components,
            )

            # R04: Lock UserLicense row before reading football_skills JSONB.
            # Prevents two concurrent distributions from both reading stale skills,
            # merging independently, and last-writer-wins overwriting the other.
            # lock_timer measures time from FOR UPDATE through flag_modified (true hold time).
            with lock_timer("skill", "UserLicense", None, logger,
                            caller="distribute_rewards_for_user.skill_writeback"):
                active_license = db.query(UserLicense).filter(
                    UserLicense.user_id == user_id,
                    UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
                    UserLicense.is_active == True
                ).with_for_update().first()

                if active_license and active_license.football_skills and participation_record:
                    # Compute the full skill profile from all participations (idempotent)
                    skill_profile = skill_progression_service.get_skill_profile(db, user_id)
                    computed = skill_profile.get("skills", {})

                    if computed:
                        updated_skills = dict(active_license.football_skills)

                        # S03: promote any float-format entries to dict before the merge loop.
                        # Prevents silent omission of skills written by the assessment path
                        # (FootballSkillService) or by V1 onboarding (bare float format).
                        for sk in list(updated_skills.keys()):
                            updated_skills[sk] = _normalise_skill_entry(updated_skills[sk])

                        # F4b — laterality write-back context
                        _foot_ctx   = getattr(participation_record, "foot_context", "neutral") or "neutral"
                        _raw_deltas = participation_record.skill_rating_delta or {}
                        _right_ft   = active_license.right_foot_score
                        _left_ft    = active_license.left_foot_score

                        changed = 0
                        for skill_key, sdata in computed.items():
                            if skill_key not in updated_skills:
                                continue
                            entry = updated_skills[skill_key]
                            if not isinstance(entry, dict):
                                # Should not happen after normalisation — defensive guard
                                continue

                            # ── Lateral component update (F4b) ────────────────────────
                            # Apply this tournament's EMA delta to the foot-context bucket.
                            # update_lateral_component initialises the bucket from the
                            # pre-tournament current_level on first contact so that
                            # existing skill history is preserved.
                            _skill_delta = float(_raw_deltas.get(skill_key, 0.0))
                            _skill_fc = (
                                _preset.foot_context_for(skill_key)
                                if _preset is not None
                                else _foot_ctx
                            )
                            entry = update_lateral_component(entry, _skill_fc, _skill_delta)

                            # Re-aggregate current_level from all lateral components.
                            # Falls back to the EMA-derived sdata["current_level"] when
                            # no lateral_components exist (backward-compatible old records).
                            _agg = aggregate_lateral_components(entry, _right_ft, _left_ft)
                            entry["current_level"] = _agg

                            # ── Global tracking fields (unchanged semantics) ──────────
                            entry["tournament_delta"] = sdata["tournament_delta"]
                            entry["total_delta"]      = sdata["total_delta"]
                            entry["tournament_count"] = sdata["tournament_count"]
                            entry["last_updated"]     = datetime.now(timezone.utc).isoformat()
                            updated_skills[skill_key] = entry
                            changed += 1

                        active_license.football_skills = updated_skills
                        active_license.skills_last_updated_at = datetime.now(timezone.utc)
                        active_license.skills_updated_by = distributed_by or user_id
                        flag_modified(active_license, "football_skills")
                        logger.info(
                            f"✅ Persisted skill deltas for user {user_id} "
                            f"(license {active_license.id}): {changed} skills updated, "
                            f"placement={participation_record.placement}, "
                            f"foot_context=per-skill"
                        )
                else:
                    logger.warning(
                        f"Skipped skill write-back: user_id={user_id}, "
                        f"has_license={active_license is not None}, "
                        f"has_skills={active_license.football_skills is not None if active_license else False}, "
                        f"has_participation={participation_record is not None}"
                    )
        except Exception as e:
            logger.error(f"Failed to persist skill deltas for user {user_id}: {e}", exc_info=True)
            # Non-fatal: badges and XP are still awarded even if skill write-back fails

    # Build participation reward DTO
    skill_points_awarded = [
        SkillPointsAwarded(
            skill_name=skill_name,
            points=points,
            skill_category=None  # TODO: Get from mapping
        )
        for skill_name, points in skill_points.items()
    ]

    participation_reward = ParticipationReward(
        user_id=user_id,
        placement=placement,
        skill_points=skill_points_awarded,
        base_xp=base_xp,
        bonus_xp=bonus_xp,
        total_xp=total_xp,
        credits=credits
    )

    # ========================================================================
    # STEP 2: BADGE REWARDS (Visual Achievements)
    # ========================================================================

    awarded_badges = []

    # Award placement-based badges (Champion, Runner-Up, Third Place)
    if placement is not None and placement <= 3:
        placement_badges = badge_service.award_placement_badges(
            db, user_id, tournament_id, placement, total_participants
        )
        awarded_badges.extend(placement_badges)

    # Award participation badge (includes first tournament check)
    participation_badge = badge_service.award_participation_badge(
        db, user_id, tournament_id
    )
    awarded_badges.append(participation_badge)

    # Check and award milestone badges (Veteran, Legend, Triple Crown)
    milestone_badges = badge_service.check_and_award_milestone_badges(
        db, user_id, tournament_id
    )
    awarded_badges.extend(milestone_badges)

    # TODO: Award achievement badges (Undefeated, Comeback King, etc.)
    # This requires additional game data analysis - implement in Phase 2

    # Build badge reward DTO
    badges_awarded = [
        BadgeAwarded(
            badge_type=badge.badge_type,
            badge_category=badge.badge_category,
            title=badge.title,
            description=badge.description,
            icon=badge.icon,
            rarity=badge.rarity,
            metadata=badge.badge_metadata
        )
        for badge in awarded_badges
    ]

    # Determine rarest badge
    rarity_order = {"LEGENDARY": 1, "EPIC": 2, "RARE": 3, "UNCOMMON": 4, "COMMON": 5}
    rarest_badge = None
    if badges_awarded:
        rarest = min(badges_awarded, key=lambda b: rarity_order.get(b.rarity, 99))
        rarest_badge = rarest.rarity

    badge_reward = BadgeReward(
        user_id=user_id,
        badges=badges_awarded,
        total_badges_earned=len(badges_awarded),
        rarest_badge=rarest_badge
    )

    # ========================================================================
    # STEP 3: BUILD UNIFIED RESULT
    # ========================================================================

    result = TournamentRewardResult(
        user_id=user_id,
        tournament_id=tournament_id,
        tournament_name=tournament_name,
        participation=participation_reward,
        badges=badge_reward,
        distributed_at=datetime.now(),
        distributed_by=distributed_by
    )

    # Commit all changes
    # R02: If a racing thread committed first (uq_user_semester_participation fires),
    # roll back and return the already-committed summary rather than raising HTTP 500.
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        logger.warning(
            "distribute_rewards_for_user: IntegrityError at commit for user=%d tournament=%d "
            "— concurrent distribution already committed. Returning existing summary.",
            user_id, tournament_id,
        )
        return get_user_reward_summary(db, user_id, tournament_id)

    return result


def distribute_rewards_for_tournament(
    db: Session,
    tournament_id: int,
    reward_policy: Optional[RewardPolicy] = None,
    distributed_by: Optional[int] = None,
    force_redistribution: bool = False,
    is_sandbox_mode: bool = False
) -> BulkRewardDistributionResult:
    """
    Distribute rewards for all participants in a tournament.

    🎁 V2: Automatically loads reward policy from tournament.reward_config.
    If no config found, falls back to DEFAULT_REWARD_POLICY.

    Args:
        db: Database session
        tournament_id: Tournament ID
        reward_policy: Custom reward policy (optional, overrides config)
        distributed_by: Admin/instructor who triggered distribution
        force_redistribution: If True, allow re-distribution of rewards
        is_sandbox_mode: If True, skip skill profile persistence (sandbox isolation)

    Returns:
        BulkRewardDistributionResult with all rewards
    """
    # Get tournament
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not tournament:
        raise ValueError(f"Tournament {tournament_id} not found")

    # 🎁 V2: Load reward policy from config (unless overridden)
    if reward_policy is None:
        reward_policy = load_reward_policy_from_config(db, tournament_id)
        logger.info(f"Using reward policy from config for tournament {tournament_id}")

    # Get all rankings
    rankings = db.query(TournamentRanking).filter(
        TournamentRanking.tournament_id == tournament_id
    ).all()

    if not rankings:
        raise ValueError(f"No rankings found for tournament {tournament_id}")

    total_participants = len(rankings)
    rewards_distributed = []
    distribution_errors = []

    # Distribute rewards for each participant
    from app.models.tournament_achievement import TournamentParticipation

    for ranking in rankings:
        if ranking.user_id is None and ranking.team_id is not None:
            # TEAM ranking: expand to all active team members
            from app.models.team import TeamMember
            members = db.query(TeamMember).filter(
                TeamMember.team_id == ranking.team_id,
                TeamMember.is_active == True,
            ).all()
            for member in members:
                existing = db.query(TournamentParticipation).filter(
                    TournamentParticipation.user_id == member.user_id,
                    TournamentParticipation.semester_id == tournament_id,
                ).first()
                if existing and not force_redistribution:
                    continue
                try:
                    result = distribute_rewards_for_user(
                        db, member.user_id, tournament_id, ranking.rank,
                        total_participants, reward_policy, distributed_by,
                        force_redistribution, is_sandbox_mode,
                        team_id=ranking.team_id,
                    )
                    rewards_distributed.append(result)
                except Exception as e:
                    logger.error(
                        f"distribute_rewards_for_tournament: failed for "
                        f"user_id={member.user_id} team_id={ranking.team_id} "
                        f"tournament_id={tournament_id}: {e}",
                        exc_info=True,
                    )
                    distribution_errors.append({
                        "user_id": member.user_id,
                        "team_id": ranking.team_id,
                        "error": str(e),
                    })
            continue

        if ranking.user_id is None:
            continue  # safety guard: no user_id and no team_id — skip

        # INDIVIDUAL ranking
        existing = db.query(TournamentParticipation).filter(
            TournamentParticipation.user_id == ranking.user_id,
            TournamentParticipation.semester_id == tournament_id
        ).first()

        if existing and not force_redistribution:
            # Skip - already distributed
            continue

        try:
            result = distribute_rewards_for_user(
                db, ranking.user_id, tournament_id, ranking.rank,
                total_participants, reward_policy, distributed_by, force_redistribution, is_sandbox_mode
            )
            rewards_distributed.append(result)
        except Exception as e:
            logger.error(
                f"distribute_rewards_for_tournament: failed for "
                f"user_id={ranking.user_id} tournament_id={tournament_id}: {e}",
                exc_info=True,
            )
            distribution_errors.append({
                "user_id": ranking.user_id,
                "team_id": None,
                "error": str(e),
            })

    if distribution_errors:
        raise ValueError(
            f"Reward distribution partially failed for tournament {tournament_id}. "
            f"{len(distribution_errors)} participant(s) failed: {distribution_errors}"
        )

    # Build summary
    total_xp_awarded = sum(r.participation.total_xp for r in rewards_distributed)
    total_credits_awarded = sum(r.participation.credits for r in rewards_distributed)
    total_badges_awarded = sum(r.badges.total_badges_earned for r in rewards_distributed)

    placement_counts = {1: 0, 2: 0, 3: 0, None: 0}
    for r in rewards_distributed:
        placement = r.participation.placement
        if placement in placement_counts:
            placement_counts[placement] += 1
        elif placement is None:
            placement_counts[None] += 1

    summary = {
        "total_xp_awarded": total_xp_awarded,
        "total_credits_awarded": total_credits_awarded,
        "total_badges_awarded": total_badges_awarded,
        "placement_distribution": {
            "first_place": placement_counts[1],
            "second_place": placement_counts[2],
            "third_place": placement_counts[3],
            "participants": placement_counts[None]
        }
    }

    return BulkRewardDistributionResult(
        tournament_id=tournament_id,
        tournament_name=tournament.name,
        total_participants=total_participants,
        rewards_distributed=rewards_distributed,
        distribution_summary=summary,
        distributed_at=datetime.now(),
        distributed_by=distributed_by
    )


def get_user_reward_summary(
    db: Session,
    user_id: int,
    tournament_id: int
) -> Optional[TournamentRewardResult]:
    """
    Get reward summary for a user in a specific tournament.

    Fetches existing participation and badge data.
    """
    from app.models.tournament_achievement import TournamentParticipation, TournamentBadge

    # Get participation record
    participation = db.query(TournamentParticipation).filter(
        TournamentParticipation.user_id == user_id,
        TournamentParticipation.semester_id == tournament_id
    ).first()

    if not participation:
        return None

    # Get tournament
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    tournament_name = tournament.name if tournament else f"Tournament #{tournament_id}"

    # Build participation reward
    skill_points_awarded = []
    if participation.skill_points_awarded:
        skill_points_awarded = [
            SkillPointsAwarded(skill_name=k, points=v)
            for k, v in participation.skill_points_awarded.items()
        ]

    participation_reward = ParticipationReward(
        user_id=user_id,
        placement=participation.placement,
        skill_points=skill_points_awarded,
        base_xp=participation.xp_awarded,  # Note: includes bonus
        bonus_xp=0,  # Not stored separately
        total_xp=participation.xp_awarded,
        credits=participation.credits_awarded
    )

    # Get badges
    badges = db.query(TournamentBadge).filter(
        TournamentBadge.user_id == user_id,
        TournamentBadge.semester_id == tournament_id
    ).all()

    badges_awarded = [
        BadgeAwarded(
            badge_type=b.badge_type,
            badge_category=b.badge_category,
            title=b.title,
            description=b.description,
            icon=b.icon,
            rarity=b.rarity,
            metadata=b.badge_metadata
        )
        for b in badges
    ]

    # Determine rarest badge
    rarity_order = {"LEGENDARY": 1, "EPIC": 2, "RARE": 3, "UNCOMMON": 4, "COMMON": 5}
    rarest_badge = None
    if badges_awarded:
        rarest = min(badges_awarded, key=lambda b: rarity_order.get(b.rarity, 99))
        rarest_badge = rarest.rarity

    badge_reward = BadgeReward(
        user_id=user_id,
        badges=badges_awarded,
        total_badges_earned=len(badges_awarded),
        rarest_badge=rarest_badge
    )

    return TournamentRewardResult(
        user_id=user_id,
        tournament_id=tournament_id,
        tournament_name=tournament_name,
        participation=participation_reward,
        badges=badge_reward,
        distributed_at=participation.achieved_at,
        distributed_by=None  # Not tracked in current schema
    )
