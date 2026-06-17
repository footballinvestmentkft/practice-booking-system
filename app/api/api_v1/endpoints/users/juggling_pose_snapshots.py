"""
Juggling Phase 2A — Pose Snapshot endpoints.

POST /api/v1/users/me/juggling/videos/{video_id}/contacts/{event_id}/pose-snapshot
  Upload a Vision body pose snapshot for a contact event.
  Idempotent: same event_id → upsert (returns 201 on create, 200 on update).
  Gated: JUGGLING_POC_ENABLED + POSE_SNAPSHOT_ENABLED (both must be True).

GET /api/v1/users/me/juggling/videos/{video_id}/pose-snapshots
  Return all pose snapshots for a video, ordered by timestamp_ms.
  Gated: JUGGLING_POC_ENABLED + POSE_SNAPSHOT_ENABLED.

All endpoints:
  - require_juggling_enabled (503 when JUGGLING_POC_ENABLED=False)
  - require_pose_snapshot_enabled (503 when POSE_SNAPSHOT_ENABLED=False)
  - get_current_user (401 when unauthenticated)
  - video and event ownership checked in service layer (404 for not-found/wrong owner)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.juggling import PoseSnapshotCreateRequest, PoseSnapshotOut
from app.services.juggling import pose_snapshot_service
from app.services.juggling.feature_flag import require_juggling_enabled

router = APIRouter()

_TAG    = "juggling"
_PREFIX = "/me/juggling/videos/{video_id}/contacts/{event_id}/pose-snapshot"
_LIST   = "/me/juggling/videos/{video_id}/pose-snapshots"


async def require_pose_snapshot_enabled() -> None:
    """FastAPI dependency — raises 503 when POSE_SNAPSHOT_ENABLED=False."""
    if not settings.POSE_SNAPSHOT_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "Pose snapshot feature is not enabled on this server. "
                "Set POSE_SNAPSHOT_ENABLED=true to activate."
            ),
        )


_GUARDS = [Depends(require_juggling_enabled), Depends(require_pose_snapshot_enabled)]


# ── POST /contacts/{event_id}/pose-snapshot ───────────────────────────────────

@router.post(
    _PREFIX,
    dependencies=_GUARDS,
    summary="Upload a pose snapshot for a contact event (Phase 2A)",
    tags=[_TAG],
)
def upsert_pose_snapshot(
    video_id: str,
    event_id: str,
    body:     PoseSnapshotCreateRequest,
    response: Response,
    db:       Session      = Depends(get_db),
    current_user: User     = Depends(get_current_user),
) -> PoseSnapshotOut:
    """
    Create or update the Vision pose snapshot for a single contact event.

    - 201: new snapshot created
    - 200: existing snapshot updated (idempotent retry)
    - 404: video or event not found / not owned by current user
    - 422: keypoints missing 'body' key or invalid inference_confidence range
    - 503: JUGGLING_POC_ENABLED=False or POSE_SNAPSHOT_ENABLED=False
    """
    snapshot, created = pose_snapshot_service.upsert_pose_snapshot(
        video_id=video_id,
        event_id=event_id,
        user_id=current_user.id,
        payload=body,
        db=db,
    )
    response.status_code = 201 if created else 200
    return PoseSnapshotOut.model_validate(snapshot)


# ── GET /pose-snapshots ───────────────────────────────────────────────────────

@router.get(
    _LIST,
    response_model=list[PoseSnapshotOut],
    dependencies=_GUARDS,
    summary="List pose snapshots for a video (Phase 2A)",
    tags=[_TAG],
)
def list_pose_snapshots(
    video_id: str,
    db:       Session  = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PoseSnapshotOut]:
    """
    Returns all pose snapshots for the video, ordered by timestamp_ms.
    Empty list if no snapshots have been uploaded yet.

    - 200: list (may be empty)
    - 404: video not found / not owned by current user
    - 503: JUGGLING_POC_ENABLED=False or POSE_SNAPSHOT_ENABLED=False
    """
    snapshots = pose_snapshot_service.list_pose_snapshots(
        video_id=video_id,
        user_id=current_user.id,
        db=db,
    )
    return [PoseSnapshotOut.model_validate(s) for s in snapshots]
