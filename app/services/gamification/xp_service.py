"""
XP Service - Experience Points Management

Handles all XP calculation, awarding, and stat updates.
"""

from sqlalchemy.orm import Session
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
from typing import Optional

from ...models.gamification import UserStats
from ...models.attendance import Attendance
from ...models.session import Session as SessionType
from ...models.semester import Semester
from ...models.booking import Booking
from ...models.feedback import Feedback
from ...models.license import UserLicense
from ...models.xp_transaction import XPTransaction

from .utils import get_or_create_user_stats
from app.models.user import User
from app.services.player_identity_service import get_active_football_entitlement
from app.services.player_progression_service import PlayerProgressionService


def _canonical_player_entitlement(db: Session, user_id: int) -> UserLicense | None:
    """Return only a real canonical entitlement, not a permissive test/mock value."""
    entitlement = get_active_football_entitlement(db, user_id=user_id)
    return entitlement if isinstance(entitlement, UserLicense) else None


def _award_legacy_xp(
    db: Session,
    *,
    user_id: int,
    amount: int,
    transaction_type: str,
    idempotency_key: str,
    description: str,
    semester_id: int | None,
) -> tuple[UserStats, bool]:
    """Preserve the pre-PC5 path for specializations outside Player Core."""
    new_balance = db.execute(
        text(
            "UPDATE users SET xp_balance = xp_balance + :delta "
            "WHERE id = :uid RETURNING xp_balance"
        ),
        {"delta": amount, "uid": user_id},
    ).scalar() or 0
    savepoint = db.begin_nested()
    db.add(XPTransaction(
        user_id=user_id,
        transaction_type=transaction_type,
        amount=amount,
        balance_after=new_balance,
        description=description,
        idempotency_key=idempotency_key,
        semester_id=semester_id,
    ))
    try:
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()

    stats = get_or_create_user_stats(db, user_id)
    old_level = stats.level
    stats.total_xp = (stats.total_xp or 0) + amount
    stats.level = max(1, stats.total_xp // 1000)
    stats.updated_at = datetime.now(timezone.utc)
    return stats, stats.level > old_level


def award_attendance_xp(
    db: Session,
    attendance_id: int,
    quiz_score_percent: Optional[float] = None
) -> int:
    """Award attendance XP once; canonical Players receive a progression outcome."""
    attendance = db.query(Attendance).filter(Attendance.id == attendance_id).first()
    if not attendance:
        return 0
    session = db.query(SessionType).filter(SessionType.id == attendance.session_id).first()
    if not session:
        return 0
    if attendance.xp_earned > 0:
        return attendance.xp_earned

    base_xp = 50
    instructor_xp = 0
    quiz_xp = 0
    instructor_feedback = db.query(Feedback).filter(
        Feedback.session_id == session.id, Feedback.user_id == attendance.user_id
    ).first()
    if instructor_feedback and hasattr(instructor_feedback, "performance_rating"):
        instructor_xp = instructor_feedback.performance_rating * 10
    elif instructor_feedback and hasattr(instructor_feedback, "rating"):
        instructor_xp = instructor_feedback.rating * 10

    session_type = session.sport_type.upper() if hasattr(session.sport_type, "upper") else str(session.sport_type).upper()
    if session_type in ["HYBRID", "VIRTUAL"]:
        from ...models.quiz import SessionQuiz, QuizAttempt
        session_quiz = db.query(SessionQuiz).filter(
            SessionQuiz.session_id == session.id, SessionQuiz.is_required == True
        ).first()
        if session_quiz:
            if quiz_score_percent is None:
                best_attempt = db.query(QuizAttempt).filter(
                    QuizAttempt.user_id == attendance.user_id, QuizAttempt.quiz_id == session_quiz.quiz_id,
                    QuizAttempt.completed_at.isnot(None)
                ).order_by(QuizAttempt.score.desc()).first()
                if best_attempt:
                    quiz_score_percent = best_attempt.score
            if quiz_score_percent is not None:
                quiz_xp = 150 if quiz_score_percent >= 90 else 75 if quiz_score_percent >= 70 else 0

    xp_earned = int(base_xp + instructor_xp + quiz_xp)
    player = db.get(User, attendance.user_id)
    if _canonical_player_entitlement(db, attendance.user_id) is not None:
        PlayerProgressionService(db).apply_outcome(
            player=player, source_player_user_id=player.id,
            source_type="SESSION_ATTENDANCE", source_id=str(attendance.id),
            idempotency_key=f"attendance:{attendance.id}:progression", actor=player,
            xp_delta=xp_earned, provenance={"attendance_id": attendance.id, "session_id": session.id},
            context={"semester_id": session.semester_id},
        )
    else:
        stats, _ = _award_legacy_xp(
            db,
            user_id=attendance.user_id,
            amount=xp_earned,
            transaction_type="ATTENDANCE_XP",
            idempotency_key=f"attendance_xp_{attendance.id}",
            semester_id=session.semester_id,
            description=f"Attendance XP for session {session.id}",
        )
        stats.level = max(1, (stats.total_xp // 500) + 1)
    attendance.xp_earned = xp_earned
    db.commit()
    return xp_earned


def calculate_user_stats(db: Session, user_id: int) -> UserStats:
    """Calculate and update comprehensive user statistics"""
    stats = get_or_create_user_stats(db, user_id)

    bookings_query = db.query(
        Booking, SessionType, Semester
    ).join(
        SessionType, Booking.session_id == SessionType.id
    ).join(
        Semester, SessionType.semester_id == Semester.id
    ).filter(
        Booking.user_id == user_id
    ).all()

    unique_semesters = set()
    semester_dates = []
    total_bookings = 0
    total_cancelled = 0

    for booking, session, semester in bookings_query:
        unique_semesters.add(semester.id)
        semester_dates.append(semester.start_date)
        total_bookings += 1

        if booking.status.value == 'cancelled':
            total_cancelled += 1

    attendances = db.query(Attendance).filter(Attendance.user_id == user_id).count()
    feedback_count = db.query(Feedback).filter(Feedback.user_id == user_id).count()
    avg_rating = db.query(func.avg(Feedback.rating)).filter(Feedback.user_id == user_id).scalar() or 0.0

    attendance_xp = db.query(func.sum(Attendance.xp_earned)).filter(
        Attendance.user_id == user_id
    ).scalar() or 0

    stats.semesters_participated = len(unique_semesters)
    stats.first_semester_date = min(semester_dates) if semester_dates else None
    stats.total_bookings = total_bookings
    stats.total_attended = attendances
    stats.total_cancelled = total_cancelled
    stats.attendance_rate = (attendances / total_bookings * 100) if total_bookings > 0 else 0.0
    stats.feedback_given = feedback_count
    stats.average_rating_given = float(avg_rating)

    if attendance_xp > 0:
        stats.total_xp = max(stats.total_xp, attendance_xp)

    stats.level = max(1, (stats.total_xp // 500) + 1)
    stats.updated_at = datetime.now(timezone.utc)
    db.commit()

    return stats


def award_xp(
    db: Session,
    user_id: int,
    xp_amount: int,
    reason: str = "Quiz completion",
    idempotency_key: Optional[str] = None,
    transaction_type: str = "GENERAL_XP_AWARD",
    semester_id: Optional[int] = None,
) -> UserStats:
    """Compatibility wrapper over the transactional XP primitive.

    Canonical Football Players are routed through PlayerProgressionService.
    Legacy/non-Player callers retain their API without bypassing ledger integrity.
    """
    user = db.get(User, user_id)
    if user is None:
        raise ValueError("User not found")
    if _canonical_player_entitlement(db, user_id) is not None:
        if idempotency_key is None:
            raise ValueError("Canonical Player XP awards require an idempotency key")
        key = idempotency_key
        PlayerProgressionService(db).apply_outcome(
            player=user, source_player_user_id=user_id, source_type="LEGACY_PLAYER_XP_COMMAND",
            source_id=key, idempotency_key=f"player-xp:{key}", actor=user,
            xp_delta=int(xp_amount), provenance={"reason": reason, "transaction_type": transaction_type},
            context={"semester_id": semester_id},
        )
        stats = db.query(UserStats).filter_by(user_id=user_id).one()
    else:
        key = idempotency_key or (
            f"xp_{''.join(c for c in reason.lower() if c.isalnum() or c == '_')}"
            f"_{user_id}_{int(datetime.now(timezone.utc).timestamp())}"
        )
        stats, level_up = _award_legacy_xp(
            db,
            user_id=user_id,
            amount=int(xp_amount),
            transaction_type=transaction_type,
            idempotency_key=key,
            description=reason,
            semester_id=semester_id,
        )
        if level_up:
            print(f"🎉 User {user_id} leveled up to level {stats.level}!")
    db.commit()
    return stats
