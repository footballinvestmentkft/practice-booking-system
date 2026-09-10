"""
Instructor Assignment Endpoints

Master instructors can:
- Create assignments (after accepting applications)
- View assignments for their location
- Deactivate assignments

Admins and instructors can:
- View assignments
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User, UserRole, Location, InstructorAssignment, LocationMasterInstructor, Semester, SemesterStatus
from app.models.specialization import SpecializationType
from app.schemas.instructor_management import (
    AssignmentCreate,
    AssignmentResponse,
    AssignmentUpdate,
    AssignmentListResponse,
    MatrixCellInstructors,
    CellInstructorInfo
)
from app.services.semester_status_service import check_and_transition_semester
from app.services.teaching_permission_service import TeachingPermissionService

router = APIRouter()


@router.post("/", response_model=AssignmentResponse, status_code=status.HTTP_201_CREATED)
def create_assignment(
    data: AssignmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Master Instructor: Create instructor assignment

    Usually created after accepting an application.
    Supports co-instructors (multiple assignments for same period).
    """

    # Check if user is master for this location
    master = db.query(LocationMasterInstructor).filter(
        LocationMasterInstructor.location_id == data.location_id,
        LocationMasterInstructor.instructor_id == current_user.id,
        LocationMasterInstructor.is_active == True
    ).first()

    if not master:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the master instructor for this location"
        )

    # ========================================================================
    # VALIDATION: Instructor License & Teaching Permissions
    # ========================================================================

    # Validate instructor exists
    instructor = db.query(User).filter(User.id == data.instructor_id).first()
    if not instructor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Instructor with ID {data.instructor_id} not found"
        )

    # Check instructor has specialization/license
    if not instructor.specialization:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Instructor {instructor.name} has no specialization/license. Cannot create assignment."
        )

    # For LFA_COACH assignments, validate teaching permissions
    if data.specialization_type == "LFA_COACH":
        # Instructor must have LFA_COACH license
        if instructor.specialization != SpecializationType.LFA_COACH:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Assignment requires LFA_COACH license. Instructor has: {instructor.specialization.value}"
            )

        # Get teaching permissions
        permissions = TeachingPermissionService.get_teaching_permissions(instructor, db)

        # For regular assignments (not master), instructor must be able to teach independently
        # unless they're explicitly marked as co-instructor
        if not data.is_master and not permissions["can_teach_independently"]:
            current_level = permissions.get("current_level", "unknown")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Instructor must be Head Coach (Level 2,4,6,8) to teach independently. Current level: {current_level} (Assistant Coach). Consider assigning as co-instructor."
            )

        # Validate age group compatibility
        instructor_age_group = permissions["age_group"]  # e.g., "YOUTH_FOOTBALL"

        normalized_age_group = {
            "Pre Football Coach": "PRE",
            "Youth Football Coach": "YOUTH",
            "Amateur Football Coach": "AMATEUR",
            "Pro Football Coach": "PRO",
        }.get(data.age_group, data.age_group)
        if not TeachingPermissionService.can_teach_scope(
            permissions.get("current_level", 0), normalized_age_group, "HEAD"
        ):
            allowed_groups = _get_allowed_age_groups(instructor_age_group)
            instructor_level = permissions.get("current_level", 0)

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "License incompatibility",
                    "instructor_license": f"LFA_COACH Level {instructor_level}",
                    "instructor_age_group": instructor_age_group,
                    "can_teach": allowed_groups,
                    "requested_age_group": data.age_group,
                    "recommendation": f"This instructor can only teach: {', '.join(allowed_groups)}. Assign a Level {instructor_level + 2} or higher instructor for {data.age_group}."
                }
            )

    # For other specialization types, just verify instructor has matching specialization
    else:
        if instructor.specialization.value != data.specialization_type:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Instructor specialization ({instructor.specialization.value}) does not match assignment ({data.specialization_type})"
            )

    # ========================================================================
    # Create assignment
    assignment = InstructorAssignment(
        location_id=data.location_id,
        instructor_id=data.instructor_id,
        specialization_type=data.specialization_type,
        age_group=data.age_group,
        year=data.year,
        time_period_start=data.time_period_start,
        time_period_end=data.time_period_end,
        is_master=data.is_master,
        assigned_by=current_user.id,
        is_active=True
    )

    db.add(assignment)
    db.commit()
    db.refresh(assignment)

    # 🎯 STATUS WORKFLOW: Find and transition matching semesters
    # When a master creates an assignment (assistant instructor), check if any
    # semesters matching this assignment's criteria should transition to READY_FOR_ENROLLMENT
    #
    # Note: InstructorAssignment doesn't have semester_id, so we find matching semesters
    # based on location_id, specialization_type, and time overlap
    location = db.query(Location).filter(Location.id == assignment.location_id).first()
    if location:
        # Find semesters at this location with same specialization
        # that might now be ready for enrollment
        matching_semesters = db.query(Semester).filter(
            Semester.location_id == location.id,
            Semester.specialization_type == assignment.specialization_type,
            Semester.status == SemesterStatus.INSTRUCTOR_ASSIGNED
        ).all()

        # Check each semester and transition if ready
        for semester in matching_semesters:
            check_and_transition_semester(db, semester.id)

    # Build response
    response = AssignmentResponse.from_orm(assignment)
    if assignment.instructor:
        response.instructor_name = assignment.instructor.name
        response.instructor_email = assignment.instructor.email
    if assignment.location:
        response.location_name = assignment.location.name
    if assignment.assigner:
        response.assigner_name = assignment.assigner.name

    return response


