"""
MP-S01..MP-S08 — unit tests for mood_photo_service.

Uses MagicMock for the SQLAlchemy Session; no real DB required.
Disk I/O is patched via tmp_path / monkeypatch so tests stay hermetic.
"""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

# ── helpers ───────────────────────────────────────────────────────────────────

_SVC = "app.services.mood_photo_service"


def _make_jpeg_bytes(width: int = 100, height: int = 100) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color=(80, 120, 60)).save(buf, "JPEG")
    return buf.getvalue()


def _make_db(existing_row=None):
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = existing_row
    return db


# ── MP-S01 ── valid upload → DB row created, status='uploaded' ───────────────

def test_mp_s01_valid_upload_creates_row(tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_SVC}.MOOD_PHOTO_DIR", tmp_path)
    db = _make_db(existing_row=None)

    from app.services.mood_photo_service import save_mood_photo

    row = save_mood_photo(
        file_bytes   = _make_jpeg_bytes(),
        content_type = "image/jpeg",
        user_id      = 1,
        slot         = "mood_happy_smile",
        db           = db,
    )

    db.add.assert_called_once()
    db.flush.assert_called()
    added = db.add.call_args[0][0]
    assert added.status == "uploaded"
    assert added.processed_png_url is None
    assert added.processed_at is None
    assert "mood_happy_smile" in added.original_url


# ── MP-S02 ── invalid slot → ValueError ──────────────────────────────────────

def test_mp_s02_invalid_slot_raises():
    from app.services.mood_photo_service import save_mood_photo

    with pytest.raises(ValueError, match="Invalid mood photo slot"):
        save_mood_photo(
            file_bytes   = _make_jpeg_bytes(),
            content_type = "image/jpeg",
            user_id      = 1,
            slot         = "angry_rage",
            db           = MagicMock(),
        )


# ── MP-S03 ── file too large → ValueError ────────────────────────────────────

def test_mp_s03_file_too_large_raises():
    from app.services.mood_photo_service import MAX_BYTES, save_mood_photo

    big = b"x" * (MAX_BYTES + 1)
    with pytest.raises(ValueError, match="too large"):
        save_mood_photo(
            file_bytes   = big,
            content_type = "image/jpeg",
            user_id      = 1,
            slot         = "mood_happy_smile",
            db           = MagicMock(),
        )


# ── MP-S04 ── invalid MIME → ValueError ──────────────────────────────────────

def test_mp_s04_invalid_mime_raises():
    from app.services.mood_photo_service import save_mood_photo

    with pytest.raises(ValueError, match="Unsupported content type"):
        save_mood_photo(
            file_bytes   = b"GIF89a",
            content_type = "image/gif",
            user_id      = 1,
            slot         = "mood_celebration",
            db           = MagicMock(),
        )


# ── MP-S05 ── replace: existing row updated in-place ─────────────────────────

def test_mp_s05_replace_updates_existing_row(tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_SVC}.MOOD_PHOTO_DIR", tmp_path)

    existing = MagicMock()
    existing.original_url      = "/static/uploads/mood_photos/1_mood_happy_smile_orig_111.png"
    existing.processed_png_url = "/static/some_old.png"
    existing.status            = "ready"

    db = _make_db(existing_row=existing)

    from app.services.mood_photo_service import save_mood_photo

    save_mood_photo(
        file_bytes   = _make_jpeg_bytes(),
        content_type = "image/jpeg",
        user_id      = 1,
        slot         = "mood_happy_smile",
        db           = db,
    )

    db.add.assert_not_called()
    assert existing.status == "uploaded"
    assert existing.processed_png_url is None
    assert existing.processed_at is None
    assert "mood_happy_smile" in existing.original_url


# ── MP-S06 ── delete removes row ─────────────────────────────────────────────

def test_mp_s06_delete_removes_row(tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_SVC}.MOOD_PHOTO_DIR", tmp_path)

    existing = MagicMock()
    db = _make_db(existing_row=existing)

    from app.services.mood_photo_service import delete_mood_photo

    delete_mood_photo(user_id=99, slot="mood_intro_neutral", db=db)

    db.delete.assert_called_once_with(existing)
    db.flush.assert_called()


# ── MP-S07 ── delete non-existent slot is a no-op ────────────────────────────

def test_mp_s07_delete_nonexistent_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(f"{_SVC}.MOOD_PHOTO_DIR", tmp_path)
    db = _make_db(existing_row=None)

    from app.services.mood_photo_service import delete_mood_photo

    delete_mood_photo(user_id=99, slot="mood_celebration", db=db)

    db.delete.assert_not_called()


# ── MP-S08 ── get_mood_photos_for_user always returns all 6 keys ─────────────

def test_mp_s08_get_returns_all_six_slots():
    row = MagicMock()
    row.slot = "mood_happy_smile"

    db = MagicMock()
    db.query.return_value.filter_by.return_value.all.return_value = [row]

    from app.services.mood_photo_service import get_mood_photos_for_user

    result = get_mood_photos_for_user(user_id=42, db=db)

    assert set(result.keys()) == {
        "mood_intro_neutral",
        "mood_happy_smile",
        "mood_celebration",
        "mood_sad_disappointed",
        "mood_angry_competitive",
        "mood_surprised_shocked",
    }
    assert result["mood_happy_smile"]       is row
    assert result["mood_intro_neutral"]     is None
    assert result["mood_celebration"]       is None
    assert result["mood_sad_disappointed"]  is None
    assert result["mood_angry_competitive"] is None
    assert result["mood_surprised_shocked"] is None
