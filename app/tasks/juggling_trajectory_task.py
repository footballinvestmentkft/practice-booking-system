"""
Dense ball trajectory Celery task — AN-3B2D-1.

Samples every 100ms (10 FPS) across the full video, runs ONNX ball
detection on each frame, and applies Kalman smoothing for inter-frame
tracking.  Results bulk-inserted into juggling_ball_trajectories.

Queue: analysis (--pool=solo -c 1, one task at a time).
Trigger: auto-dispatch from POST /complete (countdown=120s) or admin.

IMPORTANT: frame_extractor and onnx_ball_detector are IMPORTED, not modified.
"""
from __future__ import annotations

import logging
import time
import uuid as _uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models.juggling import JugglingBallTrajectory, JugglingVideo
from app.services.juggling.analysis_model_registry import get_model_config
from app.services.juggling.kalman_ball_tracker import KalmanBallTracker

logger = logging.getLogger(__name__)


@dataclass
class _TrajectoryPoint:
    frame_ms: int
    ball_x: float | None = None
    ball_y: float | None = None
    confidence: float | None = None
    state: str = "lost"
    image_width_px: int | None = None
    image_height_px: int | None = None


def _video_path(video: JugglingVideo) -> str | None:
    for attr in ("processed_path", "storage_path"):
        raw = getattr(video, attr, None)
        if raw and Path(raw).is_file():
            return raw
    return None


def _get_duration_ms(video: JugglingVideo) -> int | None:
    for meta_attr in ("server_detected_metadata", "client_reported_metadata"):
        meta = getattr(video, meta_attr, None)
        if meta and isinstance(meta, dict):
            dur = meta.get("duration_seconds")
            if dur is not None:
                return int(float(dur) * 1000)
    return None


def _set_status(video_id: str, status: str, db: Session) -> None:
    vid_uuid = _uuid.UUID(video_id)
    db.query(JugglingVideo).filter(JugglingVideo.id == vid_uuid).update(
        {"ball_trajectory_status": status}
    )
    db.commit()


