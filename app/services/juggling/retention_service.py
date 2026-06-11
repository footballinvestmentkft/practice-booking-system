"""
Juggling P3 — Retention Service.

Internal service: no HTTP endpoints.  Called by the Celery retention task
and (future) admin/GDPR HTTP handlers.

Invariants enforced throughout:
  1. dry_run=True  → Path.unlink() never called, db.commit() never called for mutations.
  2. raw path       → never written to DB; only HMAC(secret, path) stored.
  3. raw user_id    → never written to audit log; only HMAC(secret, str(user_id)) stored.
  4. gdpr_deleted is irreversible — apply_gdpr_delete() is idempotent on already-deleted records.
  5. Partial delete (OSError on any file) → status stays unchanged; deletion_reason not set;
     retention_error written; audit log success=False.

Scope boundary (NEVER add):
  MediaPipe / ONNX / FootAndBall / contact detection / streaming / S3 / labeling.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.juggling import (
    JugglingFileDeletionLog,
    JugglingVideo,
    JugglingVideoStatus,
)

logger = logging.getLogger(__name__)


# ── HMAC helpers ──────────────────────────────────────────────────────────────

def _get_secret() -> bytes:
    """Return the HMAC secret as bytes. Raises ValueError if empty."""
    secret = settings.JUGGLING_AUDIT_HASH_SECRET
    if not secret:
        raise ValueError(
            "JUGGLING_AUDIT_HASH_SECRET must be set when audit hashing is active. "
            "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return secret.encode("utf-8")


def hmac_path_hash(path: str) -> str:
    """HMAC-SHA256(secret, raw_path) → hex digest.  Raises ValueError if secret empty."""
    return _hmac.new(_get_secret(), path.encode("utf-8"), hashlib.sha256).hexdigest()


def hmac_user_pseudonym(user_id: int) -> str:
    """HMAC-SHA256(secret, str(user_id)) → hex digest.  Raises ValueError if secret empty."""
    return _hmac.new(
        _get_secret(), str(user_id).encode("utf-8"), hashlib.sha256
    ).hexdigest()


# ── Audit log writer ──────────────────────────────────────────────────────────

def write_deletion_log(
    db: Session,
    event_type: str,
    video_id: Optional[str] = None,
    user_id: Optional[int] = None,
    file_type: Optional[str] = None,
    raw_path: Optional[str] = None,
    dry_run: bool = True,
    success: Optional[bool] = None,
    error_message: Optional[str] = None,
    task_run_id: Optional[str] = None,
) -> None:
    """
    Write one row to juggling_file_deletion_log.
    raw_path → HMAC hash.  user_id → pseudonym.  Raw values never stored.
    """
    pseudonym: Optional[str] = None
    path_hash: Optional[str] = None

    if user_id is not None:
        try:
            pseudonym = hmac_user_pseudonym(user_id)
        except ValueError:
            pseudonym = None

    if raw_path is not None:
        try:
            path_hash = hmac_path_hash(raw_path)
        except ValueError:
            path_hash = None

    log_entry = JugglingFileDeletionLog(
        video_id=video_id,
        user_pseudonym=pseudonym,
        event_type=event_type,
        file_type=file_type,
        file_path_hash=path_hash,
        dry_run=dry_run,
        success=success,
        error_message=error_message,
        task_run_id=task_run_id,
    )
    db.add(log_entry)
    db.commit()


# ── File helpers ──────────────────────────────────────────────────────────────

def _try_unlink(path: Path, dry_run: bool) -> bool:
    """
    Attempt to unlink a file.
    dry_run=True → no-op, returns True (simulated success).
    Returns True on success, False on failure.
    """
    if dry_run:
        return True
    try:
        if path.exists():
            path.unlink()
        return True
    except OSError as exc:
        logger.warning("retention_unlink_failed", extra={"path": str(path), "error": str(exc)})
        return False


# ── GDPR delete ───────────────────────────────────────────────────────────────

def apply_gdpr_delete(
    video_id: str,
    db: Session,
    dry_run: bool = False,
    deletion_reason: str = "gdpr_request",
    task_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Apply GDPR delete to a single video record.

    Success path (all files deleted, dry_run=False):
      - original, processed, thumbnail files deleted
      - paths, checksums, metadata, quality fields → NULL
      - status → gdpr_deleted, deleted_at → now(), deletion_reason set
      - audit log success=True

    Failure path (any file OSError, dry_run=False):
      - best-effort: other files still attempted
      - status NOT changed to gdpr_deleted
      - path/checksum/metadata fields NOT nulled
      - retention_error set
      - audit log success=False

    dry_run=True:
      - no unlink, no db.commit for mutations
      - audit log written with dry_run=True

    Idempotent: status=gdpr_deleted → returns {"skipped": True}.
    """
    video = db.query(JugglingVideo).filter(JugglingVideo.id == video_id).first()
    if video is None:
        return {"status": "error", "reason": "record_not_found"}

    if video.status == JugglingVideoStatus.gdpr_deleted.value:
        return {"status": "skipped", "reason": "already_deleted"}

    user_id: Optional[int] = video.user_id

    # Collect file paths to attempt deletion
    paths_to_delete: List[tuple[str, str]] = []
    for field_name, attr in [
        ("original", video.original_path),
        ("processed", video.processed_path),
        ("thumbnail", video.thumbnail_path),
    ]:
        if attr:
            paths_to_delete.append((field_name, attr))
    # Also try storage_path if original_path is missing
    if video.storage_path and not video.original_path:
        paths_to_delete.append(("original", video.storage_path))

    # Attempt each file deletion
    all_succeeded = True
    failed_files: List[str] = []

    for file_type, raw_path in paths_to_delete:
        success = _try_unlink(Path(raw_path), dry_run)
        if not success:
            all_succeeded = False
            failed_files.append(file_type)
        write_deletion_log(
            db=db,
            event_type="dry_run_would_delete" if dry_run else "gdpr_delete",
            video_id=video_id,
            user_id=user_id,
            file_type=file_type,
            raw_path=raw_path,
            dry_run=dry_run,
            success=success if not dry_run else None,
            task_run_id=task_run_id,
        )

    if dry_run:
        return {"status": "dry_run", "files_would_delete": len(paths_to_delete)}

    if not all_succeeded:
        # Partial failure: retain_error, do NOT change status
        error_msg = f"file_delete_failed:{','.join(failed_files)}"
        video.retention_error = error_msg
        video.retention_last_checked_at = datetime.now(timezone.utc)
        video.updated_at = datetime.now(timezone.utc)
        db.commit()
        logger.warning(
            "gdpr_delete_partial_failure",
            extra={"video_id": video_id, "failed_files": failed_files},
        )
        write_deletion_log(
            db=db,
            event_type="gdpr_delete",
            video_id=video_id,
            user_id=user_id,
            file_type="all",
            dry_run=False,
            success=False,
            error_message=error_msg,
            task_run_id=task_run_id,
        )
        return {"status": "failed", "reason": error_msg}

    # All files deleted successfully — null out personal data, set gdpr_deleted
    _null_personal_fields(video)
    video.status = JugglingVideoStatus.gdpr_deleted.value
    video.deleted_at = datetime.now(timezone.utc)
    video.deletion_reason = deletion_reason
    video.retention_error = None
    video.updated_at = datetime.now(timezone.utc)
    db.commit()

    write_deletion_log(
        db=db,
        event_type="gdpr_delete",
        video_id=video_id,
        user_id=user_id,
        file_type="all",
        dry_run=False,
        success=True,
        task_run_id=task_run_id,
    )
    logger.info("gdpr_delete_completed", extra={"video_id": video_id})
    return {"status": "deleted", "video_id": video_id}


