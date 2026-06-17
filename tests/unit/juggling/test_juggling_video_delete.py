"""
Juggling media delete unit tests — VD-01..18.

Tests for:
  - video_service.delete_media()          (VD-01..10, VD-15..18)
  - video_service.apply_analysis()        (VD-11)
  - video_service.apply_rejection()       (VD-12)
  - video_service.apply_failure()         (VD-13, VD-14)

Service-layer tests only — no HTTP endpoints.
Uses the real PostgreSQL test DB via the test_db fixture (SAVEPOINT rollback).

Run: pytest tests/unit/juggling/test_juggling_video_delete.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.models.juggling import (
    JugglingContactEvent,
    JugglingVideo,
    JugglingVideoStatus,
)
from app.models.user import User, UserRole
from app.services.juggling.video_service import (
    apply_analysis,
    apply_failure,
    apply_rejection,
    delete_media,
)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _make_user(db) -> User:
    user = User(
        name=f"DelTest {uuid.uuid4().hex[:6]}",
        email=f"deltest+{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x",
        role=UserRole.STUDENT,
    )
    db.add(user)
    db.flush()
    return user


def _make_video(
    db,
    user_id: int,
    status: str = JugglingVideoStatus.analyzed.value,
    original_path: str | None = None,
    processed_path: str | None = None,
    thumbnail_path: str | None = None,
    storage_path: str | None = None,
    quality_score: str | None = "0.85",
    quality_status: str | None = "acceptable",
    quality_detail: dict | None = None,
    server_detected_metadata: dict | None = None,
    annotation_status: str | None = "annotated",
    total_juggling_count: int | None = 42,
) -> JugglingVideo:
    v = JugglingVideo(
        id=uuid.uuid4(),
        user_id=user_id,
        source_type="in_app_capture",
        upload_source="camera",
        status=status,
        original_path=original_path,
        processed_path=processed_path,
        thumbnail_path=thumbnail_path,
        storage_path=storage_path,
        quality_score=quality_score,
        quality_status=quality_status,
        quality_detail=quality_detail or {"fps_detected": 30},
        server_detected_metadata=server_detected_metadata or {"fps": 30},
        annotation_status=annotation_status,
        total_juggling_count=total_juggling_count,
    )
    db.add(v)
    db.flush()
    return v


def _make_contact_event(db, video: JugglingVideo, user_id: int) -> JugglingContactEvent:
    ev = JugglingContactEvent(
        id=uuid.uuid4(),
        video_id=video.id,
        created_by_user_id=user_id,
        device_event_id=uuid.uuid4(),
        timestamp_ms=1000,
        contact_type="right_foot_top",
        annotation_confidence="certain",
        annotation_source="manual_user",
    )
    db.add(ev)
    db.flush()
    return ev


# ── VD-01..04: Success from various statuses ─────────────────────────────────

class TestDeleteMediaSuccess:
    def test_vd01_analyzed_video_deleted(self, test_db, tmp_path):
        """VD-01: analyzed video → media_deleted, paths nulled."""
        orig = tmp_path / "orig.mp4"
        proc = tmp_path / "proc.mp4"
        thumb = tmp_path / "thumb.jpg"
        orig.write_bytes(b"o")
        proc.write_bytes(b"p")
        thumb.write_bytes(b"t")

        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.analyzed.value,
            original_path=str(orig),
            processed_path=str(proc),
            thumbnail_path=str(thumb),
        )

        result = delete_media(str(video.id), user.id, test_db)

        assert result["status"] == "deleted"
        assert not orig.exists()
        assert not proc.exists()
        assert not thumb.exists()

        test_db.refresh(video)
        assert video.status == JugglingVideoStatus.media_deleted.value
        assert video.original_path is None
        assert video.processed_path is None
        assert video.thumbnail_path is None
        assert video.deletion_reason == "user_request"
        assert video.deleted_at is not None

    def test_vd02_uploaded_video_deleted(self, test_db, tmp_path):
        """VD-02: uploaded video (only original file) → media_deleted."""
        orig = tmp_path / "video.mp4"
        orig.write_bytes(b"x")

        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.uploaded.value,
            original_path=str(orig),
            quality_score=None,
            quality_status=None,
            quality_detail=None,
            server_detected_metadata=None,
            annotation_status=None,
            total_juggling_count=None,
        )

        result = delete_media(str(video.id), user.id, test_db)

        assert result["status"] == "deleted"
        assert not orig.exists()
        test_db.refresh(video)
        assert video.status == JugglingVideoStatus.media_deleted.value

    def test_vd03_rejected_video_deleted(self, test_db, tmp_path):
        """VD-03: rejected video → media_deleted."""
        orig = tmp_path / "bad.mp4"
        orig.write_bytes(b"x")

        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.rejected.value,
            original_path=str(orig),
        )

        result = delete_media(str(video.id), user.id, test_db)

        assert result["status"] == "deleted"
        test_db.refresh(video)
        assert video.status == JugglingVideoStatus.media_deleted.value

    def test_vd04_failed_video_deleted(self, test_db, tmp_path):
        """VD-04: failed video → media_deleted."""
        orig = tmp_path / "corrupt.mp4"
        orig.write_bytes(b"x")

        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.failed.value,
            original_path=str(orig),
            quality_score=None,
            quality_status=None,
            quality_detail=None,
            server_detected_metadata=None,
            annotation_status=None,
            total_juggling_count=None,
        )

        result = delete_media(str(video.id), user.id, test_db)

        assert result["status"] == "deleted"
        test_db.refresh(video)
        assert video.status == JugglingVideoStatus.media_deleted.value


# ── VD-05..06: Idempotency and terminal guard ─────────────────────────────────

class TestDeleteMediaIdempotencyAndGuards:
    def test_vd05_already_media_deleted_is_idempotent(self, test_db):
        """VD-05: Calling delete_media on an already media_deleted video returns skipped."""
        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.media_deleted.value,
        )

        result = delete_media(str(video.id), user.id, test_db)

        assert result["status"] == "skipped"
        assert result["reason"] == "already_deleted"
        test_db.refresh(video)
        assert video.status == JugglingVideoStatus.media_deleted.value

    def test_vd06_gdpr_deleted_returns_error(self, test_db):
        """VD-06: gdpr_deleted video → error with reason gdpr_deleted (caller should 410)."""
        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.gdpr_deleted.value,
        )

        result = delete_media(str(video.id), user.id, test_db)

        assert result["status"] == "error"
        assert result["reason"] == "gdpr_deleted"


# ── VD-07: Partial file failure ───────────────────────────────────────────────

class TestDeleteMediaFileFailure:
    def test_vd07_partial_file_failure_status_unchanged(self, test_db, tmp_path):
        """VD-07: OSError during file deletion → status NOT changed, retention_error set."""
        orig = tmp_path / "video.mp4"
        orig.write_bytes(b"x")

        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.analyzed.value,
            original_path=str(orig),
        )
        original_status = video.status

        with patch(
            "app.services.juggling.video_service._try_delete_file",
            return_value=False,
        ):
            result = delete_media(str(video.id), user.id, test_db)

        assert result["status"] == "failed"
        assert "file_delete_failed" in result["reason"]

        test_db.refresh(video)
        assert video.status == original_status
        assert video.retention_error is not None
        assert "file_delete_failed" in video.retention_error


# ── VD-08..09: Data preservation ─────────────────────────────────────────────

class TestDeleteMediaDataPreservation:
    def test_vd08_analysis_data_preserved_after_delete(self, test_db, tmp_path):
        """VD-08: quality_score, quality_status, quality_detail, server_detected_metadata,
        processed_resolution, processed_fps, annotation_status, total_juggling_count
        are all preserved after media delete."""
        orig = tmp_path / "v.mp4"
        orig.write_bytes(b"x")

        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.analyzed.value,
            original_path=str(orig),
            quality_score="0.92",
            quality_status="acceptable",
            quality_detail={"blur_score": 0.95, "fps_detected": 30},
            server_detected_metadata={"fps": 30, "resolution": "1280x720"},
            annotation_status="annotated",
            total_juggling_count=77,
        )
        video.processed_resolution = "1280x720"
        video.processed_fps = 30.0
        test_db.flush()

        result = delete_media(str(video.id), user.id, test_db)
        assert result["status"] == "deleted"

        test_db.refresh(video)
        assert video.quality_score == "0.92"
        assert video.quality_status == "acceptable"
        assert video.quality_detail is not None
        assert video.server_detected_metadata is not None
        assert video.annotation_status == "annotated"
        assert video.total_juggling_count == 77
        assert video.processed_resolution == "1280x720"
        assert video.processed_fps == 30.0

    def test_vd09_contact_events_unchanged_after_delete(self, test_db, tmp_path):
        """VD-09: Contact events remain fully active after media delete."""
        orig = tmp_path / "v.mp4"
        orig.write_bytes(b"x")

        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.analyzed.value,
            original_path=str(orig),
        )
        ev1 = _make_contact_event(test_db, video, user.id)
        ev2 = _make_contact_event(test_db, video, user.id)
        test_db.commit()

        result = delete_media(str(video.id), user.id, test_db)
        assert result["status"] == "deleted"

        test_db.refresh(ev1)
        test_db.refresh(ev2)
        assert ev1.deleted_at is None
        assert ev2.deleted_at is None

        remaining = (
            test_db.query(JugglingContactEvent)
            .filter(JugglingContactEvent.video_id == video.id)
            .all()
        )
        assert len(remaining) == 2


# ── VD-10..14: Race guards for Celery task callbacks ─────────────────────────

class TestRaceGuards:
    def test_vd10_apply_analysis_noop_on_media_deleted(self, test_db):
        """VD-10: apply_analysis on a media_deleted video is a no-op (race guard)."""
        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.media_deleted.value,
            quality_score=None,
        )
        original_status = video.status

        result = apply_analysis(
            video_id=str(video.id),
            server_detected_metadata={"fps": 30},
            quality_score=0.99,
            quality_status="acceptable",
            quality_detail={"fps_detected": 30},
            db=test_db,
        )

        test_db.refresh(video)
        assert video.status == original_status
        assert video.quality_score is None

    def test_vd11_apply_rejection_noop_on_media_deleted(self, test_db):
        """VD-11: apply_rejection on a media_deleted video is a no-op."""
        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.media_deleted.value,
        )
        original_status = video.status

        apply_rejection(
            video_id=str(video.id),
            server_detected_metadata=None,
            rejection_reason="too_dark",
            db=test_db,
        )

        test_db.refresh(video)
        assert video.status == original_status

    def test_vd12_apply_failure_noop_on_media_deleted(self, test_db):
        """VD-12: apply_failure on a media_deleted video is a no-op."""
        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.media_deleted.value,
        )
        original_status = video.status

        apply_failure(
            video_id=str(video.id),
            reason="ffprobe_timeout",
            db=test_db,
        )

        test_db.refresh(video)
        assert video.status == original_status

    def test_vd13_apply_analysis_noop_on_gdpr_deleted(self, test_db):
        """VD-13: apply_analysis on a gdpr_deleted video is also a no-op."""
        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.gdpr_deleted.value,
            quality_score=None,
        )

        apply_analysis(
            video_id=str(video.id),
            server_detected_metadata={"fps": 30},
            quality_score=0.99,
            quality_status="acceptable",
            quality_detail={"fps_detected": 30},
            db=test_db,
        )

        test_db.refresh(video)
        assert video.status == JugglingVideoStatus.gdpr_deleted.value
        assert video.quality_score is None

    def test_vd14_apply_analysis_still_works_on_processing(self, test_db):
        """VD-14: apply_analysis on a processing video still works (regression guard)."""
        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.processing.value,
            quality_score=None,
        )

        apply_analysis(
            video_id=str(video.id),
            server_detected_metadata={"fps": 30},
            quality_score=0.88,
            quality_status="acceptable",
            quality_detail={"fps_detected": 30},
            db=test_db,
        )

        test_db.refresh(video)
        assert video.status == JugglingVideoStatus.analyzed.value
        assert video.quality_score == "0.88"


# ── VD-15..18: Ownership, not_found, deletion metadata ───────────────────────

class TestDeleteMediaOwnershipAndMeta:
    def test_vd15_wrong_user_returns_not_found(self, test_db, tmp_path):
        """VD-15: delete_media with a different user_id returns not_found."""
        orig = tmp_path / "v.mp4"
        orig.write_bytes(b"x")

        owner = _make_user(test_db)
        other = _make_user(test_db)
        video = _make_video(
            test_db, owner.id,
            status=JugglingVideoStatus.analyzed.value,
            original_path=str(orig),
        )

        result = delete_media(str(video.id), other.id, test_db)

        assert result["status"] == "error"
        assert result["reason"] == "not_found"
        assert orig.exists()

    def test_vd16_nonexistent_video_returns_not_found(self, test_db):
        """VD-16: delete_media for a non-existent video_id returns not_found."""
        user = _make_user(test_db)
        result = delete_media(str(uuid.uuid4()), user.id, test_db)
        assert result["status"] == "error"
        assert result["reason"] == "not_found"

    def test_vd17_deletion_reason_is_user_request(self, test_db, tmp_path):
        """VD-17: deletion_reason is set to 'user_request' after successful media delete."""
        orig = tmp_path / "v.mp4"
        orig.write_bytes(b"x")

        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.analyzed.value,
            original_path=str(orig),
        )

        delete_media(str(video.id), user.id, test_db)

        test_db.refresh(video)
        assert video.deletion_reason == "user_request"

    def test_vd18_deleted_at_set_after_media_delete(self, test_db, tmp_path):
        """VD-18: deleted_at timestamp is set after successful media delete."""
        orig = tmp_path / "v.mp4"
        orig.write_bytes(b"x")

        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.analyzed.value,
            original_path=str(orig),
        )
        assert video.deleted_at is None

        delete_media(str(video.id), user.id, test_db)

        test_db.refresh(video)
        assert video.deleted_at is not None
        assert video.deleted_at.tzinfo is not None
