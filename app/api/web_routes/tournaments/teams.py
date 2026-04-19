"""Admin team management routes for TEAM tournaments."""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.club import Club
from ....models.semester import Semester
from ....models.team import Team, TeamMember, TournamentTeamEnrollment
from ....models.user import User, UserRole
from ....services.tournament import team_service as _team_service
from . import templates, _admin_only

router = APIRouter()


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
    enrollment.payment_verified = True
    enrollment.payment_verified_by = user.id
    enrollment.payment_verified_at = datetime.now(timezone.utc)
    db.commit()
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
