"""
Direct Hire Endpoints (Pathway A)

Master instructor hiring via direct offers:
- Admin directly invites instructor
- Instructor receives offer with deadline
- Instructor accepts/declines
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone

from app.database import get_db
from app.dependencies import get_current_admin_user
from app.models import User, Location
from app.models.semester import Semester
from app.models.specialization import SpecializationType
from app.models.instructor_assignment import (
    LocationMasterInstructor,
    MasterOfferStatus
)
from app.services.teaching_permission_service import TeachingPermissionService
from app.schemas.instructor_management import (
    MasterDirectHireCreate,
    MasterOfferResponse,
    MasterOfferStatusEnum,
    HiringPathwayEnum
)
from app.services.availability_service import (
    check_availability_match,
    check_instructor_has_active_master_position,
    get_instructor_active_master_location
)
from app.services.shared.license_validator import LicenseValidator
from .utils import (
    get_semester_age_group,
    can_teach_age_group,
    get_allowed_age_groups
)

router = APIRouter()


@router.post("/direct-hire", response_model=MasterOfferResponse, status_code=status.HTTP_201_CREATED)
def create_direct_hire_offer(
    data: MasterDirectHireCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_admin_user)
):
    """
    Admin: Create direct hire offer (Pathway A)

    Sends offer to instructor → Instructor must accept/decline

    Business Rules:
    - Only one active master per location
    - Instructor cannot have active master position elsewhere
    - Availability validation is ADVISORY only (admin can override)
    - Offer has deadline (default 14 days)
    """

    # Validate location exists
    location = db.query(Location).filter(Location.id == data.location_id).first()
    if not location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Location {data.location_id} not found"
        )

    # Validate instructor exists
    instructor = db.query(User).filter(User.id == data.instructor_id).first()
    if not instructor:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Instructor {data.instructor_id} not found"
        )

    # Check if location already has active master
    existing_master = db.query(LocationMasterInstructor).filter(
        LocationMasterInstructor.location_id == data.location_id,
        LocationMasterInstructor.is_active == True,
        (
            (LocationMasterInstructor.offer_status == None) |  # Legacy active
            (LocationMasterInstructor.offer_status == MasterOfferStatus.ACCEPTED)
        )
    ).first()

    if existing_master:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Location {location.name} already has an active master instructor"
        )

    # Check if instructor already has active master position elsewhere
    if check_instructor_has_active_master_position(data.instructor_id, db):
        active_location = get_instructor_active_master_location(data.instructor_id, db)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Instructor is already master at {active_location}. Can only be master at ONE location."
        )

    # Check availability (ADVISORY - does not block)
    year = data.contract_start.year
    availability_result = check_availability_match(
        instructor_id=data.instructor_id,
        year=year,
        contract_start=data.contract_start,
        contract_end=data.contract_end,
        db=db
    )

    # If poor availability match and no override, return warning
    if availability_result.match_score < 50 and not data.override_availability:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Availability mismatch",
                "match_score": availability_result.match_score,
                "warnings": availability_result.warnings,
                "instructor_availability": availability_result.instructor_availability,
                "contract_coverage": availability_result.contract_coverage,
                "action_required": "Set override_availability=true to proceed anyway"
            }
        )

    # ========================================================================
    # CRITICAL: License & Teaching Permission Validation
    # ========================================================================

    # Check instructor has specialization
    if not instructor.specialization:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Instructor {instructor.name} has no specialization/license assigned. Cannot hire as Master Instructor."
        )

    # Check instructor has LFA_COACH license
    if instructor.specialization != SpecializationType.LFA_COACH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Master Instructor must have LFA_COACH license. Instructor has: {instructor.specialization.value if instructor.specialization else 'None'}"
        )

    # Verify the LFA_COACH UserLicense is active and not expired
    LicenseValidator.get_coach_license(db, instructor.id, raise_if_missing=True)

    # Get teaching permissions
    permissions = TeachingPermissionService.get_teaching_permissions(instructor, db)

    # Check can teach independently (Head Coach = Level 2,4,6,8)
    if not permissions["can_teach_independently"]:
        current_level = permissions.get("current_level", "unknown")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Master Instructor must be Head Coach (Level 2,4,6,8). Instructor is Level {current_level} (Assistant Coach)."
        )

    # Check location semesters compatibility with instructor's age group
    location_semesters = db.query(Semester).filter(
        Semester.location_city == location.city,
        Semester.status.in_(['DRAFT', 'INSTRUCTOR_ASSIGNED', 'READY_FOR_ENROLLMENT', 'ONGOING'])
    ).all()

    if location_semesters:
        instructor_age_group = permissions["age_group"]  # e.g., "YOUTH_FOOTBALL"
        incompatible_semesters = []

        for semester in location_semesters:
            semester_age_group = get_semester_age_group(semester.specialization_type)

            if not can_teach_age_group(instructor_age_group, semester_age_group):
                incompatible_semesters.append({
                    "id": semester.id,
                    "code": semester.code,
                    "age_group": semester_age_group
                })

        if incompatible_semesters:
            instructor_level = permissions.get("current_level", 0)
            allowed_groups = get_allowed_age_groups(instructor_age_group)

            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": "License incompatibility",
                    "instructor_license": f"LFA_COACH Level {instructor_level}",
                    "instructor_age_group": instructor_age_group,
                    "can_teach": allowed_groups,
                    "location": location.name,
                    "incompatible_semesters": incompatible_semesters,
                    "recommendation": f"Assign a Level {instructor_level + 2} or higher instructor for this location, or adjust location semesters."
                }
            )

    # Calculate offer deadline
    now = datetime.now(timezone.utc)
    offer_deadline = now + timedelta(days=data.offer_deadline_days)

    # Create OFFERED contract
    master = LocationMasterInstructor(
        location_id=data.location_id,
        instructor_id=data.instructor_id,
        contract_start=data.contract_start,
        contract_end=data.contract_end,
        is_active=False,  # Not active until accepted
        offer_status=MasterOfferStatus.OFFERED,
        offered_at=now,
        offer_deadline=offer_deadline,
        hiring_pathway='DIRECT',
        availability_override=data.override_availability
    )

    db.add(master)
    db.commit()
    db.refresh(master)

    # Build response with availability info
    response = MasterOfferResponse(
        id=master.id,
        location_id=master.location_id,
        instructor_id=master.instructor_id,
        contract_start=master.contract_start,
        contract_end=master.contract_end,
        offer_status=MasterOfferStatusEnum.OFFERED,
        is_active=master.is_active,
        offered_at=master.offered_at,
        offer_deadline=master.offer_deadline,
        hiring_pathway=HiringPathwayEnum.DIRECT,
        availability_override=master.availability_override,
        availability_warnings=availability_result.warnings,
        availability_match_score=availability_result.match_score,
        location_name=location.name,
        location_city=location.city,
        instructor_name=instructor.name,
        instructor_email=instructor.email
    )

    return response
