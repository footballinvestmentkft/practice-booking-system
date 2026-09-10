"""
CRUD operations for semester enrollments
Creating, deleting, and toggling enrollments
"""
import logging
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from datetime import datetime

from app.core.metrics import metrics
from app.core.structured_log import log_info, log_warn

_logger = logging.getLogger(__name__)

from .....database import get_db
from .....dependencies import get_current_admin_user_hybrid
from .....models.user import User, UserRole
from .....models.semester import Semester
from .....models.license import UserLicense
from .....models.semester_enrollment import SemesterEnrollment
from .schemas import EnrollmentCreate
from .....services.canonical_policy import CanonicalProgram, resolve_license_program
from .....services.program_eligibility_service import is_user_eligible_for_program
from .....services.player_identity_service import (
    PlayerIdentityError,
    prepare_football_player_enrollment,
)

router = APIRouter()


@router.post("/enroll")
async def create_enrollment(
    request: Request,
    enrollment: EnrollmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user_hybrid)
) -> Dict[str, Any]:
    """
    Enroll a student in a specialization for a specific semester (Admin only)
    """
    # Validate student exists
    student = db.query(User).filter(User.id == enrollment.user_id, User.role == UserRole.STUDENT).first()
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")

    # Validate semester exists
    semester = db.query(Semester).filter(Semester.id == enrollment.semester_id).first()
    if not semester:
        raise HTTPException(status_code=404, detail="Semester not found")

    # Validate user_license exists and belongs to student
    user_license = db.query(UserLicense).filter(
        UserLicense.id == enrollment.user_license_id,
        UserLicense.user_id == enrollment.user_id
    ).first()
    if not user_license:
        raise HTTPException(status_code=404, detail="UserLicense not found or does not belong to student")

    program_resolution = resolve_license_program(
        user_license.canonical_program_id, user_license.specialization_type
    )
    if not program_resolution.usable:
        raise HTTPException(status_code=409, detail="PROGRAM_ID_MANUAL_REVIEW_OR_INVALID")
    player_assignment = None
    if program_resolution.canonical_program is CanonicalProgram.LFA_FOOTBALL_PLAYER:
        try:
            player_assignment = prepare_football_player_enrollment(
                db,
                user=student,
                user_license=user_license,
            ).assignment
        except PlayerIdentityError as exc:
            db.rollback()
            raise HTTPException(status_code=403, detail=exc.code) from exc
    else:
        eligible, denial_reason = is_user_eligible_for_program(
            db, student, program_resolution.canonical_program
        )
        if not eligible:
            raise HTTPException(status_code=403, detail=denial_reason)

    # Check if enrollment already exists
    existing = db.query(SemesterEnrollment).filter(
        SemesterEnrollment.user_id == enrollment.user_id,
        SemesterEnrollment.semester_id == enrollment.semester_id,
        SemesterEnrollment.user_license_id == enrollment.user_license_id
    ).first()

    if existing:
        raise HTTPException(status_code=400, detail="Student is already enrolled in this specialization for this semester")

    metrics.increment("enrollment_attempts")

    # 🏗️ Hierarchy access gate (M-02): if this semester is nested inside a parent,
    # the student must already have an active enrollment in the parent semester.
    if semester.parent_semester_id is not None:
        parent_enrollment = db.query(SemesterEnrollment).filter(
            SemesterEnrollment.user_id == enrollment.user_id,
            SemesterEnrollment.semester_id == semester.parent_semester_id,
            SemesterEnrollment.is_active == True,
        ).first()
        if not parent_enrollment:
            metrics.increment("enrollment_gate_blocked")
            log_warn(
                _logger, "enrollment_gate_blocked",
                user_id=enrollment.user_id,
                semester_id=enrollment.semester_id,
                parent_semester_id=semester.parent_semester_id,
                reason="no_active_parent_enrollment",
            )
            raise HTTPException(
                status_code=403,
                detail=(
                    "Student must be enrolled in the parent program before joining "
                    "this nested semester"
                )
            )
        log_info(
            _logger, "enrollment_gate_passed",
            user_id=enrollment.user_id,
            semester_id=enrollment.semester_id,
            parent_semester_id=semester.parent_semester_id,
            parent_enrollment_id=parent_enrollment.id,
        )

    # Create enrollment
    new_enrollment = SemesterEnrollment(
        user_id=enrollment.user_id,
        semester_id=enrollment.semester_id,
        user_license_id=enrollment.user_license_id,
        payment_verified=False,
        is_active=True,
        enrolled_at=datetime.utcnow(),
        age_category=None,
        age_category_overridden=False  # 🎯 NEW: Not overridden yet
    )

    db.add(new_enrollment)
    db.flush()
    if program_resolution.canonical_program is CanonicalProgram.LFA_FOOTBALL_PLAYER:
        new_enrollment.football_category_assignment_id = player_assignment.id
        new_enrollment.age_category = player_assignment.effective_category
    db.commit()
    db.refresh(new_enrollment)

    return {
        "success": True,
        "message": f"Enrolled {student.name} in {user_license.specialization_type} for {semester.code}",
        "enrollment_id": new_enrollment.id
    }


@router.delete("/{enrollment_id}")
async def delete_enrollment(
    request: Request,
    enrollment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user_hybrid)
) -> Dict[str, Any]:
    """
    Delete an enrollment (Admin only)
    Warning: This does NOT delete the UserLicense (progress is preserved)
    """
    enrollment = db.query(SemesterEnrollment).filter(SemesterEnrollment.id == enrollment_id).first()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    db.delete(enrollment)
    db.commit()

    return {
        "success": True,
        "message": "Enrollment deleted successfully (UserLicense progress preserved)"
    }


@router.post("/{enrollment_id}/toggle-active")
async def toggle_enrollment_active(
    request: Request,
    enrollment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user_hybrid)
) -> Dict[str, Any]:
    """
    Toggle enrollment active status (Admin only)
    """
    enrollment = db.query(SemesterEnrollment).filter(SemesterEnrollment.id == enrollment_id).first()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    if enrollment.is_active:
        enrollment.deactivate()
        message = "Enrollment deactivated"
    else:
        enrollment.reactivate()
        message = "Enrollment reactivated"

    db.commit()

    return {
        "success": True,
        "message": message,
        "is_active": enrollment.is_active
    }
