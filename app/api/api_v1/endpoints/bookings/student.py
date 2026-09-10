"""
Student booking operations
Create, view, cancel bookings and view statistics
"""
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta, timezone

from .....database import get_db
from .....dependencies import get_current_user
from .....models.user import User, UserRole
from .....models.session import Session as SessionTypel
from .....models.booking import Booking, BookingStatus
from .....models.attendance import Attendance, AttendanceStatus
from .....models.semester import Semester
from .....schemas.booking import (
    Booking as BookingSchema, BookingCreate, BookingWithRelations,
    BookingList
)
from .....api.helpers.participation_errors import participation_http_error
from .....core.metrics import metrics
from .....services.player_participation_service import (
    ParticipationError,
    book_player_session,
    cancel_player_booking,
)

router = APIRouter()


@router.post("/", response_model=BookingSchema)
def create_booking(
    booking_data: BookingCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Create new booking - STUDENTS ONLY

    🎯 REFACTORED: Uses spec services for validation
    - Session-based (LFA Player): Requires only UserLicense
    - Semester-based (Coach/Internship): Requires UserLicense + SemesterEnrollment + payment
    """
    try:
        result = book_player_session(
            db,
            player=current_user,
            session_id=booking_data.session_id,
            notes=booking_data.notes,
            source="API",
        )
    except ParticipationError as exc:
        raise participation_http_error(exc) from exc
    if not result.replayed:
        metrics.increment("bookings_created")
        if result.booking.status == BookingStatus.WAITLISTED:
            metrics.increment("bookings_waitlisted")
    return result.booking


@router.get("/me", response_model=BookingList)
def get_my_bookings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    semester_id: Optional[int] = Query(None),
    status: Optional[BookingStatus] = Query(None)
) -> Any:
    """
    Get current user's bookings
    """
    query = db.query(Booking).filter(Booking.user_id == current_user.id)

    # Apply filters
    if semester_id:
        query = query.join(SessionTypel).filter(SessionTypel.semester_id == semester_id)
    if status:
        query = query.filter(Booking.status == status)

    # Get total count
    total = query.count()

    # OPTIMIZED: Eager load relationships to avoid N+1 query pattern
    query = query.options(
        joinedload(Booking.user),
        joinedload(Booking.session)
    )

    # Apply pagination
    offset = (page - 1) * size
    bookings = query.offset(offset).limit(size).all()

    # OPTIMIZED: Batch fetch attendance status (reduces N+1 queries to 1 query)
    session_ids = [b.session_id for b in bookings]
    attended_sessions = db.query(Attendance.session_id).filter(
        and_(
            Attendance.user_id == current_user.id,
            Attendance.session_id.in_(session_ids),
            Attendance.status == AttendanceStatus.present
        )
    ).all()
    attended_session_ids = {row.session_id for row in attended_sessions}

    # Convert to response schema with attendance calculation
    booking_responses = []
    for booking in bookings:
        attended = booking.session_id in attended_session_ids

        # Explicitly create schema object instead of using __dict__
        booking_responses.append(BookingWithRelations(
            id=booking.id,
            user_id=booking.user_id,
            session_id=booking.session_id,
            status=booking.status,
            waitlist_position=booking.waitlist_position,
            notes=booking.notes,
            created_at=booking.created_at,
            updated_at=booking.updated_at,
            cancelled_at=booking.cancelled_at,
            attended_status=booking.attended_status,
            user=booking.user,
            session=booking.session,
            attended=attended
        ))

    return BookingList(
        bookings=booking_responses,
        total=total,
        page=page,
        size=size
    )


@router.get("/{booking_id}", response_model=BookingWithRelations)
def get_booking(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Get a specific booking by ID.
    Students can only view their own bookings.
    Admins and instructors can view any booking.
    """
    booking = db.query(Booking).options(
        joinedload(Booking.user),
        joinedload(Booking.session)
    ).filter(Booking.id == booking_id).first()

    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Authorization: Students can only view their own bookings
    if current_user.role == UserRole.STUDENT and booking.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not authorized to view this booking")

    # Check attendance
    attendance = db.query(Attendance).filter(
        and_(
            Attendance.user_id == booking.user_id,
            Attendance.session_id == booking.session_id,
            Attendance.status == AttendanceStatus.present
        )
    ).first()

    attended = attendance is not None

    # Return booking with relations
    return BookingWithRelations(
        id=booking.id,
        user_id=booking.user_id,
        session_id=booking.session_id,
        status=booking.status,
        waitlist_position=booking.waitlist_position,
        notes=booking.notes,
        created_at=booking.created_at,
        updated_at=booking.updated_at,
        cancelled_at=booking.cancelled_at,
        attended_status=booking.attended_status,
        user=booking.user,
        session=booking.session,
        attended=attended
    )


@router.delete("/{booking_id}")
def cancel_booking(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """
    Cancel own booking and auto-promote from waitlist
    """
    try:
        result = cancel_player_booking(
            db, actor=current_user, booking_id=booking_id, source="API"
        )
    except ParticipationError as exc:
        raise participation_http_error(exc) from exc
    response = {
        "message": "Booking cancelled successfully",
        "cancelled_booking_id": booking_id,
        "session_id": result.booking.session_id,
        "replayed": result.replayed,
    }
    if result.promoted_booking_id is not None:
        response["promoted_booking_id"] = result.promoted_booking_id
    return response


@router.get("/my-stats")
def get_my_booking_statistics(
    semester_id: Optional[int] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Any:
    """Get current user's booking statistics"""

    # Base query for user's bookings
    bookings_query = db.query(Booking).filter(Booking.user_id == current_user.id)

    # Filter by semester if provided
    if semester_id:
        bookings_query = bookings_query.join(SessionTypel).filter(
            SessionTypel.semester_id == semester_id
        )

    # Basic statistics
    total_bookings = bookings_query.count()
    confirmed_bookings = bookings_query.filter(Booking.status == BookingStatus.CONFIRMED).count()
    cancelled_bookings = bookings_query.filter(Booking.status == BookingStatus.CANCELLED).count()
    waitlisted_bookings = bookings_query.filter(Booking.status == BookingStatus.WAITLISTED).count()

    # Attendance statistics using Attendance model
    attended_sessions = db.query(Attendance).filter(
        and_(
            Attendance.user_id == current_user.id,
            Attendance.status == AttendanceStatus.PRESENT
        )
    )

    # Filter attendance by semester if provided
    if semester_id:
        attended_sessions = attended_sessions.join(SessionTypel).filter(
            SessionTypel.semester_id == semester_id
        )

    attended_count = attended_sessions.count()

    # Calculate rates
    attendance_rate = round((attended_count / max(confirmed_bookings, 1)) * 100, 1)
    booking_success_rate = round((confirmed_bookings / max(total_bookings, 1)) * 100, 1)

    # Current semester info
    current_semester = None
    if semester_id:
        current_semester = db.query(Semester).filter(Semester.id == semester_id).first()
    else:
        # Get active semester
        current_date = datetime.now().date()
        current_semester = db.query(Semester).filter(
            and_(
                Semester.start_date <= current_date,
                Semester.end_date >= current_date
            )
        ).first()

    # Calculate semester progress if we have a current semester
    semester_progress = None
    if current_semester:
        total_days = (current_semester.end_date - current_semester.start_date).days
        elapsed_days = (current_date - current_semester.start_date).days
        semester_progress = max(0, min(100, round((elapsed_days / max(total_days, 1)) * 100, 1)))

    return {
        "user_id": current_user.id,
        "user_name": current_user.name,
        "statistics": {
            "total_bookings": total_bookings,
            "confirmed_bookings": confirmed_bookings,
            "cancelled_bookings": cancelled_bookings,
            "waitlisted_bookings": waitlisted_bookings,
            "attended_sessions": attended_count,
            "attendance_rate": attendance_rate,
            "booking_success_rate": booking_success_rate
        },
        "current_semester": {
            "id": current_semester.id if current_semester else None,
            "name": current_semester.name if current_semester else None,
            "progress_percentage": semester_progress
        } if current_semester else None,
        "generated_at": datetime.now().isoformat()
    }
