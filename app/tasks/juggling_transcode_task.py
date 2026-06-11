"""
Juggling video transcode Celery task.

Pipeline position (P2):
  POST /complete
    → status=processing
    → transcode_video_task.delay(video_id)   ← this task
    → transcode_status=processing
    → transcode_status=done | skipped | failed
    → analyze_video_task.delay(video_id)     ← dispatched ONLY on done/skipped
    → quality result

Guard:
  If transcode_status ends up as "failed", analyze_video_task is NOT dispatched.

Retry policy:
  max_retries=2, retry_delay=30s → 3 total attempts before failed is written.
  Retries only on unexpected exceptions, NOT on clean ffmpeg failures.

Queues:
  Both transcode and analyze tasks use the "juggling_videos" queue.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.services.juggling import metadata_service, video_service
from app.services.juggling.metadata_service import VideoProbeError
from app.services.juggling import transcode_service

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    queue="juggling_videos",
    name="app.tasks.juggling_transcode_task.transcode_video_task",
    soft_time_limit=180,
    time_limit=240,
)
def transcode_video_task(self, video_id: str) -> dict:
    """
    1. Probe original with ffprobe (to determine transcode parameters).
    2. Run transcode_service.transcode() (skip / audio-strip / full transcode).
    3. Persist transcode fields to DB.
    4. Dispatch analyze_video_task ONLY if status is done or skipped.
    """
    db = SessionLocal()
    try:
        from app.models.juggling import JugglingVideo, JugglingTranscodeStatus

        video = db.query(JugglingVideo).filter_by(id=video_id).first()
        if video is None:
            logger.error("transcode_missing_record", extra={"video_id": video_id})
            return {"status": "failed", "reason": "record_not_found"}

        original = Path(video.storage_path) if video.storage_path else None
        if not original or not original.exists():
            reason = "missing_storage_path" if not original else "file_not_found"
            video_service.apply_transcode_failure(video_id, reason, db)
            return {"status": "failed", "reason": reason}

        # ── Step 1: set transcode_status=processing ───────────────────────────
        video_service.set_transcode_processing(video_id, db)

        # ── Step 2: probe original to get metadata ────────────────────────────
        try:
            probe_data = metadata_service.probe_video(
                original,
                timeout_seconds=settings.JUGGLING_FFPROBE_TIMEOUT_SECONDS,
            )
            metadata = metadata_service.extract_server_metadata(probe_data)
        except VideoProbeError as exc:
            logger.warning(
                "transcode_probe_error",
                extra={"video_id": video_id, "error": str(exc)},
            )
            try:
                raise self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                video_service.apply_transcode_failure(video_id, "probe_failed", db)
                return {"status": "failed", "reason": "probe_failed"}

        # ── Step 3: run transcode pipeline ────────────────────────────────────
        upload_dir = original.parent
        result = transcode_service.transcode(
            original_path=original,
            video_id=video_id,
            metadata=metadata,
            upload_dir=upload_dir,
            target_fps=settings.JUGGLING_FFMPEG_TARGET_FPS,
            target_height=settings.JUGGLING_FFMPEG_TARGET_HEIGHT,
            timeout_seconds=settings.JUGGLING_FFMPEG_TIMEOUT_SECONDS,
        )

        # ── Step 4: persist result ────────────────────────────────────────────
        video_service.apply_transcode_result(video_id, result, db)

        logger.info(
            "transcode_complete",
            extra={"video_id": video_id, "status": result.status},
        )

        # ── Step 5: dispatch analyze ONLY on done/skipped ─────────────────────
        if result.status in ("done", "skipped"):
            from app.tasks.juggling_tasks import analyze_video_task
            analyze_video_task.delay(video_id)
            return {"status": result.status, "next": "analyze_queued"}
        else:
            # transcode_status=failed → analyze is blocked
            logger.warning(
                "transcode_failed_analyze_blocked",
                extra={"video_id": video_id, "error": result.error},
            )
            return {"status": "failed", "reason": result.error}

    except Exception as exc:
        logger.warning(
            "transcode_task_error",
            extra={"video_id": video_id, "error": str(exc)},
        )
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            video_service.apply_transcode_failure(
                video_id, f"task_exception:{exc}", db
            )
            return {"status": "failed", "reason": str(exc)}
    finally:
        db.close()