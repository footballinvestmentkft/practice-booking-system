"""
Football skills assessment endpoints
"""
from datetime import datetime, timezone
from typing import Any, List, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .....database import get_db
from .....dependencies import get_current_user
from .....models.user import User, UserRole
from .....models.license import UserLicense
from .....models.audit_log import AuditAction
from .....services.audit_service import AuditService

router = APIRouter()

@router.get("/{license_id}/football-skills", response_model=Dict[str, Any])
async def get_football_skills(
    license_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get football skills for a specific license (LFA Player specializations only).

    Returns the raw football_skills JSONB for the license. Each skill entry contains:
    - `current_level`   — EMA-updated visible skill level (starts at SYSTEM_BASELINE = 60.0)
    - `system_baseline` — fixed EMA anchor, always 60.0 for post-onboarding players
    - `baseline`        — backward-compatible alias for system_baseline (60.0)
    - `self_assessment` — onboarding self-evaluation entered by the player (0-100 scale).
                          **Motivational reference only. Not a skill level. Never used
                          as input by EMA, baseline extraction, or any calculation service.**
    - `assessment_delta`— computed from FootballSkillAssessment rows (coach evaluations),
                          NOT from self_assessment
    - `tournament_delta`— cumulative EMA delta from tournament placements

    - **license_id**: UserLicense ID
    """
    license = db.query(UserLicense).filter(UserLicense.id == license_id).first()

    if not license:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="License not found"
        )

    # Check permissions - user can view their own, instructors can view anyone's
    if license.user_id != current_user.id and current_user.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own skills"
        )

    # Check if this is an LFA Player specialization
    if not license.specialization_type.startswith("LFA_PLAYER_"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Football skills are only available for LFA Player specializations, not {license.specialization_type}"
        )

    # Return skills or null if not yet set
    if not license.football_skills:
        return {
            "license_id": license_id,
            "specialization_type": license.specialization_type,
            "skills": None,
            "message": "Skills not yet assessed"
        }

    # Get instructor name who last updated
    updated_by_name = None
    if license.skills_updated_by:
        updater = db.query(User).filter(User.id == license.skills_updated_by).first()
        if updater:
            updated_by_name = updater.name

    return {
        "license_id": license_id,
        "specialization_type": license.specialization_type,
        "skills": license.football_skills,
        "skills_last_updated_at": license.skills_last_updated_at.isoformat() if license.skills_last_updated_at else None,
        "skills_updated_by_id": license.skills_updated_by,
        "skills_updated_by_name": updated_by_name
    }


@router.put("/{license_id}/football-skills", response_model=Dict[str, Any])
async def update_football_skills(
    license_id: int,
    skills_data: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update football skills for a student (INSTRUCTOR ONLY)

    Updates 6 skill percentages (0-100): heading, shooting, crossing, passing, dribbling, ball_control

    Request body:
    - **heading**: 0-100
    - **shooting**: 0-100
    - **crossing**: 0-100
    - **passing**: 0-100
    - **dribbling**: 0-100
    - **ball_control**: 0-100
    - **instructor_notes**: Optional notes about the assessment
    """
    # Only instructors can update skills
    if current_user.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only instructors can update football skills"
        )

    license = db.query(UserLicense).filter(UserLicense.id == license_id).first()

    if not license:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="License not found"
        )

    # Check if this is an LFA Player specialization
    if not license.specialization_type.startswith("LFA_PLAYER_"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Football skills are only available for LFA Player specializations, not {license.specialization_type}"
        )

    # Validate all 6 skills are provided
    required_skills = ['heading', 'shooting', 'crossing', 'passing', 'dribbling', 'ball_control']
    for skill in required_skills:
        if skill not in skills_data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing required skill: {skill}"
            )

        # Validate range 0-100
        value = skills_data[skill]
        if not isinstance(value, (int, float)) or value < 0 or value > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Skill '{skill}' must be between 0 and 100, got {value}"
            )

    # Round to 1 decimal place and build skills dict
    skills_dict = {
        skill: round(float(skills_data[skill]), 1)
        for skill in required_skills
    }

    # Update license
    license.football_skills = skills_dict
    license.skills_last_updated_at = datetime.now(timezone.utc)
    license.skills_updated_by = current_user.id

    # Optional: update instructor_notes if provided
    if 'instructor_notes' in skills_data:
        license.instructor_notes = skills_data['instructor_notes']

    db.commit()
    db.refresh(license)

    # Log audit
    audit_service = AuditService(db)
    audit_service.log(
        action=AuditAction.USER_UPDATED,
        user_id=current_user.id,
        resource_type="football_skills",
        resource_id=license_id,
        details={
            "student_id": license.user_id,
            "specialization": license.specialization_type,
            "skills": skills_dict,
            "instructor_notes": skills_data.get('instructor_notes')
        }
    )

    return {
        "success": True,
        "message": "Football skills updated successfully",
        "license_id": license_id,
        "skills": skills_dict,
        "updated_at": license.skills_last_updated_at.isoformat(),
        "updated_by": current_user.name
    }


@router.get("/user/{user_id}/football-skills", response_model=List[Dict[str, Any]])
async def get_user_all_football_skills(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all football skills for a user across all their LFA Player licenses

    Returns array of licenses with skills for each LFA Player specialization
    """
    # Check permissions
    if user_id != current_user.id and current_user.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only view your own skills"
        )

    licenses = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type.like("LFA_PLAYER_%")
    ).all()

    # OPTIMIZED: Batch fetch all updaters to avoid N+1 query pattern (reduces N queries to 1)
    updater_ids = [lic.skills_updated_by for lic in licenses if lic.skills_updated_by]
    updaters = db.query(User).filter(User.id.in_(updater_ids)).all() if updater_ids else []
    updater_dict = {u.id: u for u in updaters}

    result = []
    for license in licenses:
        # OPTIMIZED: Use pre-fetched updater dictionary (no query in loop)
        updated_by_name = None
        if license.skills_updated_by:
            updater = updater_dict.get(license.skills_updated_by)
            if updater:
                updated_by_name = updater.name

        result.append({
            "license_id": license.id,
            "specialization_type": license.specialization_type,
            "current_level": license.current_level,
            "skills": license.football_skills,
            "skills_last_updated_at": license.skills_last_updated_at.isoformat() if license.skills_last_updated_at else None,
            "skills_updated_by_id": license.skills_updated_by,
            "skills_updated_by_name": updated_by_name
        })

    return result