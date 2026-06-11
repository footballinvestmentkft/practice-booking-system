"""
Juggling P3 Retention Service unit tests — RET-01..24.

Service-layer tests only — no HTTP layer.
Uses the real PostgreSQL test DB via the test_db fixture (SAVEPOINT rollback).

Run: pytest tests/unit/juggling/test_retention_service.py -v
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from app.config import settings
from app.models.juggling import (
    JugglingFileDeletionLog,
    JugglingVideo,
    JugglingVideoStatus,
)
from app.models.user import User, UserRole
from app.services.juggling.retention_service import (
    apply_gdpr_delete,
    cleanup_temp_files,
    hmac_path_hash,
    hmac_user_pseudonym,
    retention_expiry_scan,
    scan_missing_files,
    scan_orphan_files,
    write_deletion_log,
)


# ── DB helpers ────────────────────────────────────────────────────────────────

def _make_user(db) -> User:
    user = User(
        name=f"RetTest {uuid.uuid4().hex[:6]}",
        email=f"rettest+{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x",
        role=UserRole.STUDENT,
    )
    db.add(user)
    db.flush()
    return user


def _make_video(
    db,
    user_id: int,
    status: str = JugglingVideoStatus.uploaded.value,
    storage_path: str | None = None,
    original_path: str | None = None,
    processed_path: str | None = None,
    thumbnail_path: str | None = None,
    retention_expires_at: datetime | None = None,
) -> JugglingVideo:
    v = JugglingVideo(
        id=uuid.uuid4(),
        user_id=user_id,
        source_type="uploaded_video",
        upload_source="gallery",
        status=status,
        storage_path=storage_path,
        original_path=original_path,
        processed_path=processed_path,
        thumbnail_path=thumbnail_path,
        retention_expires_at=retention_expires_at,
    )
    db.add(v)
    db.flush()
    return v


# ── RET-01..04: HMAC helpers ──────────────────────────────────────────────────

class TestHmacHelpers:
    def test_ret01_hmac_path_hash_consistent(self, monkeypatch):
        """RET-01: hmac_path_hash returns same hex digest for same input."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        h1 = hmac_path_hash("/uploads/video.mp4")
        h2 = hmac_path_hash("/uploads/video.mp4")
        assert h1 == h2
        assert len(h1) == 64
        assert all(c in "0123456789abcdef" for c in h1)

    def test_ret02_hmac_user_pseudonym_consistent(self, monkeypatch):
        """RET-02: hmac_user_pseudonym returns same hex digest for same user_id."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        p1 = hmac_user_pseudonym(42)
        p2 = hmac_user_pseudonym(42)
        assert p1 == p2
        assert len(p1) == 64

    def test_ret03_hmac_different_paths_differ(self, monkeypatch):
        """RET-03: Different paths produce different digests."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        h1 = hmac_path_hash("/uploads/video1.mp4")
        h2 = hmac_path_hash("/uploads/video2.mp4")
        assert h1 != h2

    def test_ret04_empty_secret_raises(self, monkeypatch):
        """RET-04: Empty JUGGLING_AUDIT_HASH_SECRET raises ValueError."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "")
        with pytest.raises(ValueError, match="JUGGLING_AUDIT_HASH_SECRET"):
            hmac_path_hash("/some/path")


# ── RET-05..07: write_deletion_log ───────────────────────────────────────────

class TestWriteDeletionLog:
    def test_ret05_creates_row(self, test_db, monkeypatch):
        """RET-05: write_deletion_log inserts a row in juggling_file_deletion_log."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        run_id = uuid.uuid4().hex
        write_deletion_log(
            db=test_db,
            event_type="scan_started",
            dry_run=True,
            task_run_id=run_id,
        )
        row = test_db.query(JugglingFileDeletionLog).filter_by(
            task_run_id=run_id
        ).first()
        assert row is not None
        assert row.event_type == "scan_started"
        assert row.dry_run is True

    def test_ret06_never_stores_raw_path(self, test_db, monkeypatch):
        """RET-06: raw_path is HMAC-hashed; raw string never appears in DB."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        raw = f"/uploads/juggling/secret_{uuid.uuid4().hex}.mp4"
        write_deletion_log(
            db=test_db, event_type="gdpr_delete", raw_path=raw, dry_run=False,
        )
        rows = test_db.query(JugglingFileDeletionLog).filter_by(
            event_type="gdpr_delete"
        ).all()
        assert rows
        row = rows[-1]
        assert row.file_path_hash is not None
        assert raw not in (row.file_path_hash or "")
        assert len(row.file_path_hash) == 64

    def test_ret07_never_stores_raw_user_id(self, test_db, monkeypatch):
        """RET-07: user_id is pseudonymised; the raw integer is never stored."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        run_id = uuid.uuid4().hex
        write_deletion_log(
            db=test_db, event_type="gdpr_delete",
            user_id=99999, dry_run=False, task_run_id=run_id,
        )
        row = test_db.query(JugglingFileDeletionLog).filter_by(
            task_run_id=run_id
        ).first()
        assert row is not None
        assert row.user_pseudonym is not None
        assert "99999" not in row.user_pseudonym


