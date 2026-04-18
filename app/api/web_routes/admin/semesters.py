"""Admin semester and schedule management routes."""
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlalchemy.orm import Session
from datetime import datetime, timezone, date
import logging

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.user import User, UserRole
from ....models.semester import Semester, SemesterStatus, SemesterCategory
from ....models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ....models.specialization import SpecializationType
from ....models.location import Location
from ....models.session import Session as SessionModel
from ....models.attendance import Attendance
from ....models.audit_log import AuditLog
from ....models.semester_schedule_config import SemesterScheduleConfig
from ....models.campus import Campus
from ....services.scheduling.mini_season_generator import MiniSeasonSessionGenerator, PitchConflictError
from ....services.location_validation_service import LocationValidationService

from . import templates, _admin_guard

logger = logging.getLogger(__name__)

router = APIRouter()

_SCHEDULING_CATEGORIES = {SemesterCategory.MINI_SEASON, SemesterCategory.ACADEMY_SEASON}


@router.get("/admin/semesters", response_class=HTMLResponse)
async def admin_semesters_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Semester management page"""
    _admin_guard(user)

    # Get all semesters
    semesters = db.query(Semester).order_by(Semester.start_date.desc()).all()

    return templates.TemplateResponse(
        "admin/semesters.html",
        {
            "request": request,
            "user": user,
            "semesters": semesters
        }
    )


@router.get("/admin/semesters/new", response_class=HTMLResponse)
async def admin_new_semester_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Create semester form"""
    _admin_guard(user)

    instructors = db.query(User).filter(User.role == UserRole.INSTRUCTOR, User.is_active == True).all()
    locations = db.query(Location).filter(Location.is_active == True).order_by(Location.city).all()
    return templates.TemplateResponse(
        "admin/semester_new.html",
        {
            "request": request, "user": user,
            "instructors": instructors,
            "locations": locations,
            "today": date.today().isoformat()
        }
    )


@router.post("/admin/semesters/new")
async def admin_new_semester_submit(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    enrollment_cost: int = Form(500),
    specialization_type: str = Form(""),
    master_instructor_id: str = Form(""),
    location_id: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Create new semester"""
    _admin_guard(user)

    instructors = db.query(User).filter(User.role == UserRole.INSTRUCTOR, User.is_active == True).all()
    locations = db.query(Location).filter(Location.is_active == True).order_by(Location.city).all()

    def form_error(msg: str):
        return templates.TemplateResponse(
            "admin/semester_new.html",
            {
                "request": request, "user": user,
                "error": msg, "instructors": instructors,
                "locations": locations,
                "today": date.today().isoformat(),
                "form": {
                    "code": code, "name": name, "start_date": start_date,
                    "end_date": end_date, "enrollment_cost": enrollment_cost,
                    "specialization_type": specialization_type,
                    "location_id": location_id,
                }
            }
        )

    # Validate dates
    try:
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return form_error("Invalid date format.")

    if ed <= sd:
        return form_error("End date must be after start date.")

    # Validate CENTER vs PARTNER rule
    _ACADEMY_TYPES = {
        SpecializationType.LFA_PLAYER_PRE_ACADEMY,
        SpecializationType.LFA_PLAYER_YOUTH_ACADEMY,
        SpecializationType.LFA_PLAYER_AMATEUR_ACADEMY,
        SpecializationType.LFA_PLAYER_PRO_ACADEMY,
    }
    parsed_location_id = int(location_id) if location_id.strip() else None
    spec_str = specialization_type.strip()
    try:
        spec_enum = SpecializationType(spec_str) if spec_str else None
    except ValueError:
        spec_enum = None

    # Academy Season requires a location (so the CENTER rule can be evaluated)
    if spec_enum in _ACADEMY_TYPES and not parsed_location_id:
        return form_error(
            "Academy Season típus létrehozásához kötelező helyszínt kiválasztani, "
            "hogy a CENTER / PARTNER szabályt ellenőrizni lehessen."
        )

    # If location is selected, check the CENTER / PARTNER capability rule
    if parsed_location_id and spec_enum:
        result = LocationValidationService.can_create_semester_at_location(
            parsed_location_id, spec_enum, db
        )
        if not result["allowed"]:
            return form_error(result["reason"])

    # Check code uniqueness
    existing = db.query(Semester).filter(Semester.code == code.strip()).first()
    if existing:
        return form_error(f"Semester code '{code}' already exists.")

    instructor_id = int(master_instructor_id) if master_instructor_id.strip() else None

    new_sem = Semester(
        code=code.strip(),
        name=name.strip(),
        start_date=sd,
        end_date=ed,
        enrollment_cost=enrollment_cost,
        specialization_type=specialization_type.strip() or None,
        master_instructor_id=instructor_id,
    )
    db.add(new_sem)
    db.commit()
    logger.info("admin_semester_created", extra={"admin": user.email, "code": new_sem.code})
    return RedirectResponse(url="/admin/semesters", status_code=303)


@router.post("/admin/semesters/{semester_id}/delete")
async def admin_delete_semester(
    semester_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Admin-only: Cancel a semester (status=CANCELLED) or hard-delete if no enrollments."""
    _admin_guard(user)

    sem = db.query(Semester).filter(Semester.id == semester_id).first()
    if not sem:
        raise HTTPException(status_code=404, detail="Semester not found")

    # Check if semester has active enrollments
    active_count = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.semester_id == semester_id,
        SemesterEnrollment.request_status == EnrollmentStatus.APPROVED
    ).count()

    if active_count > 0:
        # Don't delete — cancel the semester
        sem.status = SemesterStatus.CANCELLED
        db.commit()
        logger.info("admin_semester_cancelled", extra={"admin": user.email, "code": sem.code, "active_enrollments": active_count})
    else:
        db.delete(sem)
        db.commit()
        logger.info("admin_semester_deleted", extra={"admin": user.email, "code": sem.code})

    return RedirectResponse(url="/admin/semesters", status_code=303)


