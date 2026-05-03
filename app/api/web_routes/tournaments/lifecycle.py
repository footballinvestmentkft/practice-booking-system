"""Admin tournament lifecycle routes: list, create, start, cancel, delete, rollback, promotion events."""
from datetime import date

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.campus import Campus
from ....models.game_preset import GamePreset
from ....models.location import Location
from ....models.semester import Semester, SemesterStatus, SemesterCategory
from ....models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ....models.session import Session as SessionModel
from ....models.team import Team, TournamentTeamEnrollment
from ....models.club import Club
from ....models.tournament_type import TournamentType
from ....models.tournament_configuration import TournamentConfiguration
from ....models.tournament_instructor_slot import TournamentInstructorSlot, SlotRole, SlotStatus
from ....models.user import User
from . import templates, _admin_only

router = APIRouter()


@router.get("/admin/promotion-events", response_class=HTMLResponse)
async def admin_promotion_events_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: list all promotion events (PROMOTION_EVENT category)."""
    _admin_only(user)

    # All semesters in the dedicated PROMOTION_EVENT category.
    # participant_type filter no longer needed — the category is the discriminator.
    promotions = (
        db.query(Semester)
        .filter(
            Semester.semester_category == SemesterCategory.PROMOTION_EVENT,
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
            "tournament":        t,
            "participant_type":  participant_type,
            "team_count":        team_count,
            "player_count":      player_count,
            "session_count":     session_count,
            "campus":            campus,
            "source_club":       source_club,
            "organizer_club":    t.organizer_club,
            "organizer_sponsor": t.organizer_sponsor,
            "slot_total":        len(slots),
            "slot_planned":      sum(1 for s in slots if s.status == SlotStatus.PLANNED.value),
            "slot_checked":      sum(1 for s in slots if s.status == SlotStatus.CHECKED_IN.value),
            "slot_absent":       sum(1 for s in slots if s.status == SlotStatus.ABSENT.value),
            "has_absent_field":  any(
                s.status == SlotStatus.ABSENT.value and s.role == SlotRole.FIELD.value
                for s in slots
            ),
            "fmt":               t.format,
            "type_code":         tt.code if tt else None,
            "max_players":       cfg.max_players if cfg else None,
            "scoring_type":      cfg.scoring_type if cfg else None,
            "ranking_direction": cfg.ranking_direction if cfg else None,
            "measurement_unit":  cfg.measurement_unit if cfg else None,
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
        instructor = None
        if t.master_instructor_id:
            instructor = db.query(User).filter(User.id == t.master_instructor_id).first()
        tournament_info.append({
            "tournament": t,
            "enrollment_count": enroll_count,
            "session_count": session_count,
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
):
    """Admin: create a new tournament."""
    _admin_only(user)

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
    from ....models.game_configuration import GameConfiguration
    cfg = TournamentConfiguration(
        semester_id=t.id,
        tournament_type_id=int(tournament_type_id) if tournament_type_id.strip() else None,
        assignment_type=assignment_type,
        participant_type=participant_type,
        number_of_rounds=max(1, min(10, number_of_rounds)),
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

    # MF-02 guard: block delete when enrollments exist to prevent silent credit loss.
    # Individual enrollments (SemesterEnrollment) have credits already deducted;
    # team enrollments (TournamentTeamEnrollment) have captain credits already deducted.
    # Use Cancel (which processes refunds) instead of Delete in these cases.
    ind_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == tournament_id,
        SemesterEnrollment.is_active == True,
    ).count()
    team_count = db.query(TournamentTeamEnrollment).filter(
        TournamentTeamEnrollment.semester_id == tournament_id,
        TournamentTeamEnrollment.is_active == True,
    ).count()
    total_enrollments = ind_count + team_count
    if total_enrollments > 0:
        return RedirectResponse(
            url=(
                f"/admin/tournaments/{tournament_id}/edit"
                f"?error=Cannot+delete:+{total_enrollments}+active+enrollment(s)+exist."
                f"+Use+Cancel+to+process+refunds+first."
            ),
            status_code=303,
        )

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
