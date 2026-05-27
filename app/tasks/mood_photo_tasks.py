"""
Mood photo background removal Celery task.

State flow (managed by mood_photo_service helpers):
  uploaded → processing  (set by the /remove-bg route before enqueue)
           → ready       (set by this task on success)
           → failed      (set by this task on max-retries exceeded or missing file)

Phase 1: NullProcessor is used — no real background removal occurs.
         The "Remove Background" button is hidden from users when
         BG_REMOVAL_PROCESSOR="null", so this task is not reachable from
         the UI in Phase 1.  It is fully exercised by the test suite.

Real background removal remains Phase 2 (rembg + onnxruntime-cpu).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from app.celery_app import celery_app
from app.database import SessionLocal
from app.services.background_removal import get_processor
from app.services.mood_photo_service import (
    MOOD_PHOTO_DIR,
    apply_removal_failure,
    apply_removal_result,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=10,
    queue="mood_photos",
    name="app.tasks.mood_photo_tasks.remove_background_task",
)
def remove_background_task(
    self,
    user_id: int,
    slot: str,
    original_url: str,
) -> dict:
    """
    Load the original PNG, run the background processor, save the result,
    update the DB record.

    original_url is passed from the route so the task can locate the file
    without an extra DB read.  The task opens its own SessionLocal for the
    write-back; it does not share the web worker's DB session.
    """
    db = SessionLocal()
    try:
        filename  = Path(original_url).name
        orig_path = MOOD_PHOTO_DIR / filename

        if not orig_path.exists():
            apply_removal_failure(user_id, slot, db)
            logger.warning(
                "bg_removal_missing_file",
                extra={"user_id": user_id, "slot": slot, "path": str(orig_path)},
            )
            return {"status": "failed", "reason": "missing_file"}

        input_bytes  = orig_path.read_bytes()
        processor    = get_processor()
        output_bytes = processor.remove(input_bytes)

        # Remove any previous processed file for this slot before writing
        for old in MOOD_PHOTO_DIR.glob(f"{user_id}_mood_{slot}_proc_*.png"):
            old.unlink(missing_ok=True)

        ts            = int(time.time())
        proc_filename = f"{user_id}_mood_{slot}_proc_{ts}.png"
        (MOOD_PHOTO_DIR / proc_filename).write_bytes(output_bytes)

        processed_url = f"/static/uploads/mood_photos/{proc_filename}"
        apply_removal_result(user_id, slot, processed_url, db)
        logger.info(
            "bg_removal_done",
            extra={"user_id": user_id, "slot": slot, "processor": type(processor).__name__},
        )
        return {"status": "ready", "processed_url": processed_url}

    except Exception as exc:
        logger.warning(
            "bg_removal_error",
            extra={"user_id": user_id, "slot": slot, "error": str(exc)},
        )
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            apply_removal_failure(user_id, slot, db)
            return {"status": "failed", "reason": str(exc)}
    finally:
        db.close()
