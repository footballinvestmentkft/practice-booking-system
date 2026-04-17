"""
Student-facing enrollment for MINI_SEASON / ACADEMY_SEASON semesters.

Routes:
    GET  /semesters/enroll               — browse matching semesters
    POST /semesters/request-enrollment   — auto-approved enrollment + credit deduction
    POST /semesters/withdraw-enrollment  — 50% refund + booking cleanup

Pattern: tournaments.py CAMP enrollment (auto-approve, credit deduct, session auto-book).
"""
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.booking import Booking, BookingStatus
from ...models.credit_transaction import CreditTransaction
from ...models.license import UserLicense
from ...models.semester import Semester, SemesterCategory, SemesterStatus
from ...models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ...models.session import Session as SessionModel
from ...models.user import User, UserRole

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
        # Re-enrollment: reactivate WITHDRAWN enrollment if it exists (avoids unique constraint violation)
        withdrawn = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.user_id == user.id,
                SemesterEnrollment.semester_id == semester_id,
                SemesterEnrollment.user_license_id == license_.id,
                SemesterEnrollment.is_active == False,
                SemesterEnrollment.request_status == EnrollmentStatus.WITHDRAWN,
            )
            .first()
        )
        if withdrawn:
            withdrawn.request_status = EnrollmentStatus.APPROVED
            withdrawn.is_active = True
            withdrawn.approved_at = now
            withdrawn.enrolled_at = now
            enrollment = withdrawn
            db.flush()
        else:
            enrollment = SemesterEnrollment(
                user_id=user.id,
                semester_id=semester_id,
                user_license_id=license_.id,
                request_status=EnrollmentStatus.APPROVED,
                is_active=True,
                requested_at=now,
                approved_at=now,
                enrolled_at=now,
            )
            db.add(enrollment)
            db.flush()

        if cost > 0:
            db.add(CreditTransaction(
                user_license_id=license_.id,
                transaction_type="SEMESTER_ENROLLMENT",
                amount=-cost,
                balance_after=user.credit_balance,
                description=f"Semester enrollment: {semester.name} ({semester.code})",
                semester_id=semester_id,
                enrollment_id=enrollment.id,
                idempotency_key=str(uuid.uuid4()),
            ))

        # Auto-book all existing auto_generated sessions.
        # Batch dedup (1 query) + SELECT FOR UPDATE per session (capacity race guard)
        # + bulk insert — all committed atomically with enrollment + credit above.
        sessions = (
            db.query(SessionModel)
            .filter(
                SessionModel.semester_id == semester_id,
                SessionModel.auto_generated == True,
            )
            .all()
        )
        session_ids = [s.id for s in sessions]
        already_booked: set = set()
        if session_ids:
            already_booked = {
                r[0]
                for r in db.query(Booking.session_id).filter(
                    Booking.user_id == user.id,
                    Booking.session_id.in_(session_ids),
                ).all()
            }

        new_bookings = []
        for s in sessions:
            if s.id in already_booked:
                continue
            # Lock session row before capacity read — serializes concurrent auto-bookings
            # and prevents TOCTOU race where two students both see capacity available.
            s = db.query(SessionModel).filter(SessionModel.id == s.id).with_for_update().one()
            confirmed_count = (
                db.query(func.count(Booking.id))
                .filter(Booking.session_id == s.id, Booking.status == BookingStatus.CONFIRMED)
                .scalar() or 0
            )
            booking_status = (
                BookingStatus.CONFIRMED if confirmed_count < s.capacity
                else BookingStatus.WAITLISTED
            )
            new_bookings.append(Booking(
                user_id=user.id,
                session_id=s.id,
                enrollment_id=enrollment.id,
                status=booking_status,
                created_at=now,
            ))

        if new_bookings:
            db.bulk_save_objects(new_bookings)

        db.commit()
    except IntegrityError:
        db.rollback()
        return _err("Already+enrolled+(concurrent+submission)")
    except Exception:
        db.rollback()
        raise

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

    db.query(Booking).filter(
        Booking.enrollment_id == enrollment.id,
        Booking.user_id == user.id,
    ).delete(synchronize_session=False)

    db.commit()
    return RedirectResponse(url="/semesters/enroll?success=withdrawn", status_code=303)
