"""Tournament student browse + enroll/unenroll routes."""
from datetime import datetime, date
import uuid

from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, update as sql_update
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.booking import Booking, BookingStatus
from ....models.credit_transaction import CreditTransaction
from ....models.license import UserLicense
from ....models.semester import Semester, SemesterStatus
from ....models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ....models.session import Session as SessionModel
from ....models.user import User, UserRole
from . import templates, _get_player_age_category

router = APIRouter()


# ── Student: browse + enroll ───────────────────────────────────────────────────

@router.get("/tournaments", response_class=HTMLResponse)
async def tournaments_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Browse ENROLLMENT_OPEN / IN_PROGRESS tournaments available to the student."""
    tournaments = (
        db.query(Semester)
        .filter(
            and_(
                Semester.code.like("TOURN-%"),
                Semester.tournament_status.in_(["ENROLLMENT_OPEN", "IN_PROGRESS"]),
                Semester.specialization_type == "LFA_FOOTBALL_PLAYER",
                Semester.status != SemesterStatus.CANCELLED,
                Semester.end_date >= date.today(),
            )
        )
        .order_by(Semester.start_date.asc())
        .all()
    )

    tournament_data = []
    for t in tournaments:
        enrollment_count = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == t.id,
                SemesterEnrollment.is_active == True,
            )
            .count()
        )
        user_enrollment = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == t.id,
                SemesterEnrollment.user_id == user.id,
                SemesterEnrollment.is_active == True,
            )
            .first()
        )
        # Instructor info
        instructor = None
        if t.master_instructor_id:
            instructor = db.query(User).filter(User.id == t.master_instructor_id).first()

        tournament_data.append({
            "tournament": t,
            "enrollment_count": enrollment_count,
            "max_players": t.max_players or 999,
            "is_enrolled": user_enrollment is not None,
            "enrollment_status": user_enrollment.request_status.value if user_enrollment else None,
            "instructor": instructor,
        })

    return templates.TemplateResponse(
        "tournaments.html",
        {
            "request": request,
            "user": user,
            "tournaments": tournament_data,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type", "info"),
        },
    )


@router.post("/tournaments/{tournament_id}/enroll", response_class=HTMLResponse)
async def tournament_enroll(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Enroll current student in the given tournament (auto-approved, deducts credits)."""

    def _err(msg: str):
        return RedirectResponse(
            url=f"/tournaments?flash={msg}&flash_type=error", status_code=303
        )

    # 1. Fetch tournament
    tournament = db.query(Semester).filter(
        Semester.id == tournament_id, Semester.status != SemesterStatus.CANCELLED
    ).first()
    if not tournament:
        return _err("Tournament+not+found")

    # 2. Status check
    if tournament.tournament_status not in ("ENROLLMENT_OPEN", "IN_PROGRESS"):
        return _err("Tournament+not+open+for+enrollment")

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
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.user_id == user.id,
        SemesterEnrollment.is_active == True,
    ).first()
    if existing:
        return RedirectResponse(
            url="/tournaments?flash=Already+enrolled&flash_type=info", status_code=303
        )

    # 6. Credits check
    cost = tournament.enrollment_cost if tournament.enrollment_cost is not None else 500
    if user.credit_balance < cost:
        return _err(f"Insufficient+credits+(need+{cost}%2C+have+{user.credit_balance})")

    # 7. Capacity check
    enrolled_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.is_active == True,
        SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
    ).count()
    max_p = tournament.max_players if tournament.max_players else 999
    if enrolled_count >= max_p:
        return _err("Tournament+is+full")

    # 8. Create enrollment (auto-approved)
    age_category = _get_player_age_category(user)
    enrollment = SemesterEnrollment(
        user_id=user.id,
        semester_id=tournament_id,
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

    # 9. Atomic credit deduction
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

    # 10. Credit transaction record
    db.add(CreditTransaction(
        user_license_id=license.id,
        transaction_type="TOURNAMENT_ENROLLMENT",
        amount=-cost,
        balance_after=user.credit_balance,
        description=f"Tournament enrollment: {tournament.name} ({tournament.code})",
        semester_id=tournament_id,
        enrollment_id=enrollment.id,
        idempotency_key=str(uuid.uuid4()),
    ))

    # 11. Auto-book existing tournament sessions
    sessions = db.query(SessionModel).filter(
        SessionModel.semester_id == tournament_id
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

    tournament_name = tournament.name.replace(" ", "+")
    return RedirectResponse(
        url=f"/tournaments?flash=Successfully+enrolled+in+{tournament_name}&flash_type=success",
        status_code=303,
    )


@router.post("/tournaments/{tournament_id}/unenroll", response_class=HTMLResponse)
async def tournament_unenroll(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Withdraw student from tournament (50 % refund)."""
    enrollment = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id == user.id,
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.is_active == True,
    ).first()
    if not enrollment:
        return RedirectResponse(
            url="/tournaments?flash=No+active+enrollment+found&flash_type=error",
            status_code=303,
        )

    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    cost = (tournament.enrollment_cost if tournament and tournament.enrollment_cost else 500)
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
        transaction_type="TOURNAMENT_UNENROLL_REFUND",
        amount=refund,
        balance_after=user.credit_balance,
        description=f"Tournament unenrollment refund (50%): {tournament.name if tournament else tournament_id}",
        semester_id=tournament_id,
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
        url=f"/tournaments?flash=Unenrolled.+{refund}+credits+refunded.&flash_type=info",
        status_code=303,
    )
