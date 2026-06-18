"""
Ball feedback endpoints — AN-3B2D-B0.

POST /users/me/juggling/videos/{video_id}/ball-feedback
  Submit one user feedback record for a single trajectory frame.
  Returns 201 with BallFeedbackOut on success.
  Returns 409 if user already submitted for this video+frame.
  Returns 404 if video not found or gdpr_deleted.

GET  /users/me/juggling/videos/{video_id}/ball-feedback/queue
  Return prioritized list of frames needing user validation.
  Excludes frames user already reviewed. Excludes frames with ≥3 feedbacks.
  Priority: uncertain + lost frames first.

Gated: JUGGLING_POC_ENABLED + BALL_FEEDBACK_ENABLED (both must be True).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.juggling import BallFeedbackOut, BallFeedbackQueueResponse, BallFeedbackRequest
from app.services.juggling import ball_feedback_service
from app.services.juggling.feature_flag import require_juggling_enabled

router = APIRouter()

_TAG = "juggling"
_PREFIX = "/me/juggling/videos/{video_id}/ball-feedback"


async def require_ball_feedback_enabled() -> None:
    if not settings.BALL_FEEDBACK_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Ball feedback is not enabled. Set BALL_FEEDBACK_ENABLED=true.",
        )


@router.post(
    _PREFIX,
    response_model=BallFeedbackOut,
    status_code=201,
    dependencies=[Depends(require_juggling_enabled), Depends(require_ball_feedback_enabled)],
    summary="Submit ball detection feedback for a trajectory frame",
    tags=[_TAG],
)
def post_ball_feedback(
    video_id: str,
    body: BallFeedbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BallFeedbackOut:
    record = ball_feedback_service.submit_feedback(
        db=db,
        video_id=video_id,
        user_id=current_user.id,
        req=body,
    )
    return BallFeedbackOut.model_validate(record)


@router.get(
    _PREFIX + "/queue",
    response_model=BallFeedbackQueueResponse,
    dependencies=[Depends(require_juggling_enabled), Depends(require_ball_feedback_enabled)],
    summary="Get prioritized feedback queue for a video",
    tags=[_TAG],
)
def get_feedback_queue(
    video_id: str,
    limit: int = Query(5, ge=1, le=20),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BallFeedbackQueueResponse:
    items = ball_feedback_service.get_feedback_queue(
        db=db,
        video_id=video_id,
        user_id=current_user.id,
        limit=limit,
    )
    return BallFeedbackQueueResponse(
        video_id=video_id,
        queue_items=items,
        total=len(items),
        max_per_session=3,
    )