def _null_personal_fields(video: JugglingVideo) -> None:
    """Null all personal / file-related fields on the video record."""
    # File paths
    video.storage_path     = None
    video.original_path    = None
    video.processed_path   = None
    video.thumbnail_path   = None
    video.filename_stored  = None
    # Checksums
    video.checksum_sha256  = None
    video.checksum_processed = None
    # Metadata
    video.client_reported_metadata = None
    video.server_detected_metadata = None
    # Quality
    video.quality_score    = None
    video.quality_detail   = None
    # Transcode derived — POC conservative: null
    video.processed_resolution     = None
    video.processed_fps            = None
    video.processed_file_size_bytes = None


# ── Orphan file scanner ───────────────────────────────────────────────────────

def scan_orphan_files(
    upload_dir: Path,
    db: Session,
    dry_run: bool = True,
    grace_period_hours: int = 24,
    batch_size: int = 200,
    task_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Find files in upload_dir that have no corresponding DB reference.
    Files newer than grace_period_hours are excluded (in-progress uploads).
    dry_run=True → log only, no deletion.
    """
    now = datetime.now(timezone.utc)
    write_deletion_log(db=db, event_type="scan_started", file_type="orphan",
                       dry_run=dry_run, task_run_id=task_run_id)

    # Collect all DB-known paths
    known_paths: set[str] = set()
    for row in db.query(
        JugglingVideo.storage_path, JugglingVideo.original_path,
        JugglingVideo.processed_path, JugglingVideo.thumbnail_path,
    ).all():
        for p in row:
            if p:
                known_paths.add(str(p))

    found = deleted = errors = 0
    upload_dir.mkdir(parents=True, exist_ok=True)

    for fpath in upload_dir.iterdir():
        if not fpath.is_file():
            continue
        # Skip temp files here (handled by cleanup_temp_files)
        if ".tmp." in fpath.name:
            continue
        if str(fpath) in known_paths:
            continue

        try:
            mtime = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc)
            age_hours = (now - mtime).total_seconds() / 3600
            if age_hours < grace_period_hours:
                continue
        except OSError:
            continue

        found += 1
        success = _try_unlink(fpath, dry_run)
        if not success:
            errors += 1
        else:
            deleted += 1

        write_deletion_log(
            db=db,
            event_type="dry_run_would_delete" if dry_run else "orphan_cleanup",
            file_type="orphan",
            raw_path=str(fpath),
            dry_run=dry_run,
            success=success if not dry_run else None,
            task_run_id=task_run_id,
        )

    summary = {
        "orphan_files_found": found,
        "orphan_files_deleted": deleted if not dry_run else 0,
        "errors": errors,
        "dry_run": dry_run,
    }
    write_deletion_log(
        db=db, event_type="scan_completed", file_type="orphan",
        dry_run=dry_run, success=True,
        error_message=str(summary),
        task_run_id=task_run_id,
    )
    return summary


# ── Missing file scanner ──────────────────────────────────────────────────────

def scan_missing_files(
    db: Session,
    dry_run: bool = True,
    batch_size: int = 100,
    task_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Find DB records where a path is set but the file no longer exists.
    dry_run=True → log only, paths not nulled.
    dry_run=False → path fields nulled, retention_error set.
    """
    write_deletion_log(db=db, event_type="scan_started", file_type="missing",
                       dry_run=dry_run, task_run_id=task_run_id)

    found = nulled = errors = 0

    rows = (
        db.query(JugglingVideo)
        .filter(JugglingVideo.deleted_at.is_(None))
        .filter(
            (JugglingVideo.storage_path.isnot(None))
            | (JugglingVideo.original_path.isnot(None))
            | (JugglingVideo.processed_path.isnot(None))
            | (JugglingVideo.thumbnail_path.isnot(None))
        )
        .limit(batch_size)
        .all()
    )

    for video in rows:
        missing: List[str] = []
        for attr_name in ("storage_path", "original_path", "processed_path", "thumbnail_path"):
            path_val = getattr(video, attr_name)
            if path_val and not Path(path_val).exists():
                missing.append(attr_name)

        if not missing:
            continue

        found += 1
        video_id = str(video.id)
        write_deletion_log(
            db=db,
            event_type="missing_file_audit",
            video_id=video_id,
            user_id=video.user_id,
            file_type="missing",
            dry_run=dry_run,
            success=None,
            error_message=f"missing_fields:{','.join(missing)}",
            task_run_id=task_run_id,
        )

        if not dry_run:
            try:
                for attr_name in missing:
                    setattr(video, attr_name, None)
                video.retention_error = f"file_missing:{','.join(missing)}"
                video.retention_last_checked_at = datetime.now(timezone.utc)
                video.updated_at = datetime.now(timezone.utc)
                db.commit()
                nulled += 1
            except Exception as exc:
                db.rollback()
                errors += 1
                logger.warning(
                    "missing_file_null_failed",
                    extra={"video_id": video_id, "error": str(exc)},
                )

    summary = {
        "missing_files_found": found,
        "missing_paths_nulled": nulled if not dry_run else 0,
        "errors": errors,
        "dry_run": dry_run,
    }
    write_deletion_log(
        db=db, event_type="scan_completed", file_type="missing",
        dry_run=dry_run, success=True,
        error_message=str(summary),
        task_run_id=task_run_id,
    )
    return summary


# ── Temp file cleanup ─────────────────────────────────────────────────────────

def cleanup_temp_files(
    upload_dir: Path,
    db: Session,
    dry_run: bool = True,
    min_age_hours: float = 1.0,
    task_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Delete *.tmp.* files older than min_age_hours.
    video_id and user_pseudonym are NULL in audit log (no DB reference).
    """
    now = datetime.now(timezone.utc)
    write_deletion_log(db=db, event_type="scan_started", file_type="temp",
                       dry_run=dry_run, task_run_id=task_run_id)

    found = deleted = errors = 0
    upload_dir.mkdir(parents=True, exist_ok=True)

    for fpath in upload_dir.iterdir():
        if not fpath.is_file():
            continue
        if ".tmp." not in fpath.name:
            continue

        try:
            mtime = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc)
            age_hours = (now - mtime).total_seconds() / 3600
            if age_hours < min_age_hours:
                continue
        except OSError:
            continue

        found += 1
        success = _try_unlink(fpath, dry_run)
        if not success:
            errors += 1
        else:
            deleted += 1

        write_deletion_log(
            db=db,
            event_type="dry_run_would_delete" if dry_run else "temp_cleanup",
            video_id=None,
            user_id=None,
            file_type="temp",
            raw_path=str(fpath),
            dry_run=dry_run,
            success=success if not dry_run else None,
            task_run_id=task_run_id,
        )

    summary = {
        "temp_files_found": found,
        "temp_files_deleted": deleted if not dry_run else 0,
        "errors": errors,
        "dry_run": dry_run,
    }
    write_deletion_log(
        db=db, event_type="scan_completed", file_type="temp",
        dry_run=dry_run, success=True,
        error_message=str(summary),
        task_run_id=task_run_id,
    )
    return summary


# ── Retention expiry scan ─────────────────────────────────────────────────────

def retention_expiry_scan(
    db: Session,
    upload_dir: Path,
    dry_run: bool = True,
    batch_size: int = 50,
    task_run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Find records past their retention_expires_at date and apply GDPR-equivalent
    deletion (deletion_reason="retention_expired").
    Idempotent: status=gdpr_deleted records are skipped.
    """
    now = datetime.now(timezone.utc)
    write_deletion_log(db=db, event_type="scan_started", file_type="all",
                       dry_run=dry_run, task_run_id=task_run_id)

    eligible = (
        db.query(JugglingVideo)
        .filter(
            JugglingVideo.retention_expires_at <= now,
            JugglingVideo.deleted_at.is_(None),
            JugglingVideo.status != JugglingVideoStatus.gdpr_deleted.value,
        )
        .limit(batch_size)
        .all()
    )

    found = deleted = errors = 0
    for video in eligible:
        found += 1
        if dry_run:
            write_deletion_log(
                db=db, event_type="dry_run_would_delete",
                video_id=str(video.id), user_id=video.user_id,
                file_type="all", dry_run=True, task_run_id=task_run_id,
            )
            continue

        result = apply_gdpr_delete(
            video_id=str(video.id),
            db=db,
            dry_run=False,
            deletion_reason="retention_expired",
            task_run_id=task_run_id,
        )
        if result.get("status") == "deleted":
            deleted += 1
        else:
            errors += 1

    summary = {
        "retention_expired_found": found,
        "retention_expired_deleted": deleted,
        "errors": errors,
        "dry_run": dry_run,
    }
    write_deletion_log(
        db=db, event_type="scan_completed", file_type="all",
        dry_run=dry_run, success=True,
        error_message=str(summary),
        task_run_id=task_run_id,
    )
    return summary