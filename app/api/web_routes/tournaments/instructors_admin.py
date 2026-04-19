"""Admin instructor list and detail pages."""
from datetime import date as _date

from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from ....database import get_db
from ....dependencies import get_current_user_web
from ....models.instructor_assignment import (
    InstructorAssignment,
    InstructorAssignmentRequest,
    InstructorAvailabilityWindow,
    AssignmentRequestStatus,
    LocationMasterInstructor,
    MasterOfferStatus,
)
from ....models.license import UserLicense
from ....models.location import Location
from ....models.semester import Semester
from ....models.user import User, UserRole
from . import templates, _admin_only

router = APIRouter()


# ── Instructor Management Pages ─────────────────────────────────────────────────

@router.get("/admin/instructors", response_class=HTMLResponse)
async def admin_instructors_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
) -> HTMLResponse:
    """Admin instructor list — all users with role=INSTRUCTOR."""
    _admin_only(user)

    instructors = (
        db.query(User)
        .filter(User.role == UserRole.INSTRUCTOR)
        .order_by(User.name)
        .all()
    )

    # Per-instructor counts (batch — avoid N+1)
    instructor_ids = [i.id for i in instructors]

    license_counts: dict[int, int] = {}
    active_assignment_counts: dict[int, int] = {}
    master_location_counts: dict[int, int] = {}

    if instructor_ids:
        from sqlalchemy import func as sqlfunc
        for row in (
            db.query(UserLicense.user_id, sqlfunc.count(UserLicense.id))
            .filter(UserLicense.user_id.in_(instructor_ids), UserLicense.is_active == True)
            .group_by(UserLicense.user_id)
            .all()
        ):
            license_counts[row[0]] = row[1]

        for row in (
            db.query(InstructorAssignment.instructor_id, sqlfunc.count(InstructorAssignment.id))
            .filter(
                InstructorAssignment.instructor_id.in_(instructor_ids),
                InstructorAssignment.is_active == True,
            )
            .group_by(InstructorAssignment.instructor_id)
            .all()
        ):
            active_assignment_counts[row[0]] = row[1]

        for row in (
            db.query(LocationMasterInstructor.instructor_id, sqlfunc.count(LocationMasterInstructor.id))
            .filter(
                LocationMasterInstructor.instructor_id.in_(instructor_ids),
                LocationMasterInstructor.is_active == True,
            )
            .group_by(LocationMasterInstructor.instructor_id)
            .all()
        ):
            master_location_counts[row[0]] = row[1]

    stats = {
        "total": len(instructors),
        "active": sum(1 for i in instructors if i.is_active),
        "with_assignments": sum(1 for i in instructors if active_assignment_counts.get(i.id, 0) > 0),
        "masters": sum(1 for i in instructors if master_location_counts.get(i.id, 0) > 0),
    }

    return templates.TemplateResponse(
        request,
        "admin/instructors.html",
        {
            "instructors": instructors,
            "license_counts": license_counts,
            "active_assignment_counts": active_assignment_counts,
            "master_location_counts": master_location_counts,
            "stats": stats,
        },
    )


@router.get("/admin/instructors/{instructor_id}", response_class=HTMLResponse)
async def admin_instructor_detail_page(
    instructor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
) -> HTMLResponse:
    """Admin instructor detail — licenses, assignments, availability, requests."""
    _admin_only(user)

    instructor = db.query(User).filter(
        User.id == instructor_id, User.role == UserRole.INSTRUCTOR
    ).first()
    if not instructor:
        raise HTTPException(status_code=404, detail="Instructor not found")

    # Licenses
    licenses = (
        db.query(UserLicense)
        .filter(UserLicense.user_id == instructor_id)
        .order_by(UserLicense.is_active.desc(), UserLicense.started_at.desc())
        .all()
    )

    # Active assignments
    assignments = (
        db.query(InstructorAssignment)
        .filter(
            InstructorAssignment.instructor_id == instructor_id,
            InstructorAssignment.is_active == True,
        )
        .order_by(InstructorAssignment.year.desc(), InstructorAssignment.time_period_start)
        .all()
    )
    # Enrich with location names
    assignment_locations: dict[int, str] = {}
    loc_ids = {a.location_id for a in assignments}
    if loc_ids:
        for loc in db.query(Location).filter(Location.id.in_(loc_ids)).all():
            assignment_locations[loc.id] = loc.name

    # Availability windows (last 2 years)
    current_year = _date.today().year
    availability = (
        db.query(InstructorAvailabilityWindow)
        .filter(
            InstructorAvailabilityWindow.instructor_id == instructor_id,
            InstructorAvailabilityWindow.year >= current_year - 1,
        )
        .order_by(InstructorAvailabilityWindow.year.desc(), InstructorAvailabilityWindow.time_period)
        .all()
    )

    # Assignment requests (last 20, all statuses)
    requests = (
        db.query(InstructorAssignmentRequest)
        .filter(InstructorAssignmentRequest.instructor_id == instructor_id)
        .order_by(InstructorAssignmentRequest.created_at.desc())
        .limit(20)
        .all()
    )
    # Enrich requests with semester names
    sem_ids = {r.semester_id for r in requests}
    semester_names: dict[int, str] = {}
    if sem_ids:
        for sem in db.query(Semester).filter(Semester.id.in_(sem_ids)).all():
            semester_names[sem.id] = sem.name

    # Master locations
    master_contracts = (
        db.query(LocationMasterInstructor)
        .filter(LocationMasterInstructor.instructor_id == instructor_id)
        .order_by(LocationMasterInstructor.is_active.desc(), LocationMasterInstructor.created_at.desc())
        .all()
    )
    master_loc_ids = {m.location_id for m in master_contracts}
    master_location_names: dict[int, str] = {}
    if master_loc_ids:
        for loc in db.query(Location).filter(Location.id.in_(master_loc_ids)).all():
            master_location_names[loc.id] = loc.name

    return templates.TemplateResponse(
        request,
        "admin/instructor_detail.html",
        {
            "instructor": instructor,
            "licenses": licenses,
            "assignments": assignments,
            "assignment_locations": assignment_locations,
            "availability": availability,
            "requests": requests,
            "semester_names": semester_names,
            "master_contracts": master_contracts,
            "master_location_names": master_location_names,
            "AssignmentRequestStatus": AssignmentRequestStatus,
            "MasterOfferStatus": MasterOfferStatus,
            "flash": request.query_params.get("flash"),
            "error": request.query_params.get("error"),
        },
    )
