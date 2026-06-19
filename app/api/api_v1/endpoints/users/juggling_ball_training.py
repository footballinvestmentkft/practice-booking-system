"""
Global Ball Training Hub endpoints — AN-3B2F PR-1A.

GET  /me/ball-training/queue
  Return up to `limit` training tasks. Each task contains an opaque assignment_id
  (UUID4). The client never receives video_id, frame_ms, storage_path, or the
  video owner's identity.

POST /me/ball-training/feedback
  Submit a decision (confirm | no_ball) for an assignment.
  'corrected' action is deferred to PR-1B.

Access control:
  - ADMIN users always allowed.
  - Other users allowed only if listed in BALL_TRAINING_ALLOWED_USER_IDS.
  - JUGGLING_POC_ENABLED must be True (shared juggling gate).

Feature flag: BALL_FEEDBACK_ENABLED must also be True (reuses the ball feedback flag
as the training-hub gate in PR-1A; a dedicated BALL_TRAINING_HUB_ENABLED will be
introduced in PR-1B alongside BALL_TRAINING_FRAME_ENABLED).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_ball_training_poc_user
from app.models.user import User
from app.schemas.juggling import (
    BallTrainingFeedbackRequest,
    BallTrainingFeedbackResponse,
    GlobalTrainingQueueResponse,
)
from app.services.juggling import ball_training_service
from app.services.juggling.feature_flag import require_juggling_enabled

router = APIRouter()

_TAG = "juggling"


async def _require_ball_feedback_enabled() -> None:
    if not settings.BALL_FEEDBACK_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Ball training hub is not enabled. Set BALL_FEEDBACK_ENABLED=true.",
        )


@router.get(
    "/me/ball-training/queue",
    response_model=GlobalTrainingQueueResponse,
    dependencies=[
        Depends(require_juggling_enabled),
        Depends(_require_ball_feedback_enabled),
    ],
    summary="Get global ball training task queue",
    tags=[_TAG],
)
def get_ball_training_queue(
    limit: int = Query(3, ge=1, le=10, description="Maximum tasks to return"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_ball_training_poc_user),
) -> GlobalTrainingQueueResponse:
    return ball_training_service.get_global_training_queue(
        db=db,
        user_id=current_user.id,
        limit=limit,
    )


@router.post(
    "/me/ball-training/feedback",
    response_model=BallTrainingFeedbackResponse,
    status_code=201,
    dependencies=[
        Depends(require_juggling_enabled),
        Depends(_require_ball_feedback_enabled),
    ],
    summary="Submit a ball training feedback decision",
    tags=[_TAG],
)
def post_ball_training_feedback(
    body: BallTrainingFeedbackRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_ball_training_poc_user),
) -> BallTrainingFeedbackResponse:
    return ball_training_service.submit_training_feedback(
        db=db,
        user_id=current_user.id,
        req=body,
    )
