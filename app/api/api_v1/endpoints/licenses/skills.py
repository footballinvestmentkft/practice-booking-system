"""
Football skills assessment endpoints
"""
import hashlib
import json
from typing import Any, List, Dict
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .....database import get_db
from .....dependencies import get_current_user
from .....models.user import User, UserRole
from .....models.license import UserLicense
from .....models.audit_log import AuditAction
from .....services.audit_service import AuditService
from .....services.canonical_policy import CanonicalProgram, resolve_license_program
from .....services.player_progression_service import PlayerProgressionError, PlayerProgressionService

router = APIRouter()

def _is_canonical_player_license(license_row: UserLicense) -> bool:
    resolution = resolve_license_program(
        license_row.canonical_program_id, license_row.specialization_type
    )
    return resolution.usable and resolution.canonical_program is CanonicalProgram.LFA_FOOTBALL_PLAYER


def _is_readable_player_license(license_row: UserLicense) -> bool:
    # Historical category-named licenses remain readable, but cannot be written.
    return _is_canonical_player_license(license_row) or str(
        license_row.specialization_type
    ).startswith("LFA_PLAYER_")


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
    if not _is_readable_player_license(license):
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
    """Compatibility command translated into one canonical PC5 outcome."""
    if current_user.role not in [UserRole.INSTRUCTOR, UserRole.ADMIN]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only instructors can update football skills")
    license_row = db.query(UserLicense).filter(UserLicense.id == license_id).first()
    if not license_row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License not found")
    if not _is_canonical_player_license(license_row):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Canonical Football Player entitlement required")
    required_skills = ["heading", "shooting", "crossing", "passing", "dribbling", "ball_control"]
    for skill in required_skills:
        value = skills_data.get(skill)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 or value > 100:
            detail = f"Missing required skill: {skill}" if skill not in skills_data else f"Skill '{skill}' must be between 0 and 100"
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    skills = {key: round(float(skills_data[key]), 1) for key in required_skills}
    payload_hash = hashlib.sha256(json.dumps(skills, sort_keys=True).encode("utf-8")).hexdigest()
    try:
        outcome = PlayerProgressionService(db).apply_outcome(
            player=license_row.user, source_player_user_id=license_row.user_id,
            source_type="LEGACY_INSTRUCTOR_SKILL_UPDATE",
            source_id=f"{license_id}:{current_user.id}:{payload_hash}",
            idempotency_key=f"legacy-skill-update:{license_id}:{current_user.id}:{payload_hash}",
            actor=current_user, skill_updates=skills,
            provenance={"api": "PUT /licenses/{license_id}/football-skills", "payload_hash": payload_hash},
            context={"notes_present": bool(skills_data.get("instructor_notes"))},
        )
        if "instructor_notes" in skills_data:
            license_row.instructor_notes = skills_data["instructor_notes"]
        AuditService(db).log(
            action=AuditAction.USER_UPDATED, user_id=current_user.id,
            resource_type="player_progression_outcome", resource_id=license_id,
            details={"student_id": license_row.user_id, "outcome_id": str(outcome.id), "skills": skills},
        )
        db.refresh(license_row)
    except PlayerProgressionError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.code) from exc
    return {
        "success": True, "message": "Football skills applied through canonical Player progression",
        "license_id": license_id, "skills": skills,
        "updated_at": license_row.skills_last_updated_at.isoformat() if license_row.skills_last_updated_at else None, "updated_by": current_user.name,
        "progression_outcome_id": str(outcome.id),
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

    licenses = [
        row for row in db.query(UserLicense).filter(UserLicense.user_id == user_id).all()
        if _is_readable_player_license(row)
    ]

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