def run_dense_ball_trajectory(
    video_id: str,
    db: Session,
    *,
    _extract_frame=None,
    _get_detector=None,
    sampling_interval_ms: int = 100,
    max_consecutive_miss: int = 5,
) -> dict:
    """
    Core logic — testable without Celery.

    Walks the video at sampling_interval_ms steps, runs ONNX detection,
    applies Kalman smoothing, and bulk-inserts trajectory points.
    """
    if not settings.BALL_TRAJECTORY_ENABLED:
        return {"status": "skipped", "reason": "BALL_TRAJECTORY_ENABLED=False"}

    vid_uuid = _uuid.UUID(video_id)
    video = db.query(JugglingVideo).filter(JugglingVideo.id == vid_uuid).first()
    if video is None:
        return {"status": "failed", "reason": "video not found"}

    if video.transcode_status not in ("done", "skipped"):
        return {"status": "skipped", "reason": "transcode not done yet"}

    vpath = _video_path(video)
    if vpath is None:
        _set_status(video_id, "failed", db)
        return {"status": "failed", "reason": "video file not found on disk"}

    duration_ms = _get_duration_ms(video)
    if duration_ms is None or duration_ms <= 0:
        _set_status(video_id, "failed", db)
        return {"status": "failed", "reason": "no duration metadata"}

    _set_status(video_id, "processing", db)

    config = get_model_config(video.training_video_type or "juggling")
    model_path = getattr(settings, config.model_path_key)

    if not Path(model_path).is_file():
        _set_status(video_id, "failed", db)
        return {"status": "failed", "reason": f"model file missing: {model_path}"}

    if _extract_frame is None:
        from app.services.juggling.frame_extractor import extract_frame_at_ms
        _extract_frame = extract_frame_at_ms
    if _get_detector is None:
        from app.services.juggling.onnx_ball_detector import get_detector
        _get_detector = get_detector

    detector = _get_detector(model_path)
    tracker = KalmanBallTracker(max_miss=max_consecutive_miss)

    t_start = time.monotonic()
    points: list[_TrajectoryPoint] = []
    counts = {"detected": 0, "predicted": 0, "lost": 0}

    for frame_ms in range(0, duration_ms + 1, sampling_interval_ms):
        try:
            frame_rgb, w, h = _extract_frame(vpath, frame_ms)
        except (ValueError, OSError):
            tracker.mark_miss()
            points.append(_TrajectoryPoint(frame_ms=frame_ms, state="lost"))
            counts["lost"] += 1
            continue

        result = detector.detect(
            frame_rgb,
            target_class_id=config.target_class_id,
            confidence_threshold=config.confidence_threshold,
        )

        if result is not None:
            cx, cy, conf = result
            sx, sy = tracker.update(cx, cy)
            points.append(_TrajectoryPoint(
                frame_ms=frame_ms,
                ball_x=sx, ball_y=sy,
                confidence=conf,
                state="detected",
                image_width_px=w, image_height_px=h,
            ))
            counts["detected"] += 1
        else:
            pred = tracker.predict_only()
            if pred is not None:
                px, py = pred
                points.append(_TrajectoryPoint(
                    frame_ms=frame_ms,
                    ball_x=px, ball_y=py,
                    confidence=None,
                    state="predicted",
                    image_width_px=w, image_height_px=h,
                ))
                counts["predicted"] += 1
            else:
                points.append(_TrajectoryPoint(
                    frame_ms=frame_ms, state="lost",
                ))
                counts["lost"] += 1

    # Bulk insert in batches, preserving is_manual=TRUE rows
    BATCH_SIZE = 200
    manual_frames = set(
        r[0] for r in db.query(JugglingBallTrajectory.frame_ms).filter(
            JugglingBallTrajectory.video_id == vid_uuid,
            JugglingBallTrajectory.is_manual.is_(True),
        ).all()
    )

    inserted = 0
    for i in range(0, len(points), BATCH_SIZE):
        batch = points[i:i + BATCH_SIZE]
        objects = []
        for p in batch:
            if p.frame_ms in manual_frames:
                continue
            objects.append(JugglingBallTrajectory(
                video_id=vid_uuid,
                frame_ms=p.frame_ms,
                ball_x=p.ball_x,
                ball_y=p.ball_y,
                confidence=p.confidence,
                is_manual=False,
                tracking_state=p.state,
                model_version=config.model_version,
                image_width_px=p.image_width_px,
                image_height_px=p.image_height_px,
            ))
        if objects:
            db.bulk_save_objects(objects)
            db.commit()
            inserted += len(objects)

    elapsed = time.monotonic() - t_start
    _set_status(video_id, "complete", db)

    logger.info(
        "dense_trajectory_complete: video=%s frames=%d detected=%d "
        "predicted=%d lost=%d inserted=%d elapsed=%.1fs",
        video_id, len(points),
        counts["detected"], counts["predicted"], counts["lost"],
        inserted, elapsed,
    )

    return {
        "status": "complete",
        "frames": len(points),
        "detected": counts["detected"],
        "predicted": counts["predicted"],
        "lost": counts["lost"],
        "inserted": inserted,
        "elapsed_sec": round(elapsed, 1),
    }


# ── Celery wrapper ────────────────────────────────────────────────────────────

from app.celery_app import celery_app  # noqa: E402
from app.database import SessionLocal   # noqa: E402


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=60,
    queue="analysis",
    time_limit=600,
    soft_time_limit=540,
)
def dense_ball_trajectory_task(  # pragma: no cover — Celery wrapper
    self,
    video_id: str,
) -> dict:
    db = SessionLocal()
    try:
        return run_dense_ball_trajectory(video_id, db)
    except Exception as exc:
        db.rollback()
        logger.exception(
            "dense_trajectory_error: video=%s", video_id,
        )
        try:
            _set_status(video_id, "failed", db)
        except Exception:
            pass
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"status": "failed", "reason": str(exc)}
    finally:
        db.close()
