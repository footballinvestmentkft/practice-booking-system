"""
Juggling Phase 2A — Pose Snapshot Service.

Stores and retrieves body pose keypoints captured by iOS Vision at annotation timestamps.
No ML inference runs server-side in Phase 2A; backend is storage-only.

Service invariants:
  - video_id and event_id ownership are checked before any write or read
  - contact_event must belong to the video
  - POST is idempotent: same event_id → upsert (UPDATE existing snapshot)
  - GET returns all snapshots for a video ordered by timestamp_ms
  - POSE_SNAPSHOT_ENABLED=False causes 503 at the endpoint layer, not here
"""
from __future__ import annotations

import uuid as _uuid_mod

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.juggling import (
    JugglingContactEvent,
    JugglingPoseSnapshot,
    JugglingVideo,
)
from app.schemas.juggling import PoseSnapshotCreateRequest


def _get_video_owned(video_id: str, user_id: int, db: Session) -> JugglingVideo:
    try:
        vid_uuid = _uuid_mod.UUID(video_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Video not found")
    video = db.query(JugglingVideo).filter(JugglingVideo.id == vid_uuid).first()
    if video is None or video.user_id != user_id:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


def _get_event_for_video(event_id: str, video: JugglingVideo, db: Session) -> JugglingContactEvent:
    try:
        evt_uuid = _uuid_mod.UUID(event_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Event not found")
    event = (
        db.query(JugglingContactEvent)
        .filter(
            JugglingContactEvent.id       == evt_uuid,
            JugglingContactEvent.video_id == video.id,
            JugglingContactEvent.deleted_at.is_(None),
        )
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def upsert_pose_snapshot(
    video_id: str,
    event_id: str,
    user_id:  int,
    payload:  PoseSnapshotCreateRequest,
    db:       Session,
) -> tuple[JugglingPoseSnapshot, bool]:
    """
    Create or update the pose snapshot for a contact event.

    Returns (snapshot, created) where created=True means HTTP 201, False means HTTP 200.
    Raises 404 if the video or event is not found / not owned by user_id.
    """
    video = _get_video_owned(video_id, user_id, db)
    event = _get_event_for_video(event_id, video, db)

    existing = (
        db.query(JugglingPoseSnapshot)
        .filter(JugglingPoseSnapshot.contact_event_id == event.id)
        .first()
    )

    if existing is not None:
        existing.keypoints            = payload.keypoints
        existing.model_version        = payload.model_version
        existing.capture_source       = payload.capture_source
        existing.inference_confidence = payload.inference_confidence
        existing.image_width_px       = payload.image_width_px
        existing.image_height_px      = payload.image_height_px
        db.commit()
        db.refresh(existing)
        return existing, False

    snapshot = JugglingPoseSnapshot(
        contact_event_id     = event.id,
        video_id             = video.id,
        timestamp_ms         = payload.captured_at_ms,
        keypoints            = payload.keypoints,
        model_version        = payload.model_version,
        capture_source       = payload.capture_source,
        inference_confidence = payload.inference_confidence,
        image_width_px       = payload.image_width_px,
        image_height_px      = payload.image_height_px,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot, True


def list_pose_snapshots(
    video_id: str,
    user_id:  int,
    db:       Session,
) -> list[JugglingPoseSnapshot]:
    """
    Return all pose snapshots for a video, ordered by timestamp_ms.
    Raises 404 if the video is not found or not owned by user_id.
    """
    video = _get_video_owned(video_id, user_id, db)
    return (
        db.query(JugglingPoseSnapshot)
        .filter(JugglingPoseSnapshot.video_id == video.id)
        .order_by(JugglingPoseSnapshot.timestamp_ms)
        .all()
    )
