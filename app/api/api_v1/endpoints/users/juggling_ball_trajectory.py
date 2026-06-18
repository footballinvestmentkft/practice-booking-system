"""
Dense ball trajectory endpoints — AN-3B2D-1.

GET  /users/me/juggling/videos/{video_id}/ball-trajectory
  Windowed trajectory query. Max 60s window (600 points at 10 FPS).

POST /users/me/juggling/videos/{video_id}/ball-trajectory/manual-seed
  Manual ball position seed. UPSERT: 201 create / 200 update.

Gated: JUGGLING_POC_ENABLED + BALL_TRAJECTORY_ENABLED.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.juggling import (
    BallTrajectoryManualSeedOut,
    BallTrajectoryManualSeedRequest,
    BallTrajectoryPointOut,
    BallTrajectoryResponse,
)
from app.services.juggling import ball_trajectory_service
from app.services.juggling.feature_flag import require_juggling_enabled

router = APIRouter()

_TAG = "juggling"
_PREFIX = "/me/juggling/videos/{video_id}/ball-trajectory"


async def require_ball_trajectory_enabled() -> None:
    if not settings.BALL_TRAJECTORY_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Ball trajectory is not enabled. Set BALL_TRAJECTORY_ENABLED=true.",
        )


@router.get(
    _PREFIX,
    response_model=BallTrajectoryResponse,
    dependencies=[Depends(require_juggling_enabled), Depends(require_ball_trajectory_enabled)],
    summary="Get dense ball trajectory window",
    tags=[_TAG],
)
def get_ball_trajectory(
    video_id: str,
    from_ms: int = Query(0, ge=0),
    to_ms: int = Query(60_000, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BallTrajectoryResponse:
    result = ball_trajectory_service.get_trajectory_window(
        video_id=video_id,
        user_id=current_user.id,
        from_ms=from_ms,
        to_ms=to_ms,
        db=db,
    )
    return BallTrajectoryResponse(
        status=result["status"],
        points=[BallTrajectoryPointOut.model_validate(p) for p in result["points"]],
    )


@router.post(
    _PREFIX + "/manual-seed",
    response_model=BallTrajectoryManualSeedOut,
    dependencies=[Depends(require_juggling_enabled), Depends(require_ball_trajectory_enabled)],
    summary="Manual ball position seed",
    tags=[_TAG],
)
def post_manual_seed(
    video_id: str,
    body: BallTrajectoryManualSeedRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BallTrajectoryManualSeedOut:
    point, created = ball_trajectory_service.upsert_manual_seed(
        video_id=video_id,
        user_id=current_user.id,
        frame_ms=body.frame_ms,
        ball_x=body.ball_x,
        ball_y=body.ball_y,
        db=db,
    )
    if created:
        response.status_code = 201
    return BallTrajectoryManualSeedOut.model_validate(point)
