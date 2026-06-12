"""
Juggling P4 — media_service unit tests.

Pure-function tests: no HTTP layer, no DB, no Celery.

Run: pytest tests/unit/juggling/test_media_service.py -v
"""
from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.models.juggling import JugglingVideoStatus, JugglingTranscodeStatus
from app.services.juggling.media_service import (
    MediaMissingError,
    MediaNotReadyError,
    PathSafetyError,
    ThumbnailMissingError,
    ThumbnailNotReadyError,
    resolve_media_path,
    resolve_thumbnail_path,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _video(
    status: str = JugglingVideoStatus.analyzed.value,
    transcode_status: str | None = JugglingTranscodeStatus.done.value,
    thumbnail_path: str | None = None,
    processed_path: str | None = None,
) -> MagicMock:
    v = MagicMock()
    v.id = uuid.uuid4()
    v.status = status
    v.transcode_status = transcode_status
    v.thumbnail_path = thumbnail_path
    v.processed_path = processed_path
    return v


# ── resolve_thumbnail_path ────────────────────────────────────────────────────

class TestResolveThumbnailPath:
    def test_ms01_pending_upload_raises_not_ready(self):
        """MS-01: pending_upload status → ThumbnailNotReadyError."""
        v = _video(status=JugglingVideoStatus.pending_upload.value)
        with pytest.raises(ThumbnailNotReadyError):
            resolve_thumbnail_path(v)

    def test_ms02_uploaded_raises_not_ready(self):
        """MS-02: uploaded status → ThumbnailNotReadyError."""
        v = _video(status=JugglingVideoStatus.uploaded.value)
        with pytest.raises(ThumbnailNotReadyError):
            resolve_thumbnail_path(v)

    def test_ms03_processing_raises_not_ready(self):
        """MS-03: processing status → ThumbnailNotReadyError."""
        v = _video(status=JugglingVideoStatus.processing.value)
        with pytest.raises(ThumbnailNotReadyError):
            resolve_thumbnail_path(v)

    def test_ms04_thumbnail_path_none_raises_missing(self, tmp_path):
        """MS-04: analyzed + thumbnail_path=None → ThumbnailMissingError."""
        v = _video(thumbnail_path=None)
        with pytest.raises(ThumbnailMissingError):
            resolve_thumbnail_path(v)

    def test_ms05_file_not_on_disk_raises_missing(self, tmp_path, monkeypatch):
        """MS-05: thumbnail_path set but file absent → ThumbnailMissingError."""
        from app.services.juggling import media_service
        monkeypatch.setattr(media_service, "_UPLOAD_DIR", tmp_path)
        ghost = tmp_path / "ghost.jpg"
        v = _video(thumbnail_path=str(ghost))
        with pytest.raises(ThumbnailMissingError):
            resolve_thumbnail_path(v)

    def test_ms06_success_returns_path(self, tmp_path, monkeypatch):
        """MS-06: analyzed + file exists → returns Path."""
        from app.services.juggling import media_service
        monkeypatch.setattr(media_service, "_UPLOAD_DIR", tmp_path)
        f = tmp_path / "thumb.jpg"
        f.write_bytes(b"\xff\xd8\xff")
        v = _video(thumbnail_path=str(f))
        result = resolve_thumbnail_path(v)
        assert result == f.resolve()

    def test_ms07_rejected_with_thumbnail_returns_path(self, tmp_path, monkeypatch):
        """MS-07: rejected status + thumbnail_path exists → 200 (Option B policy)."""
        from app.services.juggling import media_service
        monkeypatch.setattr(media_service, "_UPLOAD_DIR", tmp_path)
        f = tmp_path / "thumb_rejected.jpg"
        f.write_bytes(b"\xff\xd8\xff")
        v = _video(status=JugglingVideoStatus.rejected.value, thumbnail_path=str(f))
        result = resolve_thumbnail_path(v)
        assert result == f.resolve()

    def test_ms08_failed_with_thumbnail_returns_path(self, tmp_path, monkeypatch):
        """MS-08: failed status + thumbnail_path exists → serves thumbnail."""
        from app.services.juggling import media_service
        monkeypatch.setattr(media_service, "_UPLOAD_DIR", tmp_path)
        f = tmp_path / "thumb_failed.jpg"
        f.write_bytes(b"\xff\xd8\xff")
        v = _video(status=JugglingVideoStatus.failed.value, thumbnail_path=str(f))
        result = resolve_thumbnail_path(v)
        assert result == f.resolve()

    def test_ms09_path_safety_violation_raises(self, tmp_path, monkeypatch):
        """MS-09: path outside JUGGLING_UPLOAD_DIR → PathSafetyError."""
        from app.services.juggling import media_service
        safe_dir = tmp_path / "uploads"
        safe_dir.mkdir()
        monkeypatch.setattr(media_service, "_UPLOAD_DIR", safe_dir)
        outside = tmp_path / "outside.jpg"
        outside.write_bytes(b"\xff\xd8\xff")
        v = _video(thumbnail_path=str(outside))
        with pytest.raises(PathSafetyError):
            resolve_thumbnail_path(v)


# ── resolve_media_path ────────────────────────────────────────────────────────

class TestResolveMediaPath:
    def test_ms10_rejected_raises_not_ready(self):
        """MS-10: rejected status → MediaNotReadyError."""
        v = _video(status=JugglingVideoStatus.rejected.value)
        with pytest.raises(MediaNotReadyError, match="rejected"):
            resolve_media_path(v)

    def test_ms11_failed_raises_not_ready(self):
        """MS-11: failed status → MediaNotReadyError."""
        v = _video(status=JugglingVideoStatus.failed.value)
        with pytest.raises(MediaNotReadyError, match="failed"):
            resolve_media_path(v)

    def test_ms12_processing_raises_not_ready(self):
        """MS-12: processing status → MediaNotReadyError."""
        v = _video(status=JugglingVideoStatus.processing.value)
        with pytest.raises(MediaNotReadyError):
            resolve_media_path(v)

    def test_ms13_transcode_failed_raises_not_ready(self):
        """MS-13: analyzed + transcode_status=failed → MediaNotReadyError."""
        v = _video(transcode_status=JugglingTranscodeStatus.failed.value)
        with pytest.raises(MediaNotReadyError, match="transcode_failed"):
            resolve_media_path(v)

    def test_ms14_transcode_skipped_no_processed_path_raises(self):
        """MS-14: analyzed + transcode_status=skipped + processed_path=None → MediaNotReadyError."""
        v = _video(transcode_status=JugglingTranscodeStatus.skipped.value, processed_path=None)
        with pytest.raises(MediaNotReadyError, match="transcode_skipped"):
            resolve_media_path(v)

    def test_ms15_no_processed_path_raises_not_ready(self):
        """MS-15: analyzed + transcode=done + processed_path=None → MediaNotReadyError (conservative guard)."""
        v = _video(processed_path=None)
        with pytest.raises(MediaNotReadyError, match="no_processed_path"):
            resolve_media_path(v)

    def test_ms16_file_not_on_disk_raises_missing(self, tmp_path, monkeypatch):
        """MS-16: processed_path set but file absent on disk → MediaMissingError."""
        from app.services.juggling import media_service
        monkeypatch.setattr(media_service, "_UPLOAD_DIR", tmp_path)
        ghost = tmp_path / "ghost.mp4"
        v = _video(processed_path=str(ghost))
        with pytest.raises(MediaMissingError):
            resolve_media_path(v)

    def test_ms17_success_returns_path(self, tmp_path, monkeypatch):
        """MS-17: analyzed + done + file exists → returns Path."""
        from app.services.juggling import media_service
        monkeypatch.setattr(media_service, "_UPLOAD_DIR", tmp_path)
        f = tmp_path / "processed.mp4"
        f.write_bytes(b"\x00\x00\x00")
        v = _video(processed_path=str(f))
        result = resolve_media_path(v)
        assert result == f.resolve()

    def test_ms18_original_path_never_fallback(self, tmp_path, monkeypatch):
        """MS-18: processed_path=None → raises, even if original_path were set."""
        from app.services.juggling import media_service
        monkeypatch.setattr(media_service, "_UPLOAD_DIR", tmp_path)
        v = _video(processed_path=None)
        v.original_path = str(tmp_path / "original.mp4")
        with pytest.raises(MediaNotReadyError):
            resolve_media_path(v)

    def test_ms19_path_safety_violation_raises(self, tmp_path, monkeypatch):
        """MS-19: processed_path outside JUGGLING_UPLOAD_DIR → PathSafetyError."""
        from app.services.juggling import media_service
        safe_dir = tmp_path / "uploads"
        safe_dir.mkdir()
        monkeypatch.setattr(media_service, "_UPLOAD_DIR", safe_dir)
        outside = tmp_path / "outside.mp4"
        outside.write_bytes(b"\x00")
        v = _video(processed_path=str(outside))
        with pytest.raises(PathSafetyError):
            resolve_media_path(v)

    def test_ms20_transcode_skipped_with_processed_path_serves(self, tmp_path, monkeypatch):
        """MS-20: transcode_skipped + processed_path IS set → serves the file."""
        from app.services.juggling import media_service
        monkeypatch.setattr(media_service, "_UPLOAD_DIR", tmp_path)
        f = tmp_path / "skipped_but_has_processed.mp4"
        f.write_bytes(b"\x00")
        v = _video(
            transcode_status=JugglingTranscodeStatus.skipped.value,
            processed_path=str(f),
        )
        result = resolve_media_path(v)
        assert result == f.resolve()

    def test_ms21_transcode_none_with_processed_path_serves(self, tmp_path, monkeypatch):
        """MS-21: transcode_status=None (pre-P2 row) + processed_path exists → skips transcode block → serves file."""
        from app.services.juggling import media_service
        monkeypatch.setattr(media_service, "_UPLOAD_DIR", tmp_path)
        f = tmp_path / "no_transcode_status.mp4"
        f.write_bytes(b"\x00")
        v = _video(transcode_status=None, processed_path=str(f))
        result = resolve_media_path(v)
        assert result == f.resolve()

    def test_ms22_transcode_processing_raises_not_ready(self):
        """MS-22: analyzed + transcode_status=processing → MediaNotReadyError(transcode_processing)."""
        v = _video(transcode_status=JugglingTranscodeStatus.processing.value, processed_path="/some/path.mp4")
        with pytest.raises(MediaNotReadyError, match="transcode_processing"):
            resolve_media_path(v)