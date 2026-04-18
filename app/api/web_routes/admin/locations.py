"""Admin location and campus management routes."""
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone, date, timedelta
from collections import defaultdict
import logging
import uuid as _uuid

from sqlalchemy import func as sqlfunc, or_

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.user import User, UserRole
from ....models.semester import Semester, SemesterStatus, SemesterCategory
from ....models.location import Location, LocationType
from ....models.campus import Campus
from ....models.session import Session as SessionModel
from ....models.instructor_assignment import InstructorAssignment

from . import templates, _admin_guard

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/admin/locations", response_class=HTMLResponse)
async def admin_locations_page(
    request: Request,
    city_filter: str = "",
    status_filter: str = "active",
    name_search: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Locations & Campuses management with filters"""
    _admin_guard(user)

    q = db.query(Location)
    if status_filter == "active":
        q = q.filter(Location.is_active == True)
    elif status_filter == "inactive":
        q = q.filter(Location.is_active == False)
    if city_filter:
        q = q.filter(Location.city == city_filter)
    if name_search:
        q = q.filter(Location.name.ilike(f"%{name_search}%"))

    locations = q.order_by(Location.name).all()
    # Batch-load all campuses for filtered locations (avoid N+1)
    loc_ids = [loc.id for loc in locations]
    if loc_ids:
        all_campuses = db.query(Campus).filter(Campus.location_id.in_(loc_ids)).order_by(Campus.name).all()
        campus_by_loc = defaultdict(list)
        for c in all_campuses:
            campus_by_loc[c.location_id].append(c)
    else:
        campus_by_loc = {}
    for loc in locations:
        loc.campuses_list = campus_by_loc.get(loc.id, [])

    all_cities = sorted(set(
        loc.city for loc in db.query(Location).all() if loc.city
    ))

    # Batch: active semester counts per location
    semester_counts: dict = {}
    if loc_ids:
        for row in db.query(Semester.location_id, sqlfunc.count(Semester.id)).filter(
            Semester.location_id.in_(loc_ids),
            Semester.status.in_([SemesterStatus.READY_FOR_ENROLLMENT, SemesterStatus.ONGOING])
        ).group_by(Semester.location_id).all():
            semester_counts[row[0]] = row[1]

    # Batch: active instructor counts per location
    instructor_counts: dict = {}
    if loc_ids:
        for row in db.query(
            InstructorAssignment.location_id,
            sqlfunc.count(sqlfunc.distinct(InstructorAssignment.instructor_id))
        ).filter(
            InstructorAssignment.location_id.in_(loc_ids),
            InstructorAssignment.is_active == True  # noqa: E712
        ).group_by(InstructorAssignment.location_id).all():
            instructor_counts[row[0]] = row[1]

    # Group locations by country (sorted alphabetically)
    locations_by_country: dict = defaultdict(list)
    for loc in locations:
        locations_by_country[loc.country or "Unknown"].append(loc)
    locations_by_country = dict(sorted(locations_by_country.items()))

    return templates.TemplateResponse(
        "admin/locations.html",
        {
            "request": request,
            "user": user,
            "locations": locations,
            "locations_by_country": locations_by_country,
            "LocationType": LocationType,
            "all_cities": all_cities,
            "city_filter": city_filter,
            "status_filter": status_filter,
            "name_search": name_search,
            "semester_counts": semester_counts,
            "instructor_counts": instructor_counts,
        }
    )


@router.get("/admin/locations/{location_id}", response_class=HTMLResponse)
async def admin_location_detail_page(
    location_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Location detail — hierarchical view of campus, programs, sessions, instructors."""
    _admin_guard(user)

    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")

    # Q2: Campuses
    campuses = db.query(Campus).filter(Campus.location_id == location_id).order_by(Campus.name).all()
    campus_ids = [c.id for c in campuses]

    # Q3: Active/upcoming semesters at this location
    _active_statuses = [
        SemesterStatus.READY_FOR_ENROLLMENT,
        SemesterStatus.ONGOING,
        SemesterStatus.INSTRUCTOR_ASSIGNED,
    ]
    semesters = db.query(Semester).filter(
        Semester.location_id == location_id,
        Semester.status.in_(_active_statuses)
    ).order_by(Semester.start_date).all()
    semester_ids = [s.id for s in semesters]

    # Q4: Upcoming sessions — UNION: campus_id IN + semester_id IN
    _today = date.today()
    _two_weeks = _today + timedelta(days=14)
    _base_filters = [
        sqlfunc.date(SessionModel.date_start) >= _today,
        sqlfunc.date(SessionModel.date_start) <= _two_weeks,
        SessionModel.session_status != 'cancelled',
    ]
    if campus_ids and semester_ids:
        _loc_filter = or_(
            SessionModel.campus_id.in_(campus_ids),
            SessionModel.semester_id.in_(semester_ids),
        )
    elif campus_ids:
        _loc_filter = SessionModel.campus_id.in_(campus_ids)
    elif semester_ids:
        _loc_filter = SessionModel.semester_id.in_(semester_ids)
    else:
        _loc_filter = None

    if _loc_filter is not None:
        upcoming_sessions = db.query(SessionModel).filter(
            _loc_filter, *_base_filters
        ).order_by(SessionModel.date_start).limit(30).all()
    else:
        upcoming_sessions = []

    # Q5: Active instructor assignments at this location
    assignments = db.query(InstructorAssignment).filter(
        InstructorAssignment.location_id == location_id,
        InstructorAssignment.is_active == True  # noqa: E712
    ).order_by(InstructorAssignment.age_group, InstructorAssignment.year.desc()).all()

    # Q6: Batch load instructor user objects
    _instr_ids = list({a.instructor_id for a in assignments})
    instructor_map: dict = (
        {u.id: u for u in db.query(User).filter(User.id.in_(_instr_ids)).all()}
        if _instr_ids else {}
    )

    # Q7: Upcoming session counts per campus (direct campus_id only)
    campus_session_counts: dict = {}
    if campus_ids:
        for row in db.query(SessionModel.campus_id, sqlfunc.count(SessionModel.id)).filter(
            SessionModel.campus_id.in_(campus_ids),
            sqlfunc.date(SessionModel.date_start) >= _today,
            SessionModel.session_status != 'cancelled',
        ).group_by(SessionModel.campus_id).all():
            campus_session_counts[row[0]] = row[1]

    # Group semesters by age_group (ordered logically)
    semesters_by_group: dict = defaultdict(list)
    for s in semesters:
        key = s.age_group or (s.semester_category.value if s.semester_category else "OTHER")
        semesters_by_group[key].append(s)
    _group_order = ["PRE", "YOUTH", "AMATEUR", "PRO", "TOURNAMENT", "CAMP", "OTHER"]
    semesters_by_group = {k: semesters_by_group[k] for k in _group_order if k in semesters_by_group}

    return templates.TemplateResponse(
        "admin/location_detail.html",
        {
            "request": request,
            "user": user,
            "loc": loc,
            "campuses": campuses,
            "semesters": semesters,
            "semesters_by_group": semesters_by_group,
            "upcoming_sessions": upcoming_sessions,
            "assignments": assignments,
            "instructor_map": instructor_map,
            "campus_session_counts": campus_session_counts,
            "LocationType": LocationType,
            "SemesterStatus": SemesterStatus,
            "today": _today,
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        }
    )


@router.post("/admin/locations")
async def admin_create_location(
    request: Request,
    name: str = Form(...),
    city: str = Form(...),
    country: str = Form(...),
    country_code: str = Form(""),
    location_code: str = Form(""),
    postal_code: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    location_type: str = Form("PARTNER"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    loc = Location(
        name=name.strip(),
        city=city.strip(),
        country=country.strip(),
        country_code=country_code.strip().upper() or None,
        location_code=location_code.strip().upper() or None,
        postal_code=postal_code.strip() or None,
        address=address.strip() or None,
        notes=notes.strip() or None,
        location_type=LocationType(location_type),
        is_active=True,
    )
    db.add(loc)
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


@router.post("/admin/locations/{location_id}/toggle")
async def admin_toggle_location(
    location_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    loc.is_active = not loc.is_active
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


@router.post("/admin/locations/{location_id}/delete")
async def admin_delete_location(
    location_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    db.delete(loc)
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


@router.get("/admin/locations/{location_id}/edit", response_class=HTMLResponse)
async def admin_edit_location_page(
    location_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    return templates.TemplateResponse(
        "admin/location_edit.html",
        {"request": request, "user": user, "loc": loc, "LocationType": LocationType}
    )


@router.post("/admin/locations/{location_id}/edit")
async def admin_update_location(
    location_id: int,
    request: Request,
    name: str = Form(...),
    city: str = Form(...),
    country: str = Form(...),
    country_code: str = Form(""),
    location_code: str = Form(""),
    postal_code: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    location_type: str = Form("PARTNER"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    loc.name = name.strip()
    loc.city = city.strip()
    loc.country = country.strip()
    loc.country_code = country_code.strip().upper() or None
    loc.location_code = location_code.strip().upper() or None
    loc.postal_code = postal_code.strip() or None
    loc.address = address.strip() or None
    loc.notes = notes.strip() or None
    # K2: Block CENTER→PARTNER when active Academy semesters exist at this location.
    _ACADEMY_SPECS = {
        "LFA_PLAYER_PRE_ACADEMY", "LFA_PLAYER_YOUTH_ACADEMY",
        "LFA_PLAYER_AMATEUR_ACADEMY", "LFA_PLAYER_PRO_ACADEMY",
    }
    _ACTIVE_STATUSES = {SemesterStatus.READY_FOR_ENROLLMENT, SemesterStatus.ONGOING}
    try:
        new_loc_type = LocationType(location_type)
    except ValueError:
        new_loc_type = loc.location_type  # unchanged
    if loc.location_type == LocationType.CENTER and new_loc_type == LocationType.PARTNER:
        conflict = (
            db.query(Semester)
            .filter(
                Semester.location_id == location_id,
                Semester.specialization_type.in_(_ACADEMY_SPECS),
                Semester.status.in_(_ACTIVE_STATUSES),
            )
            .first()
        )
        if conflict:
            loc_for_template = db.query(Location).filter(Location.id == location_id).first()
            return templates.TemplateResponse(
                "admin/location_edit.html",
                {
                    "request": request,
                    "user": user,
                    "loc": loc_for_template,
                    "LocationType": LocationType,
                    "error": (
                        f"Nem változtatható CENTER→PARTNER típusra: "
                        f"'{conflict.name}' ({conflict.code}) aktív Academy Season "
                        f"ehhez a helyszínhez van rendelve."
                    ),
                },
                status_code=409,
            )
    loc.location_type = new_loc_type
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


@router.get("/admin/events/locations/{location_id}", response_class=HTMLResponse)
async def admin_location_events_page(
    location_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin-only: Per-location event management — all event types CRUD in one place."""
    _admin_guard(user)
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")

    campuses = db.query(Campus).filter(Campus.location_id == location_id).order_by(Campus.name).all()
    campus_ids = [c.id for c in campuses]
    campus_map = {c.id: c for c in campuses}

    # Tournaments (TOURNAMENT category OR legacy TOURN-/OPS- codes) at this location
    tournaments = (
        db.query(Semester)
        .filter(
            or_(
                Semester.semester_category == SemesterCategory.TOURNAMENT,
                Semester.code.like("TOURN-%"),
                Semester.code.like("OPS-%"),
            ),
            Semester.location_id == location_id,
            Semester.status != SemesterStatus.CANCELLED,
        )
        .order_by(Semester.start_date.desc())
        .all()
    )

    # Camps at this location
    camps = (
        db.query(Semester)
        .filter(
            Semester.semester_category == SemesterCategory.CAMP,
            Semester.location_id == location_id,
            Semester.status != SemesterStatus.CANCELLED,
        )
        .order_by(Semester.start_date.desc())
        .all()
    )

    # Academy / Mini Seasons at this location
    seasons = (
        db.query(Semester)
        .filter(
            Semester.semester_category.in_(
                [SemesterCategory.ACADEMY_SEASON, SemesterCategory.MINI_SEASON]
            ),
            Semester.location_id == location_id,
            Semester.status != SemesterStatus.CANCELLED,
        )
        .order_by(Semester.start_date.desc())
        .all()
    )

    # Upcoming sessions at this location's campuses (next 30 days)
    in_30d = date.today() + timedelta(days=30)
    sessions = (
        db.query(SessionModel)
        .filter(
            SessionModel.campus_id.in_(campus_ids),
            sqlfunc.date(SessionModel.date_start) >= date.today(),
            sqlfunc.date(SessionModel.date_start) <= in_30d,
            SessionModel.session_status != "cancelled",
        )
        .order_by(SessionModel.date_start)
        .limit(20)
        .all()
        if campus_ids
        else []
    )

    return templates.TemplateResponse(
        "admin/events_location.html",
        {
            "request": request,
            "user": user,
            "loc": loc,
            "campuses": campuses,
            "campus_map": campus_map,
            "tournaments": tournaments,
            "camps": camps,
            "seasons": seasons,
            "sessions": sessions,
            "SemesterStatus": SemesterStatus,
            "SemesterCategory": SemesterCategory,
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/admin/locations/{location_id}/campuses")
async def admin_create_campus(
    location_id: int,
    name: str = Form(...),
    venue: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    loc = db.query(Location).filter(Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    campus = Campus(
        location_id=location_id,
        name=name.strip(),
        venue=venue.strip() or None,
        address=address.strip() or None,
        notes=notes.strip() or None,
        is_active=True,
    )
    db.add(campus)
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


@router.get("/admin/campuses/{campus_id}", response_class=HTMLResponse)
async def admin_campus_detail_page(
    campus_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Campus detail — programs, upcoming sessions, location instructors."""
    _admin_guard(user)

    campus = db.query(Campus).filter(Campus.id == campus_id).first()
    if not campus:
        raise HTTPException(status_code=404, detail="Campus not found")

    loc = db.query(Location).filter(Location.id == campus.location_id).first()

    # Q2: Active semesters at this campus
    _active_statuses = [
        SemesterStatus.READY_FOR_ENROLLMENT,
        SemesterStatus.ONGOING,
        SemesterStatus.INSTRUCTOR_ASSIGNED,
    ]
    semesters = db.query(Semester).filter(
        Semester.campus_id == campus_id,
        Semester.status.in_(_active_statuses)
    ).order_by(Semester.start_date).all()
    semester_ids = [s.id for s in semesters]

    # Q3: Upcoming sessions at this campus (campus_id OR semester)
    _today = date.today()
    _base_filters = [
        sqlfunc.date(SessionModel.date_start) >= _today,
        SessionModel.session_status != 'cancelled',
    ]
    if semester_ids:
        _sess_filter = or_(
            SessionModel.campus_id == campus_id,
            SessionModel.semester_id.in_(semester_ids),
        )
    else:
        _sess_filter = SessionModel.campus_id == campus_id

    sessions = db.query(SessionModel).filter(
        _sess_filter, *_base_filters
    ).order_by(SessionModel.date_start).limit(20).all()

    # Q4: Active instructor assignments at parent location
    assignments = db.query(InstructorAssignment).filter(
        InstructorAssignment.location_id == campus.location_id,
        InstructorAssignment.is_active == True  # noqa: E712
    ).order_by(InstructorAssignment.age_group).all()
    _instr_ids = list({a.instructor_id for a in assignments})
    instructor_map: dict = (
        {u.id: u for u in db.query(User).filter(User.id.in_(_instr_ids)).all()}
        if _instr_ids else {}
    )

    return templates.TemplateResponse(
        "admin/campus_detail.html",
        {
            "request": request,
            "user": user,
            "campus": campus,
            "loc": loc,
            "semesters": semesters,
            "sessions": sessions,
            "assignments": assignments,
            "instructor_map": instructor_map,
            "today": _today,
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        }
    )


@router.get("/admin/campuses/{campus_id}/edit", response_class=HTMLResponse)
async def admin_edit_campus_page(
    campus_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    campus = db.query(Campus).filter(Campus.id == campus_id).first()
    if not campus:
        raise HTTPException(status_code=404, detail="Campus not found")
    return templates.TemplateResponse(
        "admin/campus_edit.html",
        {"request": request, "user": user, "campus": campus}
    )


@router.post("/admin/campuses/{campus_id}/edit")
async def admin_update_campus(
    campus_id: int,
    request: Request,
    name: str = Form(...),
    venue: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    campus = db.query(Campus).filter(Campus.id == campus_id).first()
    if not campus:
        raise HTTPException(status_code=404, detail="Campus not found")
    campus.name = name.strip()
    campus.venue = venue.strip() or None
    campus.address = address.strip() or None
    campus.notes = notes.strip() or None
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


@router.post("/admin/campuses/{campus_id}/toggle")
async def admin_toggle_campus(
    campus_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    campus = db.query(Campus).filter(Campus.id == campus_id).first()
    if not campus:
        raise HTTPException(status_code=404, detail="Campus not found")
    campus.is_active = not campus.is_active
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)


@router.post("/admin/campuses/{campus_id}/delete")
async def admin_delete_campus(
    campus_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    campus = db.query(Campus).filter(Campus.id == campus_id).first()
    if not campus:
        raise HTTPException(status_code=404, detail="Campus not found")
    db.delete(campus)
    db.commit()
    return RedirectResponse(url="/admin/locations", status_code=303)
