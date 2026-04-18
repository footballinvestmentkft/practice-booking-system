"""Admin analytics, hub pages, motivation assessment, and system events routes."""
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload
from datetime import datetime, timezone, date, timedelta
from collections import defaultdict
import logging

from sqlalchemy import func as sqlfunc, or_

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.user import User, UserRole
from ....models.semester import Semester, SemesterStatus, SemesterCategory
from ....models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ....models.license import UserLicense
from ....models.session import Session as SessionModel, EventCategory
from ....models.booking import Booking
from ....models.location import Location
from ....models.campus import Campus
from ....models.system_event import SystemEvent, SystemEventLevel
from ....models.game_preset import GamePreset
from ....models.football_skill_assessment import FootballSkillAssessment
from ....models.notification import Notification, NotificationType
from .finance import _build_financial_kpi

from . import templates, _admin_guard

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/admin/analytics", response_class=HTMLResponse)
async def admin_analytics_page(
    request: Request,
    location_id: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Analytics and reports page"""
    _admin_guard(user)

    # Platform stats
    total_users = db.query(User).count()
    total_students = db.query(User).filter(User.role == UserRole.STUDENT).count()
    total_instructors = db.query(User).filter(User.role == UserRole.INSTRUCTOR).count()
    total_sessions = db.query(SessionModel).count()
    total_bookings = db.query(Booking).count()

    stats = {
        "total_users": total_users,
        "total_students": total_students,
        "total_instructors": total_instructors,
        "total_sessions": total_sessions,
        "total_bookings": total_bookings,
    }

    # Financial snapshot (all 8 metrics)
    fin = _build_financial_kpi(db)

    # Locations for filter selector
    all_locations = db.query(Location).filter(Location.is_active == True).order_by(Location.city).all()
    selected_location = db.query(Location).filter(Location.id == location_id).first() if location_id else None

    # Semesters grouped by specialization — eager-load location + campus for table columns
    all_semesters = (
        db.query(Semester)
        .filter(Semester.status != SemesterStatus.CANCELLED)
        .options(joinedload(Semester.location), joinedload(Semester.campus))
        .order_by(Semester.start_date.desc())
        .all()
    )
    spec_semesters = defaultdict(list)
    for sem in all_semesters:
        spec = sem.specialization_type if sem.specialization_type else "Unknown"
        spec_semesters[spec].append(sem)

    # Campuses + session counts for selected location
    location_campuses = []
    if selected_location:
        campuses = db.query(Campus).filter(
            Campus.location_id == selected_location.id,
            Campus.is_active == True
        ).all()
        now = datetime.now(timezone.utc)
        # Build campus session counts in two queries (no per-campus loop)
        campus_names = [c.name for c in campuses]
        if campus_names:
            from sqlalchemy import or_
            all_sessions = db.query(SessionModel.location, SessionModel.date_start).filter(
                or_(*[SessionModel.location.ilike(f"%{n}%") for n in campus_names])
            ).all()
            for campus in campuses:
                matching = [s for s in all_sessions if campus.name.lower() in (s.location or "").lower()]
                upcoming = sum(
                    1 for s in matching
                    if s.date_start and s.date_start.replace(
                        tzinfo=timezone.utc if s.date_start.tzinfo is None else s.date_start.tzinfo
                    ) > now
                )
                location_campuses.append({
                    "campus": campus,
                    "total": len(matching),
                    "upcoming": upcoming,
                    "past": len(matching) - upcoming,
                })

    # ── FÁZIS 5: Skill tier distribution ─────────────────────────────────────────
    all_active_assessments = (
        db.query(FootballSkillAssessment)
        .filter(FootballSkillAssessment.status != "ARCHIVED")
        .all()
    )
    from collections import defaultdict as _dd
    _by_skill: dict = _dd(list)
    for _a in all_active_assessments:
        _by_skill[_a.skill_name].append(_a.percentage)
    skill_dist = [
        {
            "skill": sn,
            "beginner": sum(1 for p in pcts if p < 60),
            "intermediate": sum(1 for p in pcts if 60 <= p < 75),
            "advanced": sum(1 for p in pcts if 75 <= p < 90),
            "expert": sum(1 for p in pcts if p >= 90),
        }
        for sn, pcts in sorted(_by_skill.items())
    ]
    tier_milestone_count = db.query(Notification).filter(
        Notification.type == NotificationType.SKILL_TIER_REACHED
    ).count()

    return templates.TemplateResponse(
        "admin/analytics.html",
        {
            "request": request,
            "user": user,
            "stats": stats,
            "fin": fin,
            "all_locations": all_locations,
            "selected_location": selected_location,
            "selected_location_id": location_id,
            "spec_semesters": dict(spec_semesters),
            "location_campuses": location_campuses,
            "skill_dist": skill_dist,
            "tier_milestone_count": tier_milestone_count,
        }
    )


@router.get("/admin/students/{student_id}/motivation/{specialization}", response_class=HTMLResponse)
async def motivation_assessment_page(
    request: Request,
    student_id: int,
    specialization: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin/Instructor-only: Motivation assessment page for a student's specialization"""
    if user.role not in [UserRole.ADMIN, UserRole.INSTRUCTOR]:
        raise HTTPException(status_code=403, detail="Admin or Instructor access required")

    # Get student
    student = db.query(User).filter(User.id == student_id, User.role == UserRole.STUDENT).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    # Get student's license for this specialization
    license = db.query(UserLicense).filter(
        UserLicense.user_id == student_id,
        UserLicense.specialization_type == specialization
    ).first()

    if not license:
        raise HTTPException(status_code=404, detail=f"Student does not have {specialization} license")

    # Format specialization name for display
    specialization_display = specialization.replace('_', ' ').title()

    # Check if there are existing scores
    existing_scores = license.motivation_scores is not None

    return templates.TemplateResponse(
        "admin/motivation_assessment.html",
        {
            "request": request,
            "user": user,
            "student": student,
            "license": license,
            "specialization": specialization,
            "specialization_display": specialization_display,
            "existing_scores": existing_scores
        }
    )


@router.post("/admin/students/{student_id}/motivation/{specialization}")
async def motivation_assessment_submit(
    request: Request,
    student_id: int,
    specialization: str,
    goal_clarity: int = Form(...),
    commitment_level: int = Form(...),
    engagement: int = Form(...),
    progress_mindset: int = Form(...),
    initiative: int = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin/Instructor-only: Save motivation assessment"""
    if user.role not in [UserRole.ADMIN, UserRole.INSTRUCTOR]:
        raise HTTPException(status_code=403, detail="Admin or Instructor access required")

    # Validate scores (1-5)
    scores = [goal_clarity, commitment_level, engagement, progress_mindset, initiative]
    for score in scores:
        if score < 1 or score > 5:
            raise HTTPException(status_code=400, detail="Scores must be between 1 and 5")

    # Get student's license
    license = db.query(UserLicense).filter(
        UserLicense.user_id == student_id,
        UserLicense.specialization_type == specialization
    ).first()

    if not license:
        raise HTTPException(status_code=404, detail=f"Student does not have {specialization} license")

    # Create motivation scores JSON
    motivation_data = {
        "goal_clarity": goal_clarity,
        "commitment_level": commitment_level,
        "engagement": engagement,
        "progress_mindset": progress_mindset,
        "initiative": initiative,
        "notes": notes,
        "assessed_at": datetime.now(timezone.utc).isoformat(),
        "assessed_by_id": user.id,
        "assessed_by_name": user.name
    }

    # Calculate average
    average_score = sum(scores) / len(scores)

    # Update license
    license.motivation_scores = motivation_data
    license.average_motivation_score = average_score
    license.motivation_last_assessed_at = datetime.now(timezone.utc)
    license.motivation_assessed_by = user.id

    db.commit()

    logger.info("admin_motivation_assessed", extra={"assessor": user.email, "student_id": student_id, "spec": specialization, "avg_score": round(average_score, 1)})

    # Redirect back to user management page
    return RedirectResponse(url="/admin/users", status_code=303)


@router.get("/admin/programs", response_class=HTMLResponse)
async def admin_programs_hub_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Programs operational hub (Semesters + Enrollments)"""
    _admin_guard(user)

    active_semesters_count = db.query(Semester).filter(
        Semester.status == SemesterStatus.ONGOING
    ).count()
    pending_enrollments_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.request_status == EnrollmentStatus.PENDING
    ).count()
    today = date.today()
    upcoming_semesters = (
        db.query(Semester)
        .filter(Semester.start_date >= today, Semester.status != SemesterStatus.CANCELLED)
        .options(joinedload(Semester.location))
        .order_by(Semester.start_date.asc())
        .limit(5)
        .all()
    )
    total_semesters = db.query(Semester).filter(Semester.status != SemesterStatus.CANCELLED).count()
    total_enrollments = db.query(SemesterEnrollment).count()

    return templates.TemplateResponse(
        "admin/programs_hub.html",
        {
            "request": request,
            "user": user,
            "active_semesters_count": active_semesters_count,
            "pending_enrollments_count": pending_enrollments_count,
            "upcoming_semesters": upcoming_semesters,
            "total_semesters": total_semesters,
            "total_enrollments": total_enrollments,
        }
    )


@router.get("/admin/config", response_class=HTMLResponse)
async def admin_config_hub_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Game Config hub (Game Presets only; Locations moved to top-level nav)"""
    _admin_guard(user)

    game_presets_count = db.query(GamePreset).count()

    return templates.TemplateResponse(
        "admin/config_hub.html",
        {
            "request": request,
            "user": user,
            "game_presets_count": game_presets_count,
        }
    )


@router.get("/admin/events", response_class=HTMLResponse)
async def admin_events_hub_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Event Management hub — tournaments, camps, training sessions, match sessions."""
    _admin_guard(user)

    today = date.today()
    two_weeks = today + timedelta(days=14)

    tournament_count = db.query(sqlfunc.count(Semester.id)).filter(
        or_(
            Semester.code.like("TOURN-%"),
            Semester.code.like("OPS-%"),
            Semester.semester_category == SemesterCategory.TOURNAMENT,
        ),
        Semester.status != SemesterStatus.CANCELLED,
    ).scalar() or 0

    camp_count = db.query(sqlfunc.count(Semester.id)).filter(
        Semester.semester_category == SemesterCategory.CAMP,
        Semester.status != SemesterStatus.CANCELLED,
    ).scalar() or 0

    training_count = db.query(sqlfunc.count(SessionModel.id)).filter(
        SessionModel.event_category == EventCategory.TRAINING,
        sqlfunc.date(SessionModel.date_start) >= today,
        sqlfunc.date(SessionModel.date_start) <= two_weeks,
        SessionModel.session_status != "cancelled",
    ).scalar() or 0

    match_count = db.query(sqlfunc.count(SessionModel.id)).filter(
        SessionModel.event_category == EventCategory.MATCH,
        sqlfunc.date(SessionModel.date_start) >= today,
        sqlfunc.date(SessionModel.date_start) <= two_weeks,
        SessionModel.session_status != "cancelled",
    ).scalar() or 0

    upcoming_events = db.query(Semester).filter(
        or_(
            Semester.semester_category.in_([SemesterCategory.TOURNAMENT, SemesterCategory.CAMP]),
            Semester.code.like("TOURN-%"),
            Semester.code.like("OPS-%"),
        ),
        Semester.start_date >= today,
        Semester.status != SemesterStatus.CANCELLED,
    ).order_by(Semester.start_date).limit(5).all()

    # Batch-load locations and campuses for upcoming events table
    ev_loc_ids = list({e.location_id for e in upcoming_events if e.location_id})
    ev_cam_ids = list({e.campus_id for e in upcoming_events if e.campus_id})
    ev_loc_map = {l.id: l for l in db.query(Location).filter(Location.id.in_(ev_loc_ids)).all()} if ev_loc_ids else {}
    ev_cam_map = {c.id: c for c in db.query(Campus).filter(Campus.id.in_(ev_cam_ids)).all()} if ev_cam_ids else {}

    # Location cards — aggregate semester + session counts per location
    all_locations = (
        db.query(Location).filter(Location.is_active == True).order_by(Location.city).all()
    )
    all_loc_ids = [l.id for l in all_locations]
    sem_by_loc = (
        dict(
            db.query(Semester.location_id, sqlfunc.count(Semester.id))
            .filter(
                Semester.location_id.in_(all_loc_ids),
                Semester.status != SemesterStatus.CANCELLED,
            )
            .group_by(Semester.location_id)
            .all()
        )
        if all_loc_ids
        else {}
    )
    # Session has campus_id (no location_id) — count via Campus join
    if all_loc_ids:
        all_campuses = (
            db.query(Campus).filter(Campus.location_id.in_(all_loc_ids)).all()
        )
        campus_to_loc = {c.id: c.location_id for c in all_campuses}
        all_campus_ids = [c.id for c in all_campuses]
        sess_by_campus = (
            dict(
                db.query(SessionModel.campus_id, sqlfunc.count(SessionModel.id))
                .filter(
                    SessionModel.campus_id.in_(all_campus_ids),
                    SessionModel.session_status != "cancelled",
                )
                .group_by(SessionModel.campus_id)
                .all()
            )
            if all_campus_ids
            else {}
        )
        sess_by_loc: dict = {}
        for cam_id, cnt in sess_by_campus.items():
            loc_id = campus_to_loc.get(cam_id)
            if loc_id:
                sess_by_loc[loc_id] = sess_by_loc.get(loc_id, 0) + cnt
    else:
        sess_by_loc = {}

    return templates.TemplateResponse(
        "admin/events_hub.html",
        {
            "request": request,
            "user": user,
            "tournament_count": tournament_count,
            "camp_count": camp_count,
            "training_count": training_count,
            "match_count": match_count,
            "upcoming_events": upcoming_events,
            "ev_loc_map": ev_loc_map,
            "ev_cam_map": ev_cam_map,
            "SemesterCategory": SemesterCategory,
            "all_locations": all_locations,
            "sem_by_loc": sem_by_loc,
            "sess_by_loc": sess_by_loc,
        }
    )


@router.get("/admin/system-events", response_class=HTMLResponse)
async def admin_system_events_page(
    request: Request,
    level: str = "",
    resolved: str = "open",
    page: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: System Events log with filters"""
    _admin_guard(user)
    PAGE_SIZE = 50
    q = db.query(SystemEvent)
    if level and level != "All":
        q = q.filter(SystemEvent.level == level)
    if resolved == "open":
        q = q.filter(SystemEvent.resolved == False)
    elif resolved == "resolved":
        q = q.filter(SystemEvent.resolved == True)
    total = q.count()
    events = q.order_by(SystemEvent.created_at.desc()).offset(page * PAGE_SIZE).limit(PAGE_SIZE).all()
    total_pages = max(1, -(-total // PAGE_SIZE))
    return templates.TemplateResponse(
        "admin/system_events.html",
        {
            "request": request, "user": user,
            "events": events, "total": total,
            "page": page, "total_pages": total_pages, "page_size": PAGE_SIZE,
            "filter_level": level, "filter_resolved": resolved,
        }
    )


@router.post("/admin/system-events/{event_id}/resolve")
async def admin_resolve_system_event(
    event_id: int,
    page: int = Form(0),
    level: str = Form(""),
    resolved: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    ev = db.query(SystemEvent).filter(SystemEvent.id == event_id).first()
    if ev:
        ev.resolved = True
        db.commit()
    return RedirectResponse(url=f"/admin/system-events?level={level}&resolved={resolved}&page={page}", status_code=303)


@router.post("/admin/system-events/{event_id}/unresolve")
async def admin_unresolve_system_event(
    event_id: int,
    page: int = Form(0),
    level: str = Form(""),
    resolved: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    ev = db.query(SystemEvent).filter(SystemEvent.id == event_id).first()
    if ev:
        ev.resolved = False
        db.commit()
    return RedirectResponse(url=f"/admin/system-events?level={level}&resolved={resolved}&page={page}", status_code=303)


@router.post("/admin/system-events/purge")
async def admin_purge_system_events(
    retention_days: int = Form(90),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    _admin_guard(user)
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = db.query(SystemEvent).filter(
        SystemEvent.resolved == True,
        SystemEvent.created_at < cutoff
    ).delete(synchronize_session=False)
    db.commit()
    logger.info("admin_events_purged", extra={"admin": user.email, "deleted": deleted, "retention_days": retention_days})
    return RedirectResponse(url="/admin/system-events", status_code=303)


@router.get("/admin/camps", response_class=HTMLResponse)
async def admin_camps_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
    status_filter: str = "active",
    age_group_filter: str = "",
    location_filter: str = "",
    name_search: str = "",
):
    """Admin-only: Camp management — list of CAMP-category semesters."""
    _admin_guard(user)

    query = db.query(Semester).filter(Semester.semester_category == SemesterCategory.CAMP)
    if status_filter == "active":
        query = query.filter(Semester.status != SemesterStatus.CANCELLED)
    elif status_filter == "cancelled":
        query = query.filter(Semester.status == SemesterStatus.CANCELLED)
    if age_group_filter:
        query = query.filter(Semester.age_group == age_group_filter)
    if name_search:
        query = query.filter(Semester.name.ilike(f"%{name_search}%"))
    camps = query.order_by(Semester.start_date.desc()).all()

    loc_ids = list({c.location_id for c in camps if c.location_id})
    campus_ids_set = list({c.campus_id for c in camps if c.campus_id})
    location_map = {l.id: l for l in db.query(Location).filter(Location.id.in_(loc_ids)).all()} if loc_ids else {}
    campus_map = {c.id: c for c in db.query(Campus).filter(Campus.id.in_(campus_ids_set)).all()} if campus_ids_set else {}

    all_locations = db.query(Location).filter(Location.is_active == True).order_by(Location.city).all()

    total = len(camps)
    ongoing = sum(1 for c in camps if c.status == SemesterStatus.ONGOING)
    upcoming = sum(1 for c in camps if c.start_date and c.start_date >= date.today() and c.status not in [SemesterStatus.CANCELLED, SemesterStatus.COMPLETED])
    completed = sum(1 for c in camps if c.status == SemesterStatus.COMPLETED)

    return templates.TemplateResponse(
        "admin/camps.html",
        {
            "request": request,
            "user": user,
            "camps": camps,
            "location_map": location_map,
            "campus_map": campus_map,
            "all_locations": all_locations,
            "status_filter": status_filter,
            "age_group_filter": age_group_filter,
            "name_search": name_search,
            "total": total,
            "ongoing": ongoing,
            "upcoming": upcoming,
            "completed": completed,
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
            "SemesterStatus": SemesterStatus,
        }
    )


@router.post("/admin/camps", response_class=HTMLResponse)
async def admin_create_camp(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
    name: str = Form(...),
    code: str = Form(""),
    start_date: str = Form(...),
    end_date: str = Form(...),
    age_group: str = Form(""),
    location_id: str = Form(""),
    campus_id: str = Form(""),
    enrollment_cost: str = Form("0"),
):
    """Admin-only: Create a new Camp semester."""
    _admin_guard(user)
    import uuid as _uuid

    camp_code = code.strip() if code.strip() else f"CAMP-{_uuid.uuid4().hex[:6].upper()}"
    if not camp_code.startswith("CAMP-"):
        camp_code = f"CAMP-{camp_code}"

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return RedirectResponse(f"/admin/camps?error=Invalid+date+format", status_code=303)

    semester = Semester(
        name=name.strip(),
        code=camp_code,
        start_date=start,
        end_date=end,
        semester_category=SemesterCategory.CAMP,
        status=SemesterStatus.DRAFT,
        age_group=age_group.strip() or None,
        location_id=int(location_id) if location_id.strip() else None,
        campus_id=int(campus_id) if campus_id.strip() else None,
        enrollment_cost=int(enrollment_cost) if enrollment_cost.strip().isdigit() else 0,
        specialization_type="LFA_FOOTBALL_PLAYER",
    )
    db.add(semester)
    db.commit()
    if semester.location_id:
        return RedirectResponse(
            f"/admin/events/locations/{semester.location_id}?flash=Camp+created",
            status_code=303,
        )
    return RedirectResponse(f"/admin/camps?flash=Camp+created", status_code=303)


@router.get("/admin/camps/{camp_id}/edit", response_class=HTMLResponse)
async def admin_camp_edit_page(
    camp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Admin-only: Edit a Camp semester."""
    _admin_guard(user)
    camp = db.query(Semester).filter(
        Semester.id == camp_id,
        Semester.semester_category == SemesterCategory.CAMP,
    ).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Camp not found")

    all_locations = db.query(Location).filter(Location.is_active == True).order_by(Location.city).all()
    campuses_for_loc = (
        db.query(Campus).filter(Campus.location_id == camp.location_id).all()
        if camp.location_id else []
    )
    return templates.TemplateResponse(
        "admin/camp_edit.html",
        {
            "request": request,
            "user": user,
            "camp": camp,
            "all_locations": all_locations,
            "campuses_for_loc": campuses_for_loc,
            "SemesterStatus": SemesterStatus,
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/admin/camps/{camp_id}/edit", response_class=HTMLResponse)
async def admin_update_camp(
    camp_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
    name: str = Form(...),
    code: str = Form(""),
    start_date: str = Form(...),
    end_date: str = Form(...),
    age_group: str = Form(""),
    location_id: str = Form(""),
    campus_id: str = Form(""),
    enrollment_cost: str = Form(""),
    status: str = Form(""),
):
    """Admin-only: Update a Camp semester."""
    _admin_guard(user)
    camp = db.query(Semester).filter(
        Semester.id == camp_id,
        Semester.semester_category == SemesterCategory.CAMP,
    ).first()
    if not camp:
        raise HTTPException(status_code=404, detail="Camp not found")

    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError:
        return RedirectResponse(
            f"/admin/camps/{camp_id}/edit?error=Invalid+date+format", status_code=303
        )

    camp.name = name.strip()
    if code.strip():
        camp.code = code.strip()
    camp.start_date = start
    camp.end_date = end
    camp.age_group = age_group.strip() or None
    camp.location_id = int(location_id) if location_id.strip() else None
    camp.campus_id = int(campus_id) if campus_id.strip() else None
    if enrollment_cost.strip().isdigit():
        camp.enrollment_cost = int(enrollment_cost)
    if status.strip():
        try:
            camp.status = SemesterStatus(status)
        except ValueError:
            pass
    db.commit()

    loc_id = camp.location_id
    if loc_id:
        return RedirectResponse(
            f"/admin/events/locations/{loc_id}?flash=Camp+updated", status_code=303
        )
    return RedirectResponse(f"/admin/camps/{camp_id}/edit?flash=Camp+updated", status_code=303)
