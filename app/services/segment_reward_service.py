"""
Segment Reward Service

Handles per-segment skill delta computation and XP distribution for
training sessions.

Design principles:
  - No mutation of tournament EMA state (TournamentParticipation is untouched)
  - No direct writes to UserLicense.football_skills
  - XP flows exclusively through xp_service.award_xp → xp_transactions ledger
  - All writes are idempotent: re-running for the same (segment, attendance)
    pair is safe and produces no duplicates
  - Service is transaction-agnostic: callers own commit/rollback
  - Sessions with zero active segments return empty lists immediately
    (full backward compatibility)
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import distinct, func, text

from app.models.attendance import Attendance, AttendanceStatus
from app.models.session import Session as SessionModel
from app.models.session_segment import SessionSegment
from app.models.session_segment_result import SessionSegmentResult
from app.services.gamification import xp_service

_logger = logging.getLogger(__name__)

_DEFAULT_XP_PER_POINT = 10   # fallback when skill_point_conversion_rates is empty
_DEFAULT_SEGMENT_XP = 10     # XP per segment when session base_xp is unavailable


# ─────────────────────────────────────────────────────────────────────────────
# Pure functions (no DB access)
# ─────────────────────────────────────────────────────────────────────────────

def resolve_segment_skill_targets(
    segment: SessionSegment,
    session: SessionModel,
) -> dict[str, float]:
    """
    Resolve the effective skill target weights for a segment.

    Priority chain (first non-empty wins):
      1. segment.skill_targets                              (instructor override)
      2. session.session_reward_config["skill_areas"]       (session-level override)
      3. session.game_preset.game_config["skill_config"]["skill_weights"]  (preset)
      4. {}                                                 (no skills → no delta)

    Returns: {skill_key: weight}  — empty dict means no skill delta.
    Pure function; no DB access.
    """
    # Priority 1: instructor explicit override on the segment
    if segment.skill_targets and isinstance(segment.skill_targets, dict):
        return dict(segment.skill_targets)

    # Priority 2: session-level override from session_reward_config
    if session.session_reward_config and isinstance(session.session_reward_config, dict):
        skill_areas = session.session_reward_config.get("skill_areas")
        if skill_areas and isinstance(skill_areas, dict):
            return dict(skill_areas)
        # Also handle list-of-strings format (skill_areas as plain list)
        if skill_areas and isinstance(skill_areas, list):
            return {sk: 1.0 for sk in skill_areas if isinstance(sk, str)}

    # Priority 3: game preset skill weights
    if session.game_preset is not None:
        try:
            weights = (
                session.game_preset.game_config
                .get("skill_config", {})
                .get("skill_weights", {})
            )
            if weights and isinstance(weights, dict):
                return dict(weights)
        except (AttributeError, TypeError):
            pass

    # Priority 4: no skills available
    return {}


def compute_skill_deltas(
    skill_targets: dict[str, float],
    xp_awarded: int,
    conversion_rates: dict[str, int],
) -> dict[str, float]:
    """
    Translate skill weights + XP into per-skill additive deltas.

    Formula:
        raw_delta(skill) = (weight / sum_weights) * xp_awarded / conversion_rate(skill)

    Args:
        skill_targets:    {skill_key: weight}
        xp_awarded:       XP budget for this segment
        conversion_rates: {skill_key: xp_per_point} from skill_point_conversion_rates.
                          Default = _DEFAULT_XP_PER_POINT (10) for missing keys.

    Returns: {skill_key: delta}  — only skills with delta > 0 included.
    Pure function; no DB access.
    """
    if not skill_targets or xp_awarded <= 0:
        return {}

    sum_weights = sum(skill_targets.values())
    if sum_weights <= 0:
        return {}

    result: dict[str, float] = {}
    for skill_key, weight in skill_targets.items():
        rate = conversion_rates.get(skill_key, _DEFAULT_XP_PER_POINT)
        if rate <= 0:
            rate = _DEFAULT_XP_PER_POINT
        delta = round((weight / sum_weights) * xp_awarded / rate, 2)
        if delta > 0:
            result[skill_key] = delta

    return result


# ─────────────────────────────────────────────────────────────────────────────
# DB-backed helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_conversion_rates(db: Session) -> dict[str, int]:
    """
    Load skill_point_conversion_rates as {skill_category: xp_per_point}.
    Returns empty dict (triggers default fallback) if table is empty.
    """
    from app.models.tournament_achievement import SkillPointConversionRate
    rows = db.query(SkillPointConversionRate).all()
    return {r.skill_category: r.xp_per_point for r in rows}


def _xp_per_segment(session: SessionModel, active_segment_count: int) -> int:
    """
    Determine XP budget per segment by evenly dividing the session's base XP.

    Priority:
      1. session.session_reward_config["base_xp"]
      2. session.base_xp
      3. _DEFAULT_SEGMENT_XP (10)
    """
    if active_segment_count <= 0:
        return 0

    base_xp: int = _DEFAULT_SEGMENT_XP

    if session.session_reward_config and isinstance(session.session_reward_config, dict):
        cfg_xp = session.session_reward_config.get("base_xp")
        if cfg_xp is not None:
            base_xp = int(cfg_xp)
    elif session.base_xp is not None:
        base_xp = int(session.base_xp)

    return max(1, base_xp // active_segment_count)


# ─────────────────────────────────────────────────────────────────────────────
# Core write operations
# ─────────────────────────────────────────────────────────────────────────────

def award_segment_result(
    db: Session,
    segment: SessionSegment,
    attendance: Attendance,
    xp_per_segment: int,
    conversion_rates: dict[str, int],
) -> Optional[SessionSegmentResult]:
    """
    Write a SessionSegmentResult row for one (segment, attendance) pair.

    Returns the existing or newly created row; None if attendance is not present.
    Does NOT commit — caller owns the transaction boundary.
    """
    if attendance.status != AttendanceStatus.present:
        return None

    session = db.query(SessionModel).filter(
        SessionModel.id == segment.session_id
    ).options(
        joinedload(SessionModel.game_preset)
    ).first()

    if session is None:
        return None

    effective_targets = resolve_segment_skill_targets(segment, session)
    skill_deltas = compute_skill_deltas(effective_targets, xp_per_segment, conversion_rates)

    idem_key = f"seg_{segment.id}_att_{attendance.id}"

    sp = db.begin_nested()
    try:
        result = SessionSegmentResult(
            segment_id=segment.id,
            attendance_id=attendance.id,
            session_id=session.id,
            user_id=attendance.user_id,
            skill_deltas=skill_deltas,
            xp_awarded=xp_per_segment,
            idempotency_key=idem_key,
        )
        db.add(result)
        sp.commit()
    except IntegrityError:
        sp.rollback()
        # Already exists — fetch and return the existing row
        result = db.query(SessionSegmentResult).filter(
            SessionSegmentResult.segment_id == segment.id,
            SessionSegmentResult.attendance_id == attendance.id,
        ).first()
        if result is None:
            return None

    # XP through the unified ledger (idempotent — savepoint guard inside award_xp)
    if xp_per_segment > 0:
        xp_service.award_xp(
            db=db,
            user_id=attendance.user_id,
            xp_amount=xp_per_segment,
            reason=f"Training segment: {segment.label}",
            idempotency_key=f"{idem_key}_xp",
            transaction_type="TRAINING_SEGMENT_XP",
            semester_id=session.semester_id,
        )

    return result


def award_session_segments(
    db: Session,
    session_id: int,
    attendance_id: int,
) -> list[SessionSegmentResult]:
    """
    Award results for ALL active segments in a session for one attendance record.

    Called when an attendance record transitions to status=present and the
    session has at least one active segment.  Returns an empty list immediately
    if the session has no active segments (full backward compatibility).

    Does NOT commit — caller owns the transaction boundary.
    """
    attendance = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if attendance is None or attendance.status != AttendanceStatus.present:
        return []

    active_segments = (
        db.query(SessionSegment)
        .filter(
            SessionSegment.session_id == session_id,
            SessionSegment.is_active == True,
        )
        .order_by(SessionSegment.position)
        .all()
    )

    if not active_segments:
        return []

    # Load session once for XP calculation
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if session is None:
        return []

    xp_each = _xp_per_segment(session, len(active_segments))
    rates = _load_conversion_rates(db)

    results: list[SessionSegmentResult] = []
    for segment in active_segments:
        row = award_segment_result(db, segment, attendance, xp_each, rates)
        if row is not None:
            results.append(row)
        else:
            _logger.debug(
                "award_segment_result returned None for segment_id=%d attendance_id=%d",
                segment.id, attendance_id,
            )

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Read-path aggregate
# ─────────────────────────────────────────────────────────────────────────────

def get_training_skill_deltas_for_user(
    db: Session,
    user_id: int,
) -> dict[str, float]:
    """
    Aggregate all session_segment_results for a user into per-skill totals.

    Single JSONB expansion query — no N+1.

    Returns: {skill_key: total_delta}  — empty dict if no results exist.
    Used by get_skill_profile() to add training_delta alongside tournament_delta.
    """
    rows = db.execute(
        text(
            """
            SELECT kv.key, SUM(kv.value::float) AS total_delta
            FROM session_segment_results ssr,
                 jsonb_each_text(ssr.skill_deltas) AS kv(key, value)
            WHERE ssr.user_id = :uid
            GROUP BY kv.key
            """
        ),
        {"uid": user_id},
    ).fetchall()

    return {row[0]: round(row[1], 2) for row in rows}


def get_training_session_count_for_user(
    db: Session,
    user_id: int,
) -> int:
    """
    Count distinct training sessions that produced at least one segment result
    for the user.

    Returns: int — 0 if no results exist.
    Used by get_skill_profile() to populate the training_sessions field.
    """
    return (
        db.query(func.count(distinct(SessionSegmentResult.session_id)))
        .filter(SessionSegmentResult.user_id == user_id)
        .scalar()
        or 0
    )