@router.get("/", response_model=AssignmentListResponse)
def list_assignments(
    location_id: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    specialization: Optional[str] = Query(None),
    age_group: Optional[str] = Query(None),
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    List instructor assignments

    Filters:
    - location_id
    - year
    - specialization
    - age_group
    - include_inactive
    """

    query = db.query(InstructorAssignment)

    if location_id:
        query = query.filter(InstructorAssignment.location_id == location_id)

    if year:
        query = query.filter(InstructorAssignment.year == year)

    if specialization:
        query = query.filter(InstructorAssignment.specialization_type == specialization)

    if age_group:
        query = query.filter(InstructorAssignment.age_group == age_group)

    if not include_inactive:
        query = query.filter(InstructorAssignment.is_active == True)

    assignments = query.order_by(
        InstructorAssignment.year.desc(),
        InstructorAssignment.specialization_type,
        InstructorAssignment.age_group
    ).all()

    # Build responses
    assignment_responses = []
    for assignment in assignments:
        response = AssignmentResponse.from_orm(assignment)
        if assignment.instructor:
            response.instructor_name = assignment.instructor.name
            response.instructor_email = assignment.instructor.email
        if assignment.location:
            response.location_name = assignment.location.name
        if assignment.assigner:
            response.assigner_name = assignment.assigner.name
        assignment_responses.append(response)

    return AssignmentListResponse(
        total=len(assignment_responses),
        assignments=assignment_responses
    )


@router.get("/matrix-cell", response_model=MatrixCellInstructors)
def get_matrix_cell_instructors(
    location_id: int = Query(...),
    specialization: str = Query(...),
    age_group: str = Query(...),
    year: int = Query(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Smart Matrix Integration: Get all instructors for a specific cell

    Returns:
    - All active instructors assigned to this spec/age/year
    - Coverage information
    - Co-instructor flags
    """

    assignments = db.query(InstructorAssignment).filter(
        InstructorAssignment.location_id == location_id,
        InstructorAssignment.specialization_type == specialization,
        InstructorAssignment.age_group == age_group,
        InstructorAssignment.year == year,
        InstructorAssignment.is_active == True
    ).all()

    # Build instructor info list
    instructors = []
    for assignment in assignments:
        # Check if this is a co-instructor (multiple people same period)
        overlapping_count = sum(
            1 for a in assignments
            if a.time_period_start == assignment.time_period_start
            and a.time_period_end == assignment.time_period_end
        )
        is_co_instructor = overlapping_count > 1

        instructor_info = CellInstructorInfo(
            instructor_id=assignment.instructor_id,
            instructor_name=assignment.instructor.name if assignment.instructor else "Unknown",
            is_master=assignment.is_master,
            is_co_instructor=is_co_instructor,
            period_coverage=f"{assignment.time_period_start}-{assignment.time_period_end}"
        )
        instructors.append(instructor_info)

    # Calculate coverage (simplified - assumes monthly for PRE/YOUTH, annual for AMATEUR/PRO)
    if age_group in ["PRE", "YOUTH"]:
        required_months = 12
        # TODO: Calculate actual coverage from period codes
        total_coverage = len(assignments) * 6  # Assuming 6 months per assignment (simplified)
    else:  # AMATEUR, PRO
        required_months = 12
        total_coverage = len(assignments) * 12

    coverage_percentage = min(100.0, (total_coverage / required_months) * 100) if required_months > 0 else 0

    return MatrixCellInstructors(
        specialization_type=specialization,
        age_group=age_group,
        year=year,
        location_id=location_id,
        instructors=instructors,
        total_coverage_months=total_coverage,
        required_months=required_months,
        coverage_percentage=coverage_percentage
    )


@router.patch("/{assignment_id}", response_model=AssignmentResponse)
def update_assignment(
    assignment_id: int,
    data: AssignmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Master Instructor: Update assignment (usually to deactivate)
    """

    assignment = db.query(InstructorAssignment).filter(
        InstructorAssignment.id == assignment_id
    ).first()

    if not assignment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Assignment {assignment_id} not found"
        )

    # Check if user is master for this location
    master = db.query(LocationMasterInstructor).filter(
        LocationMasterInstructor.location_id == assignment.location_id,
        LocationMasterInstructor.instructor_id == current_user.id,
        LocationMasterInstructor.is_active == True
    ).first()

    if not master:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not the master instructor for this location"
        )

    # Update fields
    if data.is_active is not None:
        assignment.is_active = data.is_active
        if not data.is_active:
            assignment.deactivated_at = datetime.now()

    db.commit()
    db.refresh(assignment)

    # Build response
    response = AssignmentResponse.from_orm(assignment)
    if assignment.instructor:
        response.instructor_name = assignment.instructor.name
        response.instructor_email = assignment.instructor.email
    if assignment.location:
        response.location_name = assignment.location.name
    if assignment.assigner:
        response.assigner_name = assignment.assigner.name

    return response


@router.delete("/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_assignment(
    assignment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Master Instructor or Admin: Delete assignment

    Typically should deactivate instead of delete (for audit trail).
    Delete only if no sessions have been created yet.
    """

    assignment = db.query(InstructorAssignment).filter(
        InstructorAssignment.id == assignment_id
    ).first()

    if not assignment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Assignment {assignment_id} not found"
        )

    # Check permissions
    is_admin = current_user.role == UserRole.ADMIN
    is_master = db.query(LocationMasterInstructor).filter(
        LocationMasterInstructor.location_id == assignment.location_id,
        LocationMasterInstructor.instructor_id == current_user.id,
        LocationMasterInstructor.is_active == True
    ).first() is not None

    if not (is_admin or is_master):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions"
        )

    # TODO: Check if sessions exist for this assignment
    # For now, allow deletion

    db.delete(assignment)
    db.commit()

    return None