@router.get("/admin/semesters/{semester_id}/edit", response_class=HTMLResponse)
async def admin_semester_edit_dispatch(
    semester_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Dispatch: redirect to the appropriate edit page based on semester category."""
    _admin_guard(user)
    sem = db.query(Semester).filter(Semester.id == semester_id).first()
    if not sem:
        raise HTTPException(status_code=404, detail="Semester not found")
    is_tournament = (
        sem.semester_category == SemesterCategory.TOURNAMENT
        or (sem.code or "").startswith("TOURN-")
        or (sem.code or "").startswith("OPS-")
    )
    if is_tournament:
        return RedirectResponse(f"/admin/tournaments/{semester_id}/edit", status_code=303)
    if sem.semester_category == SemesterCategory.CAMP:
        return RedirectResponse(f"/admin/camps/{semester_id}/edit", status_code=303)
    return RedirectResponse(f"/admin/semesters", status_code=303)


@router.get("/admin/semesters/{semester_id}/schedule", response_class=HTMLResponse)
async def semester_schedule_view(
    semester_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    _admin_guard(user)

    semester = db.query(Semester).filter(Semester.id == semester_id).first()
    if not semester or semester.semester_category not in _SCHEDULING_CATEGORIES:
        raise HTTPException(status_code=404, detail="Semester not found or not a scheduling semester.")

    config = semester.schedule_config_obj
    sessions = (
        db.query(SessionModel)
        .filter(
            SessionModel.semester_id == semester_id,
            SessionModel.auto_generated == True,
        )
        .order_by(SessionModel.date_start.asc())
        .all()
    )
    attended_count = (
        db.query(Attendance)
        .join(SessionModel, Attendance.session_id == SessionModel.id)
        .filter(SessionModel.semester_id == semester_id)
        .count()
    )
    location_campuses = []
    if semester.location_id:
        location_campuses = (
            db.query(Campus)
            .filter(Campus.location_id == semester.location_id, Campus.is_active == True)
            .all()
        )

    return templates.TemplateResponse(
        "admin/semester_schedule.html",
        {
            "request": request,
            "user": user,
            "semester": semester,
            "config": config,
            "sessions": sessions,
            "session_count": len(sessions),
            "can_generate": config is None or not config.sessions_generated,
            "can_delete": (
                config is not None
                and config.sessions_generated
                and attended_count == 0
            ),
            "location_campuses": location_campuses,
            "flash": request.query_params.get("flash"),
            "flash_type": request.query_params.get("flash_type", "info"),
        },
    )


@router.post("/admin/semesters/{semester_id}/schedule/generate", response_class=HTMLResponse)
async def semester_schedule_generate(
    semester_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    _admin_guard(user)

    semester = db.query(Semester).filter(Semester.id == semester_id).first()
    if not semester or semester.semester_category not in _SCHEDULING_CATEGORIES:
        raise HTTPException(status_code=404, detail="Semester not found or not a scheduling semester.")

    form = await request.form()
    try:
        day_of_week = int(form.get("day_of_week", 0))
        start_time_str = form.get("start_time", "17:00")
        parts = start_time_str.split(":")
        from datetime import time as dt_time
        start_time = dt_time(int(parts[0]), int(parts[1]))
        duration_minutes = int(form.get("duration_minutes", 90))
        sessions_per_week = int(form.get("sessions_per_week", 1))
        campus_id = int(form["campus_id"]) if form.get("campus_id") else None
        pitch_id = int(form["pitch_id"]) if form.get("pitch_id") else None
        skip_conflicts = form.get("skip_conflicts") in ("on", "true", "1", True)
    except (ValueError, KeyError) as exc:
        redirect_url = (
            f"/admin/semesters/{semester_id}/schedule"
            f"?flash=Invalid+form+data&flash_type=error"
        )
        return RedirectResponse(url=redirect_url, status_code=303)

    # Upsert SemesterScheduleConfig
    config = semester.schedule_config_obj
    if config is None:
        config = SemesterScheduleConfig(semester_id=semester_id)
        db.add(config)
    config.day_of_week = day_of_week
    config.start_time = start_time
    config.duration_minutes = duration_minutes
    config.sessions_per_week = sessions_per_week
    config.campus_id = campus_id
    config.pitch_id = pitch_id
    config.sessions_generated = False
    db.flush()

    generator = MiniSeasonSessionGenerator(db)
    try:
        result = generator.generate(semester, config, skip_conflicts=skip_conflicts)
    except PitchConflictError as exc:
        db.rollback()
        redirect_url = (
            f"/admin/semesters/{semester_id}/schedule"
            f"?flash=Pitch+conflict+on+{exc.detail.date}&flash_type=error"
        )
        return RedirectResponse(url=redirect_url, status_code=303)

    db.commit()
    msg = (
        f"{result.sessions_created}+sessions+generated"
        + (f",+{result.sessions_skipped}+skipped" if result.sessions_skipped else "")
    )
    redirect_url = (
        f"/admin/semesters/{semester_id}/schedule"
        f"?flash={msg}&flash_type=success"
    )
    return RedirectResponse(url=redirect_url, status_code=303)


@router.post("/admin/semesters/{semester_id}/schedule/delete-sessions", response_class=HTMLResponse)
async def semester_schedule_delete_sessions(
    semester_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    _admin_guard(user)

    semester = db.query(Semester).filter(Semester.id == semester_id).first()
    if not semester or semester.semester_category not in _SCHEDULING_CATEGORIES:
        raise HTTPException(status_code=404, detail="Semester not found or not a scheduling semester.")

    generator = MiniSeasonSessionGenerator(db)
    try:
        deleted = generator.delete_generated_sessions(semester_id)
    except HTTPException as exc:
        redirect_url = (
            f"/admin/semesters/{semester_id}/schedule"
            f"?flash={exc.detail}&flash_type=error"
        )
        return RedirectResponse(url=redirect_url, status_code=303)

    db.commit()
    redirect_url = (
        f"/admin/semesters/{semester_id}/schedule"
        f"?flash={deleted}+sessions+deleted&flash_type=success"
    )
    return RedirectResponse(url=redirect_url, status_code=303)


@router.patch("/admin/semesters/{semester_id}/sessions/{session_id}/instructor")
async def admin_patch_session_instructor(
    semester_id: int,
    session_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Override the instructor on a single auto-generated session.

    Body: {"instructor_id": <int>}   — set to specific instructor
          {"instructor_id": null}    — clear override (reverts to semester default)
    """
    _admin_guard(user)
    body = await request.json()
    instructor_id = body.get("instructor_id")

    # Validate instructor if provided (None = clear override)
    if instructor_id is not None:
        instructor = db.query(User).filter(User.id == instructor_id).first()
        if not instructor:
            return JSONResponse({"error": "Instructor not found"}, status_code=400)
        if instructor.role != UserRole.INSTRUCTOR:
            return JSONResponse(
                {"error": f"User {instructor_id} does not have INSTRUCTOR role (role={instructor.role.value})"},
                status_code=400,
            )

    session = (
        db.query(SessionModel)
        .filter(SessionModel.id == session_id, SessionModel.semester_id == semester_id)
        .first()
    )
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    session.instructor_id = instructor_id
    db.add(AuditLog(
        user_id=user.id,
        action="INSTRUCTOR_UPDATED",
        resource_type="session",
        resource_id=int(session_id),
        details={"semester_id": int(semester_id), "new_instructor_id": instructor_id},
        request_method="PATCH",
        request_path=f"/admin/semesters/{semester_id}/sessions/{session_id}/instructor",
    ))
    db.commit()
    return JSONResponse({"ok": True, "instructor_id": instructor_id})
