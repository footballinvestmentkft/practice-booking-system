"""
Ball Detection endpoints — Phase 2B (AN-3B2B-1).

POST /users/me/juggling/videos/{video_id}/contacts/{event_id}/ball-detection
  Manual ball position override. Idempotent: upsert (201 create / 200 update).

GET  /users/me/juggling/videos/{video_id}/contacts/{event_id}/ball-detection
  Return the ball detection for a contact event. 404 if none.

Gated: JUGGLING_POC_ENABLED + BALL_DETECTION_ENABLED (both must be True).
No skill pipeline interaction — measurement data only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.juggling import BallDetectionManualRequest, BallDetectionOut
from app.services.juggling import ball_detection_service
from app.services.juggling.feature_flag import require_juggling_enabled

router = APIRouter()

_TAG = "juggling"
_PREFIX = "/me/juggling/videos/{video_id}/contacts/{event_id}/ball-detection"


async def require_ball_detection_enabled() -> None:
    if not settings.BALL_DETECTION_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Ball detection is not enabled. Set BALL_DETECTION_ENABLED=true.",
        )


@router.post(
    _PREFIX,
    response_model=BallDetectionOut,
    dependencies=[Depends(require_juggling_enabled), Depends(require_ball_detection_enabled)],
    summary="Manual ball position override",
    tags=[_TAG],
)
def post_ball_detection(
    video_id: str,
    event_id: str,
    body: BallDetectionManualRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BallDetectionOut:
    detection, created = ball_detection_service.upsert_manual_detection(
        video_id=video_id,
        event_id=event_id,
        user_id=current_user.id,
        req=body,
        db=db,
    )
    if created:
        response.status_code = 201
    return BallDetectionOut.model_validate(detection)


@router.get(
    _PREFIX,
    response_model=BallDetectionOut,
    dependencies=[Depends(require_juggling_enabled), Depends(require_ball_detection_enabled)],
    summary="Get ball detection for a contact event",
    tags=[_TAG],
)
def get_ball_detection(
    video_id: str,
    event_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BallDetectionOut:
    detection = ball_detection_service.get_detection(
        video_id=video_id,
        event_id=event_id,
        user_id=current_user.id,
        db=db,
    )
    return BallDetectionOut.model_validate(detection)
