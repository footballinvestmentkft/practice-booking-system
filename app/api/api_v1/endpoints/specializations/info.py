"""
Specialization information and metadata

🎓 Specialization API Endpoints
Handles specialization selection and information for the LFA education platform
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import Any, List, Dict
from pydantic import BaseModel

from .....database import get_db
from .....models.specialization import SpecializationType
from .....services.specialization import SpecializationService

router = APIRouter()

class SpecializationResponse(BaseModel):
    code: str
    name: str
    description: str
    features: List[str]
    icon: str

class SpecializationSetRequest(BaseModel):
    specialization: str


@router.get("/info/{specialization_code}")
async def get_specialization_info(
    specialization_code: str,
    db: Session = Depends(get_db)
) -> Dict[str, Any]:
    """Get detailed information about a specific specialization (HYBRID: DB + JSON)"""
    try:
        specialization = SpecializationType(specialization_code.upper())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Specialization '{specialization_code}' not found"
        )

    # STEP 2: Check DB existence + is_active (HYBRID)
    service = SpecializationService(db)
    if not service.validate_specialization_exists(specialization.value):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Specialization '{specialization_code}' is not active"
        )

    # STEP 3: Load full info from JSON (Source of Truth)
    loader = SpecializationConfigLoader()
    display_info = loader.get_display_info(specialization)

    return {
        "code": specialization.value,
        "name": display_info.get('name', specialization.value),
        "description": display_info.get('description', ''),
        "features": display_info.get('features', []),  # TODO: Add to JSON configs
        "icon": display_info.get('icon', '🎯'),
        "min_age": display_info.get('min_age', 0),
        "color_theme": display_info.get('color_theme', '#000000')
    }


# ========================================
# NEW: SPECIALIZATION PROGRESS & LEVELS
# ========================================

@router.get("/levels/all")
async def get_all_specializations_with_levels(
    db: Session = Depends(get_db)
):
    """Get all specializations with their level systems"""

    service = SpecializationService(db)

    try:
        specializations = service.get_all_specializations()

        # Add levels to each specialization
        for spec in specializations:
            spec['levels'] = service.get_all_levels(spec['id'])

        return {
            'success': True,
            'data': specializations
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch specializations: {str(e)}")


@router.get("/levels/{specialization_id}")
async def get_specialization_levels(
    specialization_id: str,
    db: Session = Depends(get_db)
):
    """Get all levels for a specific specialization"""

    service = SpecializationService(db)

    try:
        levels = service.get_all_levels(specialization_id)

        if not levels:
            raise HTTPException(status_code=404, detail=f"Specialization '{specialization_id}' not found")

        return {
            'success': True,
            'data': levels,
            'count': len(levels)
        }
    except HTTPException:
        raise
    except ValueError:
        # Invalid or ambiguous identifiers are client input and must fail
        # closed without exposing policy internals as a server error.
        raise HTTPException(status_code=400, detail="Invalid specialization identifier")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch levels: {str(e)}")

