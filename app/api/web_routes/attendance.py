"""
Attendance tracking routes
"""
from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel

from zoneinfo import ZoneInfo

import logging

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User, UserRole
from ...models.session import Session as SessionModel
from ...models.booking import Booking
from ...models.attendance import Attendance, AttendanceHistory, AttendanceStatus, ConfirmationStatus
from ...services.authorization_policy import AuthorizationPolicy

# Setup templates
BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/sessions/{session_id}/attendance/mark")
async def mark_attendance(
    request: Request,
    session_id: int,
    student_id: int = Form(...),
    status: str = Form(...),
    notes: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Mark attendance for a student (instructor only)"""
    if user.role != UserRole.INSTRUCTOR:
        return RedirectResponse(url=f"/sessions/{session_id}?error=unauthorized", status_code=303)

    # Verify session exists and instructor owns it
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if not session or not AuthorizationPolicy.can_manage_attendance(user, session):
        return RedirectResponse(url=f"/sessions/{session_id}?error=unauthorized", status_code=303)

    # Check if attendance can be marked (only from 15 min before start until session end)
    budapest_tz = ZoneInfo("Europe/Budapest")
    now = datetime.now(budapest_tz)

    # Database stores timestamps WITHOUT timezone (interpreted as Budapest time)
    session_start = session.date_start
    session_end = session.date_end
    if session_start.tzinfo is None:
        session_start = session_start.replace(tzinfo=budapest_tz)
    if session_end.tzinfo is None:
        session_end = session_end.replace(tzinfo=budapest_tz)

    # Allow marking 15 minutes before session starts
    if (session_start - timedelta(minutes=15)) > now:
        return RedirectResponse(url=f"/sessions/{session_id}?error=too_early", status_code=303)

    # Cannot mark attendance after session has ended
    if now > session_end:
        return RedirectResponse(url=f"/sessions/{session_id}?error=session_ended", status_code=303)

    # Verify student is enrolled
    booking = db.query(Booking).filter(
        Booking.session_id == session_id,
        Booking.user_id == student_id
    ).first()

    if not booking:
        return RedirectResponse(url=f"/sessions/{session_id}?error=student_not_enrolled", status_code=303)

    # Convert status string to enum
    try:
        attendance_status = AttendanceStatus[status.lower()]
    except KeyError:
        return RedirectResponse(url=f"/sessions/{session_id}?error=invalid_status", status_code=303)

    # Check if attendance already exists
    attendance = db.query(Attendance).filter(
        Attendance.session_id == session_id,
        Attendance.user_id == student_id
    ).first()

    if attendance:
        # Update existing attendance
        if attendance.confirmation_status == ConfirmationStatus.confirmed:
            # Create change request instead of directly changing
            attendance.pending_change_to = attendance_status.value
            attendance.change_requested_by = user.id
            attendance.change_requested_at = datetime.now(timezone.utc)
            attendance.change_request_reason = notes
            db.commit()

            logger.info("attendance_change_requested", extra={"from": attendance.status.value, "to": attendance_status.value})

            return RedirectResponse(
                url=f"/sessions/{session_id}?success=change_requested",
                status_code=303
            )

        old_status = attendance.status.value

        # Only log if status actually changed
        if attendance.status != attendance_status:
            # Create history entry
            history_entry = AttendanceHistory(
                attendance_id=attendance.id,
                changed_by=user.id,
                change_type='status_change',
                old_value=old_status,
                new_value=attendance_status.value,
                reason=notes
            )
            db.add(history_entry)

        attendance.status = attendance_status
        attendance.notes = notes
        attendance.updated_at = datetime.now(timezone.utc)
        attendance.marked_by = user.id

        # Update check-in time if marking as present
        if attendance_status == AttendanceStatus.present and not attendance.check_in_time:
            attendance.check_in_time = datetime.now(timezone.utc)
    else:
        # Create new attendance record
        attendance = Attendance(
            user_id=student_id,
            session_id=session_id,
            booking_id=booking.id,
            status=attendance_status,
            notes=notes,
            marked_by=user.id,
            confirmation_status=ConfirmationStatus.pending_confirmation,
            check_in_time=datetime.now(timezone.utc) if attendance_status == AttendanceStatus.present else None
        )
        db.add(attendance)
        db.flush()  # Get attendance.id

        # Create initial history entry
        history_entry = AttendanceHistory(
            attendance_id=attendance.id,
            changed_by=user.id,
            change_type='status_change',
            old_value=None,
            new_value=attendance_status.value,
            reason=notes
        )
        db.add(history_entry)

    db.commit()

    return RedirectResponse(url=f"/sessions/{session_id}?success=attendance_marked", status_code=303)


@router.post("/sessions/{session_id}/attendance/confirm")
async def confirm_attendance(
    request: Request,
    session_id: int,
    action: str = Form(...),  # "confirm" or "dispute"
    dispute_reason: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Student confirms or disputes attendance marked by instructor"""
    if user.role != UserRole.STUDENT:
        return RedirectResponse(url=f"/sessions/{session_id}?error=unauthorized", status_code=303)

    # Get attendance record for this student
    attendance = db.query(Attendance).filter(
        Attendance.session_id == session_id,
        Attendance.user_id == user.id
    ).first()

    if not attendance:
        return RedirectResponse(url=f"/sessions/{session_id}?error=no_attendance", status_code=303)

    # Check if session has ended - student can only confirm/dispute until session end
    session = db.query(SessionModel).filter(SessionModel.id == session_id).first()
    if session:
        budapest_tz = ZoneInfo("Europe/Budapest")
        now = datetime.now(budapest_tz)
        session_end = session.date_end
        if session_end.tzinfo is None:
            session_end = session_end.replace(tzinfo=budapest_tz)

        if now > session_end:
            return RedirectResponse(url=f"/sessions/{session_id}?error=session_ended", status_code=303)

    # Update confirmation status
    if action == "confirm":
        attendance.confirmation_status = ConfirmationStatus.confirmed
        attendance.student_confirmed_at = datetime.now(timezone.utc)
        attendance.dispute_reason = None
        message = "confirmed"

        # Log confirmation
        history_entry = AttendanceHistory(
            attendance_id=attendance.id,
            changed_by=user.id,
            change_type='confirmation',
            old_value='pending_confirmation',
            new_value='confirmed',
            reason='Student confirmed attendance'
        )
        db.add(history_entry)

    elif action == "dispute":
        attendance.confirmation_status = ConfirmationStatus.disputed
        attendance.student_confirmed_at = datetime.now(timezone.utc)
        attendance.dispute_reason = dispute_reason
        message = "disputed"

        # Log dispute
        history_entry = AttendanceHistory(
            attendance_id=attendance.id,
            changed_by=user.id,
            change_type='dispute',
            old_value='pending_confirmation',
            new_value='disputed',
            reason=dispute_reason
        )
        db.add(history_entry)

    else:
        return RedirectResponse(url=f"/sessions/{session_id}?error=invalid_action", status_code=303)

    attendance.updated_at = datetime.now(timezone.utc)
    db.commit()

    return RedirectResponse(url=f"/sessions/{session_id}?success=attendance_{message}", status_code=303)


@router.post("/sessions/{session_id}/attendance/change-request")
async def handle_change_request(
    request: Request,
    session_id: int,
    action: str = Form(...),  # "approve" or "reject"
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Student approves or rejects instructor's change request"""
    if user.role != UserRole.STUDENT:
        return RedirectResponse(url=f"/sessions/{session_id}?error=unauthorized", status_code=303)

    # Get attendance record for this student
    attendance = db.query(Attendance).filter(
        Attendance.session_id == session_id,
        Attendance.user_id == user.id
    ).first()

    if not attendance or not attendance.pending_change_to:
        return RedirectResponse(url=f"/sessions/{session_id}?error=no_change_request", status_code=303)

    if action == "approve":
        # Student approved the change - apply it
        old_status = attendance.status.value
        new_status = attendance.pending_change_to

        # Log the approved change
        history_entry = AttendanceHistory(
            attendance_id=attendance.id,
            changed_by=user.id,
            change_type='change_approved',
            old_value=old_status,
            new_value=new_status,
            reason=f"Student approved instructor's change request. Reason: {attendance.change_request_reason or 'N/A'}"
        )
        db.add(history_entry)

        # Apply the change
        attendance.status = AttendanceStatus[new_status]
        attendance.notes = attendance.change_request_reason

        # Clear change request fields
        attendance.pending_change_to = None
        attendance.change_requested_by = None
        attendance.change_requested_at = None
        attendance.change_request_reason = None

        # Keep confirmation status as confirmed
        attendance.updated_at = datetime.now(timezone.utc)

        message = "change_approved"
        logger.info("attendance_change_approved", extra={"from": old_status, "to": new_status})

    elif action == "reject":
        # Student rejected the change - clear the request
        old_value = attendance.pending_change_to

        # Log the rejection
        history_entry = AttendanceHistory(
            attendance_id=attendance.id,
            changed_by=user.id,
            change_type='change_rejected',
            old_value=attendance.status.value,
            new_value=old_value,
            reason=f"Student rejected instructor's change request to {old_value}"
        )
        db.add(history_entry)

        # Clear change request
        attendance.pending_change_to = None
        attendance.change_requested_by = None
        attendance.change_requested_at = None
        attendance.change_request_reason = None
        attendance.updated_at = datetime.now(timezone.utc)

        message = "change_rejected"
        logger.info("attendance_change_rejected", extra={"rejected_status": old_value})

    else:
        return RedirectResponse(url=f"/sessions/{session_id}?error=invalid_action", status_code=303)

    db.commit()

    return RedirectResponse(url=f"/sessions/{session_id}?success={message}", status_code=303)


# Pydantic model for toggle request
class ToggleSpecializationRequest(BaseModel):
    specialization: str
    is_active: bool

