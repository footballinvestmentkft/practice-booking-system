"""
Juggling video service — DB state transition helpers.

All DB writes go through these helpers so the Celery task and endpoints
share consistent state logic.  Pattern mirrors mood_photo_service.py.

State transitions:
  create_pending      — upload-init creates pending_upload record
  set_uploaded        — upload endpoint marks file received
  set_processing      — complete endpoint sets processing (before Celery enqueue)
  apply_analysis      — Celery task writes quality result → analyzed
  apply_rejection     — Celery task writes gate decision → rejected
  apply_failure       — Celery task writes technical error → failed
  reset_processing    — admin/stuck recovery → uploaded
  delete_media        — user request: remove media files, preserve analysis/annotation data
"""
from __future__ import annotations

import logging
import uuid as _uuid_mod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.config import settings
from app.models.juggling import (
    JugglingVideo,
    JugglingVideoStatus,
    JugglingVideoQualityStatus,
    JugglingTranscodeStatus,
)
from app.services.juggling.retention_service import write_deletion_log

logger = logging.getLogger(__name__)

JUGGLING_UPLOAD_DIR = Path(settings.JUGGLING_UPLOAD_DIR)

# Transitions that must NOT accept a new complete() call
_COMPLETE_BLOCKED_STATUSES = {
    JugglingVideoStatus.pending_upload.value,
    JugglingVideoStatus.processing.value,
    JugglingVideoStatus.analyzed.value,
    JugglingVideoStatus.rejected.value,
    JugglingVideoStatus.gdpr_deleted.value,
}

# Celery task callbacks are no-ops when the video has reached a terminal deletion state.
# This guards against the race where a processing task writes back a new status after
# the video has been user-deleted or GDPR-deleted.
_TERMINAL_STATUSES: frozenset[str] = frozenset({
    JugglingVideoStatus.media_deleted.value,
    JugglingVideoStatus.gdpr_deleted.value,
})


