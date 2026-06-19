"""
Global Ball Training Hub service — AN-3B2F PR-1A.

get_global_training_queue():
  Selects uncertain trajectory frames from consented, non-own videos that the
  requesting user has not yet reviewed. Issues server-side BallTrainingAssignment
  rows (opaque UUID4) so the client never sees video_id or frame_ms.

  Idempotency: if a non-expired, unconsumed assignment already exists for a
  (user, video, frame) combination, it is returned as-is. A partial unique index
  (uix_bta_active_per_user_video_frame) enforces this at the DB level for concurrent
  requests; a savepoint-guarded INSERT handles any residual race.

  Sweep step: expired-pending assignments are marked consumed before new ones are
  created, so the partial unique index is never stale.

submit_training_feedback():
  Validates and consumes an assignment, inserts a JugglingBallFeedback row, and
  dispatches compute_frame_consensus. All mutations happen in one atomic transaction:
  feedback INSERT + assignment consumed_at are committed together or not at all.

  Locking order (deadlock-free, always acquired in this order):
    1. BallTrainingAssignment    FOR UPDATE  (binding, expiry, consumed check)
    2. JugglingBallTrajectory    FOR UPDATE  (3-vote capacity recount)
  Consent is checked after the trajectory lock, before the insert.

  Error mapping:
    404  — assignment not found or cross-user (same response, no info-leak)
    403  — video not found / training_consent revoked
    409  — already submitted (consumed_at non-NULL) or task closed (≥3 feedbacks)
    410  — assignment expired
"""
from __future__ import annotations

import hashlib
import uuid as _uuid_mod
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import HTTPException
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.config import settings
from app.models.juggling import (
    BallTrainingAssignment,
    JugglingBallFeedback,
    JugglingBallTrajectory,
    JugglingConsent,
    JugglingVideo,
    UserAnnotationReliability,
)
from app.schemas.juggling import (
    BallTrainingFeedbackRequest,
    BallTrainingFeedbackResponse,
    GlobalTrainingQueueItem,
    GlobalTrainingQueueResponse,
)
from app.services.juggling.coordinate_transform import (
    canonical_crop_box,
    clamp_unit,
    tap_to_full_frame,
)

_MAX_FEEDBACKS_PER_FRAME = 3


def _get_user_reliability(db: Session, user_id: int) -> float:
    rel = db.get(UserAnnotationReliability, user_id)
    if rel is None:
        rel = UserAnnotationReliability(user_id=user_id)
        db.add(rel)
        db.flush()
    return rel.ball_annotation_reliability


def _sweep_expired_assignments(db: Session, user_id: int, now: datetime) -> None:
    """Mark expired-pending assignments as consumed (consumed_at = expires_at).

    This keeps the partial unique index (uix_bta_active_per_user_video_frame)
    accurate: only truly-active (unconsumed) assignments are in the index, so
    re-assignment after expiry is always possible.
    """
    db.execute(
        update(BallTrainingAssignment)
        .where(
            BallTrainingAssignment.user_id == user_id,
            BallTrainingAssignment.consumed_at.is_(None),
            BallTrainingAssignment.expires_at <= now,
        )
        .values(consumed_at=BallTrainingAssignment.expires_at)
    )


def _get_or_create_assignment(
    db: Session,
    user_id: int,
    video_id: _uuid_mod.UUID,
    frame_ms: int,
    expires_at: datetime,
    now: datetime,
) -> BallTrainingAssignment:
    """Return an existing active assignment or create a new one.

    An advisory lock keyed on (user_id, video_id, frame_ms) serialises concurrent
    queue requests for the same combination. pg_advisory_xact_lock is released when
    the outer transaction commits; it blocks at the DB level, requiring no
    application-level retry logic.
    """
    # Advisory lock key: deterministic 31-bit integer from (user_id, video_id, frame_ms).
    raw = f"{user_id}:{video_id}:{frame_ms}".encode()
    lock_key = int(hashlib.sha256(raw).hexdigest()[:8], 16) % (2 ** 31 - 1)
    db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": lock_key})

    existing = db.execute(
        select(BallTrainingAssignment).where(
            BallTrainingAssignment.user_id == user_id,
            BallTrainingAssignment.video_id == video_id,
            BallTrainingAssignment.frame_ms == frame_ms,
            BallTrainingAssignment.consumed_at.is_(None),
            BallTrainingAssignment.expires_at > now,
        )
    ).scalar_one_or_none()

    if existing:
        return existing

    new_a = BallTrainingAssignment(
        user_id=user_id,
        video_id=video_id,
        frame_ms=frame_ms,
        expires_at=expires_at,
    )
    db.add(new_a)
    db.flush()
    return new_a


