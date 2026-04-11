"""
Tournament web routes — cookie auth HTML frontend
Mirrors Streamlit Tournament_Monitor / Tournament_Manager flow.

Student routes:
    GET  /tournaments              — browse ENROLLMENT_OPEN tournaments
    POST /tournaments/{id}/enroll  — enroll (auto-approved, deducts credits)
    POST /tournaments/{id}/unenroll — withdraw (50 % refund)

Instructor routes:
    GET  /instructor/tournaments   — view assigned tournaments + participants

Admin routes:
    GET  /admin/tournaments                — all tournaments list + create form
    POST /admin/tournaments                — create new tournament
    POST /admin/tournaments/{id}/start     — ENROLLMENT_CLOSED → IN_PROGRESS
    POST /admin/tournaments/{id}/cancel    — any → CANCELLED
    POST /admin/tournaments/{id}/delete    — permanent delete
    POST /admin/tournaments/{id}/rollback  — IN_PROGRESS → ENROLLMENT_CLOSED (stuck recovery)
"""
from datetime import datetime, date
from pathlib import Path
from typing import Optional
import uuid

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, or_, update as sql_update
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web, get_current_admin_user_hybrid, get_current_admin_or_instructor_user_hybrid
from ...models.booking import Booking, BookingStatus
from ...models.campus import Campus
from ...models.credit_transaction import CreditTransaction
from ...models.game_preset import GamePreset
from ...models.license import UserLicense
from ...models.location import Location
from ...models.semester import Semester, SemesterStatus, SemesterCategory
from ...models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ...models.session import Session as SessionModel, EventCategory
from ...models.quiz import SessionQuiz, QuizAttempt
from ...models.tournament_ranking import TournamentRanking
from ...models.team import Team, TeamMember, TournamentTeamEnrollment, TournamentPlayerCheckin
from ...models.club import Club
from ...models.instructor_assignment import (
    InstructorAssignment,
    InstructorAssignmentRequest,
    InstructorAvailabilityWindow,
    AssignmentRequestStatus,
    LocationMasterInstructor,
    MasterOfferStatus,
)
from ...models.tournament_type import TournamentType
from ...models.tournament_configuration import TournamentConfiguration
from ...models.tournament_instructor_slot import TournamentInstructorSlot, SlotRole, SlotStatus
from ...models.user import User, UserRole
from ...services.tournament import team_service as _team_service
import app.services.tournament.instructor_planning_service as _ip_service
import app.services.tournament.attendance_service as _att_service
import app.services.tournament.enrollment_service as _enroll_service
from ...services.age_category_service import (
    calculate_age_at_season_start,
    get_automatic_age_category,
    get_current_season_year,
)
from .student_features import _spec_ctx

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_player_age_category(user: User) -> str:
    """Derive AMATEUR/PRE/YOUTH/PRO age category from user DOB. Defaults to AMATEUR."""
    if not user.date_of_birth:
        return "AMATEUR"
    season_year = get_current_season_year()
    age_at = calculate_age_at_season_start(user.date_of_birth, season_year)
    return get_automatic_age_category(age_at) or "AMATEUR"


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

    enrolled_events = []
    browse_events = []
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

        # Extra context for event-first UX
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
        "tournaments.html",
        {
            "request": request,
            "user": user,
            "enrolled_events": enrolled_events,
            "browse_events": browse_events,
            # backwards-compat alias used by tests
            "tournaments": enrolled_events + browse_events,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type", "info"),
            "active_page": "tournaments",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
        },
    )