def create_pending(
    user_id: int,
    source_type: str,
    upload_source: str,
    client_reported_metadata: Optional[Dict[str, Any]],
    db: Session,
) -> JugglingVideo:
    """Create a new juggling_video record in pending_upload state."""
    video = JugglingVideo(
        id=_uuid_mod.uuid4(),
        user_id=user_id,
        source_type=source_type,
        upload_source=upload_source,
        status=JugglingVideoStatus.pending_upload.value,
        client_reported_metadata=client_reported_metadata,
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    return video


def set_uploaded(
    video: JugglingVideo,
    storage_path: str,
    filename_stored: str,
    file_size_bytes: int,
    checksum_sha256: str,
    db: Session,
) -> JugglingVideo:
    """Transition pending_upload → uploaded after file is safely written to disk."""
    video.status          = JugglingVideoStatus.uploaded.value
    video.storage_path    = storage_path
    video.filename_stored = filename_stored
    video.file_size_bytes = file_size_bytes
    video.checksum_sha256 = checksum_sha256
    video.updated_at      = datetime.now(timezone.utc)
    db.commit()
    db.refresh(video)
    return video


def set_processing(video_id: str, db: Session) -> JugglingVideo:
    """
    Transition uploaded → processing.
    Called by the complete endpoint BEFORE enqueuing the Celery task.
    Raises ValueError if the transition is not allowed.
    """
    video = db.query(JugglingVideo).filter(JugglingVideo.id == video_id).first()
    if video is None:
        raise ValueError(f"video_not_found: {video_id}")
    if video.status != JugglingVideoStatus.uploaded.value:
        raise ValueError(
            f"invalid_transition: cannot call complete from status={video.status!r}. "
            f"Only 'uploaded' may proceed."
        )
    video.status     = JugglingVideoStatus.processing.value
    video.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(video)
    return video


def apply_analysis(
    video_id: str,
    server_detected_metadata: Dict[str, Any],
    quality_score: float,
    quality_status: str,
    quality_detail: Dict[str, Any],
    db: Session,
) -> JugglingVideo:
    """Transition processing → analyzed (quality result ready)."""
    video = db.query(JugglingVideo).filter(JugglingVideo.id == video_id).first()
    if video is None:
        raise ValueError(f"video_not_found: {video_id}")
    if video.status in _TERMINAL_STATUSES:
        # Race: video was deleted while the Celery task was running. No-op.
        return video
    video.status                   = JugglingVideoStatus.analyzed.value
    video.server_detected_metadata = server_detected_metadata
    video.quality_score            = str(round(quality_score, 4))
    video.quality_status           = quality_status
    video.quality_detail           = quality_detail
    video.updated_at               = datetime.now(timezone.utc)
    db.commit()
    db.refresh(video)
    return video


def apply_rejection(
    video_id: str,
    server_detected_metadata: Optional[Dict[str, Any]],
    rejection_reason: str,
    db: Session,
) -> JugglingVideo:
    """
    Transition processing → rejected.
    Used for deliberate gate decisions: unsupported_codec, too_long, quality thresholds.
    """
    video = db.query(JugglingVideo).filter(JugglingVideo.id == video_id).first()
    if video is None:
        raise ValueError(f"video_not_found: {video_id}")
    if video.status in _TERMINAL_STATUSES:
        # Race: video was deleted while the Celery task was running. No-op.
        return video
    video.status                   = JugglingVideoStatus.rejected.value
    video.quality_status           = JugglingVideoQualityStatus.rejected.value
    video.rejection_reason         = rejection_reason
    video.server_detected_metadata = server_detected_metadata
    video.updated_at               = datetime.now(timezone.utc)
    db.commit()
    db.refresh(video)
    return video


def apply_failure(
    video_id: str,
    reason: str,
    db: Session,
) -> JugglingVideo:
    """
    Transition processing → failed.
    Used for technical errors: ffprobe crash, timeout, corrupt file.
    The record is kept for debugging; storage_path remains valid.
    """
    video = db.query(JugglingVideo).filter(JugglingVideo.id == video_id).first()
    if video is None:
        raise ValueError(f"video_not_found: {video_id}")
    if video.status in _TERMINAL_STATUSES:
        # Race: video was deleted while the Celery task was running. No-op.
        return video
    video.status           = JugglingVideoStatus.failed.value
    video.rejection_reason = reason
    video.updated_at       = datetime.now(timezone.utc)
    db.commit()
    db.refresh(video)
    return video


def reset_processing(video_id: str, db: Session) -> Optional[JugglingVideo]:
    """
    Reset a stuck 'processing' record back to 'uploaded'.
    Idempotent: no-op if status != processing.
    Used by admin tooling to recover from worker crashes.
    """
    video = db.query(JugglingVideo).filter(JugglingVideo.id == video_id).first()
    if video is None or video.status != JugglingVideoStatus.processing.value:
        return video
    video.status     = JugglingVideoStatus.uploaded.value
    video.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(video)
    return video


def save_file(file_bytes: bytes, filename: str) -> Path:
    """
    Write file_bytes to JUGGLING_UPLOAD_DIR/{filename}.
    Creates the directory if it does not exist.
    Returns the full Path.
    """
    JUGGLING_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = JUGGLING_UPLOAD_DIR / filename
    dest.write_bytes(file_bytes)
    return dest


# ── P2 transcode state helpers ────────────────────────────────────────────────

def set_transcode_processing(video_id: str, db: Session) -> JugglingVideo:
    """Set transcode_status=processing before dispatching ffmpeg."""
    video = db.query(JugglingVideo).filter(JugglingVideo.id == video_id).first()
    if video is None:
        raise ValueError(f"video_not_found: {video_id}")
    video.transcode_status = JugglingTranscodeStatus.processing.value
    video.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(video)
    return video


def apply_transcode_result(
    video_id: str,
    result: "TranscodeResult",
    db: Session,
) -> JugglingVideo:
    """
    Persist TranscodeResult fields to DB after transcode_service.transcode() returns.
    Imports TranscodeResult locally to avoid circular imports.
    """
    video = db.query(JugglingVideo).filter(JugglingVideo.id == video_id).first()
    if video is None:
        raise ValueError(f"video_not_found: {video_id}")

    video.transcode_status    = result.status
    video.transcode_error     = result.error
    video.audio_stripped      = result.audio_stripped if result.audio_stripped else None
    video.processed_path      = str(result.processed_path) if result.processed_path else None
    video.thumbnail_path      = str(result.thumbnail_path) if result.thumbnail_path else None
    video.processed_resolution     = result.processed_resolution
    video.processed_fps            = result.processed_fps
    video.processed_file_size_bytes = result.processed_file_size_bytes
    video.checksum_processed  = result.checksum_processed
    video.updated_at          = datetime.now(timezone.utc)
    db.commit()
    db.refresh(video)
    return video


def apply_transcode_failure(
    video_id: str,
    reason: str,
    db: Session,
) -> JugglingVideo:
    """Set transcode_status=failed with an error message."""
    video = db.query(JugglingVideo).filter(JugglingVideo.id == video_id).first()
    if video is None:
        raise ValueError(f"video_not_found: {video_id}")
    video.transcode_status = JugglingTranscodeStatus.failed.value
    video.transcode_error  = reason
    video.updated_at       = datetime.now(timezone.utc)
    db.commit()
    db.refresh(video)
    return video


def is_gdpr_deleted(video: JugglingVideo) -> bool:
    """Return True if this video has been permanently GDPR-deleted."""
    return video.status == JugglingVideoStatus.gdpr_deleted.value


# ── Media delete ──────────────────────────────────────────────────────────────

def _try_delete_file(raw_path: str) -> bool:
    """
    Unlink a file at raw_path. Returns True on success or if the file is already absent.
    Returns False on OSError (permissions, I/O error, etc.).
    """
    try:
        p = Path(raw_path)
        if p.exists():
            p.unlink()
        return True
    except OSError as exc:
        logger.warning("media_delete_unlink_failed", extra={"error": str(exc)})
        return False


def delete_media(
    video_id: str,
    user_id: int,
    db: Session,
) -> Dict[str, Any]:
    """
    User-initiated media delete: removes physical files and nulls path/checksum fields.

    Preserved (never touched by this function):
      quality_score, quality_status, quality_detail, rejection_reason,
      server_detected_metadata, processed_resolution, processed_fps,
      processed_file_size_bytes, transcode_status, audio_stripped,
      annotation_status, annotation_finished_at, total_juggling_count,
      file_size_bytes, source_type, upload_source, created_at.

    Contact events are not touched — they remain fully active.

    Returns one of:
      {"status": "deleted",  "video_id": str}              success
      {"status": "skipped",  "reason": "already_deleted"}  already media_deleted (idempotent)
      {"status": "error",    "reason": "not_found"}         no video for this user
      {"status": "error",    "reason": "gdpr_deleted"}      terminal state — caller should 410
      {"status": "failed",   "reason": str}                 file deletion OSError
    """
    video = (
        db.query(JugglingVideo)
        .filter(JugglingVideo.id == video_id, JugglingVideo.user_id == user_id)
        .first()
    )
    if video is None:
        return {"status": "error", "reason": "not_found"}

    if video.status == JugglingVideoStatus.gdpr_deleted.value:
        return {"status": "error", "reason": "gdpr_deleted"}

    if video.status == JugglingVideoStatus.media_deleted.value:
        return {"status": "skipped", "reason": "already_deleted"}

    # Collect file paths to attempt (original + processed + thumbnail).
    # storage_path is a fallback for older rows where original_path was not set.
    paths_to_delete: List[Tuple[str, str]] = []
    for field_name, attr in [
        ("original",   video.original_path),
        ("processed",  video.processed_path),
        ("thumbnail",  video.thumbnail_path),
    ]:
        if attr:
            paths_to_delete.append((field_name, attr))
    if video.storage_path and not video.original_path:
        paths_to_delete.append(("original", video.storage_path))

    # Attempt each file deletion, collecting failures.
    all_succeeded = True
    failed_files: List[str] = []

    for file_type, raw_path in paths_to_delete:
        success = _try_delete_file(raw_path)
        if not success:
            all_succeeded = False
            failed_files.append(file_type)
        write_deletion_log(
            db=db,
            event_type="user_media_delete",
            video_id=video_id,
            user_id=user_id,
            file_type=file_type,
            raw_path=raw_path,
            dry_run=False,
            success=success,
        )

    if not all_succeeded:
        # Partial or full file failure: do NOT transition to media_deleted.
        # The record stays in its current status; retention_error is set for debugging.
        error_msg = f"file_delete_failed:{','.join(failed_files)}"
        video.retention_error = error_msg
        video.retention_last_checked_at = datetime.now(timezone.utc)
        video.updated_at = datetime.now(timezone.utc)
        db.commit()
        write_deletion_log(
            db=db,
            event_type="user_media_delete",
            video_id=video_id,
            user_id=user_id,
            file_type="all",
            dry_run=False,
            success=False,
            error_message=error_msg,
        )
        return {"status": "failed", "reason": error_msg}

    # All files deleted (or were already absent). Null media-related fields only.
    # Analysis, quality, and annotation fields are intentionally left untouched.
    video.storage_path            = None
    video.original_path           = None
    video.processed_path          = None
    video.thumbnail_path          = None
    video.filename_stored         = None
    video.checksum_sha256         = None
    video.checksum_processed      = None
    video.client_reported_metadata = None

    video.status          = JugglingVideoStatus.media_deleted.value
    video.deleted_at      = datetime.now(timezone.utc)
    video.deletion_reason = "user_request"
    video.retention_error = None
    video.updated_at      = datetime.now(timezone.utc)
    db.commit()

    write_deletion_log(
        db=db,
        event_type="user_media_delete",
        video_id=video_id,
        user_id=user_id,
        file_type="all",
        dry_run=False,
        success=True,
    )
    logger.info("media_delete_completed", extra={"video_id": video_id})
    return {"status": "deleted", "video_id": video_id}


def set_uploaded_with_original(
    video: JugglingVideo,
    storage_path: str,
    filename_stored: str,
    file_size_bytes: int,
    checksum_sha256: str,
    db: Session,
) -> JugglingVideo:
    """
    Extended set_uploaded that also writes original_path = storage_path.
    Used by the upload endpoint for all new uploads in P2.
    """
    video.status          = JugglingVideoStatus.uploaded.value
    video.storage_path    = storage_path
    video.original_path   = storage_path   # P2: track original path explicitly
    video.filename_stored = filename_stored
    video.file_size_bytes = file_size_bytes
    video.checksum_sha256 = checksum_sha256
    video.updated_at      = datetime.now(timezone.utc)
    db.commit()
    db.refresh(video)
    return video