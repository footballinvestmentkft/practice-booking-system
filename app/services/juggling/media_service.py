"""
Juggling P4 — Media Service.

Resolves file paths for thumbnail and media endpoints.
All logic here is pure-function and testable without HTTP layer.

Invariants:
  1. original_path / storage_path NEVER returned — only thumbnail_path and processed_path.
  2. Raw path NEVER logged — only video_id and field name.
  3. Path safety: resolved path must be under JUGGLING_UPLOAD_DIR.
  4. gdpr_deleted is handled upstream by _get_video_or_404 (410 before reaching here).

Scope boundary (NEVER add):
  MediaPipe / ONNX / FootAndBall / contact detection / labeling / S3 / public URL.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings
from app.models.juggling import JugglingVideo, JugglingVideoStatus, JugglingTranscodeStatus

logger = logging.getLogger(__name__)

_UPLOAD_DIR = Path(settings.JUGGLING_UPLOAD_DIR)

# Statuses where thumbnail may not be generated yet
_THUMBNAIL_NOT_READY_STATUSES = frozenset({
    JugglingVideoStatus.pending_upload.value,
    JugglingVideoStatus.uploaded.value,
    JugglingVideoStatus.processing.value,
})

# Statuses where media is not serveable
_MEDIA_NOT_READY_STATUSES = frozenset({
    JugglingVideoStatus.pending_upload.value,
    JugglingVideoStatus.uploaded.value,
    JugglingVideoStatus.processing.value,
    JugglingVideoStatus.rejected.value,
    JugglingVideoStatus.failed.value,
})


class ThumbnailNotReadyError(Exception):
    """Video status does not permit thumbnail serving yet."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ThumbnailMissingError(Exception):
    """thumbnail_path is None or file does not exist on disk."""


class MediaNotReadyError(Exception):
    """Video status or transcode state does not permit media serving."""
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class MediaMissingError(Exception):
    """processed_path is None or file does not exist on disk."""


class PathSafetyError(Exception):
    """Resolved path is outside JUGGLING_UPLOAD_DIR — should never happen in normal operation."""


def _assert_safe_path(raw_path: str, video_id: str, field: str) -> Path:
    """Verify path is under JUGGLING_UPLOAD_DIR. Never logs the raw path."""
    p = Path(raw_path).resolve()
    upload_dir = _UPLOAD_DIR.resolve()
    try:
        p.relative_to(upload_dir)
    except ValueError:
        logger.warning(
            "media_path_safety_violation",
            extra={"video_id": video_id, "field": field},
        )
        raise PathSafetyError(f"path_outside_upload_dir: field={field}")
    return p


def resolve_thumbnail_path(video: JugglingVideo) -> Path:
    """
    Return the resolved thumbnail path for the given video.

    Raises:
        ThumbnailNotReadyError  — status is pending_upload / uploaded / processing
        ThumbnailMissingError   — thumbnail_path is None or file not on disk
        PathSafetyError         — path is outside JUGGLING_UPLOAD_DIR
    """
    video_id = str(video.id)

    if video.status in _THUMBNAIL_NOT_READY_STATUSES:
        raise ThumbnailNotReadyError(f"thumbnail_not_ready: status={video.status}")

    if not video.thumbnail_path:
        raise ThumbnailMissingError("thumbnail_not_generated")

    p = _assert_safe_path(video.thumbnail_path, video_id, "thumbnail_path")

    if not p.exists():
        logger.info(
            "juggling_thumbnail_file_missing",
            extra={"video_id": video_id, "field": "thumbnail_path"},
        )
        raise ThumbnailMissingError("thumbnail_file_missing")

    return p


def resolve_media_path(video: JugglingVideo) -> Path:
    """
    Return the resolved processed_path for the given video.

    original_path / storage_path are NEVER returned — processed_path only.

    Raises:
        MediaNotReadyError  — status blocks serving, or transcode skipped/failed with no processed file
        MediaMissingError   — processed_path file not found on disk
        PathSafetyError     — path is outside JUGGLING_UPLOAD_DIR
    """
    video_id = str(video.id)

    # Status-level gate
    if video.status in _MEDIA_NOT_READY_STATUSES:
        raise MediaNotReadyError(f"media_not_ready: status={video.status}")

    # Transcode-level gate (analyzed status but transcode not done)
    transcode = video.transcode_status
    if transcode is not None:
        if transcode == JugglingTranscodeStatus.failed.value:
            raise MediaNotReadyError("media_not_ready: transcode_failed")
        if transcode == JugglingTranscodeStatus.skipped.value and not video.processed_path:
            raise MediaNotReadyError("media_not_ready: transcode_skipped")
        if transcode == JugglingTranscodeStatus.processing.value:
            raise MediaNotReadyError("media_not_ready: transcode_processing")

    # processed_path must be set (conservative fallback guard)
    if not video.processed_path:
        raise MediaNotReadyError("media_not_ready: no_processed_path")

    p = _assert_safe_path(video.processed_path, video_id, "processed_path")

    if not p.exists():
        logger.info(
            "juggling_media_file_missing",
            extra={"video_id": video_id, "field": "processed_path"},
        )
        raise MediaMissingError("media_file_missing")

    return p