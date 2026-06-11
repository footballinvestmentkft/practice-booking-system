"""
Juggling video analysis Celery task.

State flow (managed by video_service helpers):
  processing → analyzed   (quality result ready)
             → rejected   (codec / duration / quality gate decision)
             → failed     (ffprobe crash, timeout, corrupt file — after max retries)

Retry policy: max_retries=2, default_retry_delay=15s
  → 3 total attempts before failed state is written.

Accepted codecs:  h264, hevc (h265)
Duration gate:    JUGGLING_VIDEO_MAX_DURATION_SECONDS from config

Audio note:
  has_audio is detected and stored in server_detected_metadata.
  If has_audio=True, quality_detail includes audio_present=True (warning only).
  Audio stripping is P2 scope.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.services.juggling import metadata_service, quality_service, video_service
from app.services.juggling.metadata_service import VideoProbeError

logger = logging.getLogger(__name__)

_ACCEPTED_CODECS: frozenset[str] = frozenset({"h264", "hevc"})


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    queue="juggling_videos",
    name="app.tasks.juggling_tasks.analyze_video_task",
    soft_time_limit=60,
    time_limit=90,
)
def analyze_video_task(self, video_id: str) -> dict:
    """
    Load the stored video file, run ffprobe metadata detection, validate
    codec/duration, run quality analysis, write results to DB.

    video_id is the UUID string of the juggling_videos row.
    """
    db = SessionLocal()
    try:
        video = db.query(
            __import__("app.models.juggling", fromlist=["JugglingVideo"]).JugglingVideo
        ).filter_by(id=video_id).first()

        if video is None:
            logger.error("juggling_analyze_missing_record", extra={"video_id": video_id})
            return {"status": "failed", "reason": "record_not_found"}

        if not video.storage_path:
            video_service.apply_failure(video_id, "missing_storage_path", db)
            return {"status": "failed", "reason": "missing_storage_path"}

        # P2 guard: never analyze if transcode failed
        if (
            video.transcode_status is not None
            and video.transcode_status == "failed"
        ):
            logger.warning(
                "juggling_analyze_blocked_transcode_failed",
                extra={"video_id": video_id},
            )
            return {"status": "blocked", "reason": "transcode_failed"}

        file_path = Path(video.storage_path)
        if not file_path.exists():
            video_service.apply_failure(video_id, "file_not_found", db)
            return {"status": "failed", "reason": "file_not_found"}

        # ── Step 1: ffprobe metadata detection ───────────────────────────────
        try:
            probe_data = metadata_service.probe_video(
                file_path,
                timeout_seconds=settings.JUGGLING_FFPROBE_TIMEOUT_SECONDS,
            )
        except VideoProbeError as exc:
            logger.warning(
                "juggling_ffprobe_error",
                extra={"video_id": video_id, "error": str(exc)},
            )
            try:
                raise self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                video_service.apply_failure(video_id, "corrupt_video", db)
                return {"status": "failed", "reason": "corrupt_video"}

        server_metadata = metadata_service.extract_server_metadata(probe_data)

        # ── Step 2: Codec validation ──────────────────────────────────────────
        codec = (server_metadata.get("codec") or "").lower()
        if codec not in _ACCEPTED_CODECS:
            video_service.apply_rejection(
                video_id, server_metadata, "unsupported_codec", db
            )
            logger.info(
                "juggling_rejected_codec",
                extra={"video_id": video_id, "codec": codec},
            )
            return {"status": "rejected", "reason": "unsupported_codec"}

        # ── Step 3: Duration validation ───────────────────────────────────────
        duration = server_metadata.get("duration_seconds")
        if duration and duration > settings.JUGGLING_VIDEO_MAX_DURATION_SECONDS:
            video_service.apply_rejection(
                video_id, server_metadata, "too_long", db
            )
            logger.info(
                "juggling_rejected_duration",
                extra={"video_id": video_id, "duration": duration},
            )
            return {"status": "rejected", "reason": "too_long"}

        # ── Step 4: Quality analysis ──────────────────────────────────────────
        file_bytes = file_path.read_bytes()
        quality_score, quality_status, quality_detail, rejection_reason = (
            quality_service.analyze(file_bytes, server_metadata)
        )

        if rejection_reason:
            video_service.apply_rejection(
                video_id, server_metadata, rejection_reason, db
            )
            logger.info(
                "juggling_rejected_quality",
                extra={"video_id": video_id, "reason": rejection_reason},
            )
            return {"status": "rejected", "reason": rejection_reason}

        # ── Step 5: Write analyzed result ─────────────────────────────────────
        video_service.apply_analysis(
            video_id, server_metadata, quality_score, quality_status, quality_detail, db
        )
        logger.info(
            "juggling_analyzed",
            extra={
                "video_id": video_id,
                "quality_score": quality_score,
                "quality_status": quality_status,
            },
        )
        return {
            "status": "analyzed",
            "quality_score": quality_score,
            "quality_status": quality_status,
        }

    except Exception as exc:
        logger.warning(
            "juggling_task_error",
            extra={"video_id": video_id, "error": str(exc)},
        )
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            video_service.apply_failure(video_id, "analysis_timeout", db)
            return {"status": "failed", "reason": str(exc)}
    finally:
        db.close()