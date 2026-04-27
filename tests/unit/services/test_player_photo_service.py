"""
Player Photo Service — URL uniqueness and cache-busting tests
=============================================================
Validates that every upload produces a new unique URL so browser/CDN caches
cannot serve stale images after delete + re-upload.

Root cause that prompted this suite:
  _save_variant_photo used a fixed filename ({user_id}_{suffix}.png) with no
  epoch component. Re-uploading wrote new content to the SAME path, leaving the
  URL unchanged. Browsers cached the old response and returned it even after
  the file content changed.

Fix: every variant save now appends _{epoch} to the filename, and old files
(timestamped + legacy fixed-name) are deleted before writing the new one.

All tests use tmp_path (pytest built-in) — no network, no DB, no disk side-effects.
"""
import io
import time
from unittest.mock import patch

import pytest
from PIL import Image

import app.services.player_photo_service as svc


# ── Helpers ───────────────────────────────────────────────────────────────────

def _png_bytes(size: tuple[int, int] = (100, 100), mode: str = "RGBA") -> bytes:
    """Return minimal valid PNG bytes."""
    img = Image.new(mode, size, color=(255, 0, 0, 128))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _jpeg_bytes(size: tuple[int, int] = (100, 100)) -> bytes:
    img = Image.new("RGB", size, color=(0, 128, 0))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def _patch_photo_dir(tmp_path):
    """Context manager: redirect PHOTO_DIR to tmp_path for isolation."""
    return patch.object(svc, "PHOTO_DIR", tmp_path)


# ── URL uniqueness: each upload must produce a different URL ──────────────────

