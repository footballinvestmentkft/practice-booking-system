"""
LFA Player license management

Provides REST API for LFA Player license management:
- License listing (admin)
- License detail (current user)

Note: License creation is handled via the specialization unlock flow
      at POST /onboarding/specialization.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, Field

from .....database import get_db
from .....dependencies import get_current_user
from .....models.user import User, UserRole

router = APIRouter()


# ==================== Pydantic Schemas ====================

class LicenseCreate(BaseModel):
    """Request to create LFA Player license — kept for schema compat, endpoint is deprecated"""
    age_group: str = Field(..., description="Age group: PRE, YOUTH, AMATEUR, PRO")
    initial_credits: int = Field(0, ge=0)


class LicenseResponse(BaseModel):
    """LFA Player license response"""
    id: int
    user_id: int
    specialization_type: str
    current_level: int
    is_active: bool
    onboarding_completed: bool
    started_at: Optional[str] = None
    expires_at: Optional[str] = None  # ISO 8601 — null means perpetual (no expiry set)


# ==================== Endpoints ====================


@router.get("/licenses", response_model=List[LicenseResponse])
def list_all_licenses(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get all LFA Player licenses (Admin only)

    Returns a list of all active LFA_FOOTBALL_PLAYER licenses in the system.
    Skills are stored in UserLicense.football_skills JSONB (44 skills, baseline 60.0).
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admin can view all licenses"
        )

    from app.models.license import UserLicense
    licenses = db.query(UserLicense).filter(
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).order_by(UserLicense.id.desc()).all()

    return [
        LicenseResponse(
            id=lic.id,
            user_id=lic.user_id,
            specialization_type=lic.specialization_type,
            current_level=lic.current_level,
            is_active=lic.is_active,
            onboarding_completed=lic.onboarding_completed,
            started_at=lic.started_at.isoformat() if lic.started_at else None,
            expires_at=lic.expires_at.isoformat() if lic.expires_at else None,
        )
        for lic in licenses
    ]


@router.post("/licenses", status_code=status.HTTP_410_GONE)
def create_license(
    data: LicenseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Create a new LFA Player license — **DEPRECATED (410 Gone)**

    License creation is now handled via the specialization unlock flow.
    Use POST /onboarding/specialization to unlock LFA Football Player.
    """
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="License creation via this endpoint is deprecated. Use POST /onboarding/specialization instead."
    )


@router.get("/licenses/me", response_model=LicenseResponse)
def get_my_license(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get the current user's active LFA Player license"""
    from app.models.license import UserLicense
    lfa_license = db.query(UserLicense).filter(
        UserLicense.user_id == current_user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()

    if not lfa_license:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active LFA Player license found"
        )

    return LicenseResponse(
        id=lfa_license.id,
        user_id=lfa_license.user_id,
        specialization_type=lfa_license.specialization_type,
        current_level=lfa_license.current_level,
        is_active=lfa_license.is_active,
        onboarding_completed=lfa_license.onboarding_completed,
        started_at=lfa_license.started_at.isoformat() if lfa_license.started_at else None,
        expires_at=lfa_license.expires_at.isoformat() if lfa_license.expires_at else None,
    )
