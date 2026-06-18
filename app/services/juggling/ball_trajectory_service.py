"""
Ball trajectory service — dense trajectory CRUD.

No ONNX inference (that's in the Celery task).
This module: query trajectories, upsert manual seeds, status management.
"""
from __future__ import annotations

import uuid as _uuid_mod

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.juggling import JugglingBallTrajectory, JugglingVideo


MAX_WINDOW_MS = 60_000


def _get_video_owned(video_id: str, user_id: int, db: Session) -> JugglingVideo:
    try:
        vid_uuid = _uuid_mod.UUID(video_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Video not found")
    video = db.query(JugglingVideo).filter(JugglingVideo.id == vid_uuid).first()
    if video is None or video.user_id != user_id:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


def get_trajectory_window(
    video_id: str,
    user_id: int,
    from_ms: int,
    to_ms: int,
    db: Session,
) -> dict:
    """Query trajectory points in [from_ms, to_ms] for a user's video."""
    video = _get_video_owned(video_id, user_id, db)

    if to_ms - from_ms > MAX_WINDOW_MS:
        raise HTTPException(
            status_code=422,
            detail=f"Window too large. Max {MAX_WINDOW_MS}ms ({MAX_WINDOW_MS // 1000}s).",
        )

    status = video.ball_trajectory_status
    if status is None:
        raise HTTPException(status_code=404, detail="No trajectory data for this video")

    points = (
        db.query(JugglingBallTrajectory)
        .filter(
            JugglingBallTrajectory.video_id == video.id,
            JugglingBallTrajectory.frame_ms >= from_ms,
            JugglingBallTrajectory.frame_ms <= to_ms,
        )
        .order_by(JugglingBallTrajectory.frame_ms)
        .all()
    )

    return {"status": status, "points": points}


def upsert_manual_seed(
    video_id: str,
    user_id: int,
    frame_ms: int,
    ball_x: float,
    ball_y: float,
    db: Session,
) -> tuple[JugglingBallTrajectory, bool]:
    """UPSERT manual seed point. Returns (model, created)."""
    video = _get_video_owned(video_id, user_id, db)

    existing = (
        db.query(JugglingBallTrajectory)
        .filter(
            JugglingBallTrajectory.video_id == video.id,
            JugglingBallTrajectory.frame_ms == frame_ms,
        )
        .first()
    )

    if existing:
        existing.ball_x = ball_x
        existing.ball_y = ball_y
        existing.confidence = None
        existing.is_manual = True
        existing.tracking_state = "manual_seed"
        existing.model_version = None
        db.commit()
        db.refresh(existing)
        return existing, False

    point = JugglingBallTrajectory(
        video_id=video.id,
        frame_ms=frame_ms,
        ball_x=ball_x,
        ball_y=ball_y,
        confidence=None,
        is_manual=True,
        tracking_state="manual_seed",
    )
    db.add(point)
    db.commit()
    db.refresh(point)
    return point, True


def set_trajectory_status(video_id: str, status: str, db: Session) -> None:
    vid_uuid = _uuid_mod.UUID(video_id)
    db.query(JugglingVideo).filter(JugglingVideo.id == vid_uuid).update(
        {"ball_trajectory_status": status}
    )
    db.commit()
