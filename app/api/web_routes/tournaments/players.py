"""Admin player enrollment routes for INDIVIDUAL tournaments."""
from typing import Optional

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.responses import JSONResponse
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web, get_current_admin_or_instructor_user_hybrid
from ....models.club import Club
from ....models.license import UserLicense
from ....models.semester import Semester, SemesterCategory
from ....models.semester_enrollment import SemesterEnrollment
from ....models.team import Team, TeamMember
from ....models.user import User
import app.services.tournament.enrollment_service as _enroll_service
from . import templates, _admin_only

router = APIRouter()

_PROMO_GUARD_MSG = "Standard enrollment management is not available for promotion events."


def _fetch_tournament_or_404(db: Session, tournament_id: int) -> Semester:
    t = db.query(Semester).filter(Semester.id == tournament_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Tournament not found")
    return t


def _raise_if_promotion_event(t: Semester) -> None:
    if t.semester_category == SemesterCategory.PROMOTION_EVENT:
        raise HTTPException(status_code=400, detail=_PROMO_GUARD_MSG)


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

    if t.semester_category == SemesterCategory.PROMOTION_EVENT:
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/edit?error=Player+management+is+not+available+for+promotion+events",
            status_code=303,
        )
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
        _raise_if_promotion_event(_fetch_tournament_or_404(db, tournament_id))
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
        _raise_if_promotion_event(_fetch_tournament_or_404(db, tournament_id))
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
    if t.semester_category == SemesterCategory.PROMOTION_EVENT:
        return RedirectResponse(
            url=f"/admin/tournaments/{tournament_id}/edit?error=Player+management+is+not+available+for+promotion+events",
            status_code=303,
        )
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
    _raise_if_promotion_event(_fetch_tournament_or_404(db, tournament_id))
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
    _raise_if_promotion_event(_fetch_tournament_or_404(db, tournament_id))
    body = await request.json()
    uid = body.get("user_id")
    if not uid:
        raise HTTPException(status_code=422, detail="user_id required")

    _enroll_service.unenroll_player_admin(db, tournament_id, uid)
    db.commit()
    return JSONResponse({"success": True, "user_id": uid})
