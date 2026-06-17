"""
Admin ball detection trigger — Phase 2B (AN-3B2B-2).

POST /admin/juggling/videos/{video_id}/trigger-ball-detection
  Dispatch ball detection for all confirmed events in a video.
  Admin-only. Manual trigger — not automatic.

Gated: JUGGLING_POC_ENABLED + BALL_DETECTION_ENABLED.
No skill pipeline interaction — measurement data only.
"""
from __future__ import annotations

import uuid as _uuid_mod

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.dependencies import get_current_admin_user
from app.models.juggling import (
    JugglingBallDetection,
    JugglingContactEvent,
    JugglingVideo,
)
from app.models.user import User
from app.schemas.juggling import BallDetectionTriggerResult
from app.services.juggling.analysis_model_registry import get_model_config
from app.services.juggling.feature_flag import require_juggling_enabled

router = APIRouter()


async def _require_ball_detection_enabled() -> None:
    if not settings.BALL_DETECTION_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Ball detection is not enabled. Set BALL_DETECTION_ENABLED=true.",
        )


@router.post(
    "/videos/{video_id}/trigger-ball-detection",
    response_model=BallDetectionTriggerResult,
    dependencies=[Depends(require_juggling_enabled), Depends(_require_ball_detection_enabled)],
    summary="Trigger ball detection for confirmed events (admin only)",
    tags=["admin", "juggling"],
)
def trigger_ball_detection(
    video_id: str,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_user),
) -> BallDetectionTriggerResult:
    try:
        vid_uuid = _uuid_mod.UUID(video_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Video not found")

    video = db.query(JugglingVideo).filter(JugglingVideo.id == vid_uuid).first()
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")

    config = get_model_config(video.training_video_type)

    confirmed_events = (
        db.query(JugglingContactEvent)
        .filter(
            JugglingContactEvent.video_id == vid_uuid,
            JugglingContactEvent.annotation_review_status == "confirmed",
            JugglingContactEvent.deleted_at.is_(None),
        )
        .all()
    )

    existing_event_ids = set(
        row[0] for row in
        db.query(JugglingBallDetection.contact_event_id)
        .filter(JugglingBallDetection.video_id == vid_uuid)
        .all()
    )

    skipped_reasons: list[str] = []
    queued = 0

    from app.tasks.juggling_analysis_task import detect_ball_for_event

    for event in confirmed_events:
        if event.id in existing_event_ids:
            skipped_reasons.append(f"event {event.id}: detection already exists")
            continue
        detect_ball_for_event.delay(
            str(video.id),
            str(event.id),
            training_video_type=video.training_video_type,
        )
        queued += 1

    not_confirmed = (
        db.query(JugglingContactEvent)
        .filter(
            JugglingContactEvent.video_id == vid_uuid,
            JugglingContactEvent.annotation_review_status != "confirmed",
            JugglingContactEvent.deleted_at.is_(None),
        )
        .count()
    )
    if not_confirmed > 0:
        skipped_reasons.append(f"{not_confirmed} events not in 'confirmed' status")

    return BallDetectionTriggerResult(
        video_id=video.id,
        training_video_type=video.training_video_type,
        model_used=config.model_version,
        events_queued=queued,
        events_skipped=len(confirmed_events) - queued + not_confirmed,
        skipped_reasons=skipped_reasons,
    )
