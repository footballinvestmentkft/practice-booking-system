"""
User specialization operations

🎓 Specialization API Endpoints
Handles specialization selection and information for the LFA education platform
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Any, List, Dict
from pydantic import BaseModel

from .....database import get_db
from .....dependencies import get_current_user
from .....models.user import User
from .....models.specialization import SpecializationType
from .....services.specialization import SpecializationService
from .....services.player_identity_service import (
    PlayerEntitlementConflictError,
    PlayerEntitlementRequiredError,
    PlayerIdentityPolicyError,
    activate_football_player,
)

router = APIRouter()

class SpecializationResponse(BaseModel):
    code: str
    name: str
    description: str
    features: List[str]
    icon: str

class SpecializationSetRequest(BaseModel):
    specialization: str


@router.get("/", response_model=List[SpecializationResponse])
async def list_specializations(db: Session = Depends(get_db)):
    """
    Get available specializations with descriptions (HYBRID: DB + JSON)
    🎓 PUBLIC ENDPOINT - no authentication required for onboarding

    Process:
    1. Load active specializations from DB
    2. Load full content from JSON configs
    3. Return merged data
    """
    service = SpecializationService(db)
    all_specs = service.get_all_specializations()

    specializations = []
    for spec_data in all_specs:
        specializations.append(SpecializationResponse(
            code=spec_data['id'],
            name=spec_data['name'],
            description=spec_data['description'],
            features=spec_data.get('features', []),  # Will be added in next iteration
            icon=spec_data['icon']
        ))

    return specializations

@router.post("/me")
async def set_user_specialization(
    specialization_data: SpecializationSetRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """
    Set current user's specialization (HYBRID: with age/consent validation)
    🎓 CRITICAL: This is used during onboarding and profile updates

    Process:
    1. Validate specialization enum
    2. Check DB existence + is_active
    3. Validate age requirements (JSON)
    4. Validate parental consent (for LFA_COACH under 18)
    5. Update user specialization
    """
    try:
        specialization = SpecializationType(specialization_data.specialization)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid specialization. Must be one of: {[s.value for s in SpecializationType]}"
        )

    if specialization is SpecializationType.LFA_FOOTBALL_PLAYER:
        try:
            result = activate_football_player(db, user=current_user)
            db.commit()
        except PlayerEntitlementRequiredError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=exc.code,
            ) from exc
        except PlayerIdentityPolicyError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=exc.code,
            ) from exc
        except PlayerEntitlementConflictError as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=exc.code,
            ) from exc

        return {
            "message": "Specialization updated successfully",
            "user": {
                "id": current_user.id,
                "name": current_user.name,
                "email": current_user.email,
                "specialization": {
                    "code": result.license.canonical_program_id,
                    "name": "LFA Football Player",
                    "icon": "⚽",
                },
            },
            "football_assignment": {
                "season_start": result.assignment.season_start.isoformat(),
                "season_end": result.assignment.season_end.isoformat(),
                "season_base_category": result.assignment.season_base_category,
                "effective_category": result.assignment.effective_category,
                "base_participation_retained": result.assignment.base_participation_retained,
            },
        }

    # STEP 2-4: Use service to validate and enroll
    service = SpecializationService(db)

    try:
        result = service.enroll_user(current_user.id, specialization.value)

        if not result['success']:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result['message']
            )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

    # STEP 5: Update user's specialization
    old_specialization = current_user.specialization.value if current_user.specialization else None
    current_user.specialization = specialization
    db.commit()
    db.refresh(current_user)

    # 🔍 AUDIT: Log specialization change
    audit_service = AuditService(db)
    audit_service.log(
        action=AuditAction.SPECIALIZATION_SELECTED,
        user_id=current_user.id,
        resource_type="specialization",
        resource_id=None,
        details={
            "old_specialization": old_specialization,
            "new_specialization": specialization.value,
            "age": current_user.calculate_age() if current_user.date_of_birth else None,
            "has_parental_consent": current_user.parental_consent
        }
    )

    # 📝 Log for testing/debugging
    print(f"🎓 Specialization updated: {current_user.name} → {specialization.value}")

    # Get display info from JSON (HYBRID)
    loader = SpecializationConfigLoader()
    display_info = loader.get_display_info(specialization)

    return {
        "message": "Specialization updated successfully",
        "user": {
            "id": current_user.id,
            "name": current_user.name,
            "email": current_user.email,
            "specialization": {
                "code": current_user.specialization.value,
                "name": display_info.get('name', specialization.value),
                "icon": display_info.get('icon', '🎯')
            }
        }
    }

@router.get("/me")
async def get_user_specialization(
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """Get current user's specialization"""
    return {
        "user_id": current_user.id,
        "has_specialization": current_user.has_specialization,
        "specialization": {
            "code": current_user.specialization.value if current_user.specialization else None,
            "name": current_user.specialization_display,
            "icon": current_user.specialization_icon
        } if current_user.specialization else None
    }

@router.delete("/me")
async def clear_user_specialization(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
) -> Dict[str, Any]:
    """Clear user's specialization (set to NULL)"""
    current_user.specialization = None
    db.commit()
    
    print(f"🎓 Specialization cleared for: {current_user.name}")
    
    return {
        "message": "Specialization cleared successfully",
        "user_id": current_user.id
    }