# ── RET-08..13: apply_gdpr_delete ────────────────────────────────────────────

class TestApplyGdprDelete:
    def test_ret08_missing_record_returns_error(self, test_db, monkeypatch):
        """RET-08: Non-existent video_id returns error dict."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        result = apply_gdpr_delete(
            video_id=str(uuid.uuid4()), db=test_db, dry_run=False
        )
        assert result["status"] == "error"
        assert result["reason"] == "record_not_found"

    def test_ret09_idempotent_on_already_deleted(self, test_db, monkeypatch):
        """RET-09: Already gdpr_deleted video → skipped (idempotent)."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        user = _make_user(test_db)
        video = _make_video(test_db, user.id, status=JugglingVideoStatus.gdpr_deleted.value)
        result = apply_gdpr_delete(video_id=str(video.id), db=test_db, dry_run=False)
        assert result["status"] == "skipped"
        assert result["reason"] == "already_deleted"

    def test_ret10_dry_run_does_not_change_status(self, test_db, monkeypatch, tmp_path):
        """RET-10: dry_run=True returns dry_run status; record status is unchanged."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        user = _make_user(test_db)
        f = tmp_path / "video.mp4"
        f.write_bytes(b"data")
        video = _make_video(test_db, user.id, storage_path=str(f), original_path=str(f))
        original_status = video.status

        result = apply_gdpr_delete(video_id=str(video.id), db=test_db, dry_run=True)

        assert result["status"] == "dry_run"
        test_db.refresh(video)
        assert video.status == original_status

    def test_ret11_success_no_files(self, test_db, monkeypatch):
        """RET-11: Video with no file paths → succeeds; status set to gdpr_deleted."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        user = _make_user(test_db)
        video = _make_video(test_db, user.id)  # no file paths
        result = apply_gdpr_delete(video_id=str(video.id), db=test_db, dry_run=False)
        assert result["status"] == "deleted"
        test_db.refresh(video)
        assert video.status == JugglingVideoStatus.gdpr_deleted.value
        assert video.deletion_reason == "gdpr_request"
        assert video.deleted_at is not None

    def test_ret12_success_nulls_personal_fields(self, test_db, monkeypatch, tmp_path):
        """RET-12: Successful delete nulls all personal/file fields."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        user = _make_user(test_db)
        f = tmp_path / "orig.mp4"
        f.write_bytes(b"video_bytes")
        video = _make_video(
            test_db, user.id,
            original_path=str(f),
            storage_path=str(f),
        )
        video.checksum_sha256 = "abc123"
        video.client_reported_metadata = {"fps": 60}
        test_db.commit()

        result = apply_gdpr_delete(video_id=str(video.id), db=test_db, dry_run=False)
        assert result["status"] == "deleted"
        test_db.refresh(video)
        assert video.storage_path is None
        assert video.original_path is None
        assert video.checksum_sha256 is None
        assert video.client_reported_metadata is None
        assert video.status == JugglingVideoStatus.gdpr_deleted.value

    def test_ret13_partial_failure_retains_status(self, test_db, monkeypatch):
        """RET-13: OSError keeps status unchanged; retention_error is set."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            original_path="/nonexistent/ghost_video.mp4",
        )
        original_status = video.status

        with patch("app.services.juggling.retention_service._try_unlink", return_value=False):
            result = apply_gdpr_delete(video_id=str(video.id), db=test_db, dry_run=False)

        assert result["status"] == "failed"
        test_db.refresh(video)
        assert video.status == original_status
        assert video.retention_error is not None


