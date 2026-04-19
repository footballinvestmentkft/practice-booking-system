"""Admin tournament attendance page and check-in endpoints."""
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web, get_current_admin_user_hybrid
from ....models.semester import Semester
from ....models.user import User
import app.services.tournament.attendance_service as _att_service
from . import templates, _admin_only

router = APIRouter()


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
