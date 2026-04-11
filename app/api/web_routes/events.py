"""
Student Events web routes — event-domain landing + per-type browse/detail.

URL structure:
    GET  /events                        — landing page with type cards
    GET  /events/tournaments            — TOURNAMENT browse (enrolled + browse)
    GET  /events/tournaments/{id}       — TOURNAMENT student detail
    GET  /events/camps                  — CAMP browse (enrolled + browse)
    GET  /events/camps/{id}             — CAMP detail
    POST /events/camps/{id}/enroll      — CAMP self-service enrollment
    POST /events/camps/{id}/unenroll    — CAMP withdrawal (50% refund)
    GET  /events/academy-seasons        — Coming Soon stub
    GET  /events/mini-seasons           — Coming Soon stub
"""
from datetime import datetime, date
from pathlib import Path

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, update as sql_update
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.booking import Booking, BookingStatus
from ...models.credit_transaction import CreditTransaction
from ...models.license import UserLicense
from ...models.quiz import SessionQuiz, QuizAttempt
from ...models.semester import Semester, SemesterStatus, SemesterCategory
from ...models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ...models.session import Session as SessionModel
from ...models.tournament_configuration import TournamentConfiguration
from ...models.tournament_ranking import TournamentRanking
from ...models.user import User, UserRole
from .student_features import _spec_ctx

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()


# ── Landing ────────────────────────────────────────────────────────────────────

