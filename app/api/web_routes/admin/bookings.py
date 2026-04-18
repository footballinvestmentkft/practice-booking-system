"""Admin bookings, sessions, and pitches management routes."""
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timezone, date
from collections import defaultdict
import logging

from sqlalchemy import func as sqlfunc

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.user import User, UserRole
from ....models.semester import Semester
from ....models.session import Session as SessionModel, SessionType
from ....models.booking import Booking, BookingStatus
from ....models.attendance import Attendance, AttendanceStatus
from ....models.location import Location
from ....models.campus import Campus
from ....models.pitch import Pitch
from ....models.pitch_instructor_assignment import (
    PitchInstructorAssignment,
    PitchAssignmentType,
    PitchAssignmentStatus,
)
from ....services.tournament.pitch_instructor_service import (
    assign_instructor_to_pitch_direct,
)

from . import templates, _admin_guard

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/admin/bookings", response_class=HTMLResponse)
async def admin_bookings_page(
    request: Request,
    status_filter: str = "",
    session_id: int = 0,
    page: int = 1,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: list all bookings with filters and action buttons."""
    _admin_guard(user)

    q = db.query(Booking).options(
        joinedload(Booking.user),
        joinedload(Booking.session),
        joinedload(Booking.attendance),
    )
    if status_filter:
        try:
            q = q.filter(Booking.status == BookingStatus(status_filter))
        except ValueError:
            pass
    if session_id:
        q = q.filter(Booking.session_id == session_id)

    total = q.count()
    page = max(1, page)
    size = 50
    total_pages = max(1, (total + size - 1) // size)
    page = min(page, total_pages)
    bookings = q.order_by(Booking.created_at.desc()).offset((page - 1) * size).limit(size).all()

    # Stats
    stats = {s.value: db.query(sqlfunc.count(Booking.id)).filter(Booking.status == s).scalar() or 0
             for s in BookingStatus}

    # Sessions for filter dropdown (only those that have bookings)
    sessions_with_bookings = (
        db.query(SessionModel)
        .join(Booking, Booking.session_id == SessionModel.id)
        .distinct()
        .order_by(SessionModel.date_start.desc())
        .limit(100)
        .all()
    )

    return templates.TemplateResponse(
        "admin/bookings.html",
        {
            "request": request,
            "user": user,
            "bookings": bookings,
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "stats": stats,
            "BookingStatus": BookingStatus,
            "AttendanceStatus": AttendanceStatus,
            "filter_status": status_filter,
            "filter_session_id": session_id,
            "sessions_with_bookings": sessions_with_bookings,
        }
    )


@router.post("/admin/bookings/{booking_id}/confirm")
async def admin_booking_confirm(
    booking_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Confirm a booking (admin, cookie auth)."""
    _admin_guard(user)
    booking = db.query(Booking).filter(Booking.id == booking_id).first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status == BookingStatus.CONFIRMED:
        raise HTTPException(status_code=400, detail="Booking already confirmed")

    session_obj = db.query(SessionModel).filter(SessionModel.id == booking.session_id).first()
    if session_obj and session_obj.capacity:
        confirmed_count = db.query(sqlfunc.count(Booking.id)).filter(
            Booking.session_id == booking.session_id,
            Booking.status == BookingStatus.CONFIRMED,
        ).scalar() or 0
        if confirmed_count >= session_obj.capacity:
            raise HTTPException(status_code=409, detail=f"Session at capacity ({session_obj.capacity})")

    booking.status = BookingStatus.CONFIRMED
    db.commit()
    return JSONResponse({"success": True, "message": "Booking confirmed"})


@router.post("/admin/bookings/{booking_id}/cancel")
async def admin_booking_cancel(
    booking_id: int,
    request: Request,
    reason: str = Form("Cancelled by admin"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Cancel a booking (admin, cookie auth)."""
    _admin_guard(user)
    booking = db.query(Booking).filter(Booking.id == booking_id).with_for_update().first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    if booking.status == BookingStatus.CANCELLED:
        raise HTTPException(status_code=400, detail="Booking already cancelled")

    booking.status = BookingStatus.CANCELLED
    booking.cancelled_at = datetime.now()
    booking.notes = reason
    db.commit()
    return JSONResponse({"success": True, "message": "Booking cancelled"})


@router.post("/admin/bookings/{booking_id}/attendance")
async def admin_booking_attendance(
    booking_id: int,
    request: Request,
    attendance_status: str = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Mark/update attendance for a booking (admin, cookie auth)."""
    _admin_guard(user)
    valid_statuses = [s.value for s in AttendanceStatus]
    if attendance_status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {valid_statuses}")

    booking = db.query(Booking).filter(Booking.id == booking_id).with_for_update().first()
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")

    if booking.attendance:
        booking.attendance.status = AttendanceStatus(attendance_status)
        booking.attendance.notes = notes or booking.attendance.notes
        booking.attendance.marked_by = user.id
    else:
        att = Attendance(
            user_id=booking.user_id,
            session_id=booking.session_id,
            booking_id=booking.id,
            status=AttendanceStatus(attendance_status),
            notes=notes or None,
            marked_by=user.id,
        )
        db.add(att)

    booking.update_attendance_status()
    db.commit()
    return JSONResponse({"success": True, "message": f"Attendance marked: {attendance_status}"})


@router.get("/admin/sessions", response_class=HTMLResponse)
async def admin_sessions_page(
    request: Request,
    session_type: str = "",
    status: str = "",
    location_filter: str = "",
    date_from: str = "",
    date_to: str = "",
    cleared: str = "",
    event_category: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Session management — hierarchical view (Location → Spec → Semester → Session)"""
    _admin_guard(user)

    # Default date_from to today unless user explicitly cleared filters
    today_str = date.today().isoformat()
    if not date_from and not cleared:
        date_from = today_str

    q = db.query(SessionModel)

    if session_type:
        try:
            q = q.filter(SessionModel.session_type == SessionType(session_type))
        except ValueError:
            pass
    if status:
        q = q.filter(SessionModel.session_status == status)
    if location_filter:
        q = q.filter(SessionModel.location.ilike(f"%{location_filter}%"))
    if date_from:
        try:
            q = q.filter(SessionModel.date_start >= datetime.strptime(date_from, "%Y-%m-%d"))
        except ValueError:
            pass
    if date_to:
        try:
            q = q.filter(SessionModel.date_start <= datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59))
        except ValueError:
            pass
    if event_category in ("TRAINING", "MATCH"):
        q = q.filter(SessionModel.event_category == event_category)

    all_sessions = q.order_by(SessionModel.date_start).all()

    # Get booking counts in bulk
    booking_counts = dict(
        db.query(Booking.session_id, sqlfunc.count(Booking.id))
        .filter(Booking.session_id.in_([s.id for s in all_sessions]))
        .group_by(Booking.session_id)
        .all()
    ) if all_sessions else {}

    for s in all_sessions:
        s.booking_count = booking_counts.get(s.id, 0)

    # Attach semester info
    semesters = {sem.id: sem for sem in db.query(Semester).all()}
    locations = db.query(Location).filter(Location.is_active == True).order_by(Location.name).all()

    now = datetime.now()

    # Group hierarchically: location_key → spec → semester_id → sessions
    from collections import defaultdict, OrderedDict

    hierarchy = {}  # location_str → {spec → {semester_id → [sessions]}}
    for s in all_sessions:
        loc_key = (s.location or "Unknown Location").strip()
        spec = s.target_specialization.value if s.target_specialization else ("Mixed" if s.mixed_specialization else "General")
        sem_id = s.semester_id
        hierarchy.setdefault(loc_key, {}).setdefault(spec, {}).setdefault(sem_id, []).append(s)

    # Stats
    upcoming = sum(1 for s in all_sessions if s.date_start > now)
    past = sum(1 for s in all_sessions if s.date_start <= now)

    return templates.TemplateResponse(
        "admin/sessions.html",
        {
            "request": request,
            "user": user,
            "all_sessions": all_sessions,
            "hierarchy": hierarchy,
            "semesters": semesters,
            "locations": locations,
            "now": now,
            "upcoming": upcoming,
            "past": past,
            "filter_session_type": session_type,
            "filter_status": status,
            "filter_location": location_filter,
            "filter_date_from": date_from,
            "filter_date_to": date_to,
            "filter_event_category": event_category,
            "SessionType": SessionType,
        }
    )


@router.get("/admin/pitches", response_class=HTMLResponse)
async def admin_pitches_page(
    request: Request,
    campus_filter: int = 0,
    location_filter: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: all pitches grouped by campus, with instructor assignment status."""
    _admin_guard(user)

    locations = db.query(Location).filter(Location.is_active == True).order_by(Location.name).all()  # noqa: E712
    all_campuses = db.query(Campus).filter(Campus.is_active == True).order_by(Campus.name).all()  # noqa: E712

    q = db.query(Pitch)
    if campus_filter:
        q = q.filter(Pitch.campus_id == campus_filter)
    elif location_filter:
        campus_ids = [c.id for c in db.query(Campus).filter(Campus.location_id == location_filter).all()]
        q = q.filter(Pitch.campus_id.in_(campus_ids)) if campus_ids else q.filter(False)
    pitches = q.order_by(Pitch.campus_id, Pitch.pitch_number).all()

    # Batch-load active/pending assignments per pitch
    pitch_ids = [p.id for p in pitches]
    active_assignments: dict = defaultdict(list)
    if pitch_ids:
        for a in db.query(PitchInstructorAssignment).filter(
            PitchInstructorAssignment.pitch_id.in_(pitch_ids),
            PitchInstructorAssignment.status.in_([
                PitchAssignmentStatus.ACTIVE.value,
                PitchAssignmentStatus.PENDING.value,
            ]),
        ).all():
            active_assignments[a.pitch_id].append(a)

    campus_map = {c.id: c for c in all_campuses}
    location_map = {loc.id: loc for loc in locations}

    instructors = db.query(User).filter(
        User.role == UserRole.INSTRUCTOR,
        User.is_active == True,  # noqa: E712
    ).order_by(User.name).all()

    return templates.TemplateResponse(
        "admin/pitches.html",
        {
            "request": request,
            "user": user,
            "pitches": pitches,
            "active_assignments": active_assignments,
            "campus_map": campus_map,
            "location_map": location_map,
            "locations": locations,
            "all_campuses": all_campuses,
            "instructors": instructors,
            "campus_filter": campus_filter,
            "location_filter": location_filter,
        },
    )


@router.post("/admin/pitches/create")
async def admin_create_pitch(
    request: Request,
    campus_id: int = Form(...),
    pitch_number: int = Form(...),
    name: str = Form(...),
    capacity: int = Form(default=2),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: create a new pitch under a campus."""
    _admin_guard(user)
    campus = db.query(Campus).filter(Campus.id == campus_id).first()
    if not campus:
        return RedirectResponse(url="/admin/pitches?error=Campus+not+found", status_code=303)

    existing = db.query(Pitch).filter(
        Pitch.campus_id == campus_id,
        Pitch.pitch_number == pitch_number,
    ).first()
    if existing:
        return RedirectResponse(
            url=f"/admin/pitches?error=Pitch+{pitch_number}+already+exists+on+this+campus",
            status_code=303,
        )

    pitch = Pitch(
        campus_id=campus_id,
        pitch_number=pitch_number,
        name=name.strip(),
        capacity=max(1, capacity),
        is_active=True,
    )
    db.add(pitch)
    db.commit()
    return RedirectResponse(
        url=f"/admin/pitches?campus_filter={campus_id}&msg=Pitch+created",
        status_code=303,
    )


@router.post("/admin/pitches/{pitch_id}/toggle")
async def admin_toggle_pitch(
    pitch_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: activate or deactivate a pitch."""
    _admin_guard(user)
    pitch = db.query(Pitch).filter(Pitch.id == pitch_id).first()
    if not pitch:
        raise HTTPException(status_code=404, detail="Pitch not found")
    pitch.is_active = not pitch.is_active
    db.commit()
    return RedirectResponse(url="/admin/pitches", status_code=303)


@router.post("/admin/pitches/{pitch_id}/assign-instructor")
async def admin_assign_instructor_to_pitch(
    request: Request,
    pitch_id: int,
    instructor_id: int = Form(...),
    semester_id: int = Form(default=0),
    is_master: bool = Form(default=False),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin: directly assign an instructor to a pitch (DIRECT mode)."""
    _admin_guard(user)
    try:
        assign_instructor_to_pitch_direct(
            db=db,
            pitch_id=pitch_id,
            instructor_id=instructor_id,
            assigned_by_id=user.id,
            semester_id=semester_id if semester_id else None,
            is_master=is_master,
        )
        db.commit()
        return RedirectResponse(url="/admin/pitches?msg=Instructor+assigned", status_code=303)
    except HTTPException as e:
        return RedirectResponse(url=f"/admin/pitches?error={e.detail}", status_code=303)
