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

import logging

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User, UserRole
from ...models.session import Session as SessionModel
from ...models.attendance import Attendance, AttendanceHistory, AttendanceStatus, ConfirmationStatus
from ...services.player_participation_service import (
    ParticipationError,
    record_attendance,
    resolve_attendance_change_request,
    respond_to_attendance,
)

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
    """Mark attendance through the canonical participation authority."""
    try:
        result = record_attendance(
            db,
            actor=user,
            player_id=student_id,
            session_id=session_id,
            status=status.lower(),
            notes=notes,
            source="WEB",
        )
    except ParticipationError as exc:
        error = "student_not_enrolled" if exc.code == "BOOKING_NOT_FOUND" else exc.code.lower()
        return RedirectResponse(
            url=f"/sessions/{session_id}?error={error}", status_code=303
        )
    except ValueError:
        return RedirectResponse(url=f"/sessions/{session_id}?error=invalid_status", status_code=303)
    if result.change_requested:
        outcome = "change_requested"
    else:
        outcome = "attendance_unchanged" if result.replayed else "attendance_marked"
    return RedirectResponse(url=f"/sessions/{session_id}?success={outcome}", status_code=303)


@router.post("/sessions/{session_id}/attendance/confirm")
async def confirm_attendance(
    request: Request,
    session_id: int,
    action: str = Form(...),  # "confirm" or "dispute"
    dispute_reason: str = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Player confirms or disputes attendance through the canonical authority."""
    try:
        result = respond_to_attendance(
            db,
            player=user,
            session_id=session_id,
            action=action,
            dispute_reason=dispute_reason,
            source="WEB",
        )
    except ParticipationError as exc:
        return RedirectResponse(
            url=f"/sessions/{session_id}?error={exc.code.lower()}", status_code=303
        )
    message = "confirmed" if result.outcome == "confirm" else "disputed"
    return RedirectResponse(url=f"/sessions/{session_id}?success=attendance_{message}", status_code=303)


@router.post("/sessions/{session_id}/attendance/change-request")
async def handle_change_request(
    request: Request,
    session_id: int,
    action: str = Form(...),  # "approve" or "reject"
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Player resolves an instructor change request through the canonical authority."""
    try:
        result = resolve_attendance_change_request(
            db, player=user, session_id=session_id, action=action, source="WEB"
        )
    except ParticipationError as exc:
        return RedirectResponse(
            url=f"/sessions/{session_id}?error={exc.code.lower()}", status_code=303
        )
    return RedirectResponse(url=f"/sessions/{session_id}?success={result.outcome}", status_code=303)


# Pydantic model for toggle request
class ToggleSpecializationRequest(BaseModel):
    specialization: str
    is_active: bool