@router.get("/tournaments/{tournament_id}", response_class=HTMLResponse)
async def student_tournament_detail(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Student-facing event detail page — session schedule, quiz CTAs, enrollment status."""
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
            "active_page": "tournaments",
            "show_spec_nav": True,
            **_spec_ctx(user, db),
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
        idempotency_key=f"web_enroll_{tournament_id}_{user.id}_{enrollment.id}",
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
        idempotency_key=f"web_unenroll_{tournament_id}_{user.id}_{enrollment.id}",
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


# ── Admin: tournament management ───────────────────────────────────────────────

def _admin_only(user: User):
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")


@router.get("/admin/promotion-events", response_class=HTMLResponse)
async def admin_promotion_events_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: list all promotion tournaments (TEAM and INDIVIDUAL)."""
    _admin_only(user)

    # All TOURNAMENT-category semesters — both TEAM and INDIVIDUAL participant types.
    promotions = (
        db.query(Semester)
        .join(TournamentConfiguration,
              TournamentConfiguration.semester_id == Semester.id)
        .filter(
            Semester.semester_category == SemesterCategory.TOURNAMENT,
            TournamentConfiguration.participant_type.in_(["TEAM", "INDIVIDUAL"]),
        )
        .order_by(Semester.start_date.desc(), Semester.name.asc(), Semester.id.asc())
        .all()
    )

    promo_info = []
    for t in promotions:
        cfg = t.tournament_config_obj
        participant_type = cfg.participant_type if cfg else "TEAM"

        if participant_type == "INDIVIDUAL":
            # Individual: count approved enrolled players
            team_count = 0
            player_count = (
                db.query(SemesterEnrollment)
                .filter(
                    SemesterEnrollment.semester_id == t.id,
                    SemesterEnrollment.is_active == True,
                    SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
                )
                .count()
            )
            source_club = None
        else:
            # Team: count active team enrollments; resolve source club from first team
            team_count = (
                db.query(TournamentTeamEnrollment)
                .filter(
                    TournamentTeamEnrollment.semester_id == t.id,
                    TournamentTeamEnrollment.is_active == True,
                )
                .count()
            )
            player_count = 0
            first_enrollment = (
                db.query(TournamentTeamEnrollment)
                .filter(TournamentTeamEnrollment.semester_id == t.id)
                .first()
            )
            source_club = None
            if first_enrollment:
                team = db.query(Team).filter(Team.id == first_enrollment.team_id).first()
                if team and team.club_id:
                    source_club = db.query(Club).filter(Club.id == team.club_id).first()

        session_count = (
            db.query(SessionModel)
            .filter(SessionModel.semester_id == t.id)
            .count()
        )
        campus = db.query(Campus).filter(Campus.id == t.campus_id).first() if t.campus_id else None

        # Instructor planning status
        slots = (
            db.query(TournamentInstructorSlot)
            .filter(TournamentInstructorSlot.semester_id == t.id)
            .all()
        )
        tt = cfg.tournament_type if cfg else None
        promo_info.append({
            "tournament":       t,
            "participant_type": participant_type,
            "team_count":       team_count,
            "player_count":     player_count,
            "session_count":    session_count,
            "campus":           campus,
            "source_club":      source_club,
            "slot_total":       len(slots),
            "slot_planned":     sum(1 for s in slots if s.status == SlotStatus.PLANNED.value),
            "slot_checked":     sum(1 for s in slots if s.status == SlotStatus.CHECKED_IN.value),
            "slot_absent":      sum(1 for s in slots if s.status == SlotStatus.ABSENT.value),
            "has_absent_field": any(
                s.status == SlotStatus.ABSENT.value and s.role == SlotRole.FIELD.value
                for s in slots
            ),
            "fmt":              t.format,
            "type_code":        tt.code if tt else None,
            "max_players":      cfg.max_players if cfg else None,
            "scoring_type":     cfg.scoring_type if cfg else None,
            "ranking_direction": cfg.ranking_direction if cfg else None,
            "measurement_unit": cfg.measurement_unit if cfg else None,
        })

    return templates.TemplateResponse(
        "admin/promotion_events.html",
        {
            "request": request,
            "user": user,
            "promo_info": promo_info,
            "flash": request.query_params.get("flash"),
        },
    )


@router.get("/admin/tournaments", response_class=HTMLResponse)
async def admin_tournaments_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: list all tournaments (all statuses) + create form."""
    _admin_only(user)

    # Include both code-pattern records (legacy, semester_category=NULL)
    # and records explicitly categorised as TOURNAMENT (new creates).
    # TEAM participant_type rows are shown separately at /admin/promotion-events.
    from sqlalchemy import exists as _sq_exists
    tournaments = (
        db.query(Semester)
        .filter(
            or_(
                Semester.code.like("TOURN-%"),
                Semester.code.like("OPS-%"),
                Semester.semester_category == SemesterCategory.TOURNAMENT,
            ),
            ~_sq_exists().where(
                (TournamentConfiguration.semester_id == Semester.id)
                & (TournamentConfiguration.participant_type == "TEAM")
            ),
        )
        .order_by(Semester.start_date.desc())
        .all()
    )

    tournament_info = []
    for t in tournaments:
        enroll_count = (
            db.query(SemesterEnrollment)
            .filter(
                SemesterEnrollment.semester_id == t.id,
                SemesterEnrollment.is_active == True,
            )
            .count()
        )
        session_count = (
            db.query(SessionModel)
            .filter(SessionModel.semester_id == t.id)
            .count()
        )
        sessions_preview = (
            db.query(SessionModel)
            .filter(SessionModel.semester_id == t.id)
            .order_by(SessionModel.date_start.asc())
            .limit(5)
            .all()
        )
        instructor = None
        if t.master_instructor_id:
            instructor = db.query(User).filter(User.id == t.master_instructor_id).first()
        tournament_info.append({
            "tournament": t,
            "enrollment_count": enroll_count,
            "session_count": session_count,
            "sessions_preview": sessions_preview,
            "instructor": instructor,
        })

    locations = db.query(Location).filter(Location.is_active == True).all()
    campuses = db.query(Campus).filter(Campus.is_active == True).all()
    tournament_types = db.query(TournamentType).order_by(TournamentType.display_name).all()
    game_presets = db.query(GamePreset).filter(GamePreset.is_active == True).order_by(GamePreset.name).all()

    return templates.TemplateResponse(
        "admin/tournaments.html",
        {
            "request": request,
            "user": user,
            "tournament_info": tournament_info,
            "locations": locations,
            "campuses": campuses,
            "tournament_types": tournament_types,
            "game_presets": game_presets,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type", "success"),
            "error": request.query_params.get("error"),
            "active_tab": request.query_params.get("tab", "list"),
        },
    )


@router.post("/admin/tournaments", response_class=RedirectResponse)
async def admin_create_tournament(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
    name: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    age_group: str = Form("AMATEUR"),
    enrollment_cost: int = Form(0),
    location_id: str = Form(""),
    campus_id: str = Form(""),
    assignment_type: str = Form("OPEN_ASSIGNMENT"),
    tournament_type_id: str = Form(""),
    game_preset_id: str = Form(""),
    participant_type: str = Form("INDIVIDUAL"),
    number_of_rounds: int = Form(1),
    session_type_config: str = Form("on_site"),
    meeting_link: str = Form(""),
):
    """Admin: create a new tournament."""
    _admin_only(user)

    # Game preset is required for all new tournaments
    if not game_preset_id.strip():
        return RedirectResponse(
            url="/admin/tournaments?error=Game+Preset+is+required.+Select+a+preset+to+define+skill+rules+for+this+tournament.&tab=create",
            status_code=303,
        )

    from datetime import datetime as _dt
    code = f"TOURN-{date.fromisoformat(start_date).strftime('%Y%m%d')}-{_dt.now().strftime('%H%M%S%f')[:9]}"

    if db.query(Semester).filter(Semester.code == code).first():
        return RedirectResponse(
            url=f"/admin/tournaments?error=Code+{code}+already+exists&tab=create",
            status_code=303,
        )

    t = Semester(
        code=code,
        name=name.strip(),
        start_date=date.fromisoformat(start_date),
        end_date=date.fromisoformat(end_date),
        status=SemesterStatus.DRAFT,
        tournament_status="DRAFT",
        semester_category=SemesterCategory.TOURNAMENT,
        specialization_type="LFA_FOOTBALL_PLAYER",
        age_group=age_group,
        enrollment_cost=enrollment_cost,
        location_id=int(location_id) if location_id.strip() else None,
        campus_id=int(campus_id) if campus_id.strip() else None,
    )
    db.add(t)
    db.flush()  # get t.id before creating config

    # Always create TournamentConfiguration so edit page works immediately
    from ...models.game_configuration import GameConfiguration
    cfg = TournamentConfiguration(
        semester_id=t.id,
        tournament_type_id=int(tournament_type_id) if tournament_type_id.strip() else None,
        assignment_type=assignment_type,
        participant_type=participant_type,
        number_of_rounds=max(1, min(10, number_of_rounds)),
        session_type_config=session_type_config if session_type_config in {"on_site", "virtual", "hybrid"} else "on_site",
        meeting_link=meeting_link.strip() or None,
    )
    db.add(cfg)

    if game_preset_id.strip():
        game_cfg = GameConfiguration(
            semester_id=t.id,
            game_preset_id=int(game_preset_id),
        )
        db.add(game_cfg)

    db.commit()

    return RedirectResponse(
        url=f"/admin/tournaments/{t.id}/edit?flash=Tournament+{code}+created",
        status_code=303,
    )


@router.post("/admin/tournaments/{tournament_id}/start", response_class=RedirectResponse)
async def admin_start_tournament(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: advance ENROLLMENT_CLOSED → IN_PROGRESS."""
    _admin_only(user)

    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        return RedirectResponse(url="/admin/tournaments?error=Tournament+not+found", status_code=303)
    if t.tournament_status != "ENROLLMENT_CLOSED":
        return RedirectResponse(
            url=f"/admin/tournaments?error=Tournament+must+be+ENROLLMENT_CLOSED+to+start+(current:+{t.tournament_status})",
            status_code=303,
        )

    t.tournament_status = "IN_PROGRESS"
    t.status = SemesterStatus.ONGOING
    db.commit()

    return RedirectResponse(
        url=f"/admin/tournaments?flash=Tournament+{t.code}+started+(IN_PROGRESS)",
        status_code=303,
    )


@router.post("/admin/tournaments/{tournament_id}/cancel", response_class=RedirectResponse)
async def admin_cancel_tournament(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: cancel tournament."""
    _admin_only(user)

    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        return RedirectResponse(url="/admin/tournaments?error=Tournament+not+found", status_code=303)
    if t.tournament_status in ("COMPLETED", "CANCELLED"):
        return RedirectResponse(
            url=f"/admin/tournaments?error=Cannot+cancel+{t.tournament_status}+tournament",
            status_code=303,
        )

    t.tournament_status = "CANCELLED"
    t.status = SemesterStatus.CANCELLED
    db.commit()

    return RedirectResponse(
        url=f"/admin/tournaments?flash=Tournament+{t.code}+cancelled",
        status_code=303,
    )


@router.post("/admin/tournaments/{tournament_id}/delete", response_class=RedirectResponse)
async def admin_delete_tournament(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: permanently delete tournament."""
    _admin_only(user)

    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        return RedirectResponse(url="/admin/tournaments?error=Tournament+not+found", status_code=303)

    code = t.code
    db.delete(t)
    db.commit()

    return RedirectResponse(
        url=f"/admin/tournaments?flash=Tournament+{code}+permanently+deleted",
        status_code=303,
    )


@router.post("/admin/tournaments/{tournament_id}/rollback", response_class=RedirectResponse)
async def admin_rollback_tournament(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: rollback stuck IN_PROGRESS → ENROLLMENT_CLOSED for re-generation."""
    _admin_only(user)

    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        return RedirectResponse(url="/admin/tournaments?error=Tournament+not+found", status_code=303)
    if t.tournament_status != "IN_PROGRESS":
        return RedirectResponse(
            url=f"/admin/tournaments?error=Rollback+only+available+for+IN_PROGRESS+tournaments",
            status_code=303,
        )

    t.tournament_status = "ENROLLMENT_CLOSED"
    t.status = SemesterStatus.READY_FOR_ENROLLMENT
    db.commit()

    return RedirectResponse(
        url=f"/admin/tournaments?flash=Tournament+{t.code}+rolled+back+to+ENROLLMENT_CLOSED",
        status_code=303,
    )


# ── Tournament Edit Page ────────────────────────────────────────────────────────

@router.get("/admin/tournaments/{tournament_id}/edit", response_class=HTMLResponse)
async def admin_tournament_edit_page(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: tournament edit page — all lifecycle management in one place."""
    _admin_only(user)

    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        return RedirectResponse(url="/admin/tournaments?error=Tournament+not+found", status_code=303)

    # Enrollments with user details
    enrollments = (
        db.query(SemesterEnrollment)
        .filter(
            SemesterEnrollment.semester_id == tournament_id,
            SemesterEnrollment.is_active == True,
        )
        .all()
    )
    enrolled_user_ids = [e.user_id for e in enrollments]
    enrolled_users = {}
    if enrolled_user_ids:
        for u in db.query(User).filter(User.id.in_(enrolled_user_ids)).all():
            enrolled_users[u.id] = u

    # Sessions generated
    sessions = (
        db.query(SessionModel)
        .filter(SessionModel.semester_id == tournament_id)
        .order_by(SessionModel.date_start)
        .limit(10)
        .all()
    )
    session_count = (
        db.query(SessionModel)
        .filter(SessionModel.semester_id == tournament_id)
        .count()
    )

    # Reference data for dropdowns
    game_presets = db.query(GamePreset).filter(GamePreset.is_active == True).all()
    tournament_types = db.query(TournamentType).all()
    campuses = db.query(Campus).filter(Campus.is_active == True).all()
    locations = db.query(Location).filter(Location.is_active == True).all()

    # Schedule config (from tournament_config_obj)
    cfg = t.tournament_config_obj
    _checkin_iso = t.checkin_opens_at.isoformat() if getattr(t, 'checkin_opens_at', None) else None
    schedule = {
        "match_duration_minutes": cfg.match_duration_minutes if cfg else None,
        "break_duration_minutes": cfg.break_duration_minutes if cfg else None,
        "parallel_fields": cfg.parallel_fields if cfg else 1,
        "checkin_opens_at": _checkin_iso,
        "number_of_legs": cfg.number_of_legs if cfg else 1,
        "track_home_away": cfg.track_home_away if cfg else False,
    }

    # Reward config summary
    reward_cfg = t.reward_config  # property → dict or None

    # Game preset info (for session gen guard)
    game_cfg = t.game_config_obj
    preset = None
    preset_min_players = None
    if game_cfg and game_cfg.game_preset_id:
        preset = db.query(GamePreset).filter(GamePreset.id == game_cfg.game_preset_id).first()
        if preset:
            preset_min_players = preset.game_config.get("metadata", {}).get("min_players")

    checked_in_count = sum(
        1 for e in enrollments if e.tournament_checked_in_at is not None
    )

    # Session result status (for Section 7 — result entry panel)
    all_match_sessions = (
        db.query(SessionModel)
        .filter(
            SessionModel.semester_id == tournament_id,
            SessionModel.event_category == EventCategory.MATCH,
        )
        .order_by(SessionModel.date_start)
        .all()
    )

    def _matchup_label(s, teams_dict: dict, users_dict: dict):
        """Return 'Team A vs Team B' / 'Player X vs Player Y' / 'N participants' / None."""
        if s.participant_team_ids:
            names = [teams_dict.get(tid, f"Team #{tid}") for tid in s.participant_team_ids[:2]]
            return " vs ".join(names) if len(names) >= 2 else names[0]
        if s.participant_user_ids:
            if s.match_format == "HEAD_TO_HEAD" and len(s.participant_user_ids) >= 2:
                u1 = users_dict.get(s.participant_user_ids[0])
                u2 = users_dict.get(s.participant_user_ids[1])
                n1 = u1.name if u1 else f"Player #{s.participant_user_ids[0]}"
                n2 = u2.name if u2 else f"Player #{s.participant_user_ids[1]}"
                return f"{n1} vs {n2}"
            return f"{len(s.participant_user_ids)} participants"
        return None

    # Team name map for TEAM tournaments (team_id → name) — built first for matchup_label
    enrolled_teams: dict = {}
    team_enrollments = (
        db.query(TournamentTeamEnrollment)
        .filter(
            TournamentTeamEnrollment.semester_id == tournament_id,
            TournamentTeamEnrollment.is_active == True,
        )
        .all()
    )
    if team_enrollments:
        team_ids = [e.team_id for e in team_enrollments]
        teams = db.query(Team).filter(Team.id.in_(team_ids)).all()
        enrolled_teams = {t.id: t.name for t in teams}

    sessions_result_status = [
        {
            "id": s.id,
            "title": s.title or f"Session #{s.id}",
            "date_start": s.date_start.strftime("%Y-%m-%d %H:%M") if s.date_start else "",
            "match_format": s.match_format or "INDIVIDUAL_RANKING",
            "has_results": bool(
                (s.rounds_data and s.rounds_data.get("round_results"))
                or s.game_results
            ),
            "participant_user_ids": s.participant_user_ids or [],
            "participant_team_ids": s.participant_team_ids or [],
            "tournament_round": s.tournament_round,
            "group_identifier": s.group_identifier,
            "matchup_label": _matchup_label(s, enrolled_teams, enrolled_users),
            "postponed_reason": s.postponed_reason,
        }
        for s in all_match_sessions
    ]

    # Existing rankings (for Section 8 — rankings panel)
    existing_rankings = (
        db.query(TournamentRanking)
        .filter(TournamentRanking.tournament_id == tournament_id)
        .order_by(TournamentRanking.rank)
        .all()
    )
    ranking_users = {r.user_id: enrolled_users.get(r.user_id) for r in existing_rankings if r.user_id is not None}

    # Group standings: sessions with group_identifier → per-group TournamentRanking rows
    from collections import defaultdict as _defaultdict
    _group_participants: dict = _defaultdict(set)
    for s in all_match_sessions:
        if not s.group_identifier:
            continue
        for tid in (s.participant_team_ids or []):
            _group_participants[s.group_identifier].add(("team", tid))
        for uid in (s.participant_user_ids or []):
            _group_participants[s.group_identifier].add(("user", uid))

    group_standings: dict = {}
    for grp in sorted(_group_participants.keys()):
        parts = _group_participants[grp]
        grp_rows = [
            r for r in existing_rankings
            if ("team", r.team_id) in parts or ("user", r.user_id) in parts
        ]
        grp_rows.sort(key=lambda r: r.rank or 999)
        if grp_rows:
            group_standings[grp] = grp_rows

    # ranking_teams: team_id → Team object (parallel to ranking_users)
    _team_ids_in_rankings = {r.team_id for r in existing_rankings if r.team_id}
    ranking_teams = (
        {t.id: t for t in db.query(Team).filter(Team.id.in_(_team_ids_in_rankings)).all()}
        if _team_ids_in_rankings else {}
    )

    # Instructor roster (Section 4.5)
    from app.models.pitch import Pitch as PitchModel
    instructor_roster = _ip_service.get_roster(db, tournament_id)
    eligible_instructors = (
        db.query(User)
        .filter(User.role == UserRole.INSTRUCTOR, User.is_active == True)
        .order_by(User.name)
        .all()
    )
    pitches_for_roster = (
        db.query(PitchModel)
        .filter(PitchModel.is_active == True)
        .order_by(PitchModel.name)
        .all()
    )
    has_absent_field = any(
        s["role"] == "FIELD" and s["status"] == "ABSENT"
        for s in instructor_roster
    )

    # Wizard context: enrolled_count + completed_session_count
    _participant_type = cfg.participant_type if cfg else "INDIVIDUAL"
    enrolled_count = len(team_enrollments) if _participant_type == "TEAM" else len(enrollments)
    completed_session_count = sum(
        1 for s in all_match_sessions
        if s.game_results or (s.rounds_data and s.rounds_data.get("round_results"))
    )

    return templates.TemplateResponse(
        "admin/tournament_edit.html",
        {
            "request": request,
            "user": user,
            "t": t,
            "cfg": cfg,
            "schedule": schedule,
            "reward_cfg": reward_cfg,
            "game_cfg": game_cfg,
            "preset": preset,
            "preset_min_players": preset_min_players,
            "enrollments": enrollments,
            "enrolled_users": enrolled_users,
            "checked_in_count": checked_in_count,
            "enrolled_count": enrolled_count,
            "completed_session_count": completed_session_count,
            "sessions": sessions,
            "session_count": session_count,
            "sessions_result_status": sessions_result_status,
            "enrolled_teams": enrolled_teams,
            "existing_rankings": existing_rankings,
            "ranking_users": ranking_users,
            "group_standings": group_standings,
            "ranking_teams": ranking_teams,
            "game_presets": game_presets,
            "tournament_types": tournament_types,
            "campuses": campuses,
            "locations": locations,
            "instructor_roster": instructor_roster,
            "eligible_instructors": eligible_instructors,
            "pitches_for_roster": pitches_for_roster,
            "has_absent_field": has_absent_field,
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        },
    )


# ── Instructor Management Pages ─────────────────────────────────────────────────

@router.get("/admin/instructors", response_class=HTMLResponse)
async def admin_instructors_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
) -> HTMLResponse:
    """Admin instructor list — all users with role=INSTRUCTOR."""
    _admin_only(user)

    instructors = (
        db.query(User)
        .filter(User.role == UserRole.INSTRUCTOR)
        .order_by(User.name)
        .all()
    )

    # Per-instructor counts (batch — avoid N+1)
    instructor_ids = [i.id for i in instructors]

    license_counts: dict[int, int] = {}
    active_assignment_counts: dict[int, int] = {}
    master_location_counts: dict[int, int] = {}

    if instructor_ids:
        from sqlalchemy import func as sqlfunc
        for row in (
            db.query(UserLicense.user_id, sqlfunc.count(UserLicense.id))
            .filter(UserLicense.user_id.in_(instructor_ids), UserLicense.is_active == True)
            .group_by(UserLicense.user_id)
            .all()
        ):
            license_counts[row[0]] = row[1]

        for row in (
            db.query(InstructorAssignment.instructor_id, sqlfunc.count(InstructorAssignment.id))
            .filter(
                InstructorAssignment.instructor_id.in_(instructor_ids),
                InstructorAssignment.is_active == True,
            )
            .group_by(InstructorAssignment.instructor_id)
            .all()
        ):
            active_assignment_counts[row[0]] = row[1]

        for row in (
            db.query(LocationMasterInstructor.instructor_id, sqlfunc.count(LocationMasterInstructor.id))
            .filter(
                LocationMasterInstructor.instructor_id.in_(instructor_ids),
                LocationMasterInstructor.is_active == True,
            )
            .group_by(LocationMasterInstructor.instructor_id)
            .all()
        ):
            master_location_counts[row[0]] = row[1]

    stats = {
        "total": len(instructors),
        "active": sum(1 for i in instructors if i.is_active),
        "with_assignments": sum(1 for i in instructors if active_assignment_counts.get(i.id, 0) > 0),
        "masters": sum(1 for i in instructors if master_location_counts.get(i.id, 0) > 0),
    }

    return templates.TemplateResponse(
        request,
        "admin/instructors.html",
        {
            "instructors": instructors,
            "license_counts": license_counts,
            "active_assignment_counts": active_assignment_counts,
            "master_location_counts": master_location_counts,
            "stats": stats,
        },
    )


@router.get("/admin/instructors/{instructor_id}", response_class=HTMLResponse)
async def admin_instructor_detail_page(
    instructor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
) -> HTMLResponse:
    """Admin instructor detail — licenses, assignments, availability, requests."""
    _admin_only(user)

    instructor = db.query(User).filter(
        User.id == instructor_id, User.role == UserRole.INSTRUCTOR
    ).first()
    if not instructor:
        raise HTTPException(status_code=404, detail="Instructor not found")

    # Licenses
    licenses = (
        db.query(UserLicense)
        .filter(UserLicense.user_id == instructor_id)
        .order_by(UserLicense.is_active.desc(), UserLicense.started_at.desc())
        .all()
    )

    # Active assignments
    assignments = (
        db.query(InstructorAssignment)
        .filter(
            InstructorAssignment.instructor_id == instructor_id,
            InstructorAssignment.is_active == True,
        )
        .order_by(InstructorAssignment.year.desc(), InstructorAssignment.time_period_start)
        .all()
    )
    # Enrich with location names
    assignment_locations: dict[int, str] = {}
    loc_ids = {a.location_id for a in assignments}
    if loc_ids:
        for loc in db.query(Location).filter(Location.id.in_(loc_ids)).all():
            assignment_locations[loc.id] = loc.name

    # Availability windows (last 2 years)
    from datetime import date as _date
    current_year = _date.today().year
    availability = (
        db.query(InstructorAvailabilityWindow)
        .filter(
            InstructorAvailabilityWindow.instructor_id == instructor_id,
            InstructorAvailabilityWindow.year >= current_year - 1,
        )
        .order_by(InstructorAvailabilityWindow.year.desc(), InstructorAvailabilityWindow.time_period)
        .all()
    )

    # Assignment requests (last 20, all statuses)
    requests = (
        db.query(InstructorAssignmentRequest)
        .filter(InstructorAssignmentRequest.instructor_id == instructor_id)
        .order_by(InstructorAssignmentRequest.created_at.desc())
        .limit(20)
        .all()
    )
    # Enrich requests with semester names
    sem_ids = {r.semester_id for r in requests}
    semester_names: dict[int, str] = {}
    if sem_ids:
        for sem in db.query(Semester).filter(Semester.id.in_(sem_ids)).all():
            semester_names[sem.id] = sem.name

    # Master locations
    master_contracts = (
        db.query(LocationMasterInstructor)
        .filter(LocationMasterInstructor.instructor_id == instructor_id)
        .order_by(LocationMasterInstructor.is_active.desc(), LocationMasterInstructor.created_at.desc())
        .all()
    )
    master_loc_ids = {m.location_id for m in master_contracts}
    master_location_names: dict[int, str] = {}
    if master_loc_ids:
        for loc in db.query(Location).filter(Location.id.in_(master_loc_ids)).all():
            master_location_names[loc.id] = loc.name

    return templates.TemplateResponse(
        request,
        "admin/instructor_detail.html",
        {
            "instructor": instructor,
            "licenses": licenses,
            "assignments": assignments,
            "assignment_locations": assignment_locations,
            "availability": availability,
            "requests": requests,
            "semester_names": semester_names,
            "master_contracts": master_contracts,
            "master_location_names": master_location_names,
            "AssignmentRequestStatus": AssignmentRequestStatus,
            "MasterOfferStatus": MasterOfferStatus,
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        },
    )


# ─── TEAM MANAGEMENT ROUTES ───────────────────────────────────────────────────

@router.get("/admin/tournaments/{tournament_id}/teams", response_class=HTMLResponse)
async def admin_tournament_teams_page(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: manage team enrollments for a TEAM tournament."""
    _admin_only(user)

    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        return RedirectResponse(url="/admin/tournaments?error=Tournament+not+found", status_code=303)

    cfg = t.tournament_config_obj
    if not cfg or cfg.participant_type != "TEAM":
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/edit?error=This+tournament+is+not+TEAM+mode",
            status_code=303,
        )
    if t.tournament_status == "DRAFT":
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/edit?error=Complete+Step+1+first:+open+enrollment+before+managing+teams",
            status_code=303,
        )

    # Current team enrollments
    enrollments = (
        db.query(TournamentTeamEnrollment)
        .filter(
            TournamentTeamEnrollment.semester_id == tournament_id,
            TournamentTeamEnrollment.is_active == True,
        )
        .all()
    )
    enrolled_team_ids = {e.team_id for e in enrollments}

    # Enrolled teams with member counts
    enrolled_teams = []
    for e in enrollments:
        team = db.query(Team).filter(Team.id == e.team_id).first()
        if team:
            member_count = db.query(TeamMember).filter(
                TeamMember.team_id == team.id,
                TeamMember.is_active == True,
            ).count()
            verifier = None
            if e.payment_verified_by:
                verifier = db.query(User).filter(User.id == e.payment_verified_by).first()
            enrolled_teams.append({
                "enrollment": e,
                "team": team,
                "member_count": member_count,
                "verifier": verifier,
            })

    # Available teams grouped by club (excluding already-enrolled)
    available_clubs = []
    for club in db.query(Club).filter(Club.is_active == True).order_by(Club.name).all():
        teams = db.query(Team).filter(Team.club_id == club.id, Team.is_active == True).order_by(Team.name).all()
        unenrolled = [t2 for t2 in teams if t2.id not in enrolled_team_ids]
        if not unenrolled:
            continue
        teams_data = []
        for tm in unenrolled:
            mc = db.query(TeamMember).filter(TeamMember.team_id == tm.id, TeamMember.is_active == True).count()
            teams_data.append({"id": tm.id, "name": tm.name, "code": tm.code or "", "member_count": mc})
        available_clubs.append({"club": club, "teams": teams_data})

    return templates.TemplateResponse(
        request,
        "admin/tournament_teams.html",
        {
            "tournament": t,
            "enrolled_teams": enrolled_teams,
            "available_clubs": available_clubs,
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        },
    )


@router.get("/admin/tournaments/{tournament_id}/players", response_class=HTMLResponse)
async def admin_tournament_players_page(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: manage individual player enrollments for an INDIVIDUAL tournament."""
    _admin_only(user)

    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        return RedirectResponse(url="/admin/tournaments?error=Tournament+not+found", status_code=303)

    cfg = t.tournament_config_obj
    if cfg is None:
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/edit?error=Tournament+has+no+configuration",
            status_code=303,
        )
    if cfg.participant_type == "TEAM":
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/teams",
            status_code=303,
        )
    if t.tournament_status == "DRAFT":
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/edit?error=Complete+Step+1+first:+open+enrollment+before+managing+players",
            status_code=303,
        )

    active_enrollments = (
        db.query(SemesterEnrollment)
        .filter(
            SemesterEnrollment.semester_id == tournament_id,
            SemesterEnrollment.is_active == True,
        )
        .order_by(SemesterEnrollment.enrolled_at.asc())
        .all()
    )

    # Group enrolled players by team (via TeamMember → Team → Club)
    enrolled_groups_map: dict = {}  # team_id → group dict
    unaffiliated_players: list = []
    for enr in active_enrollments:
        u = enr.user
        tm = (
            db.query(TeamMember)
            .filter(TeamMember.user_id == enr.user_id, TeamMember.is_active == True)
            .order_by(TeamMember.joined_at.desc())
            .first()
        )
        if tm:
            team = tm.team
            club = team.club
            key = team.id
            if key not in enrolled_groups_map:
                enrolled_groups_map[key] = {
                    "club_id":   club.id if club else None,
                    "club_name": club.name if club else "—",
                    "team_id":   team.id,
                    "team_name": team.name,
                    "age_group": team.age_group_label or "",
                    "players":   [],
                }
            enrolled_groups_map[key]["players"].append(
                {"enrollment": enr, "user": u, "role": tm.role}
            )
        else:
            unaffiliated_players.append({"enrollment": enr, "user": u, "role": None})

    enrolled_groups = sorted(
        enrolled_groups_map.values(),
        key=lambda g: (g["club_name"], g["team_name"]),
    )

    return templates.TemplateResponse(
        request,
        "admin/tournament_players.html",
        {
            "tournament": t,
            "cfg": cfg,
            "enrolled_groups": enrolled_groups,
            "unaffiliated_players": unaffiliated_players,
            "total_enrolled": len(active_enrollments),
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/admin/tournaments/{tournament_id}/players/enroll", response_class=RedirectResponse)
async def admin_tournament_players_enroll(
    tournament_id: int,
    user_id: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Enroll a single player into an INDIVIDUAL tournament (admin bypass)."""
    _admin_only(user)
    try:
        _enroll_service.enroll_player_admin(db, tournament_id, user_id, user.id)
        db.commit()
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/players?flash=Player+enrolled+successfully",
            status_code=303,
        )
    except HTTPException as exc:
        import urllib.parse
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/players?error={urllib.parse.quote(exc.detail)}",
            status_code=303,
        )


@router.post("/admin/tournaments/{tournament_id}/players/{player_user_id}/remove", response_class=RedirectResponse)
async def admin_tournament_players_remove(
    tournament_id: int,
    player_user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Remove a player's active enrollment from an INDIVIDUAL tournament."""
    _admin_only(user)
    try:
        _enroll_service.unenroll_player_admin(db, tournament_id, player_user_id)
        db.commit()
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/players?flash=Player+removed",
            status_code=303,
        )
    except HTTPException as exc:
        import urllib.parse
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/players?error={urllib.parse.quote(exc.detail)}",
            status_code=303,
        )


@router.post("/admin/tournaments/{tournament_id}/players/enroll-from-team", response_class=RedirectResponse)
async def admin_tournament_players_enroll_from_team(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Bulk-enroll active members of one or more selected teams as individual players."""
    _admin_only(user)

    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        return RedirectResponse(url="/admin/tournaments?error=Tournament+not+found", status_code=303)
    if t.tournament_status != "ENROLLMENT_OPEN":
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/players?error=Enrollment+is+not+open",
            status_code=303,
        )

    form_data = await request.form()
    team_ids = [int(v) for v in form_data.getlist("team_ids")]
    if not team_ids:
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/players?error=No+teams+selected",
            status_code=303,
        )
    enrolled_count = 0
    skipped_count = 0
    for team_id in team_ids:
        members = (
            db.query(TeamMember)
            .filter(TeamMember.team_id == team_id, TeamMember.is_active == True)
            .all()
        )
        for m in members:
            try:
                _enroll_service.enroll_player_admin(db, tournament_id, m.user_id, user.id)
                enrolled_count += 1
            except HTTPException:
                skipped_count += 1
    db.commit()
    msg = f"{enrolled_count}+players+enrolled"
    if skipped_count:
        msg += f"+({skipped_count}+skipped)"
    return RedirectResponse(
        url=f"/admin/tournaments/{tournament_id}/players?flash={msg}",
        status_code=303,
    )


@router.post("/admin/tournaments/{tournament_id}/teams/enroll", response_class=RedirectResponse)
async def admin_tournament_teams_enroll(
    tournament_id: int,
    team_id: int = Form(...),
    request: Request = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Enroll a team into a TEAM tournament."""
    _admin_only(user)

    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        return RedirectResponse(url="/admin/tournaments?error=Tournament+not+found", status_code=303)
    if t.tournament_status != "ENROLLMENT_OPEN":
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/teams?error=Enrollment+is+not+open",
            status_code=303,
        )

    existing = db.query(TournamentTeamEnrollment).filter(
        TournamentTeamEnrollment.semester_id == tournament_id,
        TournamentTeamEnrollment.team_id == team_id,
    ).first()
    if existing:
        existing.is_active = True
    else:
        db.add(TournamentTeamEnrollment(
            semester_id=tournament_id,
            team_id=team_id,
            is_active=True,
            payment_verified=True,  # admin bypass — no payment required
        ))
    db.commit()
    return RedirectResponse(
        url=f"/admin/tournaments/{tournament_id}/teams?flash=Team+enrolled",
        status_code=303,
    )


@router.post("/admin/tournaments/{tournament_id}/teams/enroll-bulk", response_class=RedirectResponse)
async def admin_tournament_teams_enroll_bulk(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Bulk-enroll multiple teams into a TEAM tournament from club-grouped checkboxes."""
    _admin_only(user)

    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        return RedirectResponse(url="/admin/tournaments?error=Tournament+not+found", status_code=303)
    if t.tournament_status != "ENROLLMENT_OPEN":
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/teams?error=Enrollment+is+not+open",
            status_code=303,
        )

    form_data = await request.form()
    team_ids = [int(v) for v in form_data.getlist("team_ids")]
    if not team_ids:
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/teams?error=No+teams+selected",
            status_code=303,
        )

    enrolled_count = 0
    for team_id in team_ids:
        existing = db.query(TournamentTeamEnrollment).filter(
            TournamentTeamEnrollment.semester_id == tournament_id,
            TournamentTeamEnrollment.team_id == team_id,
        ).first()
        if existing:
            existing.is_active = True
        else:
            db.add(TournamentTeamEnrollment(
                semester_id=tournament_id,
                team_id=team_id,
                is_active=True,
                payment_verified=True,
            ))
        enrolled_count += 1
    db.commit()

    msg = f"{enrolled_count}+team{'s' if enrolled_count != 1 else ''}+enrolled"
    return RedirectResponse(
        url=f"/admin/tournaments/{tournament_id}/teams?flash={msg}",
        status_code=303,
    )


@router.post("/admin/tournaments/{tournament_id}/teams/{team_id}/remove", response_class=RedirectResponse)
async def admin_tournament_teams_remove(
    tournament_id: int,
    team_id: int,
    request: Request = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Remove a team from a TEAM tournament."""
    _admin_only(user)

    enrollment = db.query(TournamentTeamEnrollment).filter(
        TournamentTeamEnrollment.semester_id == tournament_id,
        TournamentTeamEnrollment.team_id == team_id,
        TournamentTeamEnrollment.is_active == True,
    ).first()
    if enrollment:
        enrollment.is_active = False
        db.commit()
    return RedirectResponse(
        url=f"/admin/tournaments/{tournament_id}/teams?flash=Team+removed",
        status_code=303,
    )


@router.post("/admin/tournaments/{tournament_id}/teams/{team_id}/verify", response_class=RedirectResponse)
async def admin_tournament_team_verify(
    tournament_id: int,
    team_id: int,
    request: Request = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Mark a team's enrollment payment as verified (admin only)."""
    _admin_only(user)

    enrollment = db.query(TournamentTeamEnrollment).filter(
        TournamentTeamEnrollment.semester_id == tournament_id,
        TournamentTeamEnrollment.team_id == team_id,
        TournamentTeamEnrollment.is_active == True,
    ).first()
    if not enrollment:
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/teams?error=Enrollment+not+found",
            status_code=303,
        )
    from datetime import datetime, timezone
    enrollment.payment_verified = True
    enrollment.payment_verified_by = user.id
    enrollment.payment_verified_at = datetime.now(timezone.utc)
    db.commit()
    import logging
    logging.getLogger(__name__).info(
        "Payment verified: tournament=%d team=%d by=%d", tournament_id, team_id, user.id
    )
    return RedirectResponse(
        url=f"/admin/tournaments/{tournament_id}/teams?flash=Payment+verified",
        status_code=303,
    )


@router.post("/admin/tournaments/{tournament_id}/teams/{team_id}/unverify", response_class=RedirectResponse)
async def admin_tournament_team_unverify(
    tournament_id: int,
    team_id: int,
    request: Request = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Revoke payment verification for a team enrollment (admin only)."""
    _admin_only(user)

    enrollment = db.query(TournamentTeamEnrollment).filter(
        TournamentTeamEnrollment.semester_id == tournament_id,
        TournamentTeamEnrollment.team_id == team_id,
        TournamentTeamEnrollment.is_active == True,
    ).first()
    if not enrollment:
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/teams?error=Enrollment+not+found",
            status_code=303,
        )
    enrollment.payment_verified = False
    enrollment.payment_verified_by = None
    enrollment.payment_verified_at = None
    db.commit()
    import logging
    logging.getLogger(__name__).info(
        "Payment unverified: tournament=%d team=%d by=%d", tournament_id, team_id, user.id
    )
    return RedirectResponse(
        url=f"/admin/tournaments/{tournament_id}/teams?flash=Payment+verification+revoked",
        status_code=303,
    )


@router.post(
    "/admin/tournaments/{tournament_id}/teams/{team_id}/members",
    response_class=RedirectResponse,
)
async def admin_add_team_member_direct(
    tournament_id: int,
    team_id: int,
    user_id: int = Form(...),
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
):
    """Admin directly adds a user to a team, bypassing the invite flow."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        _team_service.add_team_member(db, team_id=team_id, user_id=user_id)
    except HTTPException as exc:
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/teams?error={exc.detail}",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/admin/tournaments/{tournament_id}/teams?flash=Member+added",
        status_code=303,
    )


# ── Instructor Planning (IP-*) ────────────────────────────────────────────────

from fastapi.responses import JSONResponse


@router.get("/admin/tournaments/{tournament_id}/instructor-slots")
async def admin_get_instructor_slots(
    tournament_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Return the instructor roster for a tournament (JSON)."""
    _admin_only(user)
    roster = _ip_service.get_roster(db, tournament_id)
    return JSONResponse({"slots": roster})


@router.post("/admin/tournaments/{tournament_id}/instructor-slots")
async def admin_add_instructor_slot(
    tournament_id: int,
    instructor_id: int = Form(...),
    role: str = Form(...),
    pitch_id: Optional[int] = Form(None),
    notes: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Add an instructor slot to the tournament roster."""
    _admin_only(user)
    slot = _ip_service.add_slot(
        db=db,
        semester_id=tournament_id,
        instructor_id=instructor_id,
        role=role,
        pitch_id=pitch_id,
        assigned_by_id=user.id,
        notes=notes,
    )
    db.commit()
    return JSONResponse({"slot_id": slot.id, "status": slot.status}, status_code=201)


@router.delete("/admin/tournaments/{tournament_id}/instructor-slots/{slot_id}")
async def admin_remove_instructor_slot(
    tournament_id: int,
    slot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Remove an instructor slot."""
    _admin_only(user)
    _ip_service.remove_slot(db, slot_id=slot_id, by_user=user)
    db.commit()
    return JSONResponse({"deleted": slot_id})


@router.post("/admin/tournaments/{tournament_id}/instructor-slots/{slot_id}/checkin")
async def admin_checkin_instructor_slot(
    tournament_id: int,
    slot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Mark an instructor slot as CHECKED_IN."""
    _admin_only(user)
    slot = _ip_service.mark_checkin(db, slot_id=slot_id, requester=user)
    db.commit()
    _publish_instructor_change(tournament_id, slot, db)
    return JSONResponse({"slot_id": slot.id, "status": slot.status})


@router.post("/admin/tournaments/{tournament_id}/instructor-slots/{slot_id}/absent")
async def admin_absent_instructor_slot(
    tournament_id: int,
    slot_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Mark an instructor slot as ABSENT."""
    _admin_only(user)
    slot = _ip_service.mark_absent(db, slot_id=slot_id, requester=user)
    db.commit()
    _publish_instructor_change(tournament_id, slot, db)
    return JSONResponse({"slot_id": slot.id, "status": slot.status})


@router.get("/admin/tournaments/{tournament_id}/fallback-plan")
async def admin_get_fallback_plan(
    tournament_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Get the semi-automatic fallback plan for absent field instructors (JSON)."""
    _admin_only(user)
    plan = _ip_service.get_fallback_plan(db, semester_id=tournament_id)
    return JSONResponse(plan)


@router.post("/admin/tournaments/{tournament_id}/apply-fallback")
async def admin_apply_fallback(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Apply the fallback plan — reassign sessions + update parallel_fields."""
    _admin_only(user)
    body = await request.json()
    updated = _ip_service.apply_fallback(
        db=db,
        semester_id=tournament_id,
        admin_user=user,
        plan=body,
    )
    db.commit()
    return JSONResponse({"updated_sessions": updated})


# ── Attendance page + check-in endpoints ──────────────────────────────────────


@router.get("/admin/tournaments/{tournament_id}/attendance", response_class=HTMLResponse)
async def admin_tournament_attendance(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Attendance management page: instructor status + team/player check-in."""
    _admin_only(user)
    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tournament not found")

    summary_data = _att_service.get_attendance_summary(db, tournament_id)
    return templates.TemplateResponse(
        "admin/tournament_attendance.html",
        {
            "request": request,
            "user": user,
            "t": t,
            "participant_type": summary_data["participant_type"],
            "instructors": summary_data["instructors"],
            "teams": summary_data["teams"],
            "individual_players": summary_data["individual_players"],
            "summary": summary_data["summary"],
        },
    )


@router.post("/admin/tournaments/{tournament_id}/teams/{team_id}/checkin")
async def admin_team_checkin(
    tournament_id: int,
    team_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_admin_user_hybrid),
):
    """Mark a team as checked-in for the tournament."""
    enrollment = _att_service.checkin_team(db, tournament_id, team_id, by_user_id=user.id)
    db.commit()
    return JSONResponse({
        "ok": True,
        "checked_in_at": enrollment.checked_in_at.isoformat() if enrollment.checked_in_at else None,
    })


@router.post("/admin/tournaments/{tournament_id}/teams/{team_id}/uncheckin")
async def admin_team_uncheckin(
    tournament_id: int,
    team_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_admin_user_hybrid),
):
    """Remove check-in for a team."""
    _att_service.uncheckin_team(db, tournament_id, team_id, by_user_id=user.id)
    db.commit()
    return JSONResponse({"ok": True, "checked_in_at": None})


@router.post("/admin/tournaments/{tournament_id}/players/{player_user_id}/checkin")
async def admin_player_checkin(
    tournament_id: int,
    player_user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_admin_user_hybrid),
):
    """Check in an individual player for the tournament."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    team_id = body.get("team_id") if body else None
    checkin = _att_service.checkin_player(db, tournament_id, player_user_id, team_id, by_user_id=user.id)
    db.commit()
    return JSONResponse({
        "ok": True,
        "checked_in_at": checkin.checked_in_at.isoformat() if checkin.checked_in_at else None,
    })


@router.post("/admin/tournaments/{tournament_id}/players/{player_user_id}/uncheckin")
async def admin_player_uncheckin(
    tournament_id: int,
    player_user_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_admin_user_hybrid),
):
    """Remove pre-tournament check-in for a player."""
    _att_service.uncheckin_player(db, tournament_id, player_user_id)
    db.commit()
    return JSONResponse({"ok": True, "checked_in_at": None})


@router.patch("/admin/sessions/{session_id}/postpone")
async def admin_session_postpone(
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_admin_user_hybrid),
):
    """Set or clear a postponement reason for a session/match."""
    body = await request.json()
    reason = body.get("reason", "")
    session = _att_service.postpone_session(db, session_id, reason)
    db.commit()
    return JSONResponse({"ok": True, "postponed_reason": session.postponed_reason})


# ── Helper: publish WS instructor change event ─────────────────────────────

def _publish_instructor_change(
    tournament_id: int,
    slot: TournamentInstructorSlot,
    db: Session,
) -> None:
    """Best-effort WS broadcast of instructor status change."""
    try:
        from app.core.redis_pubsub import publish_tournament_update
        instructor_name = slot.instructor.name if slot.instructor else f"User #{slot.instructor_id}"
        absent_field_slots = db.query(TournamentInstructorSlot).filter(
            TournamentInstructorSlot.semester_id == slot.semester_id,
            TournamentInstructorSlot.role == SlotRole.FIELD.value,
            TournamentInstructorSlot.status == SlotStatus.ABSENT.value,
        ).count()
        publish_tournament_update(tournament_id, {
            "type":               "instructor_status_change",
            "slot_id":            slot.id,
            "instructor_name":    instructor_name,
            "role":               slot.role,
            "pitch_id":           slot.pitch_id,
            "new_status":         slot.status,
            "fallback_available": absent_field_slots > 0,
        })
    except Exception:
        pass  # Redis down — silent fail


# ── Admin INDIVIDUAL enrollment ───────────────────────────────────────────────

@router.get("/admin/tournaments/{tournament_id}/enrollment-clubs")
async def admin_enrollment_clubs(
    tournament_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_admin_or_instructor_user_hybrid),
):
    """Return clubs (and their teams) that are eligible for player enrollment.

    If the tournament has a campus_id, returns clubs linked to that campus's
    location. Otherwise returns all clubs (for non-campus tournaments).
    """
    tournament = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not tournament:
        raise HTTPException(status_code=404, detail="Tournament not found")

    # Determine location filter
    clubs = db.query(Club).filter(Club.is_active == True).order_by(Club.name).all()

    result = []
    for club in clubs:
        teams = db.query(Team).filter(Team.club_id == club.id, Team.is_active == True).order_by(Team.name).all()
        member_counts = {
            t.id: db.query(TeamMember).filter(
                TeamMember.team_id == t.id, TeamMember.is_active == True
            ).count()
            for t in teams
        }
        result.append({
            "club_id": club.id,
            "club_name": club.name,
            "teams": [
                {
                    "team_id": t.id,
                    "team_name": t.name,
                    "member_count": member_counts[t.id],
                }
                for t in teams
            ],
        })
    return JSONResponse(result)


@router.get("/admin/tournaments/{tournament_id}/available-players")
async def admin_available_players(
    tournament_id: int,
    q: Optional[str] = None,
    club_id: Optional[int] = None,
    team_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_admin_or_instructor_user_hybrid),
):
    """Return LFA-licensed players not yet enrolled in this tournament.

    Filters: ?q=name/email search, ?club_id=, ?team_id=
    Limit: 100
    """
    from ...models.license import UserLicense
    from ...models.semester_enrollment import SemesterEnrollment

    # Already-enrolled user IDs
    enrolled_ids = {
        row.user_id
        for row in db.query(SemesterEnrollment.user_id).filter(
            SemesterEnrollment.semester_id == tournament_id,
            SemesterEnrollment.is_active == True,
        ).all()
    }

    # Base: active LFA_FOOTBALL_PLAYER licensees
    query = (
        db.query(User, UserLicense)
        .join(UserLicense, and_(
            UserLicense.user_id == User.id,
            UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
            UserLicense.is_active == True,
        ))
        .filter(User.is_active == True)
    )

    # Team / club filter
    if team_id:
        member_ids = {
            row.user_id
            for row in db.query(TeamMember.user_id).filter(
                TeamMember.team_id == team_id, TeamMember.is_active == True
            ).all()
        }
        query = query.filter(User.id.in_(member_ids))
    elif club_id:
        team_ids = [
            t.id for t in db.query(Team.id).filter(
                Team.club_id == club_id, Team.is_active == True
            ).all()
        ]
        member_ids = {
            row.user_id
            for row in db.query(TeamMember.user_id).filter(
                TeamMember.team_id.in_(team_ids), TeamMember.is_active == True
            ).all()
        } if team_ids else set()
        query = query.filter(User.id.in_(member_ids))

    # Name / email search
    if q:
        like = f"%{q}%"
        query = query.filter(or_(User.name.ilike(like), User.email.ilike(like)))

    rows = query.limit(100).all()

    # Enrich with club/team info
    def _club_team(user_id: int):
        member = (
            db.query(TeamMember)
            .join(Team, Team.id == TeamMember.team_id)
            .filter(TeamMember.user_id == user_id, TeamMember.is_active == True)
            .first()
        )
        if not member:
            return None, None, None, None
        team = member.team
        club = db.query(Club).filter(Club.id == team.club_id).first() if team.club_id else None
        return team.id, team.name, club.id if club else None, club.name if club else None

    result = []
    for u, _lic in rows:
        if u.id in enrolled_ids:
            continue
        tid, tname, cid, cname = _club_team(u.id)
        result.append({
            "user_id": u.id,
            "name": u.name,
            "email": u.email,
            "club_id": cid,
            "club_name": cname,
            "team_id": tid,
            "team_name": tname,
        })

    return JSONResponse(result)


@router.post("/admin/tournaments/{tournament_id}/enroll-player")
async def admin_enroll_player(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_admin_or_instructor_user_hybrid),
):
    """Bulk-enroll one or more players (admin bypass — no credit deduction)."""
    body = await request.json()
    user_ids = body.get("user_ids", [])

    enrolled = []
    skipped = []
    for uid in user_ids:
        try:
            enrollment = _enroll_service.enroll_player_admin(db, tournament_id, uid, user.id)
            target = db.query(User).filter(User.id == uid).first()
            enrolled.append({"user_id": uid, "name": target.name if target else str(uid)})
        except HTTPException as exc:
            skipped.append({"user_id": uid, "reason": exc.detail})

    if enrolled:
        db.commit()
    return JSONResponse({"enrolled": enrolled, "skipped": skipped})


@router.post("/admin/tournaments/{tournament_id}/unenroll-player")
async def admin_unenroll_player(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_admin_or_instructor_user_hybrid),
):
    """Remove a player's active enrollment (admin action)."""
    body = await request.json()
    uid = body.get("user_id")
    if not uid:
        raise HTTPException(status_code=422, detail="user_id required")

    _enroll_service.unenroll_player_admin(db, tournament_id, uid)
    db.commit()
    return JSONResponse({"success": True, "user_id": uid})
