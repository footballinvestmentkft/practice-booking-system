"""
Student-facing enrollment for MINI_SEASON / ACADEMY_SEASON semesters.

Routes:
    GET  /semesters/enroll               — browse matching semesters
    POST /semesters/request-enrollment   — auto-approved enrollment + credit deduction
    POST /semesters/withdraw-enrollment  — 50% refund + booking cleanup

Pattern: tournaments.py CAMP enrollment (auto-approve, credit deduct, session auto-book).
"""
import logging
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...core.metrics import metrics
from ...core.structured_log import log_event
from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.audit_log import AuditLog
from ...models.booking import Booking, BookingStatus
from ...models.credit_transaction import CreditTransaction
from ...models.license import UserLicense
from ...models.semester import Semester, SemesterCategory, SemesterStatus
from ...models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ...models.session import Session as SessionModel
from ...models.user import User, UserRole
from ...services.semester_service import (
    create_enrollment_with_bookings,
    withdraw_enrollment_bookings,
)

_logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["programs"], include_in_schema=False)

_PROGRAM_CATEGORIES = {SemesterCategory.MINI_SEASON, SemesterCategory.ACADEMY_SEASON}
_BROWSE_STATUSES = {SemesterStatus.ONGOING, SemesterStatus.READY_FOR_ENROLLMENT}

_SPEC_COLORS = {
    "GANCUJU_PLAYER":      "#8b5cf6",
    "LFA_FOOTBALL_PLAYER": "#2563eb",
    "LFA_COACH":           "#059669",
    "INTERNSHIP":          "#d97706",
}


def _err(msg: str, return_url: str = "/semesters/enroll") -> RedirectResponse:
    return RedirectResponse(url=f"{return_url}?error={msg}", status_code=303)


# ── Browse ────────────────────────────────────────────────────────────────────