# ============================================================================
# Helper Functions (License & Permission Validation)
# ============================================================================


def _can_teach_age_group(instructor_age_group: str, assignment_age_group: str) -> bool:
    """
    Check if instructor can teach this age group based on LFA_COACH hierarchy

    LFA_COACH License Hierarchy:
    - Level 2 (PRE Head Coach) → can teach PRE only
    - Level 4 (YOUTH Head Coach) → can teach PRE + YOUTH
    - Level 6 (AMATEUR Head Coach) → can teach PRE + YOUTH + AMATEUR
    - Level 8 (PRO Head Coach) → can teach ALL (PRE + YOUTH + AMATEUR + PRO)
    """
    # Normalize assignment age group to match instructor format
    age_group_mapping = {
        "Pre Football Coach": "PRE_FOOTBALL",
        "Youth Football Coach": "YOUTH_FOOTBALL",
        "Amateur Football Coach": "AMATEUR_FOOTBALL",
        "Pro Football Coach": "PRO_FOOTBALL"
    }

    normalized_assignment = age_group_mapping.get(assignment_age_group, assignment_age_group)

    hierarchy = {
        "PRE_FOOTBALL": ["PRE_FOOTBALL"],
        "YOUTH_FOOTBALL": ["PRE_FOOTBALL", "YOUTH_FOOTBALL"],
        "AMATEUR_FOOTBALL": ["PRE_FOOTBALL", "YOUTH_FOOTBALL", "AMATEUR_FOOTBALL"],
        "PRO_FOOTBALL": ["PRE_FOOTBALL", "YOUTH_FOOTBALL", "AMATEUR_FOOTBALL", "PRO_FOOTBALL"]
    }

    allowed = hierarchy.get(instructor_age_group, [])
    return normalized_assignment in allowed


def _get_allowed_age_groups(instructor_age_group: str) -> list:
    """Get list of age groups this instructor can teach"""
    hierarchy = {
        "PRE_FOOTBALL": ["Pre Football Coach"],
        "YOUTH_FOOTBALL": ["Pre Football Coach", "Youth Football Coach"],
        "AMATEUR_FOOTBALL": ["Pre Football Coach", "Youth Football Coach", "Amateur Football Coach"],
        "PRO_FOOTBALL": ["Pre Football Coach", "Youth Football Coach", "Amateur Football Coach", "Pro Football Coach"]
    }

    return hierarchy.get(instructor_age_group, [])