class TestURLUniqueness:

    def test_portrait_upload_twice_gives_different_urls(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url1 = svc.save_portrait_photo(_png_bytes(), "image/png", user_id=11)
            time.sleep(1.1)  # ensure epoch differs
            url2 = svc.save_portrait_photo(_png_bytes(), "image/png", user_id=11)
        assert url1 != url2, (
            f"portrait re-upload must produce a new URL — got same: {url1!r}"
        )

    def test_compact_bg_upload_twice_gives_different_urls(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url1 = svc.save_compact_bg_photo(_png_bytes(), "image/png", user_id=2)
            time.sleep(1.1)
            url2 = svc.save_compact_bg_photo(_png_bytes(), "image/png", user_id=2)
        assert url1 != url2, (
            f"compact_bg re-upload must produce a new URL — got same: {url1!r}"
        )

    def test_landscape_upload_twice_gives_different_urls(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url1 = svc.save_landscape_photo(_png_bytes(), "image/png", user_id=3)
            time.sleep(1.1)
            url2 = svc.save_landscape_photo(_png_bytes(), "image/png", user_id=3)
        assert url1 != url2

    def test_showcase_bg_upload_twice_gives_different_urls(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url1 = svc.save_showcase_bg_photo(_png_bytes(), "image/png", user_id=4)
            time.sleep(1.1)
            url2 = svc.save_showcase_bg_photo(_png_bytes(), "image/png", user_id=4)
        assert url1 != url2

    def test_orig_photo_already_unique(self, tmp_path):
        """save_player_photo already used epoch — verify it still does."""
        with _patch_photo_dir(tmp_path):
            url1 = svc.save_player_photo(_png_bytes(), "image/png", user_id=5)
            time.sleep(1.1)
            url2 = svc.save_player_photo(_png_bytes(), "image/png", user_id=5)
        assert url1 != url2


# ── URL contains epoch (timestamp in filename) ────────────────────────────────

class TestURLContainsEpoch:

    def test_portrait_url_has_epoch(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url = svc.save_portrait_photo(_png_bytes(), "image/png", user_id=10)
        # URL must match pattern: .../10_portrait_{digits}.png
        assert url.startswith("/static/uploads/lfa_player_photos/10_portrait_")
        assert url.endswith(".png")
        epoch_part = url.split("_")[-1].replace(".png", "")
        assert epoch_part.isdigit(), f"Expected epoch digits in URL, got: {url!r}"

    def test_compact_bg_url_has_epoch(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url = svc.save_compact_bg_photo(_png_bytes(), "image/png", user_id=10)
        assert url.startswith("/static/uploads/lfa_player_photos/10_bg_compact_")
        epoch_part = url.split("_")[-1].replace(".png", "")
        assert epoch_part.isdigit(), f"Expected epoch digits in URL, got: {url!r}"

    def test_landscape_url_has_epoch(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url = svc.save_landscape_photo(_png_bytes(), "image/png", user_id=10)
        assert url.startswith("/static/uploads/lfa_player_photos/10_landscape_")
        epoch_part = url.split("_")[-1].replace(".png", "")
        assert epoch_part.isdigit()

    def test_showcase_bg_url_has_epoch(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url = svc.save_showcase_bg_photo(_png_bytes(), "image/png", user_id=10)
        assert url.startswith("/static/uploads/lfa_player_photos/10_bg_showcase_")
        epoch_part = url.split("_")[-1].replace(".png", "")
        assert epoch_part.isdigit()


# ── Delete + re-upload: old URL must NOT reappear ─────────────────────────────

class TestDeleteReupload:

    def test_portrait_delete_then_reupload_new_url(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url1 = svc.save_portrait_photo(_png_bytes(), "image/png", user_id=20)
            svc.delete_portrait_photo(20)
            time.sleep(1.1)
            url2 = svc.save_portrait_photo(_png_bytes(), "image/png", user_id=20)
        assert url1 != url2, "After delete+reupload, URL must be new (cache-bust)"

    def test_compact_bg_delete_then_reupload_new_url(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url1 = svc.save_compact_bg_photo(_png_bytes(), "image/png", user_id=21)
            svc.delete_compact_bg_photo(21)
            time.sleep(1.1)
            url2 = svc.save_compact_bg_photo(_png_bytes(), "image/png", user_id=21)
        assert url1 != url2

    def test_showcase_bg_delete_then_reupload_new_url(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url1 = svc.save_showcase_bg_photo(_png_bytes(), "image/png", user_id=22)
            svc.delete_showcase_bg_photo(22)
            time.sleep(1.1)
            url2 = svc.save_showcase_bg_photo(_png_bytes(), "image/png", user_id=22)
        assert url1 != url2

    def test_landscape_delete_then_reupload_new_url(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url1 = svc.save_landscape_photo(_png_bytes(), "image/png", user_id=23)
            svc.delete_landscape_photo(23)
            time.sleep(1.1)
            url2 = svc.save_landscape_photo(_png_bytes(), "image/png", user_id=23)
        assert url1 != url2


# ── Old files are removed on new upload (no stale files left) ─────────────────

class TestOldFilesCleanedUp:

    def test_portrait_old_file_deleted_on_reupload(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url1 = svc.save_portrait_photo(_png_bytes(), "image/png", user_id=30)
            filename1 = url1.split("/")[-1]
            time.sleep(1.1)
            svc.save_portrait_photo(_png_bytes(), "image/png", user_id=30)
        assert not (tmp_path / filename1).exists(), (
            f"Old portrait file {filename1!r} must be deleted when a new one is saved"
        )

    def test_compact_bg_old_file_deleted_on_reupload(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url1 = svc.save_compact_bg_photo(_png_bytes(), "image/png", user_id=31)
            filename1 = url1.split("/")[-1]
            time.sleep(1.1)
            svc.save_compact_bg_photo(_png_bytes(), "image/png", user_id=31)
        assert not (tmp_path / filename1).exists()

    def test_only_one_file_per_slot_after_multiple_uploads(self, tmp_path):
        """After N uploads, exactly 1 file must exist per slot."""
        with _patch_photo_dir(tmp_path):
            for _ in range(3):
                svc.save_portrait_photo(_png_bytes(), "image/png", user_id=32)
                time.sleep(1.1)
            files = list(tmp_path.glob("32_portrait_*.png"))
        assert len(files) == 1, f"Expected 1 portrait file, found {len(files)}: {files}"


# ── Delete removes all files (timestamped + legacy) ───────────────────────────

class TestDeleteCompleteness:

    def test_delete_portrait_removes_timestamped_file(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url = svc.save_portrait_photo(_png_bytes(), "image/png", user_id=40)
            filename = url.split("/")[-1]
            assert (tmp_path / filename).exists(), "File must exist after upload"
            svc.delete_portrait_photo(40)
            assert not (tmp_path / filename).exists(), "File must be removed after delete"

    def test_delete_compact_bg_removes_timestamped_file(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            url = svc.save_compact_bg_photo(_png_bytes(), "image/png", user_id=41)
            filename = url.split("/")[-1]
            svc.delete_compact_bg_photo(41)
            assert not (tmp_path / filename).exists()

    def test_delete_portrait_removes_legacy_fixed_name(self, tmp_path):
        """Legacy file {user_id}_portrait.png (no epoch) must also be removed."""
        legacy = tmp_path / "50_portrait.png"
        legacy.write_bytes(b"legacy")
        with _patch_photo_dir(tmp_path):
            svc.delete_portrait_photo(50)
        assert not legacy.exists(), "Legacy fixed-name portrait file must be deleted"

    def test_delete_compact_bg_removes_legacy_fixed_name(self, tmp_path):
        legacy = tmp_path / "51_bg_compact.png"
        legacy.write_bytes(b"legacy")
        with _patch_photo_dir(tmp_path):
            svc.delete_compact_bg_photo(51)
        assert not legacy.exists()

    def test_delete_showcase_bg_removes_legacy_fixed_name(self, tmp_path):
        legacy = tmp_path / "52_bg_showcase.png"
        legacy.write_bytes(b"legacy")
        with _patch_photo_dir(tmp_path):
            svc.delete_showcase_bg_photo(52)
        assert not legacy.exists()

    def test_delete_landscape_removes_legacy_fixed_name(self, tmp_path):
        legacy = tmp_path / "53_landscape.png"
        legacy.write_bytes(b"legacy")
        with _patch_photo_dir(tmp_path):
            svc.delete_landscape_photo(53)
        assert not legacy.exists()

    def test_delete_does_not_touch_other_user_files(self, tmp_path):
        """Delete for user 60 must not remove user 61's files."""
        other = tmp_path / "61_portrait_9999999999.png"
        other.write_bytes(b"other-user")
        with _patch_photo_dir(tmp_path):
            svc.delete_portrait_photo(60)
        assert other.exists(), "Other user's portrait file must not be deleted"

    def test_delete_does_not_touch_other_slot(self, tmp_path):
        """delete_portrait_photo must not remove landscape files for same user."""
        landscape = tmp_path / "70_landscape_9999999999.png"
        landscape.write_bytes(b"landscape")
        with _patch_photo_dir(tmp_path):
            svc.delete_portrait_photo(70)
        assert landscape.exists(), "Landscape file must not be removed by delete_portrait_photo"

    def test_delete_noop_when_dir_missing(self, tmp_path):
        missing_dir = tmp_path / "nonexistent"
        with patch.object(svc, "PHOTO_DIR", missing_dir):
            svc.delete_portrait_photo(99)   # must not raise
            svc.delete_compact_bg_photo(99)
            svc.delete_landscape_photo(99)
            svc.delete_showcase_bg_photo(99)

    def test_delete_noop_when_no_matching_files(self, tmp_path):
        with _patch_photo_dir(tmp_path):
            svc.delete_portrait_photo(999)  # no files exist — must not raise


# ── Upload → legacy file also cleaned up on new save ─────────────────────────

class TestLegacyFileCleanedOnUpload:

    def test_upload_removes_legacy_portrait_file(self, tmp_path):
        """If a legacy fixed-name portrait file exists, saving a new one must remove it."""
        legacy = tmp_path / "80_portrait.png"
        legacy.write_bytes(b"old-legacy-content")
        with _patch_photo_dir(tmp_path):
            svc.save_portrait_photo(_png_bytes(), "image/png", user_id=80)
        assert not legacy.exists(), (
            "Legacy portrait file must be deleted when a new timestamped one is saved"
        )

    def test_upload_removes_legacy_compact_bg_file(self, tmp_path):
        legacy = tmp_path / "81_bg_compact.png"
        legacy.write_bytes(b"old-legacy-content")
        with _patch_photo_dir(tmp_path):
            svc.save_compact_bg_photo(_png_bytes(), "image/png", user_id=81)
        assert not legacy.exists()

    def test_upload_removes_legacy_showcase_bg_file(self, tmp_path):
        legacy = tmp_path / "82_bg_showcase.png"
        legacy.write_bytes(b"old-legacy-content")
        with _patch_photo_dir(tmp_path):
            svc.save_showcase_bg_photo(_png_bytes(), "image/png", user_id=82)
        assert not legacy.exists()

    def test_upload_removes_legacy_landscape_file(self, tmp_path):
        legacy = tmp_path / "83_landscape.png"
        legacy.write_bytes(b"old-legacy-content")
        with _patch_photo_dir(tmp_path):
            svc.save_landscape_photo(_png_bytes(), "image/png", user_id=83)
        assert not legacy.exists()