# ── RET-14..17: scan_orphan_files ────────────────────────────────────────────

class TestScanOrphanFiles:
    def test_ret14_dry_run_no_deletion(self, test_db, monkeypatch, tmp_path):
        """RET-14: dry_run=True logs but never deletes files."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        orphan = tmp_path / "orphan.mp4"
        orphan.write_bytes(b"data")
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).timestamp()
        os.utime(str(orphan), (old_ts, old_ts))

        result = scan_orphan_files(
            upload_dir=tmp_path, db=test_db, dry_run=True, grace_period_hours=24
        )
        assert result["orphan_files_deleted"] == 0
        assert orphan.exists()

    def test_ret15_finds_old_orphan(self, test_db, monkeypatch, tmp_path):
        """RET-15: File older than grace period is reported as an orphan."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        orphan = tmp_path / "old_orphan.mp4"
        orphan.write_bytes(b"data")
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).timestamp()
        os.utime(str(orphan), (old_ts, old_ts))

        result = scan_orphan_files(
            upload_dir=tmp_path, db=test_db, dry_run=True, grace_period_hours=24
        )
        assert result["orphan_files_found"] >= 1

    def test_ret16_skips_files_within_grace(self, test_db, monkeypatch, tmp_path):
        """RET-16: Recently created files (within grace period) are not reported."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        new_file = tmp_path / "new_file.mp4"
        new_file.write_bytes(b"data")
        # mtime is effectively now

        result = scan_orphan_files(
            upload_dir=tmp_path, db=test_db, dry_run=True, grace_period_hours=24
        )
        assert result["orphan_files_found"] == 0

    def test_ret17_skips_known_db_paths(self, test_db, monkeypatch, tmp_path):
        """RET-17: Files referenced in DB are excluded from orphan scan."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        known = tmp_path / "known.mp4"
        known.write_bytes(b"data")
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).timestamp()
        os.utime(str(known), (old_ts, old_ts))

        user = _make_user(test_db)
        _make_video(test_db, user.id, storage_path=str(known))
        test_db.commit()

        result = scan_orphan_files(
            upload_dir=tmp_path, db=test_db, dry_run=True, grace_period_hours=24
        )
        assert result["orphan_files_found"] == 0


# ── RET-18..19: scan_missing_files ───────────────────────────────────────────

class TestScanMissingFiles:
    def test_ret18_finds_missing_path(self, test_db, monkeypatch):
        """RET-18: dry_run=True reports records where a path does not exist on disk."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        user = _make_user(test_db)
        _make_video(test_db, user.id, storage_path="/nonexistent/ghost_file.mp4")
        test_db.commit()

        result = scan_missing_files(db=test_db, dry_run=True, batch_size=100)
        assert result["missing_files_found"] >= 1

    def test_ret19_dry_run_false_nulls_paths(self, test_db, monkeypatch):
        """RET-19: dry_run=False nulls missing path fields and sets retention_error."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        user = _make_user(test_db)
        video = _make_video(
            test_db, user.id,
            storage_path="/nonexistent/ghost_file2.mp4",
        )
        test_db.commit()

        result = scan_missing_files(db=test_db, dry_run=False, batch_size=100)
        assert result["missing_paths_nulled"] >= 1
        test_db.refresh(video)
        assert video.storage_path is None
        assert video.retention_error is not None


