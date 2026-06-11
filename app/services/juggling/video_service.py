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
"""
from __future__ import annotations

import uuid as _uuid_mod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.models.juggling import (
    JugglingVideo,
    JugglingVideoStatus,
    JugglingVideoQualityStatus,
    JugglingTranscodeStatus,
)

JUGGLING_UPLOAD_DIR = Path(settings.JUGGLING_UPLOAD_DIR)

# Transitions that must NOT accept a new complete() call
_COMPLETE_BLOCKED_STATUSES = {
    JugglingVideoStatus.pending_upload.value,
    JugglingVideoStatus.processing.value,
    JugglingVideoStatus.analyzed.value,
    JugglingVideoStatus.rejected.value,
}


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