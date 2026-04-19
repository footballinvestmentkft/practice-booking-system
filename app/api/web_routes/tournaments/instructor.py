"""Instructor tournament management routes."""
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.semester import Semester, SemesterStatus
from ....models.semester_enrollment import SemesterEnrollment
from ....models.user import User, UserRole
from . import templates

router = APIRouter()


# ── Instructor: manage assigned tournaments ────────────────────────────────────

@router.get("/instructor/tournaments", response_class=HTMLResponse)
async def instructor_tournaments(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Instructor/Admin view: list assigned tournaments with participant details."""
    if user.role not in (UserRole.INSTRUCTOR, UserRole.ADMIN):
        return RedirectResponse(url="/dashboard", status_code=303)

    tournaments = (
        db.query(Semester)
        .filter(
            and_(
                Semester.code.like("TOURN-%"),
                Semester.master_instructor_id == user.id,
                Semester.status != SemesterStatus.CANCELLED,
            )
        )
        .order_by(Semester.start_date.asc())
        .all()
    )

    tournament_data = []
    for t in tournaments:
        enrollments = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == t.id,
                SemesterEnrollment.is_active == True,
            )
            .all()
        )

        participants = []
        for enr in enrollments:
            student = db.query(User).filter(User.id == enr.user_id).first()
            if student:
                participants.append({
                    "name": student.name,
                    "email": student.email,
                    "age_category": enr.age_category or "—",
                    "enrolled_at": enr.enrolled_at,
                    "status": enr.request_status.value,
                })

        tournament_data.append({
            "tournament": t,
            "participants": participants,
            "enrollment_count": len(participants),
            "max_players": t.max_players or "—",
        })

    return templates.TemplateResponse(
        "instructor/tournaments.html",
        {
            "request": request,
            "user": user,
            "tournaments": tournament_data,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type", "info"),
        },
    )