# ── RET-20..22: cleanup_temp_files ───────────────────────────────────────────

class TestCleanupTempFiles:
    def test_ret20_dry_run_no_deletion(self, test_db, monkeypatch, tmp_path):
        """RET-20: dry_run=True does NOT delete .tmp. files."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        tmp_file = tmp_path / "abc.tmp.xyz"
        tmp_file.write_bytes(b"data")
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
        os.utime(str(tmp_file), (old_ts, old_ts))

        result = cleanup_temp_files(
            upload_dir=tmp_path, db=test_db, dry_run=True, min_age_hours=1.0
        )
        assert result["temp_files_deleted"] == 0
        assert tmp_file.exists()

    def test_ret21_deletes_old_tmp_files(self, test_db, monkeypatch, tmp_path):
        """RET-21: dry_run=False deletes old .tmp.* files."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        tmp_file = tmp_path / "old.tmp.mp4"
        tmp_file.write_bytes(b"data")
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).timestamp()
        os.utime(str(tmp_file), (old_ts, old_ts))

        result = cleanup_temp_files(
            upload_dir=tmp_path, db=test_db, dry_run=False, min_age_hours=1.0
        )
        assert result["temp_files_deleted"] >= 1
        assert not tmp_file.exists()

    def test_ret22_skips_recent_tmp_files(self, test_db, monkeypatch, tmp_path):
        """RET-22: .tmp. files newer than min_age_hours are left alone."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        tmp_file = tmp_path / "recent.tmp.mp4"
        tmp_file.write_bytes(b"data")
        # mtime is effectively now

        result = cleanup_temp_files(
            upload_dir=tmp_path, db=test_db, dry_run=False, min_age_hours=1.0
        )
        assert result["temp_files_deleted"] == 0
        assert tmp_file.exists()


# ── RET-23..24: retention_expiry_scan ────────────────────────────────────────

class TestRetentionExpiryScan:
    def test_ret23_dry_run_no_deletion(self, test_db, monkeypatch, tmp_path):
        """RET-23: dry_run=True finds expired records but does NOT delete them."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        user = _make_user(test_db)
        past = datetime.now(timezone.utc) - timedelta(days=1)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.analyzed.value,
            retention_expires_at=past,
        )
        test_db.commit()

        result = retention_expiry_scan(
            db=test_db, upload_dir=tmp_path, dry_run=True, batch_size=10
        )
        assert result["retention_expired_found"] >= 1
        assert result["retention_expired_deleted"] == 0
        test_db.refresh(video)
        assert video.status == JugglingVideoStatus.analyzed.value

    def test_ret24_dry_run_false_applies_gdpr_delete(self, test_db, monkeypatch, tmp_path):
        """RET-24: dry_run=False applies gdpr_delete for expired records."""
        monkeypatch.setattr(settings, "JUGGLING_AUDIT_HASH_SECRET", "test-secret-ret")
        user = _make_user(test_db)
        past = datetime.now(timezone.utc) - timedelta(days=1)
        video = _make_video(
            test_db, user.id,
            status=JugglingVideoStatus.analyzed.value,
            retention_expires_at=past,
        )
        test_db.commit()

        result = retention_expiry_scan(
            db=test_db, upload_dir=tmp_path, dry_run=False, batch_size=10
        )
        assert result["retention_expired_deleted"] >= 1
        test_db.refresh(video)
        assert video.status == JugglingVideoStatus.gdpr_deleted.value
        assert video.deletion_reason == "retention_expired"
