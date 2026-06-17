"""
Ball detection Celery task — Phase 2B (AN-3B2B-2).

Queue: analysis (--pool=solo -c 1, one task at a time).
Trigger: explicit admin endpoint, NOT automatic.
No skill pipeline interaction — measurement data only.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.models.juggling import JugglingBallDetection, JugglingContactEvent, JugglingVideo
from app.services.juggling.analysis_model_registry import get_model_config

logger = logging.getLogger(__name__)


def _video_path(video: JugglingVideo) -> str | None:
    for path_attr in ("processed_path", "storage_path"):
        raw = getattr(video, path_attr, None)
        if raw and Path(raw).is_file():
            return raw
    return None


@celery_app.task(
    bind=True,
    max_retries=1,
    default_retry_delay=30,
    queue="analysis",
    time_limit=120,
    soft_time_limit=90,
)
def detect_ball_for_event(
    self,
    video_id: str,
    event_id: str,
    training_video_type: str = "juggling",
) -> dict:
    import uuid as _uuid

    if not settings.BALL_DETECTION_ENABLED:
        return {"status": "skipped", "reason": "BALL_DETECTION_ENABLED=False"}

    config = get_model_config(training_video_type)
    model_path = getattr(settings, config.model_path_key)

    if not Path(model_path).is_file():
        logger.error("detect_ball: model file missing: %s", model_path)
        return {"status": "failed", "reason": f"model file missing: {model_path}"}

    db = SessionLocal()
    try:
        vid_uuid = _uuid.UUID(video_id)
        evt_uuid = _uuid.UUID(event_id)

        video = db.query(JugglingVideo).filter(JugglingVideo.id == vid_uuid).first()
        if video is None:
            return {"status": "failed", "reason": "video not found"}

        event = (
            db.query(JugglingContactEvent)
            .filter(
                JugglingContactEvent.id == evt_uuid,
                JugglingContactEvent.video_id == vid_uuid,
                JugglingContactEvent.deleted_at.is_(None),
            )
            .first()
        )
        if event is None:
            return {"status": "failed", "reason": "event not found"}

        existing = (
            db.query(JugglingBallDetection)
            .filter(JugglingBallDetection.contact_event_id == evt_uuid)
            .first()
        )
        if existing is not None:
            return {"status": "skipped", "reason": "detection already exists"}

        vpath = _video_path(video)
        if vpath is None:
            logger.error("detect_ball: no video file for %s", video_id)
            return {"status": "failed", "reason": "video file not found on disk"}

        from app.services.juggling.frame_extractor import extract_frame_at_ms
        from app.services.juggling.onnx_ball_detector import get_detector

        frame_rgb, w, h = extract_frame_at_ms(vpath, event.timestamp_ms)
        detector = get_detector(model_path)
        result = detector.detect(
            frame_rgb,
            target_class_id=config.target_class_id,
            confidence_threshold=config.confidence_threshold,
        )

        detection = JugglingBallDetection(
            contact_event_id=event.id,
            video_id=video.id,
            detection_source=config.detection_source,
            model_version=config.model_version,
            image_width_px=w,
            image_height_px=h,
            excluded_from_training=True,
        )

        if result is not None:
            cx, cy, conf = result
            detection.ball_x = cx
            detection.ball_y = cy
            detection.confidence = conf
            detection.no_ball_detected = False
            status = "detected"
        else:
            detection.no_ball_detected = True
            status = "not_detected"

        db.add(detection)
        db.commit()

        logger.info(
            "detect_ball: %s video=%s event=%s ball_x=%s ball_y=%s conf=%s",
            status, video_id, event_id,
            detection.ball_x, detection.ball_y, detection.confidence,
        )
        return {
            "status": status,
            "ball_x": detection.ball_x,
            "ball_y": detection.ball_y,
            "confidence": detection.confidence,
        }

    except Exception as exc:
        db.rollback()
        logger.exception("detect_ball: error for video=%s event=%s", video_id, event_id)
        try:
            self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            return {"status": "failed", "reason": str(exc)}
    finally:
        db.close()
