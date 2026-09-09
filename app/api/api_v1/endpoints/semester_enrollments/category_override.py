"""HTTP adapter for the canonical football category movement command."""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .....database import get_db
from .....dependencies import get_current_user_web
from .....models.user import User
from .....services.football_category_movement_service import (
    FootballMovementAuthorizationError,
    FootballMovementConcurrencyError,
    FootballMovementError,
    FootballMovementReplayConflict,
    move_football_category,
)
from .schemas import CategoryOverride

router = APIRouter()


@router.post("/{enrollment_id}/override-category")
async def override_age_category(
    request: Request,
    enrollment_id: int,
    override: CategoryOverride,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_web),
) -> Dict[str, Any]:
    try:
        result = move_football_category(
            db,
            enrollment_id=enrollment_id,
            actor=current_user,
            target_category=override.age_category,
            expected_version=override.expected_version,
            base_participation_retained=override.base_participation_retained,
            reason=override.reason,
            idempotency_key=override.idempotency_key,
            source="API_V1_CATEGORY_OVERRIDE",
            context={"request_path": str(request.url.path)},
        )
        db.commit()
        db.refresh(result.assignment)
    except FootballMovementAuthorizationError as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=exc.code) from exc
    except FootballMovementConcurrencyError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.code) from exc
    except FootballMovementReplayConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=exc.code) from exc
    except FootballMovementError as exc:
        db.rollback()
        status_code = 404 if str(exc) == "Enrollment not found" else 400
        raise HTTPException(status_code=status_code, detail=exc.code) from exc

    return {
        "success": True,
        "enrollment_id": enrollment_id,
        "season_base_category": result.assignment.season_base_category,
        "effective_category": result.assignment.effective_category,
        "age_category": result.assignment.effective_category,
        "base_participation_retained": result.assignment.base_participation_retained,
        "version": result.assignment.version,
        "movement_event_id": result.event.id,
        "replayed": not result.created,
    }