@router.get("/events", response_class=HTMLResponse)
async def events_landing(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Events landing page — 4 type cards linking to per-type browse pages."""
    tournament_enrolled = (
        db.query(SemesterEnrollment)
        .join(Semester, Semester.id == SemesterEnrollment.semester_id)
        .filter(
            SemesterEnrollment.user_id == user.id,
            SemesterEnrollment.is_active.is_(True),
            SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
            Semester.semester_category == SemesterCategory.TOURNAMENT,
        )
        .count()
    )
    camp_enrolled = (
        db.query(SemesterEnrollment)
        .join(Semester, Semester.id == SemesterEnrollment.semester_id)
        .filter(
            SemesterEnrollment.user_id == user.id,
            SemesterEnrollment.is_active.is_(True),
            SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
            Semester.semester_category == SemesterCategory.CAMP,
        )
        .count()
    )
    tournament_available = (
        db.query(Semester)
        .filter(
            Semester.semester_category == SemesterCategory.TOURNAMENT,
            Semester.tournament_status == "ENROLLMENT_OPEN",
            Semester.status != SemesterStatus.CANCELLED,
        )
        .count()
    )
    camp_available = (
        db.query(Semester)
        .filter(
            Semester.semester_category == SemesterCategory.CAMP,
            Semester.tournament_status == "ENROLLMENT_OPEN",
            Semester.status != SemesterStatus.CANCELLED,
        )
        .count()
    )
    return templates.TemplateResponse(
        "events_landing.html",
        {
            "request": request,
            "user": user,
            "tournament_enrolled": tournament_enrolled,
            "camp_enrolled": camp_enrolled,
            "tournament_available": tournament_available,
            "camp_available": camp_available,
            "active_page": "events",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )


# ── Tournaments ────────────────────────────────────────────────────────────────

@router.get("/events/tournaments", response_class=HTMLResponse)
async def events_tournaments_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """TOURNAMENT browse — same content as old /tournaments, under /events/tournaments."""
    from ...models.quiz import SessionQuiz

    tournaments = (
        db.query(Semester)
        .filter(
            and_(
                Semester.semester_category == SemesterCategory.TOURNAMENT,
                Semester.tournament_status.in_(["ENROLLMENT_OPEN", "IN_PROGRESS"]),
                Semester.specialization_type == "LFA_FOOTBALL_PLAYER",
                Semester.status != SemesterStatus.CANCELLED,
                Semester.end_date >= date.today(),
            )
        )
        .order_by(Semester.start_date.asc())
        .all()
    )

    enrolled_events = []
    browse_events = []
    for t in tournaments:
        enrollment_count = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == t.id,
                SemesterEnrollment.is_active.is_(True),
            )
            .count()
        )
        user_enrollment = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == t.id,
                SemesterEnrollment.user_id == user.id,
                SemesterEnrollment.is_active.is_(True),
            )
            .first()
        )
        instructor = None
        if t.master_instructor_id:
            instructor = db.query(User).filter(User.id == t.master_instructor_id).first()

        cfg = t.tournament_config_obj
        session_count = db.query(SessionModel).filter(SessionModel.semester_id == t.id).count()
        has_quiz = (
            db.query(SessionQuiz)
            .join(SessionModel, SessionModel.id == SessionQuiz.session_id)
            .filter(SessionModel.semester_id == t.id)
            .count()
        ) > 0
        session_type_config = cfg.session_type_config if cfg else "on_site"
        tournament_type_code = (
            cfg.tournament_type.code if cfg and cfg.tournament_type else None
        )

        info = {
            "tournament": t,
            "enrollment_count": enrollment_count,
            "max_players": t.max_players or 999,
            "is_enrolled": user_enrollment is not None,
            "enrollment_status": user_enrollment.request_status.value if user_enrollment else None,
            "instructor": instructor,
            "session_count": session_count,
            "has_quiz": has_quiz,
            "session_type_config": session_type_config,
            "tournament_type_code": tournament_type_code,
        }
        if user_enrollment is not None:
            enrolled_events.append(info)
        else:
            browse_events.append(info)

    return templates.TemplateResponse(
        "events_tournaments.html",
        {
            "request": request,
            "user": user,
            "enrolled_events": enrolled_events,
            "browse_events": browse_events,
            "tournaments": enrolled_events + browse_events,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type", "info"),
            "active_page": "events",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )


@router.get("/events/tournaments/{tournament_id}", response_class=HTMLResponse)
async def events_tournament_detail(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Student-facing TOURNAMENT event detail page."""
    tournament = db.query(Semester).filter(
        Semester.id == tournament_id,
        Semester.semester_category == SemesterCategory.TOURNAMENT,
    ).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="Event not found")

    enrollment = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.user_id == user.id,
        SemesterEnrollment.is_active.is_(True),
    ).first()

    sessions = (
        db.query(SessionModel)
        .filter(SessionModel.semester_id == tournament_id)
        .order_by(SessionModel.date_start.asc())
        .all()
    )

    session_info = []
    for s in sessions:
        sq = db.query(SessionQuiz).filter(SessionQuiz.session_id == s.id).first()
        quiz_id = sq.quiz_id if sq else None
        quiz_completed = False
        if quiz_id and enrollment:
            quiz_completed = (
                db.query(QuizAttempt)
                .filter(QuizAttempt.quiz_id == quiz_id, QuizAttempt.user_id == user.id)
                .first()
            ) is not None
        session_info.append({
            "session": s,
            "quiz_id": quiz_id,
            "quiz_completed": quiz_completed,
            "quiz_pending": bool(quiz_id and not quiz_completed and enrollment),
        })

    my_ranking = db.query(TournamentRanking).filter(
        TournamentRanking.tournament_id == tournament_id,
        TournamentRanking.user_id == user.id,
    ).first()

    enrollment_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.is_active.is_(True),
    ).count()

    cfg = tournament.tournament_config_obj
    instructor = None
    if tournament.master_instructor_id:
        instructor = db.query(User).filter(User.id == tournament.master_instructor_id).first()

    return templates.TemplateResponse(
        "tournament_detail.html",
        {
            "request": request,
            "user": user,
            "tournament": tournament,
            "cfg": cfg,
            "enrollment": enrollment,
            "session_info": session_info,
            "my_ranking": my_ranking,
            "enrollment_count": enrollment_count,
            "max_players": tournament.max_players,
            "instructor": instructor,
            "active_page": "events",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )


# ── Camps ──────────────────────────────────────────────────────────────────────

@router.get("/events/camps", response_class=HTMLResponse)
async def events_camps_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """CAMP browse — enrolled section + available section."""
    camps = (
        db.query(Semester)
        .filter(
            Semester.semester_category == SemesterCategory.CAMP,
            Semester.tournament_status.in_(["ENROLLMENT_OPEN", "IN_PROGRESS", "ENROLLMENT_CLOSED"]),
            Semester.status != SemesterStatus.CANCELLED,
        )
        .order_by(Semester.start_date.asc())
        .all()
    )

    enrolled_events = []
    browse_events = []
    for c in camps:
        enrollment_count = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == c.id,
                SemesterEnrollment.is_active.is_(True),
            )
            .count()
        )
        user_enrollment = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == c.id,
                SemesterEnrollment.user_id == user.id,
                SemesterEnrollment.is_active.is_(True),
            )
            .first()
        )
        info = {
            "camp": c,
            "enrollment_count": enrollment_count,
            "max_players": c.max_players or 999,
            "is_enrolled": user_enrollment is not None,
        }
        if user_enrollment is not None:
            enrolled_events.append(info)
        else:
            browse_events.append(info)

    return templates.TemplateResponse(
        "events_camps.html",
        {
            "request": request,
            "user": user,
            "enrolled_events": enrolled_events,
            "browse_events": browse_events,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type", "info"),
            "active_page": "events",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )


@router.get("/events/camps/{camp_id}", response_class=HTMLResponse)
async def events_camp_detail(
    camp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """CAMP student detail page — enrollment panel + session list."""
    camp = db.query(Semester).filter(
        Semester.id == camp_id,
        Semester.semester_category == SemesterCategory.CAMP,
    ).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Camp not found")

    enrollment = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == camp_id,
        SemesterEnrollment.user_id == user.id,
        SemesterEnrollment.is_active.is_(True),
    ).first()

    sessions = (
        db.query(SessionModel)
        .filter(SessionModel.semester_id == camp_id)
        .order_by(SessionModel.date_start.asc())
        .all()
    )

    enrollment_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == camp_id,
        SemesterEnrollment.is_active.is_(True),
    ).count()

    instructor = None
    if camp.master_instructor_id:
        instructor = db.query(User).filter(User.id == camp.master_instructor_id).first()

    return templates.TemplateResponse(
        "events_camp_detail.html",
        {
            "request": request,
            "user": user,
            "camp": camp,
            "enrollment": enrollment,
            "sessions": sessions,
            "enrollment_count": enrollment_count,
            "max_players": camp.max_players,
            "instructor": instructor,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type", "info"),
            "active_page": "events",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )


@router.post("/events/camps/{camp_id}/enroll", response_class=HTMLResponse)
async def events_camp_enroll(
    camp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Enroll current student in a camp (auto-approved, deducts credits)."""

    def _err(msg: str):
        return RedirectResponse(
            url=f"/events/camps/{camp_id}?flash={msg}&flash_type=error", status_code=303
        )

    camp = db.query(Semester).filter(
        Semester.id == camp_id,
        Semester.semester_category == SemesterCategory.CAMP,
        Semester.status != SemesterStatus.CANCELLED,
    ).first()
    if not camp:
        return RedirectResponse(url="/events/camps?flash=Camp+not+found&flash_type=error", status_code=303)

    if camp.tournament_status not in ("ENROLLMENT_OPEN", "IN_PROGRESS"):
        return _err("Camp+not+open+for+enrollment")

    if user.role != UserRole.STUDENT:
        return _err("Only+students+can+enroll")

    # License: any active LFA_FOOTBALL_PLAYER license
    license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.is_active.is_(True),
    ).first()
    if not license:
        return _err("No+active+license+found.+Complete+onboarding+first.")

    # Not already enrolled
    existing = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == camp_id,
        SemesterEnrollment.user_id == user.id,
        SemesterEnrollment.is_active.is_(True),
    ).first()
    if existing:
        return RedirectResponse(
            url=f"/events/camps/{camp_id}?flash=Already+enrolled&flash_type=info", status_code=303
        )

    cost = camp.enrollment_cost if camp.enrollment_cost is not None else 0
    if user.credit_balance < cost:
        return _err(f"Insufficient+credits+(need+{cost}%2C+have+{user.credit_balance})")

    max_p = camp.max_players if camp.max_players else 999
    enrolled_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == camp_id,
        SemesterEnrollment.is_active.is_(True),
        SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
    ).count()
    if enrolled_count >= max_p:
        return _err("Camp+is+full")

    enrollment = SemesterEnrollment(
        user_id=user.id,
        semester_id=camp_id,
        user_license_id=license.id,
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

        db.add(CreditTransaction(
            user_license_id=license.id,
            transaction_type="TOURNAMENT_ENROLLMENT",
            amount=-cost,
            balance_after=user.credit_balance,
            description=f"Camp enrollment: {camp.name} ({camp.code})",
            semester_id=camp_id,
            enrollment_id=enrollment.id,
            idempotency_key=f"web_camp_enroll_{camp_id}_{user.id}_{enrollment.id}",
        ))

    db.commit()

    camp_name = camp.name.replace(" ", "+")
    return RedirectResponse(
        url=f"/events/camps/{camp_id}?flash=Successfully+enrolled+in+{camp_name}&flash_type=success",
        status_code=303,
    )


@router.post("/events/camps/{camp_id}/unenroll", response_class=HTMLResponse)
async def events_camp_unenroll(
    camp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Withdraw student from camp (50% refund)."""
    enrollment = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id == user.id,
        SemesterEnrollment.semester_id == camp_id,
        SemesterEnrollment.is_active.is_(True),
    ).first()
    if not enrollment:
        return RedirectResponse(
            url=f"/events/camps/{camp_id}?flash=No+active+enrollment+found&flash_type=error",
            status_code=303,
        )

    camp = db.query(Semester).filter(Semester.id == camp_id).first()
    cost = camp.enrollment_cost if camp and camp.enrollment_cost else 0
    refund = cost // 2

    enrollment.is_active = False
    enrollment.request_status = EnrollmentStatus.WITHDRAWN
    db.add(enrollment)

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
            transaction_type="TOURNAMENT_UNENROLL_REFUND",
            amount=refund,
            balance_after=user.credit_balance,
            description=f"Camp unenrollment refund (50%): {camp.name if camp else camp_id}",
            semester_id=camp_id,
            enrollment_id=enrollment.id,
            idempotency_key=f"web_camp_unenroll_{camp_id}_{user.id}_{enrollment.id}",
        ))

    db.commit()

    return RedirectResponse(
        url=f"/events/camps/{camp_id}?flash=Unenrolled.+{refund}+credits+refunded.&flash_type=info",
        status_code=303,
    )


# ── Stubs — Coming Soon ────────────────────────────────────────────────────────

@router.get("/events/academy-seasons", response_class=HTMLResponse)
async def events_academy_seasons(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    return templates.TemplateResponse(
        "events_stub.html",
        {
            "request": request,
            "user": user,
            "event_type": "Academy Seasons",
            "event_icon": "📚",
            "active_page": "events",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )


@router.get("/events/mini-seasons", response_class=HTMLResponse)
async def events_mini_seasons(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    return templates.TemplateResponse(
        "events_stub.html",
        {
            "request": request,
            "user": user,
            "event_type": "Mini Seasons",
            "event_icon": "📅",
            "active_page": "events",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )
