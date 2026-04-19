"""Camp student browse + enroll/unenroll routes."""
from datetime import datetime, date
import uuid

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import update as sql_update
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.booking import Booking, BookingStatus
from ....models.credit_transaction import CreditTransaction
from ....models.license import UserLicense
from ....models.semester import Semester, SemesterStatus, SemesterCategory
from ....models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ....models.session import Session as SessionModel
from ....models.user import User, UserRole
from . import templates, _get_player_age_category

router = APIRouter()


# ── Student: camp browse + enroll ─────────────────────────────────────────────

@router.get("/camps", response_class=HTMLResponse)
async def camps_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Student browse: list open camps and show enrollment status."""
    camps_query = (
        db.query(Semester)
        .filter(
            Semester.semester_category == SemesterCategory.CAMP,
            Semester.tournament_status == "ENROLLMENT_OPEN",
            Semester.status != SemesterStatus.CANCELLED,
            Semester.end_date >= date.today(),
        )
        .order_by(Semester.start_date.asc())
        .all()
    )
    camp_data = []
    for c in camps_query:
        enrollment_count = db.query(SemesterEnrollment).filter(
            SemesterEnrollment.semester_id == c.id,
            SemesterEnrollment.is_active == True,
        ).count()
        user_enrollment = db.query(SemesterEnrollment).filter(
            SemesterEnrollment.semester_id == c.id,
            SemesterEnrollment.user_id == user.id,
            SemesterEnrollment.is_active == True,
        ).first()
        camp_data.append({
            "camp": c,
            "enrollment_count": enrollment_count,
            "is_enrolled": user_enrollment is not None,
            "enrollment_status": user_enrollment.request_status.value if user_enrollment else None,
        })
    return templates.TemplateResponse("camps.html", {
        "request": request,
        "user": user,
        "camps": camp_data,
        "flash": request.query_params.get("flash"),
        "flash_type": request.query_params.get("flash_type", "info"),
    })


@router.post("/camps/{camp_id}/enroll", response_class=HTMLResponse)
async def camp_enroll(
    camp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Enroll current student in the given camp (auto-approved, deducts credits)."""

    def _err(msg: str):
        return RedirectResponse(
            url=f"/camps?flash={msg}&flash_type=error", status_code=303
        )

    # 1. Fetch camp
    camp = db.query(Semester).filter(
        Semester.id == camp_id,
        Semester.semester_category == SemesterCategory.CAMP,
        Semester.status != SemesterStatus.CANCELLED,
    ).first()
    if not camp:
        return _err("Camp+not+found")

    # 2. Status check
    if camp.tournament_status != "ENROLLMENT_OPEN":
        return _err("Camp+not+open+for+enrollment")

    # 3. Student only
    if user.role != UserRole.STUDENT:
        return _err("Only+students+can+enroll")

    # 4. LFA_FOOTBALL_PLAYER license required
    license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()
    if not license:
        return _err("LFA+Football+Player+license+required")

    # 4.5 Onboarding guard
    has_enrollment_for_lic = db.query(SemesterEnrollment.id).filter(
        SemesterEnrollment.user_license_id == license.id
    ).first() is not None
    effective_onboarding = (
        license.onboarding_completed
        or license.football_skills is not None
        or has_enrollment_for_lic
    )
    if not effective_onboarding:
        return _err("Complete+your+LFA+Football+Player+onboarding+before+enrolling")

    # 5. Not already enrolled
    existing = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == camp_id,
        SemesterEnrollment.user_id == user.id,
        SemesterEnrollment.is_active == True,
    ).first()
    if existing:
        return RedirectResponse(
            url="/camps?flash=Already+enrolled&flash_type=info", status_code=303
        )

    # 6. Credits check
    cost = camp.enrollment_cost if camp.enrollment_cost is not None else 500
    if user.credit_balance < cost:
        return _err(f"Insufficient+credits+(need+{cost}%2C+have+{user.credit_balance})")

    # 7. Create enrollment (auto-approved, no capacity limit for camps)
    age_category = _get_player_age_category(user)
    enrollment = SemesterEnrollment(
        user_id=user.id,
        semester_id=camp_id,
        user_license_id=license.id,
        age_category=age_category,
        request_status=EnrollmentStatus.APPROVED,
        approved_at=datetime.utcnow(),
        approved_by=user.id,
        payment_verified=True,
        is_active=True,
        enrolled_at=datetime.utcnow(),
        requested_at=datetime.utcnow(),
    )
    db.add(enrollment)
    db.flush()

    # 8. Atomic credit deduction
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

    # 9. Credit transaction record
    db.add(CreditTransaction(
        user_license_id=license.id,
        transaction_type="CAMP_ENROLLMENT",
        amount=-cost,
        balance_after=user.credit_balance,
        description=f"Camp enrollment: {camp.name} ({camp.code})",
        semester_id=camp_id,
        enrollment_id=enrollment.id,
        idempotency_key=str(uuid.uuid4()),
    ))

    # 10. Auto-book existing camp sessions (no-op if camp has none)
    sessions = db.query(SessionModel).filter(
        SessionModel.semester_id == camp_id
    ).all()
    for s in sessions:
        db.add(Booking(
            user_id=user.id,
            session_id=s.id,
            enrollment_id=enrollment.id,
            status=BookingStatus.CONFIRMED,
            created_at=datetime.utcnow(),
        ))

    db.commit()

    camp_name = camp.name.replace(" ", "+")
    return RedirectResponse(
        url=f"/camps?flash=Successfully+enrolled+in+{camp_name}&flash_type=success",
        status_code=303,
    )


@router.post("/camps/{camp_id}/unenroll", response_class=HTMLResponse)
async def camp_unenroll(
    camp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Withdraw student from camp (50% refund)."""
    enrollment = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id == user.id,
        SemesterEnrollment.semester_id == camp_id,
        SemesterEnrollment.is_active == True,
    ).first()
    if not enrollment:
        return RedirectResponse(
            url="/camps?flash=No+active+enrollment+found&flash_type=error",
            status_code=303,
        )

    camp = db.query(Semester).filter(Semester.id == camp_id).first()
    cost = (camp.enrollment_cost if camp and camp.enrollment_cost else 500)
    refund = cost // 2

    enrollment.is_active = False
    enrollment.request_status = EnrollmentStatus.WITHDRAWN
    db.add(enrollment)

    db.execute(
        sql_update(User)
        .where(User.id == user.id)
        .values(credit_balance=User.credit_balance + refund)
        .execution_options(synchronize_session=False)
    )
    db.refresh(user)

    db.add(CreditTransaction(
        user_license_id=enrollment.user_license_id,
        transaction_type="CAMP_UNENROLL_REFUND",
        amount=refund,
        balance_after=user.credit_balance,
        description=f"Camp unenrollment refund (50%): {camp.name if camp else camp_id}",
        semester_id=camp_id,
        enrollment_id=enrollment.id,
        idempotency_key=str(uuid.uuid4()),
    ))

    # Remove linked bookings
    db.query(Booking).filter(
        Booking.enrollment_id == enrollment.id,
        Booking.user_id == user.id,
    ).delete(synchronize_session=False)

    db.commit()

    return RedirectResponse(
        url=f"/camps?flash=Unenrolled.+{refund}+credits+refunded.&flash_type=info",
        status_code=303,
    )
