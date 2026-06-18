"""
Ball feedback service — AN-3B2D-B0.

submit_feedback(): persist one user feedback record.
get_feedback_queue(): return prioritized uncertain frames for a video.

B0 scope:
  - No reliability score updates (B2).
  - No consensus/majority vote compute (B2).
  - No credit grants (B2).
  - No spam detection (B2).
  - training_eligible is never set True here.
"""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.juggling import (
    JugglingBallFeedback,
    JugglingBallTrajectory,
    JugglingVideo,
    UserAnnotationReliability,
)
from app.schemas.juggling import BallFeedbackQueueItem, BallFeedbackRequest


def _get_user_reliability(db: Session, user_id: int) -> float:
    """Return user's ball annotation reliability score, lazy-creating at 0.5."""
    rel = db.get(UserAnnotationReliability, user_id)
    if rel is None:
        rel = UserAnnotationReliability(user_id=user_id)
        db.add(rel)
        db.flush()
    return rel.ball_annotation_reliability


def _resolve_trajectory_point_id(
    db: Session,
    video_id: str,
    frame_ms: int,
) -> Optional[str]:
    """Return trajectory point UUID for (video_id, frame_ms) or None."""
    row = db.execute(
        select(JugglingBallTrajectory.id).where(
            JugglingBallTrajectory.video_id == video_id,
            JugglingBallTrajectory.frame_ms == frame_ms,
        )
    ).scalar_one_or_none()
    return row


def submit_feedback(
    db: Session,
    video_id: str,
    user_id: int,
    req: BallFeedbackRequest,
) -> JugglingBallFeedback:
    """
    Persist one user feedback record.

    Raises:
        404 if video_id not found or gdpr_deleted.
        409 if user already submitted feedback for this video+frame.
    """
    # 1. Video existence check
    video = db.execute(
        select(JugglingVideo).where(JugglingVideo.id == video_id)
    ).scalar_one_or_none()
    if video is None or video.status == "gdpr_deleted":
        raise HTTPException(status_code=404, detail="Video not found.")

    # 2. Duplicate check (before hitting DB UNIQUE constraint)
    existing = db.execute(
        select(JugglingBallFeedback).where(
            JugglingBallFeedback.user_id == user_id,
            JugglingBallFeedback.video_id == video_id,
            JugglingBallFeedback.frame_ms == req.frame_ms,
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="Feedback already submitted for this frame.",
        )

    # 3. Reliability score (lazy upsert at 0.5)
    reliability = _get_user_reliability(db, user_id)

    # 4. Trajectory point FK (optional)
    traj_id = _resolve_trajectory_point_id(db, video_id, req.frame_ms)

    # 5. Persist
    record = JugglingBallFeedback(
        video_id=video_id,
        frame_ms=req.frame_ms,
        trajectory_point_id=traj_id,
        user_id=user_id,
        decision=req.decision,
        corrected_x=req.corrected_x,
        corrected_y=req.corrected_y,
        correction_method=req.correction_method,
        model_predicted_x=req.model_predicted_x,
        model_predicted_y=req.model_predicted_y,
        model_confidence=req.model_confidence,
        model_tracking_state=req.model_tracking_state,
        user_reliability_at_submit=reliability,
        approval_state="pending",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def get_feedback_queue(
    db: Session,
    video_id: str,
    user_id: int,
    limit: int = 5,
) -> list[BallFeedbackQueueItem]:
    """
    Return prioritized feedback queue for uncertain frames the user hasn't reviewed.

    Priority score (B0 simplified):
      score = (1.0 - confidence) * 0.60   for detected/predicted frames
      score = 1.0 * 0.60                  for lost frames (confidence=None)
      score -= 0.10 * min(feedback_count, 3)

    Only frames with < 3 total feedbacks are included.
    Frames already reviewed by this user are excluded.
    """
    video = db.execute(
        select(JugglingVideo).where(JugglingVideo.id == video_id)
    ).scalar_one_or_none()
    if video is None or video.status == "gdpr_deleted":
        raise HTTPException(status_code=404, detail="Video not found.")

    # Frames reviewed by this user
    user_reviewed = set(
        db.execute(
            select(JugglingBallFeedback.frame_ms).where(
                JugglingBallFeedback.video_id == video_id,
                JugglingBallFeedback.user_id == user_id,
            )
        ).scalars().all()
    )

    # Total feedback count per frame (all users)
    count_rows = db.execute(
        select(
            JugglingBallFeedback.frame_ms,
            func.count(JugglingBallFeedback.id).label("cnt"),
        )
        .where(JugglingBallFeedback.video_id == video_id)
        .group_by(JugglingBallFeedback.frame_ms)
    ).all()
    feedback_counts: dict[int, int] = {r.frame_ms: r.cnt for r in count_rows}

    # Trajectory points for this video
    trajectory_rows = db.execute(
        select(JugglingBallTrajectory).where(
            JugglingBallTrajectory.video_id == video_id
        )
    ).scalars().all()

    items: list[BallFeedbackQueueItem] = []
    for pt in trajectory_rows:
        if pt.frame_ms in user_reviewed:
            continue
        fb_count = feedback_counts.get(pt.frame_ms, 0)
        if fb_count >= 3:
            continue

        # Priority score
        if pt.tracking_state == "lost" or pt.confidence is None:
            base = 0.60
        else:
            base = (1.0 - pt.confidence) * 0.60
        score = base - 0.10 * min(fb_count, 3)

        items.append(BallFeedbackQueueItem(
            frame_ms=pt.frame_ms,
            priority_score=round(score, 4),
            model_predicted_x=pt.ball_x,
            model_predicted_y=pt.ball_y,
            model_confidence=pt.confidence,
            model_tracking_state=pt.tracking_state,
            existing_feedback_count=fb_count,
        ))

    items.sort(key=lambda x: x.priority_score, reverse=True)
    return items[:limit]
