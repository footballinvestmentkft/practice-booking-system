"""
Ball Detection Service — Phase 2B (AN-3B2B-1).

Storage-only in this layer: upsert/get ball detections.
ONNX inference is in the Celery task, not here.
No skill pipeline imports — measurement data only.
"""
from __future__ import annotations

import uuid as _uuid_mod

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.juggling import (
    JugglingBallDetection,
    JugglingContactEvent,
    JugglingVideo,
)
from app.schemas.juggling import BallDetectionManualRequest


def _get_video_owned(video_id: str, user_id: int, db: Session) -> JugglingVideo:
    try:
        vid_uuid = _uuid_mod.UUID(video_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Video not found")
    video = db.query(JugglingVideo).filter(JugglingVideo.id == vid_uuid).first()
    if video is None or video.user_id != user_id:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


def _get_event_for_video(
    event_id: str, video: JugglingVideo, db: Session,
) -> JugglingContactEvent:
    try:
        evt_uuid = _uuid_mod.UUID(event_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Event not found")
    event = (
        db.query(JugglingContactEvent)
        .filter(
            JugglingContactEvent.id == evt_uuid,
            JugglingContactEvent.video_id == video.id,
            JugglingContactEvent.deleted_at.is_(None),
        )
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def upsert_manual_detection(
    video_id: str,
    event_id: str,
    user_id: int,
    req: BallDetectionManualRequest,
    db: Session,
) -> tuple[JugglingBallDetection, bool]:
    """Manual override. Returns (detection, created)."""
    video = _get_video_owned(video_id, user_id, db)
    event = _get_event_for_video(event_id, video, db)

    existing = (
        db.query(JugglingBallDetection)
        .filter(JugglingBallDetection.contact_event_id == event.id)
        .first()
    )
    if existing:
        # AN-3B2C-1 Opció A: freeze the original automatic coordinates on the
        # FIRST manual override only (auto_ball_x is None signals not yet frozen).
        # Any non-manual source is considered "automatic" (future model names included).
        if existing.detection_source != "manual" and existing.auto_ball_x is None:
            existing.auto_ball_x = existing.ball_x
            existing.auto_ball_y = existing.ball_y
        existing.detection_source  = "manual"
        existing.ball_x            = req.ball_x
        existing.ball_y            = req.ball_y
        existing.confidence        = req.confidence
        existing.no_ball_detected  = req.no_ball_detected
        existing.model_version     = None
        existing.image_width_px    = None
        existing.image_height_px   = None
        db.commit()
        db.refresh(existing)
        return existing, False

    # Manual-first: auto pipeline never ran for this event — auto coords stay NULL.
    detection = JugglingBallDetection(
        contact_event_id=event.id,
        video_id=video.id,
        detection_source="manual",
        ball_x=req.ball_x,
        ball_y=req.ball_y,
        confidence=req.confidence,
        no_ball_detected=req.no_ball_detected,
        excluded_from_training=True,
        auto_ball_x=None,
        auto_ball_y=None,
    )
    db.add(detection)
    db.commit()
    db.refresh(detection)
    return detection, True


def get_detection(
    video_id: str,
    event_id: str,
    user_id: int,
    db: Session,
) -> JugglingBallDetection:
    video = _get_video_owned(video_id, user_id, db)
    event = _get_event_for_video(event_id, video, db)

    detection = (
        db.query(JugglingBallDetection)
        .filter(JugglingBallDetection.contact_event_id == event.id)
        .first()
    )
    if detection is None:
        raise HTTPException(status_code=404, detail="No ball detection for this event")
    return detection
