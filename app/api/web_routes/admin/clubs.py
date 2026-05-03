"""Admin club and team management routes."""
from fastapi import APIRouter, Request, Depends, HTTPException, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, date
from collections import defaultdict
import logging

from sqlalchemy import func as sqlfunc

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.user import User, UserRole
from ....models.semester import Semester, SemesterStatus, SemesterCategory
from ....models.campus import Campus
from ....models.team import Team, TeamMember, TournamentTeamEnrollment
from ....models.club import Club, CsvImportLog
from ....services.club_service import create_club, get_club, list_clubs
from ....services import csv_import_service

from . import templates, _admin_guard

logger = logging.getLogger(__name__)

router = APIRouter()

# Maps club team age-group labels (U15, U18, Adult…) → canonical Semester.age_group values.
# Semester.age_group is shared with season semesters (PRE/YOUTH/AMATEUR/PRO) so promotion
# tournaments must use the same vocabulary to stay filter-compatible.
_CLUB_AGE_GROUP_MAP: dict[str, str] = {
    "U6": "PRE", "U7": "PRE", "U8": "PRE", "U9": "PRE",
    "U10": "PRE", "U11": "PRE", "U12": "PRE",
    "U13": "YOUTH", "U14": "YOUTH", "U15": "YOUTH",
    "U16": "YOUTH", "U17": "YOUTH", "U18": "YOUTH",
    "U19": "AMATEUR", "U20": "AMATEUR", "U21": "AMATEUR",
    "U22": "AMATEUR", "U23": "AMATEUR",
    "SENIOR": "AMATEUR", "ADULT": "AMATEUR",
    "PRE": "PRE", "YOUTH": "YOUTH", "AMATEUR": "AMATEUR", "PRO": "PRO",
}


def _normalize_club_age_group(label: str) -> str:
    """Map club team age-group label to the canonical Semester.age_group vocabulary."""
    return _CLUB_AGE_GROUP_MAP.get(label.strip().upper(), "AMATEUR")


