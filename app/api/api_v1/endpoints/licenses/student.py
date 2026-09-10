"""
User license operations
Advancement, dashboard, requirements
"""
from typing import Any, List, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .....database import get_db
from .....dependencies import get_current_user
from .....models.user import User
from .....models.audit_log import AuditAction
from .....services.license_service import (
    FootballEntitlementRequiresCanonicalCommand,
    LicenseService,
)
from .....services.audit_service import AuditService

router = APIRouter()

@router.get("/progression/{specialization}", response_model=List[Dict[str, Any]])
async def get_specialization_progression(
    specialization: str,
    db: Session = Depends(get_db)
):
    """
    Get complete progression path for a specialization
    
    - **specialization**: COACH, PLAYER, or INTERNSHIP
    """
    license_service = LicenseService(db)
    return license_service.get_specialization_progression_path(specialization)


@router.get("/my-licenses", response_model=List[Dict[str, Any]])
async def get_my_licenses(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all licenses for the current user
    """
    license_service = LicenseService(db)
    return license_service.get_user_licenses(current_user.id)


@router.get("/me", response_model=List[Dict[str, Any]])
async def get_my_licenses_short(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all licenses for the current user (short URL)
    """
    license_service = LicenseService(db)
    return license_service.get_user_licenses(current_user.id)


@router.get("/dashboard", response_model=Dict[str, Any])
async def get_license_dashboard(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get comprehensive license dashboard for the current user
    """
    license_service = LicenseService(db)
    return license_service.get_user_license_dashboard(current_user.id)


@router.post("/advance", response_model=Dict[str, Any])
async def advance_license(
    data: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Request license advancement (requires instructor approval for actual advancement)
    
    Request body:
    - **specialization**: COACH, PLAYER, or INTERNSHIP
    - **target_level**: Desired level number
    - **reason**: Reason for advancement request
    """
    required_fields = ['specialization', 'target_level']
    for field in required_fields:
        if field not in data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing required field: {field}"
            )
    
    license_service = LicenseService(db)
    
    # For now, allow self-advancement for testing
    # In production, this would create an advancement request
    try:
        result = license_service.advance_license(
            user_id=current_user.id,
            specialization=data['specialization'],
            target_level=data['target_level'],
            advanced_by=current_user.id,  # Would be instructor in production
            reason=data.get('reason', 'Self-advancement request'),
            requirements_met=data.get('requirements_met', 'Auto-approved for testing')
        )
    except FootballEntitlementRequiresCanonicalCommand as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="ACTIVE_FOOTBALL_ENTITLEMENT_REQUIRED",
        ) from exc

    # 🔍 AUDIT: Log license advancement
    audit_service = AuditService(db)
    audit_service.log(
        action=AuditAction.LICENSE_UPGRADE_APPROVED,
        user_id=current_user.id,
        resource_type="license",
        resource_id=None,  # License progression doesn't have individual license IDs
        details={
            "specialization": data['specialization'],
            "target_level": data['target_level'],
            "reason": data.get('reason'),
            "success": result.get('success', False)
        }
    )

    # 🏆 GAMIFICATION: Check for achievement unlocks
    if result.get('success'):
        from app.services.gamification import GamificationService
        gamification_service = GamificationService(db)
        try:
            # Check for level-up achievements
            unlocked = gamification_service.check_and_unlock_achievements(
                user_id=current_user.id,
                trigger_action="reach_level",
                context={"level": data['target_level']}
            )

            # Also check for first license achievement (if level 1)
            if data['target_level'] == 1:
                first_license_unlocked = gamification_service.check_and_unlock_achievements(
                    user_id=current_user.id,
                    trigger_action="license_earned"
                )
                unlocked.extend(first_license_unlocked)

            if unlocked:
                print(f"🎉 Unlocked {len(unlocked)} achievement(s) for user {current_user.id}")
                # Optionally add to response
                result['achievements_unlocked'] = [
                    {"code": a.code, "name": a.name, "xp": a.xp_reward}
                    for a in unlocked
                ]
        except Exception as e:
            # Don't fail advancement if achievement check fails
            print(f"⚠️  Achievement check failed: {e}")

    return result


@router.get("/requirements/{specialization}/{level}", response_model=Dict[str, Any])
async def check_advancement_requirements(
    specialization: str,
    level: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Check if user meets requirements for license advancement
    
    - **specialization**: COACH, PLAYER, or INTERNSHIP
    - **level**: Target level number
    """
    license_service = LicenseService(db)
    return license_service.get_license_requirements_check(
        current_user.id, specialization, level
    )


@router.get("/marketing/{specialization}", response_model=Dict[str, Any])
async def get_marketing_content(
    specialization: str,
    level: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """
    Get marketing content for license levels
    
    - **specialization**: COACH, PLAYER, or INTERNSHIP
    - **level**: Optional specific level, if omitted returns all levels
    """
    license_service = LicenseService(db)
    return license_service.get_marketing_content(specialization, level)
