"""
Privacy-safe frame serving — AN-3B2F PR-1B.

GET /me/ball-training/frame/{assignment_id}
  Returns a JPEG frame for the given assignment.  The frame is either a
  canonical context crop (ball centred) or the full frame, depending on
  trajectory confidence and tracking state.

  The assignment's display_mode is set on first fetch and is subsequently
  read by POST /me/ball-training/feedback to back-calculate full-frame
  corrected coordinates from a client-side crop tap.

Privacy invariants (enforced here and in frame_service):
  - No video_id, frame_ms, storage_path or owner identity in response headers
    or body.
  - Cache-Control: no-store prevents CDN/browser caching.

Access control: ADMIN + BALL_TRAINING_ALLOWED_USER_IDS allowlist (same as hub).
Feature flag: BALL_TRAINING_FRAME_ENABLED must be True (OFF by default).
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_ball_training_poc_user
from app.models.user import User
from app.services.juggling import frame_service
from app.services.juggling.feature_flag import require_juggling_enabled

router = APIRouter()
_TAG = "juggling"


async def _require_frame_enabled() -> None:
    if not settings.BALL_TRAINING_FRAME_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "Ball training frame serving is not enabled. "
                "Set BALL_TRAINING_FRAME_ENABLED=true."
            ),
        )


@router.get(
    "/me/ball-training/frame/{assignment_id}",
    response_class=StreamingResponse,
    dependencies=[
        Depends(require_juggling_enabled),
        Depends(_require_frame_enabled),
    ],
    summary="Serve a privacy-safe training frame for an assignment",
    tags=[_TAG],
)
def get_ball_training_frame(
    assignment_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_ball_training_poc_user),
) -> StreamingResponse:
    jpeg_bytes, _display_mode = frame_service.serve_assignment_frame(
        db=db,
        assignment_id=assignment_id,
        user_id=current_user.id,
    )
    return StreamingResponse(
        iter([jpeg_bytes]),
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store, max-age=0"},
    )
