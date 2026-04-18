"""Admin sport director management routes."""
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone
import logging

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.user import User, UserRole
from ....models.location import Location
from ....models.instructor_assignment import SportDirectorAssignment

from . import templates, _admin_guard

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/admin/sport-directors", response_class=HTMLResponse)
async def admin_sport_directors_page(
    request: Request,
    location_filter: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: list and manage sport director assignments per location."""
    _admin_guard(user)

    locations = db.query(Location).filter(Location.is_active == True).order_by(Location.name).all()  # noqa: E712

    q = db.query(SportDirectorAssignment)
    if location_filter:
        q = q.filter(SportDirectorAssignment.location_id == location_filter)
    assignments = q.order_by(SportDirectorAssignment.location_id, SportDirectorAssignment.is_active.desc()).all()

    # Eligible candidates: users with SPORT_DIRECTOR role OR ADMIN (for assignment dropdown)
    candidates = db.query(User).filter(
        User.role.in_([UserRole.SPORT_DIRECTOR, UserRole.ADMIN]),
        User.is_active == True,  # noqa: E712
    ).order_by(User.name).all()

    location_map = {loc.id: loc for loc in locations}

    return templates.TemplateResponse(
        "admin/sport_directors.html",
        {
            "request": request,
            "user": user,
            "assignments": assignments,
            "locations": locations,
            "candidates": candidates,
            "location_map": location_map,
            "location_filter": location_filter,
        },
    )


@router.post("/admin/sport-directors/assign")
async def admin_assign_sport_director(
    request: Request,
    user_id: int = Form(...),
    location_id: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: assign a user as Sport Director for a location."""
    _admin_guard(user)

    # Deactivate existing active SD for this location
    existing = db.query(SportDirectorAssignment).filter(
        SportDirectorAssignment.location_id == location_id,
        SportDirectorAssignment.is_active == True,  # noqa: E712
    ).first()
    if existing:
        existing.is_active = False
        existing.deactivated_at = datetime.now(timezone.utc)

    assignment = SportDirectorAssignment(
        user_id=user_id,
        location_id=location_id,
        is_active=True,
        assigned_by=user.id,
    )
    db.add(assignment)
    db.commit()
    return RedirectResponse(url="/admin/sport-directors?msg=Sport+Director+assigned", status_code=303)


@router.post("/admin/sport-directors/{assignment_id}/deactivate")
async def admin_deactivate_sport_director(
    assignment_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: deactivate a sport director assignment."""
    _admin_guard(user)
    a = db.query(SportDirectorAssignment).filter(SportDirectorAssignment.id == assignment_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Assignment not found")
    a.is_active = False
    a.deactivated_at = datetime.now(timezone.utc)
    db.commit()
    return RedirectResponse(url="/admin/sport-directors?msg=Assignment+deactivated", status_code=303)
