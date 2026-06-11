"""
Juggling P3 — Retention Celery Task.

Orchestrates all retention scans in one execution:
  1. retention_expiry_scan  — delete records past retention_expires_at
  2. scan_orphan_files      — delete files not referenced in DB
  3. scan_missing_files     — null DB paths pointing to missing files
  4. cleanup_temp_files     — delete stale .tmp.* files

Master switch: JUGGLING_RETENTION_CLEANUP_ENABLED (must be True to run)
Dry-run flag:  JUGGLING_RETENTION_DRY_RUN       (True = log only, no mutations)

Both flags are independently overridable via task kwargs for ad-hoc runs:
  run_retention_task.delay(dry_run=False)
"""
from __future__ import annotations

import logging
import uuid as _uuid_mod
from pathlib import Path
from typing import Optional

from app.celery_app import celery_app
from app.config import settings
from app.database import SessionLocal
from app.services.juggling.retention_service import (
    cleanup_temp_files,
    retention_expiry_scan,
    scan_missing_files,
    scan_orphan_files,
)

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    max_retries=0,
    queue="juggling_retention",
    name="app.tasks.juggling_retention_task.run_retention_task",
    soft_time_limit=300,
    time_limit=360,
)
def run_retention_task(
    self,
    dry_run: Optional[bool] = None,
) -> dict:
    """
    Run all juggling retention scans in sequence.

    dry_run overrides JUGGLING_RETENTION_DRY_RUN when explicitly passed.
    If dry_run is None, the config value is used.
    """
    if not settings.JUGGLING_RETENTION_CLEANUP_ENABLED:
        logger.info("juggling_retention_task_skipped_disabled")
        return {"status": "disabled"}

    effective_dry_run = (
        dry_run if dry_run is not None else settings.JUGGLING_RETENTION_DRY_RUN
    )
    task_run_id = str(_uuid_mod.uuid4())
    upload_dir = Path(settings.JUGGLING_UPLOAD_DIR)

    db = SessionLocal()
    try:
        expiry_result = retention_expiry_scan(
            db=db,
            upload_dir=upload_dir,
            dry_run=effective_dry_run,
            task_run_id=task_run_id,
        )
        orphan_result = scan_orphan_files(
            upload_dir=upload_dir,
            db=db,
            dry_run=effective_dry_run,
            task_run_id=task_run_id,
        )
        missing_result = scan_missing_files(
            db=db,
            dry_run=effective_dry_run,
            task_run_id=task_run_id,
        )
        temp_result = cleanup_temp_files(
            upload_dir=upload_dir,
            db=db,
            dry_run=effective_dry_run,
            task_run_id=task_run_id,
        )

        summary = {
            "status": "completed",
            "dry_run": effective_dry_run,
            "task_run_id": task_run_id,
            "retention_expiry": expiry_result,
            "orphan_files": orphan_result,
            "missing_files": missing_result,
            "temp_files": temp_result,
        }
        logger.info(
            "juggling_retention_task_completed",
            extra={"task_run_id": task_run_id, "dry_run": effective_dry_run},
        )
        return summary

    except Exception as exc:
        logger.error(
            "juggling_retention_task_failed",
            extra={"task_run_id": task_run_id, "error": str(exc)},
        )
        return {"status": "failed", "error": str(exc)}
    finally:
        db.close()
