"""
Tournament Enrollment Service - Student enrollment and booking logic

This module handles student enrollment and auto-booking for tournaments.

Functions:
    - auto_book_students: Automatically book students for tournament sessions
    - enroll_player_admin: Admin enrolls a player (no credit check)
    - unenroll_player_admin: Admin removes a player's enrollment
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Dict

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.models.session import Session as SessionModel
from app.models.booking import Booking, BookingStatus
from app.models.user import User, UserRole
from app.models.semester import Semester
from app.models.tournament_configuration import TournamentConfiguration
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.services.player_identity_service import (
    PlayerIdentityError,
    get_active_football_entitlement,
    prepare_football_player_enrollment,
)


# ─────────────────────────────────────────────────────────────────────────────
# Admin enrollment helpers
# ─────────────────────────────────────────────────────────────────────────────

def enroll_player_admin(
    db: Session,
    tournament_id: int,
    user_id: int,
    admin_user_id: int,
) -> SemesterEnrollment:
    """Admin enrolls a player into an INDIVIDUAL tournament.

    Bypasses credit deduction and auto-approves the enrollment.
    Raises HTTPException for all validation failures.
    """
    # 1. Tournament exists + ENROLLMENT_OPEN
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail=f"Tournament {tournament_id} not found")
    if tournament.tournament_status != "ENROLLMENT_OPEN":
        raise HTTPException(
            status_code=409,
            detail=f"Tournament is not open for enrollment (status: {tournament.tournament_status})",
        )

    # 2. INDIVIDUAL only
    cfg = db.query(TournamentConfiguration).filter(
        TournamentConfiguration.semester_id == tournament_id
    ).first()
    if cfg and getattr(cfg, "participant_type", "INDIVIDUAL") == "TEAM":
        raise HTTPException(
            status_code=400,
            detail="Use team enrollment for TEAM tournaments",
        )

    # 3. Canonical Player identity, entitlement and season assignment.
    player = db.query(User).filter(
        User.id == user_id,
        User.role == UserRole.STUDENT,
    ).first()
    if player is None:
        raise HTTPException(status_code=404, detail=f"Player {user_id} not found")
    try:
        license = get_active_football_entitlement(db, user_id=user_id)
        if license is None:
            raise HTTPException(
                status_code=404,
                detail=f"User {user_id} does not have an active LFA Football Player license",
            )
        player_context = prepare_football_player_enrollment(
            db,
            user=player,
            user_license=license,
        )
    except PlayerIdentityError as exc:
        raise HTTPException(status_code=403, detail=exc.code) from exc
    # 4. Duplicate guard
    existing = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.user_id == user_id,
        SemesterEnrollment.is_active == True,
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"User {user_id} is already enrolled in this tournament",
        )

    # 5. Capacity check
    if cfg and cfg.max_players:
        enrolled_count = db.query(SemesterEnrollment).filter(
            SemesterEnrollment.semester_id == tournament_id,
            SemesterEnrollment.is_active == True,
            SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
        ).count()
        if enrolled_count >= cfg.max_players:
            raise HTTPException(
                status_code=409,
                detail=f"Tournament is full ({enrolled_count}/{cfg.max_players} players)",
            )

    now = datetime.now(timezone.utc)
    enrollment = SemesterEnrollment(
        user_id=user_id,
        semester_id=tournament_id,
        user_license_id=license.id,
        football_category_assignment_id=player_context.assignment.id,
        age_category=player_context.assignment.effective_category,
        request_status=EnrollmentStatus.APPROVED,
        is_active=True,
        payment_verified=True,   # admin bypass — no credit deduction
        approved_at=now,
        approved_by=admin_user_id,
        enrolled_at=now,
        requested_at=now,
    )
    db.add(enrollment)
    db.flush()
    return enrollment


def unenroll_player_admin(
    db: Session,
    tournament_id: int,
    user_id: int,
) -> None:
    """Admin removes a player's active enrollment from a tournament."""
    enrollment = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.user_id == user_id,
        SemesterEnrollment.is_active == True,
    ).first()
    if not enrollment:
        raise HTTPException(
            status_code=404,
            detail=f"No active enrollment found for user {user_id} in tournament {tournament_id}",
        )
    enrollment.is_active = False
    db.flush()


def auto_book_students(
    db: Session,
    session_ids: List[int],
    capacity_percentage: int = 70
) -> Dict[int, List[int]]:
    """
    Auto-book students for tournament sessions

    This function automatically creates bookings for active students
    up to a specified percentage of each session's capacity.

    Args:
        db: Database session
        session_ids: List of session IDs to book
        capacity_percentage: Percentage of capacity to fill (default: 70%)

    Returns:
        Dict mapping session_id -> list of booked user_ids

    Example:
        >>> bookings = auto_book_students(db, [123, 124], capacity_percentage=80)
        >>> # {123: [1, 2, 3], 124: [1, 2, 3, 4]}
    """
    bookings_map = {}

    # Get all active students
    students = db.query(User).filter(
        and_(
            User.role == UserRole.STUDENT,
            User.is_active == True
        )
    ).all()

    if not students:
        return bookings_map

    for session_id in session_ids:
        session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
        if not session:
            continue

        # Calculate target bookings
        target_bookings = int(session.capacity * (capacity_percentage / 100))
        target_bookings = min(target_bookings, len(students))

        # Book first N students
        booked_user_ids = []
        for i in range(target_bookings):
            student = students[i % len(students)]  # Cycle through students

            booking = Booking(
                user_id=student.id,
                session_id=session_id,
                status=BookingStatus.CONFIRMED
            )
            db.add(booking)
            booked_user_ids.append(student.id)

        bookings_map[session_id] = booked_user_ids

    db.commit()
    return bookings_map
