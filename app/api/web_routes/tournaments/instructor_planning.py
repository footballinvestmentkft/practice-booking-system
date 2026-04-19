"""Admin instructor planning (IP-*) routes."""
from typing import Optional

from fastapi import APIRouter, Request, Depends, Form
from fastapi.responses import JSONResponse

from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.user import User
import app.services.tournament.instructor_planning_service as _ip_service
from . import templates, _admin_only, _publish_instructor_change

router = APIRouter()


# ── Instructor Planning (IP-*) ────────────────────────────────────────────────

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
