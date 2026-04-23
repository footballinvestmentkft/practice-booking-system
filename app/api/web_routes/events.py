"""Events hub route — navigation-only landing page for all event types."""
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.semester import Semester, SemesterCategory, SemesterStatus
from ...models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ...models.user import User
from .helpers import require_student_onboarding
from .student_features import _spec_ctx

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["events"])

_BROWSE_STATUSES = {SemesterStatus.ONGOING, SemesterStatus.READY_FOR_ENROLLMENT}


@router.get("/events", response_class=HTMLResponse)
async def events_hub(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Events hub — navigation hub for Camps, Tournaments, Academy Season, Mini Season."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    today = date.today()

    # ── Counters (display only — no enrollment logic) ────────────────────────
    camps_open = db.query(Semester).filter(
        Semester.semester_category == SemesterCategory.CAMP,
        Semester.tournament_status == "ENROLLMENT_OPEN",
        Semester.status != SemesterStatus.CANCELLED,
        Semester.end_date >= today,
    ).count()

    tournaments_open = db.query(Semester).filter(
        Semester.semester_category == SemesterCategory.TOURNAMENT,
        Semester.tournament_status.in_(["ENROLLMENT_OPEN", "IN_PROGRESS"]),
        Semester.specialization_type == "LFA_FOOTBALL_PLAYER",
        Semester.status != SemesterStatus.CANCELLED,
        Semester.end_date >= today,
    ).count()

    spec_value = user.specialization.value if user.specialization else None

    academy_available = 0
    mini_available = 0
    if spec_value:
        academy_available = db.query(Semester).filter(
            Semester.semester_category == SemesterCategory.ACADEMY_SEASON,
            Semester.status.in_(_BROWSE_STATUSES),
            Semester.specialization_type == spec_value,
        ).count()

        mini_available = db.query(Semester).filter(
            Semester.semester_category == SemesterCategory.MINI_SEASON,
            Semester.status.in_(_BROWSE_STATUSES),
            Semester.specialization_type == spec_value,
        ).count()

    # ── User's active enrollments count (for context) ────────────────────────
    my_active = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id == user.id,
        SemesterEnrollment.is_active == True,
        SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
    ).count()

    return templates.TemplateResponse(
        "events_hub.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "camps_open": camps_open,
            "tournaments_open": tournaments_open,
            "academy_available": academy_available,
            "mini_available": mini_available,
            "my_active_enrollments": my_active,
        },
    )
