"""
Instructor dashboard routes
"""
from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path
from datetime import datetime, timezone

from sqlalchemy.orm import joinedload

import logging

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User, UserRole
from ...models.semester import Semester
from ...models.license import UserLicense
from ...models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from ...models.specialization import SpecializationType
from ...models.audit_log import AuditAction
from ...services.audit_service import AuditService
from ...services.canonical_policy import CanonicalProgram, resolve_license_program

# Setup templates
BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

logger = logging.getLogger(__name__)

router = APIRouter()


def _is_canonical_football_license(license: UserLicense) -> bool:
    resolution = resolve_license_program(
        license.canonical_program_id,
        license.specialization_type,
    )
    return (
        resolution.usable
        and resolution.canonical_program is CanonicalProgram.LFA_FOOTBALL_PLAYER
    )


def _football_license_display(license: UserLicense) -> str:
    return "LFA Football Player" if _is_canonical_football_license(license) else license.specialization_type


@router.get("/instructor/enrollments", response_class=HTMLResponse)
async def instructor_enrollments_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Instructor-only: View and approve enrollment requests for their semesters"""
    if user.role != UserRole.INSTRUCTOR:
        raise HTTPException(status_code=403, detail="Instructor access required")

    # Get all semesters where this instructor is the master instructor
    instructor_semesters = db.query(Semester).filter(
        Semester.master_instructor_id == user.id
    ).order_by(Semester.start_date.desc()).all()

    # Get semester IDs for filtering enrollments
    semester_ids = [s.id for s in instructor_semesters]

    # Get ALL enrollments for instructor's semesters
    all_enrollments = []
    if semester_ids:
        all_enrollments = (
            db.query(SemesterEnrollment)
            .options(
                joinedload(SemesterEnrollment.user),
                joinedload(SemesterEnrollment.user_license),
                joinedload(SemesterEnrollment.semester)
            )
            .filter(SemesterEnrollment.semester_id.in_(semester_ids))
            .order_by(SemesterEnrollment.requested_at.desc())
            .all()
        )

    # Group enrollments by specialization
    specialization_groups = {}
    for spec_type in SpecializationType:
        spec_enrollments = [e for e in all_enrollments if e.user_license.specialization_type == spec_type.value]

        # Separate pending and active
        pending = [e for e in spec_enrollments if e.request_status == EnrollmentStatus.PENDING]
        active = [e for e in spec_enrollments if e.request_status != EnrollmentStatus.PENDING]

        specialization_groups[spec_type.value] = {
            'pending': pending,
            'active': active,
            'total_count': len(spec_enrollments)
        }

    logger.info("instructor_dashboard_loaded", extra={"instructor": user.email, "semesters": len(instructor_semesters), "enrollments": len(all_enrollments)})

    return templates.TemplateResponse(
        "instructor/enrollments.html",
        {
            "request": request,
            "user": user,
            "instructor_semesters": instructor_semesters,
            "specialization_groups": specialization_groups,
            "SpecializationType": SpecializationType
        }
    )


# ⚽ INSTRUCTOR: Edit Student Football Skills
@router.get("/instructor/students/{student_id}/skills/{license_id}", response_class=HTMLResponse)
async def instructor_edit_student_skills_page(
    request: Request,
    student_id: int,
    license_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web)
):
    """Instructor-only: Edit football skills for LFA Player students"""
    if user.role != UserRole.INSTRUCTOR:
        raise HTTPException(status_code=403, detail="Instructor access required")

    # Get student
    student = db.query(User).filter(User.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    # Get license
    license = db.query(UserLicense).filter(UserLicense.id == license_id).first()
    if not license or license.user_id != student_id:
        raise HTTPException(status_code=404, detail="License not found")

    # Check if this is an LFA Player specialization
    if not _is_canonical_football_license(license):
        raise HTTPException(
            status_code=400,
            detail=f"Football skills are only available for LFA Player specializations, not {license.specialization_type}"
        )

    # Get specialization display name
    spec_display_map = {
        "LFA_PLAYER_PRE": "LFA Player PRE (Ages 5-13)",
        "LFA_PLAYER_YOUTH": "LFA Player Youth (Ages 14-18)",
        "LFA_PLAYER_AMATEUR": "LFA Player Amateur (Ages 14+)",
        "LFA_PLAYER_PRO": "LFA Player PRO (Ages 14+)"
    }
    specialization_display = spec_display_map.get(
        license.specialization_type, _football_license_display(license)
    )

    # Get color
    specialization_color = "#f1c40f"  # Yellow for all LFA Player specs

    return templates.TemplateResponse(
        "instructor/student_skills.html",
        {
            "request": request,
            "user": user,
            "student": student,
            "license": license,
            "license_id": license_id,
            "current_skills": license.football_skills,
            "specialization_display": specialization_display,
            "specialization_color": specialization_color
        }
    )


@router.post("/instructor/students/{student_id}/skills/{license_id}")
async def instructor_update_student_skills(
    request: Request,
    student_id: int,
    license_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
    heading: float = Form(...),
    shooting: float = Form(...),
    crossing: float = Form(...),
    passing: float = Form(...),
    dribbling: float = Form(...),
    ball_control: float = Form(...),
    instructor_notes: str = Form("")
):
    """Instructor-only: Update football skills for a student"""
    if user.role != UserRole.INSTRUCTOR:
        raise HTTPException(status_code=403, detail="Instructor access required")

    # Get student
    student = db.query(User).filter(User.id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    # Get license
    license = db.query(UserLicense).filter(UserLicense.id == license_id).first()
    if not license or license.user_id != student_id:
        raise HTTPException(status_code=404, detail="License not found")

    # Check if this is an LFA Player specialization
    if not _is_canonical_football_license(license):
        raise HTTPException(
            status_code=400,
            detail=f"Football skills are only available for LFA Player specializations"
        )

    # Validate ranges
    skills = {
        "heading": heading,
        "shooting": shooting,
        "crossing": crossing,
        "passing": passing,
        "dribbling": dribbling,
        "ball_control": ball_control
    }

    for skill_name, value in skills.items():
        if value < 0 or value > 100:
            # Get specialization display name
            spec_display_map = {
                "LFA_PLAYER_PRE": "LFA Player PRE (Ages 5-13)",
                "LFA_PLAYER_YOUTH": "LFA Player Youth (Ages 14-18)",
                "LFA_PLAYER_AMATEUR": "LFA Player Amateur (Ages 14+)",
                "LFA_PLAYER_PRO": "LFA Player PRO (Ages 14+)"
            }
            specialization_display = spec_display_map.get(
                license.specialization_type, _football_license_display(license)
            )
            specialization_color = "#f1c40f"

            return templates.TemplateResponse(
                "instructor/student_skills.html",
                {
                    "request": request,
                    "user": user,
                    "student": student,
                    "license": license,
                    "license_id": license_id,
                    "current_skills": license.football_skills,
                    "specialization_display": specialization_display,
                    "specialization_color": specialization_color,
                    "error": f"Skill '{skill_name}' must be between 0 and 100"
                }
            )

    # Round to 1 decimal place
    skills_dict = {k: round(v, 1) for k, v in skills.items()}

    # Update license
    license.football_skills = skills_dict
    license.skills_last_updated_at = datetime.now(timezone.utc)
    license.skills_updated_by = user.id

    # Update instructor notes if provided
    if instructor_notes.strip():
        license.instructor_notes = instructor_notes.strip()

    db.commit()
    db.refresh(license)

    # Log audit
    audit_service = AuditService(db)
    audit_service.log(
        action=AuditAction.FOOTBALL_SKILLS_UPDATED,
        user_id=user.id,
        resource_type="football_skills",
        resource_id=license_id,
        details={
            "student_id": student_id,
            "student_email": student.email,
            "specialization": license.specialization_type,
            "skills": skills_dict,
            "instructor_notes": instructor_notes
        }
    )

    logger.info("instructor_skills_updated", extra={"instructor": user.email, "student": student.email, "spec": license.specialization_type})

    # Get specialization display name
    spec_display_map = {
        "LFA_PLAYER_PRE": "LFA Player PRE (Ages 5-13)",
        "LFA_PLAYER_YOUTH": "LFA Player Youth (Ages 14-18)",
        "LFA_PLAYER_AMATEUR": "LFA Player Amateur (Ages 14+)",
        "LFA_PLAYER_PRO": "LFA Player PRO (Ages 14+)"
    }
    specialization_display = spec_display_map.get(
        license.specialization_type, _football_license_display(license)
    )
    specialization_color = "#f1c40f"

    return templates.TemplateResponse(
        "instructor/student_skills.html",
        {
            "request": request,
            "user": user,
            "student": student,
            "license": license,
            "license_id": license_id,
            "current_skills": license.football_skills,
            "specialization_display": specialization_display,
            "specialization_color": specialization_color,
            "success": True
        }
    )

# ============================================================================
# ⚽ DEPRECATED: LFA Player Skills V2 Routes
# ============================================================================
# These routes have been MOVED to app/api/routes/lfa_player_routes.py
# as part of the spec-based architecture refactoring.
#
# The new spec-based architecture separates routes by specialization:
# - LFA Player: app/api/routes/lfa_player_routes.py (skills-based)
# - Gancuju: app/api/routes/gancuju_routes.py (belt-based)
# - Internship: app/api/routes/internship_routes.py (XP-based)
# - LFA Coach: app/api/routes/lfa_coach_routes.py (certification-based)
#
# All routes are automatically included via router.include_router() at the
# end of this file.
# ============================================================================