@router.get("/admin/clubs", response_class=HTMLResponse)
async def admin_clubs_list(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: list all clubs."""
    _admin_guard(user)

    clubs = list_clubs(db, active_only=False)
    # Batch-load team counts
    club_ids = [c.id for c in clubs]
    team_counts: dict = {}
    member_counts: dict = {}
    if club_ids:
        for row in db.query(
            Team.club_id, sqlfunc.count(Team.id)
        ).filter(Team.club_id.in_(club_ids), Team.is_active == True).group_by(Team.club_id).all():  # noqa: E712
            team_counts[row[0]] = row[1]

        # total members per club via JOIN
        team_id_by_club: dict = defaultdict(list)
        for t in db.query(Team.id, Team.club_id).filter(Team.club_id.in_(club_ids), Team.is_active == True).all():  # noqa: E712
            team_id_by_club[t.club_id].append(t.id)
        all_team_ids = [tid for tids in team_id_by_club.values() for tid in tids]
        if all_team_ids:
            for row in db.query(
                TeamMember.team_id, sqlfunc.count(TeamMember.id)
            ).filter(TeamMember.team_id.in_(all_team_ids), TeamMember.is_active == True).group_by(TeamMember.team_id).all():  # noqa: E712
                team_club_id = next((cid for cid, tids in team_id_by_club.items() if row[0] in tids), None)
                if team_club_id:
                    member_counts[team_club_id] = member_counts.get(team_club_id, 0) + row[1]

    total_clubs = len(clubs)
    total_teams = sum(team_counts.values())
    total_players = sum(member_counts.values())

    return templates.TemplateResponse(
        "admin/clubs.html",
        {
            "request": request,
            "user": user,
            "clubs": clubs,
            "team_counts": team_counts,
            "member_counts": member_counts,
            "total_clubs": total_clubs,
            "total_teams": total_teams,
            "total_players": total_players,
        },
    )


@router.post("/admin/clubs/create")
async def admin_create_club(
    request: Request,
    name: str = Form(...),
    city: str = Form(""),
    country: str = Form(""),
    contact_email: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: create a new Club."""
    _admin_guard(user)

    try:
        club = create_club(
            db,
            name=name,
            city=city or None,
            country=country or None,
            contact_email=contact_email or None,
            created_by_id=user.id,
        )
        db.commit()
        logger.info("admin_club_created", extra={"admin": user.email, "club": club.name, "code": club.code})
        return RedirectResponse(url=f"/admin/clubs/{club.id}?msg=Club+created", status_code=303)
    except ValueError as exc:
        return RedirectResponse(
            url=f"/admin/clubs?create_error={str(exc).replace(' ', '+')}",
            status_code=303,
        )


@router.get("/admin/clubs/{club_id}", response_class=HTMLResponse)
async def admin_club_detail(
    club_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: club detail with teams + CSV import history."""
    _admin_guard(user)

    club = get_club(db, club_id)
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    # Load teams for this club
    teams = (
        db.query(Team)
        .filter(Team.club_id == club_id, Team.is_active == True)  # noqa: E712
        .order_by(Team.age_group_label, Team.name)
        .all()
    )
    team_ids = [t.id for t in teams]
    member_counts: dict = {}
    if team_ids:
        for row in db.query(
            TeamMember.team_id, sqlfunc.count(TeamMember.id)
        ).filter(TeamMember.team_id.in_(team_ids), TeamMember.is_active == True).group_by(TeamMember.team_id).all():  # noqa: E712
            member_counts[row[0]] = row[1]

    # Import history
    import_logs = (
        db.query(CsvImportLog)
        .filter(CsvImportLog.club_id == club_id)
        .order_by(CsvImportLog.uploaded_at.desc())
        .limit(10)
        .all()
    )

    # For promotion wizard: game presets + tournament types + campuses
    from ....models.game_preset import GamePreset
    from ....models.tournament_type import TournamentType
    game_presets = db.query(GamePreset).order_by(GamePreset.name).all()
    tournament_types = db.query(TournamentType).order_by(TournamentType.display_name).all()
    campuses = db.query(Campus).filter(Campus.is_active == True).order_by(Campus.name).all()  # noqa: E712

    # Unique age_groups from teams
    age_groups = sorted({t.age_group_label for t in teams if t.age_group_label})

    # Canonical age_group per team: U15 → YOUTH, U12 → PRE (for dual display in UI)
    team_canonical = {
        t.id: _normalize_club_age_group(t.age_group_label)
        for t in teams if t.age_group_label
    }

    return templates.TemplateResponse(
        "admin/club_detail.html",
        {
            "request": request,
            "user": user,
            "club": club,
            "teams": teams,
            "member_counts": member_counts,
            "import_logs": import_logs,
            "game_presets": game_presets,
            "tournament_types": tournament_types,
            "campuses": campuses,
            "age_groups": age_groups,
            "team_canonical": team_canonical,
        },
    )


@router.post("/admin/clubs/{club_id}/edit")
async def admin_edit_club(
    club_id: int,
    request: Request,
    name: str = Form(...),
    city: str = Form(""),
    country: str = Form(""),
    contact_email: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: update club details."""
    _admin_guard(user)
    club = get_club(db, club_id)
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    club.name = name.strip()
    club.city = city or None
    club.country = country or None
    club.contact_email = contact_email or None
    db.commit()
    return RedirectResponse(url=f"/admin/clubs/{club_id}?msg=Club+updated", status_code=303)


@router.post("/admin/clubs/{club_id}/toggle")
async def admin_toggle_club(
    club_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: activate or deactivate a club."""
    _admin_guard(user)
    club = get_club(db, club_id)
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")
    club.is_active = not club.is_active
    db.commit()
    return RedirectResponse(url=f"/admin/clubs/{club_id}?msg=Status+updated", status_code=303)


@router.post("/admin/clubs/{club_id}/csv-import")
async def admin_club_csv_import(
    club_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: upload + process a CSV file for a club."""
    _admin_guard(user)

    club = get_club(db, club_id)
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    content = await file.read()

    # Create log entry first (need id for idempotency keys)
    log = CsvImportLog(
        club_id=club_id,
        uploaded_by=user.id,
        filename=file.filename or "upload.csv",
        status="PROCESSING",
    )
    db.add(log)
    db.commit()
    db.refresh(log)

    # Parse + import
    rows = csv_import_service.parse_csv(content)
    csv_import_service.import_rows(db, rows, log, admin_user=user, default_club_id=club_id)
    db.commit()

    logger.info(
        "admin_csv_import_done admin=%s club=%s file=%s created=%d updated=%d failed=%d",
        user.email, club.name, file.filename,
        log.rows_created, log.rows_updated, log.rows_failed,
    )
    return RedirectResponse(
        url=f"/admin/clubs/{club_id}?import_log={log.id}",
        status_code=303,
    )


@router.get("/admin/clubs/{club_id}/csv-import/{log_id}", response_class=HTMLResponse)
async def admin_club_import_log(
    club_id: int,
    log_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: view a specific CSV import log in detail."""
    _admin_guard(user)
    log = db.query(CsvImportLog).filter(
        CsvImportLog.id == log_id, CsvImportLog.club_id == club_id
    ).first()
    if not log:
        raise HTTPException(status_code=404, detail="Import log not found")

    club = get_club(db, club_id)
    return templates.TemplateResponse(
        "admin/club_detail.html",
        {
            "request": request,
            "user": user,
            "club": club,
            "import_log_detail": log,
            "teams": [],
            "member_counts": {},
            "import_logs": [],
            "game_presets": [],
            "tournament_types": [],
            "campuses": [],
            "age_groups": [],
        },
    )


@router.post("/admin/clubs/{club_id}/promotion")
async def admin_club_promotion(
    club_id: int,
    request: Request,
    tournament_name: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    campus_id: str = Form(""),
    game_preset_id: str = Form(""),
    tournament_type_id: str = Form(""),
    age_groups: list[str] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: create one TEAM tournament per selected age_group and enroll that age group's teams."""
    _admin_guard(user)

    club = get_club(db, club_id)
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")

    if not age_groups:
        return RedirectResponse(
            url=f"/admin/clubs/{club_id}?error=Select+at+least+one+age+group",
            status_code=303,
        )

    from datetime import datetime as _dt
    from ....models.semester import SemesterStatus, SemesterCategory
    from ....models.tournament_configuration import TournamentConfiguration
    from ....models.game_configuration import GameConfiguration
    from ....models.team import TournamentTeamEnrollment

    created_ids = []
    for ag in age_groups:
        suffix = _dt.now().strftime("%H%M%S%f")[:9]
        code = f"PROMO-{date.fromisoformat(start_date).strftime('%Y%m%d')}-{ag.upper()[:6]}-{suffix}"

        t = Semester(
            code=code,
            name=f"{tournament_name} ({ag})",
            start_date=date.fromisoformat(start_date),
            end_date=date.fromisoformat(end_date),
            status=SemesterStatus.DRAFT,
            tournament_status="DRAFT",
            semester_category=SemesterCategory.PROMOTION_EVENT,
            specialization_type="LFA_FOOTBALL_PLAYER",
            age_group=_normalize_club_age_group(ag),  # U15 → YOUTH, U12 → PRE, etc.
            enrollment_cost=0,
            campus_id=int(campus_id) if campus_id.strip() else None,
            organizer_club_id=club.id,
        )
        db.add(t)
        db.flush()

        # Auto-derive location_id from campus (campus always belongs to a location)
        if t.campus_id:
            _campus = db.query(Campus).filter(Campus.id == t.campus_id).first()
            if _campus and _campus.location_id:
                t.location_id = _campus.location_id

        cfg = TournamentConfiguration(
            semester_id=t.id,
            tournament_type_id=int(tournament_type_id) if tournament_type_id.strip() else None,
            participant_type="TEAM",
            number_of_rounds=1,
        )
        db.add(cfg)

        if game_preset_id.strip():
            db.add(GameConfiguration(semester_id=t.id, game_preset_id=int(game_preset_id)))

        # Auto-enroll all teams from this club with matching age_group_label
        teams_for_ag = (
            db.query(Team)
            .filter(Team.club_id == club_id, Team.age_group_label == ag, Team.is_active == True)  # noqa: E712
            .all()
        )
        for team in teams_for_ag:
            active_members = db.query(TeamMember).filter(
                TeamMember.team_id == team.id,
                TeamMember.is_active == True,  # noqa: E712
            ).count()
            if active_members == 0:
                continue  # skip empty teams — they cannot participate
            db.add(TournamentTeamEnrollment(
                semester_id=t.id,
                team_id=team.id,
                is_active=True,
                payment_verified=True,  # admin bypass — no credit cost
            ))

        db.flush()
        created_ids.append(t.id)

    db.commit()
    logger.info(
        "admin_promotion_created",
        extra={"admin": user.email, "club": club.name, "age_groups": age_groups, "tournament_ids": created_ids},
    )

    # Redirect to promotion events list; flash is shown via query param
    names_enc = "+".join(ag.replace(" ", "_") for ag in age_groups)
    return RedirectResponse(
        url=f"/admin/promotion-events?flash=Promotion+tournaments+created+for+{names_enc}",
        status_code=303,
    )


@router.get("/admin/clubs/{club_id}/teams/{team_id}", response_class=HTMLResponse)
async def admin_club_team_detail(
    request: Request,
    club_id: int,
    team_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: team member list for a club team."""
    _admin_guard(user)
    club = get_club(db, club_id)
    if not club:
        raise HTTPException(status_code=404, detail="Club not found")
    team = db.query(Team).filter(Team.id == team_id, Team.club_id == club_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    members = (
        db.query(TeamMember)
        .filter(TeamMember.team_id == team_id)
        .order_by(TeamMember.role.desc(), TeamMember.joined_at)
        .all()
    )
    canonical_age = _normalize_club_age_group(team.age_group_label) if team.age_group_label else None
    return templates.TemplateResponse(
        "admin/club_team_detail.html",
        {
            "request": request,
            "user": user,
            "club": club,
            "team": team,
            "members": members,
            "canonical_age": canonical_age,
        },
    )


@router.get("/admin/teams", response_class=HTMLResponse)
async def admin_teams_page(
    request: Request,
    tournament_filter: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: global teams list, filterable by tournament."""
    _admin_guard(user)

    # All tournaments + promotion events for filter dropdown (TEAM participant type)
    tournaments = db.query(Semester).filter(
        Semester.semester_category.in_([SemesterCategory.TOURNAMENT, SemesterCategory.PROMOTION_EVENT]),
    ).order_by(Semester.start_date.desc()).all()

    if tournament_filter:
        enrolled_team_ids = [
            e.team_id for e in db.query(TournamentTeamEnrollment).filter(
                TournamentTeamEnrollment.semester_id == tournament_filter,
                TournamentTeamEnrollment.is_active == True,  # noqa: E712
            ).all()
        ]
        teams = db.query(Team).filter(
            Team.id.in_(enrolled_team_ids),
            Team.is_active == True,  # noqa: E712
        ).order_by(Team.name).all() if enrolled_team_ids else []
    else:
        teams = db.query(Team).filter(Team.is_active == True).order_by(Team.name).all()  # noqa: E712

    # Batch-load member counts
    member_counts: dict = {}
    if teams:
        team_ids = [t.id for t in teams]
        for row in db.query(
            TeamMember.team_id,
            sqlfunc.count(TeamMember.id),
        ).filter(
            TeamMember.team_id.in_(team_ids),
            TeamMember.is_active == True,  # noqa: E712
        ).group_by(TeamMember.team_id).all():
            member_counts[row[0]] = row[1]

    # Batch-load tournament enrollments per team
    team_enrollments: dict = defaultdict(list)
    if teams:
        for enr in db.query(TournamentTeamEnrollment).filter(
            TournamentTeamEnrollment.team_id.in_([t.id for t in teams]),
            TournamentTeamEnrollment.is_active == True,  # noqa: E712
        ).all():
            team_enrollments[enr.team_id].append(enr)

    return templates.TemplateResponse(
        "admin/teams.html",
        {
            "request": request,
            "user": user,
            "teams": teams,
            "tournaments": tournaments,
            "member_counts": member_counts,
            "team_enrollments": team_enrollments,
            "tournament_filter": tournament_filter,
        },
    )
