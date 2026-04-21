"""
XP Service - Experience Points Management

Handles all XP calculation, awarding, and stat updates.
"""

from sqlalchemy.orm import Session
from sqlalchemy import func, text
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
from typing import Optional

from ...models.xp_transaction import XPTransaction

from ...models.gamification import UserStats
from ...models.attendance import Attendance
from ...models.session import Session as SessionType
from ...models.semester import Semester
from ...models.booking import Booking
from ...models.feedback import Feedback

from .utils import get_or_create_user_stats


def award_attendance_xp(
    db: Session,
    attendance_id: int,
    quiz_score_percent: Optional[float] = None
) -> int:
    """
    Award XP based on session type, instructor evaluation, and quiz performance
    
    Returns total XP awarded
    """
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

    # Instructor evaluation XP
    instructor_feedback = db.query(Feedback).filter(
        Feedback.session_id == session.id,
        Feedback.user_id == attendance.user_id
    ).first()

    if instructor_feedback and hasattr(instructor_feedback, 'performance_rating'):
        instructor_xp = instructor_feedback.performance_rating * 10
    elif instructor_feedback and hasattr(instructor_feedback, 'rating'):
        instructor_xp = instructor_feedback.rating * 10

    # Quiz XP for HYBRID/VIRTUAL sessions
    session_type = session.sport_type.upper() if hasattr(session.sport_type, 'upper') else str(session.sport_type).upper()

    if session_type in ["HYBRID", "VIRTUAL"]:
        from ...models.quiz import SessionQuiz, QuizAttempt
        session_quiz = db.query(SessionQuiz).filter(
            SessionQuiz.session_id == session.id,
            SessionQuiz.is_required == True
        ).first()

        if session_quiz:
            if quiz_score_percent is None:
                best_attempt = db.query(QuizAttempt).filter(
                    QuizAttempt.user_id == attendance.user_id,
                    QuizAttempt.quiz_id == session_quiz.quiz_id,
                    QuizAttempt.completed_at.isnot(None)
                ).order_by(QuizAttempt.score.desc()).first()

                if best_attempt:
                    quiz_score_percent = best_attempt.score

            if quiz_score_percent is not None:
                if quiz_score_percent >= 90:
                    quiz_xp = 150
                elif quiz_score_percent >= 70:
                    quiz_xp = 75

    xp_earned = base_xp + instructor_xp + quiz_xp

    # 1. Audit field on attendance record (unchanged behaviour)
    attendance.xp_earned = xp_earned

    # 2. Atomic balance update — fixes the missing users.xp_balance write
    new_balance = db.execute(
        text(
            "UPDATE users SET xp_balance = xp_balance + :delta "
            "WHERE id = :uid RETURNING xp_balance"
        ),
        {"delta": xp_earned, "uid": attendance.user_id},
    ).scalar() or 0

    # 3. Ledger row — idempotency key is stable per attendance record
    idempotency_key = f"attendance_xp_{attendance.id}"
    sp = db.begin_nested()
    db.add(XPTransaction(
        user_id=attendance.user_id,
        transaction_type="ATTENDANCE_XP",
        amount=xp_earned,
        balance_after=new_balance,
        semester_id=session.semester_id,
        idempotency_key=idempotency_key,
    ))
    try:
        sp.commit()
    except IntegrityError:
        sp.rollback()  # Already awarded — safe to skip (re-run scenario)

    # 4. UserStats aggregate — kept as-is; re-derivation from ledger is deferred to F2
    stats = get_or_create_user_stats(db, attendance.user_id)
    stats.total_xp += xp_earned
    stats.level = max(1, (stats.total_xp // 500) + 1)
    stats.updated_at = datetime.now(timezone.utc)
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
    """Award XP to a user and update their stats.

    Optional kwargs allow callers to supply a stable idempotency_key,
    a specific transaction_type, and a semester_id.  When idempotency_key
    is None a timestamp-based key is generated (original one-shot-event
    behaviour).
    """
    # Atomic balance update — replaces ORM read-modify-write on users.xp_balance
    new_balance = db.execute(
        text(
            "UPDATE users SET xp_balance = xp_balance + :delta "
            "WHERE id = :uid RETURNING xp_balance"
        ),
        {"delta": xp_amount, "uid": user_id},
    ).scalar() or 0

    # Build idempotency key: caller-supplied or timestamp-based fallback
    if idempotency_key is None:
        idempotency_key = (
            f"xp_{''.join(c for c in reason.lower() if c.isalnum() or c == '_')}"
            f"_{user_id}_{int(datetime.now(timezone.utc).timestamp())}"
        )
    sp = db.begin_nested()
    db.add(XPTransaction(
        user_id=user_id,
        transaction_type=transaction_type,
        amount=xp_amount,
        balance_after=new_balance,
        description=reason,
        idempotency_key=idempotency_key,
        semester_id=semester_id,
    ))
    try:
        sp.commit()
    except IntegrityError:
        sp.rollback()

    # UserStats aggregate — kept as-is; re-derivation from ledger is deferred to F2
    stats = get_or_create_user_stats(db, user_id)
    stats.total_xp = (stats.total_xp or 0) + xp_amount
    new_level = max(1, stats.total_xp // 1000)
    level_up = new_level > stats.level
    stats.level = new_level
    stats.updated_at = datetime.now(timezone.utc)
    db.commit()

    if level_up:
        print(f"🎉 User {user_id} leveled up to level {new_level}!")

    return stats
