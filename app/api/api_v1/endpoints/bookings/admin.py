"""
Admin booking operations
Get all bookings, confirm, cancel, and update attendance
"""
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
from datetime import datetime

from .....database import get_db
from .....dependencies import get_current_admin_user, get_current_admin_or_instructor_user
from .....models.user import User
from .....models.session import Session as SessionTypel
from .....models.booking import Booking, BookingStatus
from .....models.attendance import Attendance, AttendanceStatus
from .....schemas.booking import (
    Booking as BookingSchema, BookingWithRelations,
    BookingList, BookingCancel
)
from .....api.helpers.participation_errors import participation_http_error
from .....services.player_participation_service import (
    ParticipationError,
    cancel_player_booking,
    confirm_waitlisted_booking,
    record_attendance,
)

router = APIRouter()


@router.get("/", response_model=BookingList)
def get_all_bookings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    semester_id: Optional[int] = Query(None),
    status: Optional[BookingStatus] = Query(None)
) -> Any:
    """Get all bookings (Admin only)"""
    query = db.query(Booking)

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

    # Convert to response schema
    # Exclude SQLAlchemy relationship keys from __dict__ before spreading —
    # joinedload populates 'user' and 'session' into __dict__, which would
    # cause "got multiple values for keyword argument" when also passed explicitly.
    booking_responses = []
    _skip = {'_sa_instance_state', 'user', 'session', 'attendance'}
    for booking in bookings:
        base = {k: v for k, v in booking.__dict__.items() if k not in _skip}
        booking_responses.append(BookingWithRelations(
            **base,
            user=booking.user,
            session=booking.session
        ))

    return BookingList(
        bookings=booking_responses,
        total=total,
        page=page,
        size=size
    )


@router.post("/{booking_id}/confirm")
def confirm_booking(
    booking_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
) -> Any:
    """
    Confirm booking (Admin only)
    """
    try:
        result = confirm_waitlisted_booking(
            db, actor=current_user, booking_id=booking_id, source="API"
        )
    except ParticipationError as exc:
        raise participation_http_error(exc) from exc
    return {"message": "Booking confirmed successfully", "replayed": result.replayed}


@router.post("/{booking_id}/cancel")
def admin_cancel_booking(
    booking_id: int,
    cancel_data: BookingCancel,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
) -> Any:
    """
    Cancel booking (Admin only) and auto-promote from waitlist
    """
    try:
        result = cancel_player_booking(
            db,
            actor=current_user,
            booking_id=booking_id,
            reason=cancel_data.reason,
            admin_override=True,
            source="API",
        )
    except ParticipationError as exc:
        raise participation_http_error(exc) from exc
    response = {
        "message": "Booking cancelled by admin",
        "cancelled_booking_id": booking_id,
        "session_id": result.booking.session_id,
        "replayed": result.replayed,
    }
    if result.promoted_booking_id is not None:
        response["promoted_booking_id"] = result.promoted_booking_id
    return response


@router.patch("/{booking_id}/attendance", response_model=BookingSchema)
def update_booking_attendance(
    booking_id: int,
    attendance_data: Dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_or_instructor_user)
) -> Any:
    """Update booking attendance status (Admin/Instructor only)"""
    attendance_status = attendance_data.get("status")
    try:
        result = record_attendance(
            db,
            actor=current_user,
            booking_id=booking_id,
            status=attendance_status,
            notes=attendance_data.get("notes"),
            source="API",
        )
    except ValueError as exc:
        if not isinstance(exc, ParticipationError):
            raise HTTPException(status_code=400, detail="INVALID_ATTENDANCE_STATUS") from exc
        raise participation_http_error(exc) from exc
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if booking is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="BOOKING_NOT_FOUND"
        )
    return booking