def get_global_training_queue(
    db: Session,
    user_id: int,
    limit: int = 5,
) -> GlobalTrainingQueueResponse:
    """Return up to `limit` training tasks for the requesting user.

    Privacy invariants:
      - Frames from the user's own videos are excluded.
      - Only frames from videos whose owner has training_consent=True are included.
      - video_id and frame_ms are stored in BallTrainingAssignment; the client
        receives only the opaque assignment_id UUID.
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=settings.BALL_TRAINING_ASSIGNMENT_TTL_SECONDS)

    # Sweep expired-pending assignments for this user before creating new ones.
    # This keeps the partial unique index accurate.
    _sweep_expired_assignments(db, user_id, now)

    # Frames this user has already reviewed (any non-spam feedback).
    reviewed_subq = (
        select(JugglingBallFeedback.video_id, JugglingBallFeedback.frame_ms)
        .where(
            JugglingBallFeedback.user_id == user_id,
            JugglingBallFeedback.approval_state != "spam",
        )
        .subquery()
    )

    # Non-spam feedback count per (video_id, frame_ms) across all users.
    fb_count_subq = (
        select(
            JugglingBallFeedback.video_id,
            JugglingBallFeedback.frame_ms,
            func.count(JugglingBallFeedback.id).label("cnt"),
        )
        .where(JugglingBallFeedback.approval_state != "spam")
        .group_by(JugglingBallFeedback.video_id, JugglingBallFeedback.frame_ms)
        .subquery()
    )

    # Main query: eligible trajectory frames from consented, non-own videos.
    rows = db.execute(
        select(
            JugglingBallTrajectory,
            func.coalesce(fb_count_subq.c.cnt, 0).label("fb_cnt"),
        )
        .join(JugglingVideo, JugglingBallTrajectory.video_id == JugglingVideo.id)
        .join(JugglingConsent, JugglingConsent.user_id == JugglingVideo.user_id)
        .outerjoin(
            fb_count_subq,
            (fb_count_subq.c.video_id == JugglingBallTrajectory.video_id)
            & (fb_count_subq.c.frame_ms == JugglingBallTrajectory.frame_ms),
        )
        .where(
            JugglingConsent.training_consent.is_(True),
            JugglingVideo.status.notin_(["gdpr_deleted", "media_deleted"]),
            JugglingVideo.user_id != user_id,
            JugglingBallTrajectory.tracking_state != "lost",
            func.coalesce(fb_count_subq.c.cnt, 0) < _MAX_FEEDBACKS_PER_FRAME,
            # Exclude frames already reviewed by this user.
            ~select(reviewed_subq).where(
                (reviewed_subq.c.video_id == JugglingBallTrajectory.video_id)
                & (reviewed_subq.c.frame_ms == JugglingBallTrajectory.frame_ms)
            ).exists(),
        )
        # Load a broader candidate set for priority scoring; assign at most `limit`.
        .limit(limit * 10)
    ).all()

    total_available = len(rows)

    # Priority score (mirrors B1 formula): uncertain frames first, fewer votes first.
    def _score(pt: JugglingBallTrajectory, fb_cnt: int) -> float:
        base = (1.0 - pt.confidence) * 0.60 if pt.confidence is not None else 0.60
        return base - 0.10 * min(fb_cnt, 3)

    scored = sorted(
        rows, key=lambda r: _score(r[0], r[1]), reverse=True
    )[:limit]

    tasks: List[GlobalTrainingQueueItem] = []
    for row in scored:
        pt: JugglingBallTrajectory = row[0]
        fb_cnt: int = row[1]
        score = round(_score(pt, fb_cnt), 4)

        assignment = _get_or_create_assignment(
            db, user_id, pt.video_id, pt.frame_ms, expires_at, now
        )
        tasks.append(
            GlobalTrainingQueueItem(
                assignment_id=assignment.id,
                model_predicted_x=pt.ball_x,
                model_predicted_y=pt.ball_y,
                model_confidence=pt.confidence,
                model_tracking_state=pt.tracking_state,
                existing_feedback_count=fb_cnt,
                priority_score=score,
                expires_at=assignment.expires_at,
            )
        )

    db.commit()

    return GlobalTrainingQueueResponse(
        tasks=tasks,
        max_per_session=limit,
        total_in_queue=total_available,
    )


def submit_training_feedback(
    db: Session,
    user_id: int,
    req: BallTrainingFeedbackRequest,
) -> BallTrainingFeedbackResponse:
    """Validate and consume an assignment, inserting one JugglingBallFeedback row.

    Locking order (deadlock-free):
      1. BallTrainingAssignment FOR UPDATE — binding + state checks
      2. JugglingBallTrajectory FOR UPDATE — capacity recount after lock

    The feedback INSERT and consumed_at update are committed atomically.
    If the feedback INSERT fails (e.g. duplicate from B1 endpoint), the
    savepoint rollback leaves consumed_at untouched.
    """
    now = datetime.now(timezone.utc)

    # 1. Lock assignment row.
    assignment: BallTrainingAssignment | None = db.execute(
        select(BallTrainingAssignment)
        .where(BallTrainingAssignment.id == req.assignment_id)
        .with_for_update()
    ).scalar_one_or_none()

    # 404 for both "not found" and "cross-user" — no info-leak.
    if assignment is None or assignment.user_id != user_id:
        raise HTTPException(404, "Assignment not found")
    if assignment.expires_at < now:
        raise HTTPException(410, "Assignment expired")
    if assignment.consumed_at is not None:
        raise HTTPException(409, "Assignment already submitted")

    # 2. Lock trajectory row (capacity recount after lock prevents TOCTOU race).
    trajectory: JugglingBallTrajectory | None = db.execute(
        select(JugglingBallTrajectory)
        .where(
            JugglingBallTrajectory.video_id == assignment.video_id,
            JugglingBallTrajectory.frame_ms == assignment.frame_ms,
        )
        .with_for_update()
    ).scalar_one_or_none()

    # 3. Live consent check (not cached in assignment — revoke takes effect immediately).
    video: JugglingVideo | None = db.execute(
        select(JugglingVideo).where(JugglingVideo.id == assignment.video_id)
    ).scalar_one_or_none()
    if video is None or video.status == "gdpr_deleted":
        raise HTTPException(403, "Training content no longer available")

    consent = db.execute(
        select(JugglingConsent).where(
            JugglingConsent.user_id == video.user_id,
            JugglingConsent.training_consent.is_(True),
        )
    ).scalar_one_or_none()
    if consent is None:
        raise HTTPException(403, "Training consent revoked for this content")

    # 4. Recount non-spam feedbacks after trajectory lock.
    fb_count: int = db.execute(
        select(func.count(JugglingBallFeedback.id)).where(
            JugglingBallFeedback.video_id == assignment.video_id,
            JugglingBallFeedback.frame_ms == assignment.frame_ms,
            JugglingBallFeedback.approval_state != "spam",
        )
    ).scalar() or 0

    if fb_count >= _MAX_FEEDBACKS_PER_FRAME:
        raise HTTPException(409, "Task closed — frame has reached the feedback limit")

    # 5. Build feedback row.
    reliability = _get_user_reliability(db, user_id)
    traj_id = trajectory.id if trajectory is not None else None

    # Back-calculate full-frame corrected coordinates from tap position.
    corrected_x: float | None = None
    corrected_y: float | None = None
    correction_method: str | None = None

    if req.decision == "corrected":
        if assignment.display_mode is None:
            raise HTTPException(
                422,
                "Frame must be fetched via GET /me/ball-training/frame/{assignment_id} "
                "before submitting a corrected decision.",
            )
        if assignment.display_mode == "context_crop" and trajectory is not None:
            img_w = trajectory.image_width_px or 1920
            img_h = trajectory.image_height_px or 1080
            box = canonical_crop_box(
                trajectory.ball_x,
                trajectory.ball_y,
                img_w,
                img_h,
                margin_ratio=settings.BALL_TRAINING_FRAME_MARGIN_RATIO,
            )
            corrected_x, corrected_y = tap_to_full_frame(
                req.tap_x, req.tap_y, box, img_w, img_h
            )
            correction_method = "tap_in_crop"
        else:
            # full_frame mode: tap coords ARE full-frame coords (clamped).
            corrected_x = clamp_unit(req.tap_x)
            corrected_y = clamp_unit(req.tap_y)
            correction_method = "tap_in_full_frame"

    feedback = JugglingBallFeedback(
        video_id=assignment.video_id,
        frame_ms=assignment.frame_ms,
        trajectory_point_id=traj_id,
        user_id=user_id,
        decision=req.decision,
        corrected_x=corrected_x,
        corrected_y=corrected_y,
        correction_method=correction_method,
        model_predicted_x=trajectory.ball_x if trajectory else None,
        model_predicted_y=trajectory.ball_y if trajectory else None,
        model_confidence=trajectory.confidence if trajectory else None,
        model_tracking_state=trajectory.tracking_state if trajectory else None,
        user_reliability_at_submit=reliability,
        approval_state="pending",
        spam_flags=[],
    )
    db.add(feedback)

    # 6. Flush feedback row. The DB UNIQUE constraint (user_id, video_id, frame_ms)
    #    raises IntegrityError if the user already submitted via the B1 endpoint.
    #    We catch it before marking the assignment consumed so it remains usable.
    try:
        db.flush()
    except Exception as flush_exc:
        # Rollback to the last savepoint (test) or abort the transaction (production).
        db.rollback()
        raise HTTPException(
            409,
            "Feedback already submitted for this frame via another submission path",
        ) from flush_exc

    # 7. Mark assignment consumed in the same outer transaction.
    assignment.consumed_at = now

    db.commit()

    # 8. Dispatch consensus task post-commit (fire-and-forget, Celery optional).
    try:
        from app.tasks.juggling_feedback_task import compute_frame_consensus
        compute_frame_consensus.apply_async(
            args=[str(assignment.video_id), assignment.frame_ms],
            countdown=2,
        )
    except Exception:
        pass

    return BallTrainingFeedbackResponse(
        assignment_id=req.assignment_id,
        decision=req.decision,
        submitted_at=now,
        corrected_x=corrected_x,
        corrected_y=corrected_y,
    )
