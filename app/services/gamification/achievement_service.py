"""
Achievement Service - User Achievement Data Retrieval

Handles fetching complete gamification data for users including stats, achievements, and progress.
"""

from sqlalchemy.orm import Session
from sqlalchemy import func
from types import SimpleNamespace
from typing import Dict

from ...models.gamification import UserAchievement, UserStats
from ...models.user import User
from ...models.attendance import Attendance
from ...models.feedback import Feedback
from ...models.semester import Semester
from ...models.session import Session as SessionType
from ...models.booking import Booking



def get_user_gamification_data(db: Session, user_id: int) -> Dict:
    """
    Get complete gamification data for a user

    Args:
        db: Database session
        user_id: User ID

    Returns:
        Dictionary containing:
            - stats: User statistics (XP, level, attendance, etc.)
            - achievements: List of earned achievements
            - status: Current student status (New/Returning/Veteran/Master)
            - next_level: Progress toward next level
            - semesters: List of semesters user participated in
            - current_semester: Current active semester
    """
    # Read-only projection: GET must never create/update stats or achievements.
    stored = db.query(UserStats).filter_by(user_id=user_id).one_or_none()
    booking_rows = db.query(Booking, SessionType, Semester).join(
        SessionType, Booking.session_id == SessionType.id
    ).join(Semester, SessionType.semester_id == Semester.id).filter(
        Booking.user_id == user_id
    ).all()
    semester_ids = {semester.id for _, _, semester in booking_rows}
    semester_dates = [semester.start_date for _, _, semester in booking_rows if semester.start_date]
    total_bookings = len(booking_rows)
    total_cancelled = sum(1 for booking, _, _ in booking_rows if getattr(booking.status, "value", booking.status) == "cancelled")
    total_attended = db.query(Attendance).filter(Attendance.user_id == user_id).count()
    feedback_given = db.query(Feedback).filter(Feedback.user_id == user_id).count()
    user = db.get(User, user_id)
    total_xp = int(stored.total_xp if stored is not None else (user.xp_balance if user else 0) or 0)
    level = int(stored.level if stored is not None else max(1, total_xp // 1000))
    stats = SimpleNamespace(
        semesters_participated=len(semester_ids), first_semester_date=min(semester_dates) if semester_dates else None,
        total_bookings=total_bookings, total_attended=total_attended, total_cancelled=total_cancelled,
        attendance_rate=(total_attended / total_bookings * 100) if total_bookings else 0.0,
        feedback_given=feedback_given, total_xp=total_xp, level=level,
    )

    # Get all user achievements
    achievements = db.query(UserAchievement).filter(
        UserAchievement.user_id == user_id
    ).order_by(UserAchievement.earned_at.desc()).all()

    # Get semester information for the user
    user_semesters = db.query(Semester).join(
        SessionType, Semester.id == SessionType.semester_id
    ).join(
        Booking, SessionType.id == Booking.session_id
    ).filter(Booking.user_id == user_id).distinct().order_by(Semester.start_date).all()

    # Get current semester (the latest one)
    current_semester = db.query(Semester).order_by(Semester.start_date.desc()).first()

    # Determine student status
    status = "\U0001f4da New Student"
    status_icon = "\U0001f4da"
    if stats.semesters_participated >= 5:
        status = "\U0001f451 Master Student"
        status_icon = "\U0001f451"
    elif stats.semesters_participated >= 3:
        status = "\U0001f3c5 Veteran Student"
        status_icon = "\U0001f3c5"
    elif stats.semesters_participated >= 2:
        status = "\U0001f504 Returning Student"
        status_icon = "\U0001f504"

    return {
        "stats": {
            "semesters_participated": stats.semesters_participated,
            "total_bookings": stats.total_bookings,
            "total_attended": stats.total_attended,
            "attendance_rate": stats.attendance_rate,
            "feedback_given": stats.feedback_given,
            "total_xp": stats.total_xp,
            "level": stats.level,
            "first_semester_date": stats.first_semester_date.isoformat() if stats.first_semester_date else None
        },
        "achievements": [
            {
                "id": ach.id,
                "title": ach.title,
                "description": ach.description,
                "icon": ach.icon,
                "badge_type": ach.badge_type,
                "earned_at": ach.earned_at.isoformat(),
                "semester_count": ach.semester_count
            }
            for ach in achievements
        ],
        "status": {
            "title": status,
            "icon": status_icon,
            "is_returning": stats.semesters_participated >= 2
        },
        "next_level": {
            "current_xp": stats.total_xp,
            "next_level_xp": (stats.level + 1) * 1000,
            "progress_percentage": ((stats.total_xp % 1000) / 1000) * 100
        },
        "semesters": [
            {
                "id": semester.id,
                "name": semester.name,
                "start_date": semester.start_date.isoformat() if semester.start_date else None,
                "end_date": semester.end_date.isoformat() if semester.end_date else None
            }
            for semester in user_semesters
        ],
        "current_semester": {
            "id": current_semester.id,
            "name": current_semester.name,
            "start_date": current_semester.start_date.isoformat() if current_semester.start_date else None,
            "end_date": current_semester.end_date.isoformat() if current_semester.end_date else None
        } if current_semester else None
    }