@router.get("/semesters/enroll", response_class=HTMLResponse)
async def semester_enroll_browse(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    spec_value = user.specialization.value if user.specialization else None
    color = _SPEC_COLORS.get(spec_value, "#667eea")

    available = []
    if spec_value:
        available = (
            db.query(Semester)
            .filter(
                Semester.semester_category.in_(_PROGRAM_CATEGORIES),
                Semester.status.in_(_BROWSE_STATUSES),
                Semester.specialization_type == spec_value,
            )
            .order_by(Semester.start_date.asc())
            .all()
        )

    my_enrollments = (
        db.query(SemesterEnrollment)
        .filter(
            SemesterEnrollment.user_id == user.id,
            SemesterEnrollment.semester_id.in_([s.id for s in available]),
        )
        .all()
    ) if available else []
    enrollment_map = {e.semester_id: e for e in my_enrollments}

    user_licenses = (
        db.query(UserLicense)
        .filter(UserLicense.user_id == user.id, UserLicense.is_active == True)
        .all()
    )

    session_counts: dict = {}
    if available:
        rows = (
            db.query(SessionModel.semester_id, func.count(SessionModel.id))
            .filter(
                SessionModel.semester_id.in_([s.id for s in available]),
                SessionModel.auto_generated == True,
            )
            .group_by(SessionModel.semester_id)
            .all()
        )
        session_counts = {sid: cnt for sid, cnt in rows}

    booking_stats: dict = {}
    enrolled_semester_ids = [sid for sid, e in enrollment_map.items() if e.is_active]
    if enrolled_semester_ids:
        rows = (
            db.query(SessionModel.semester_id, Booking.status, func.count(Booking.id))
            .join(Booking, Booking.session_id == SessionModel.id)
            .filter(
                Booking.user_id == user.id,
                SessionModel.semester_id.in_(enrolled_semester_ids),
                Booking.status.in_([BookingStatus.CONFIRMED, BookingStatus.WAITLISTED]),
            )
            .group_by(SessionModel.semester_id, Booking.status)
            .all()
        )
        for sid, status, count in rows:
            stats = booking_stats.setdefault(sid, {"confirmed": 0, "waitlisted": 0})
            if status == BookingStatus.CONFIRMED:
                stats["confirmed"] = count
            elif status == BookingStatus.WAITLISTED:
                stats["waitlisted"] = count

    return templates.TemplateResponse(
        "semester_enrollment_request.html",
        {
            "request": request,
            "user": user,
            "specialization_color": color,
            "available_semesters": available,
            "enrollment_map": enrollment_map,
            "user_licenses": user_licenses,
            "session_counts": session_counts,
            "booking_stats": booking_stats,
        },
    )


# ── Enroll ────────────────────────────────────────────────────────────────────

@router.post("/semesters/request-enrollment", response_class=HTMLResponse)
async def semester_request_enrollment(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    form = await request.form()
    semester_id = int(form.get("semester_id", 0))

    semester = db.query(Semester).filter(Semester.id == semester_id).first()
    if not semester or semester.semester_category not in _PROGRAM_CATEGORIES:
        return _err("Invalid+semester")

    if semester.status not in _BROWSE_STATUSES:
        return _err("Semester+not+open+for+enrollment")

    if user.role != UserRole.STUDENT:
        return _err("Student+role+required")

    license_ = (
        db.query(UserLicense)
        .filter(
            UserLicense.user_id == user.id,
            UserLicense.specialization_type == semester.specialization_type,
            UserLicense.is_active == True,
        )
        .first()
    )
    if not license_:
        return _err("No+active+license+for+this+specialization")

    existing = (
        db.query(SemesterEnrollment)
        .filter(
            SemesterEnrollment.user_id == user.id,
            SemesterEnrollment.semester_id == semester_id,
            SemesterEnrollment.is_active == True,
        )
        .first()
    )
    if existing:
        return _err("Already+enrolled")

    cost = semester.enrollment_cost or 0
    if user.credit_balance < cost:
        return _err(f"Insufficient+credits+(need+{cost})")

    if cost > 0:
        result = db.execute(
            sql_update(User)
            .where(User.id == user.id, User.credit_balance >= cost)
            .values(credit_balance=User.credit_balance - cost)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            db.rollback()
            return _err("Insufficient+credits+(concurrent+update)")
        db.refresh(user)

    now = datetime.utcnow()

    try:
        enrollment_id, n_confirmed, n_waitlisted = create_enrollment_with_bookings(
            db,
            semester_id=semester_id,
            user_id=user.id,
            license_id=license_.id,
            cost=cost,
            semester_name=semester.name,
            semester_code=semester.code,
            user_credit_balance=user.credit_balance,
            now=now,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return _err("Already+enrolled+(concurrent+submission)")
    except Exception:
        db.rollback()
        raise

    # Post-commit observability — explicit, always after commit
    log_event(_logger, "semester_enrollment_created",
              user_id=user.id, semester_id=semester_id, cost=cost,
              n_confirmed=n_confirmed, n_waitlisted=n_waitlisted)
    metrics.increment("semester_enrollments_total")
    if n_confirmed:
        metrics.increment("bookings_created", by=n_confirmed)
    if n_waitlisted:
        metrics.increment("bookings_waitlisted", by=n_waitlisted)

    return RedirectResponse(
        url=f"/semesters/enroll?success=enrolled&semester={semester.name}",
        status_code=303,
    )


# ── Withdraw ──────────────────────────────────────────────────────────────────

@router.post("/semesters/withdraw-enrollment", response_class=HTMLResponse)
async def semester_withdraw_enrollment(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    form = await request.form()
    enrollment_id = int(form.get("enrollment_id", 0))

    enrollment = (
        db.query(SemesterEnrollment)
        .filter(
            SemesterEnrollment.id == enrollment_id,
            SemesterEnrollment.user_id == user.id,
            SemesterEnrollment.is_active == True,
        )
        .first()
    )
    if not enrollment:
        return _err("Enrollment+not+found+or+already+withdrawn")

    semester = db.query(Semester).filter(Semester.id == enrollment.semester_id).first()
    cost = semester.enrollment_cost if semester and semester.enrollment_cost else 0
    refund = cost // 2

    enrollment.is_active = False
    enrollment.request_status = EnrollmentStatus.WITHDRAWN
    db.flush()

    if refund > 0:
        db.execute(
            sql_update(User)
            .where(User.id == user.id)
            .values(credit_balance=User.credit_balance + refund)
            .execution_options(synchronize_session=False)
        )
        db.refresh(user)
        db.add(CreditTransaction(
            user_license_id=enrollment.user_license_id,
            transaction_type="SEMESTER_UNENROLL_REFUND",
            amount=refund,
            balance_after=user.credit_balance,
            description=(
                f"Semester unenrollment refund (50%): "
                f"{semester.name if semester else enrollment.semester_id}"
            ),
            semester_id=enrollment.semester_id,
            enrollment_id=enrollment.id,
            idempotency_key=str(uuid.uuid4()),
        ))

    # Booking cleanup + waitlist auto-promotion (service owns query logic)
    promoted_count = withdraw_enrollment_bookings(db, enrollment.id, user.id)

    db.add(AuditLog(
        user_id=user.id,
        action="SEMESTER_WITHDRAWN",
        resource_type="semester_enrollment",
        resource_id=enrollment.id,
        details={"semester_id": enrollment.semester_id, "refund": refund},
        request_method="POST",
        request_path="/semesters/withdraw-enrollment",
    ))

    db.commit()

    # Post-commit observability — explicit, always after commit
    log_event(_logger, "semester_enrollment_withdrawn",
              user_id=user.id, enrollment_id=enrollment.id,
              refund=refund, promoted_count=promoted_count)
    metrics.increment("semester_withdrawals_total")
    if promoted_count:
        metrics.increment("waitlist_promotions_total", by=promoted_count)

    return RedirectResponse(url="/semesters/enroll?success=withdrawn", status_code=303)
