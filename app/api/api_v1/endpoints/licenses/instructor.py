"""
Instructor license operations
"""
from typing import Any, List, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .....database import get_db
from .....dependencies import get_current_user
from .....models.user import User, UserRole
from .....services.license_service import LicenseService
from .....models.license import UserLicense
from .....services.canonical_policy import (
    AgeCategory,
    CanonicalProgram,
    CoachRole,
    coach_can_teach,
    resolve_license_program,
)

router = APIRouter()

@router.post("/instructor/advance", response_model=Dict[str, Any])
async def instructor_advance_license(
    data: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Instructor-approved license advancement
    
    Request body:
    - **user_id**: ID of user to advance
    - **specialization**: COACH, PLAYER, or INTERNSHIP
    - **target_level**: Desired level number
    - **reason**: Reason for advancement
    - **requirements_met**: Description of requirements satisfied
    """
    # Check instructor permissions
    if current_user.role != UserRole.INSTRUCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors can approve license advancements"
        )
    
    required_fields = ['user_id', 'specialization', 'target_level']
    for field in required_fields:
        if field not in data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing required field: {field}"
            )
    
    license_service = LicenseService(db)
    result = license_service.advance_license(
        user_id=data['user_id'],
        specialization=data['specialization'],
        target_level=data['target_level'],
        advanced_by=current_user.id,
        reason=data.get('reason', 'Instructor approved advancement'),
        requirements_met=data.get('requirements_met', 'Requirements verified by instructor')
    )
    
    return result


@router.get("/user/{user_id}", response_model=List[Dict[str, Any]])
async def get_user_licenses(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get license information for a specific user
    - Users can view their own licenses
    - Instructors/Admins can view any user's licenses
    """
    # Users can only view their own, instructors/admins can view anyone's
    if current_user.id != user_id and current_user.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own licenses"
        )

    license_service = LicenseService(db)
    return license_service.get_user_licenses(user_id)


@router.get("/instructor/users/{user_id}/licenses", response_model=List[Dict[str, Any]])
async def get_user_licenses_by_instructor(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get license information for a specific user (instructor only) - DEPRECATED, use /user/{user_id}
    """
    if current_user.role != UserRole.INSTRUCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors can view other users' licenses"
        )

    license_service = LicenseService(db)
    return license_service.get_user_licenses(user_id)


@router.get("/instructor/dashboard/{user_id}", response_model=Dict[str, Any])
async def get_user_license_dashboard_by_instructor(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get license dashboard for a specific user (instructor only)
    """
    if current_user.role != UserRole.INSTRUCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors can view other users' license dashboards"
        )

    license_service = LicenseService(db)
    return license_service.get_user_license_dashboard(user_id)


@router.get("/instructor/{instructor_id}/teachable-specializations", response_model=List[str])
async def get_instructor_teachable_specializations(
    instructor_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get list of semester specialization types that an instructor can teach based on their licenses

    Returns list of specialization types like:
    - LFA_PLAYER_PRE, LFA_PLAYER_YOUTH (if has COACH license)
    - INTERNSHIP (if has INTERNSHIP license)
    - GANCUJU_PLAYER (if has PLAYER license with GANCUJU specialization)
    """
    # Authorization: instructors can only view their own, admins can view anyone's
    if current_user.role != UserRole.ADMIN and current_user.id != instructor_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own teachable specializations"
        )

    # Get instructor's active licenses
    licenses = db.query(UserLicense).filter(
        UserLicense.user_id == instructor_id,
        UserLicense.is_active == True
    ).all()

    if not licenses:
        return []

    # Map licenses to semester specialization types they can teach
    teachable_specs = set()

    for license in licenses:
        resolution = resolve_license_program(
            license.canonical_program_id, license.specialization_type
        )
        if not resolution.usable:
            continue
        if resolution.canonical_program is CanonicalProgram.LFA_COACH:
            for category in AgeCategory:
                if coach_can_teach(license.current_level, category, CoachRole.ASSISTANT):
                    teachable_specs.add(f"LFA_PLAYER_{category.value}")
        elif resolution.canonical_program is CanonicalProgram.INTERNSHIP:
            # INTERNSHIP license → can teach INTERNSHIP semesters
            teachable_specs.add("INTERNSHIP")

    return sorted(list(teachable_specs))


# 🔄 P0 CRITICAL: Progress-License Synchronization Endpoints
