"""
mood_photo_service — save / replace / delete / list hangulatkép slots.

Completely independent from player_photo_service.  Never reads or writes
player_card_photo_url, wc_photo_url, or any other UserLicense photo field.
No fallback in either direction.

Background removal (processed_png_url) is NOT implemented in this module —
that is a future phase requiring separate approval.
"""
from __future__ import annotations

import io
import time
from pathlib import Path

from PIL import Image
from sqlalchemy.orm import Session

from app.models.user_mood_photos import MOOD_PHOTO_SLOTS, MoodPhotoStatus, UserMoodPhoto

MOOD_PHOTO_DIR: Path = Path("app/static/uploads/mood_photos")
MAX_BYTES: int       = 5 * 1024 * 1024   # 5 MB
_ALLOWED_MIME: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)
_MAX_DIMENSION: int = 2048   # pixels; larger images are resized down


# ── Public API ────────────────────────────────────────────────────────────────

def save_mood_photo(
    file_bytes:  bytes,
    content_type: str,
    user_id:     int,
    slot:        str,
    db:          Session,
    license_id:  int | None = None,
) -> UserMoodPhoto:
    """
    Validate, save, and upsert one hangulatkép slot.

    - Deletes the previous file for this (user_id, slot) pair before saving.
    - Sets status='uploaded', processed_png_url=NULL, processed_at=NULL.
    - Returns the upserted UserMoodPhoto row (already committed).
    """
    _validate_slot(slot)
    _validate_upload(file_bytes, content_type)

    url = _write_file(file_bytes, content_type, user_id, slot)

    existing = (
        db.query(UserMoodPhoto)
        .filter_by(user_id=user_id, slot=slot)
        .first()
    )
    if existing:
        existing.original_url      = url
        existing.processed_png_url = None
        existing.status            = MoodPhotoStatus.uploaded.value
        existing.processed_at      = None
        if license_id is not None:
            existing.license_id = license_id
        db.flush()
        db.refresh(existing)
        return existing

    record = UserMoodPhoto(
        user_id           = user_id,
        license_id        = license_id,
        slot              = slot,
        original_url      = url,
        processed_png_url = None,
        status            = MoodPhotoStatus.uploaded.value,
        processed_at      = None,
    )
    db.add(record)
    db.flush()
    db.refresh(record)
    return record


def delete_mood_photo(user_id: int, slot: str, db: Session) -> None:
    """
    Delete the DB row and disk files for (user_id, slot).  Idempotent — no-op
    if the slot has no record.  Does NOT touch any other photo fields.
    """
    _validate_slot(slot)
    record = (
        db.query(UserMoodPhoto)
        .filter_by(user_id=user_id, slot=slot)
        .first()
    )
    if record is None:
        return
    _delete_files_for_slot(user_id, slot)
    db.delete(record)
    db.flush()


def get_mood_photos_for_user(
    user_id: int, db: Session
) -> dict[str, UserMoodPhoto | None]:
    """
    Return {slot: record_or_None} for all 4 MOOD_PHOTO_SLOTS.

    Always returns all 4 keys so callers can iterate predictably.
    """
    rows = (
        db.query(UserMoodPhoto)
        .filter_by(user_id=user_id)
        .all()
    )
    by_slot: dict[str, UserMoodPhoto | None] = {s: None for s in MOOD_PHOTO_SLOTS}
    for row in rows:
        if row.slot in by_slot:
            by_slot[row.slot] = row
    return by_slot


# ── Internal helpers ──────────────────────────────────────────────────────────

def _validate_slot(slot: str) -> None:
    if slot not in MOOD_PHOTO_SLOTS:
        raise ValueError(
            f"Invalid mood photo slot: {slot!r}. "
            f"Allowed: {sorted(MOOD_PHOTO_SLOTS)}"
        )


def _validate_upload(file_bytes: bytes, content_type: str) -> None:
    if content_type not in _ALLOWED_MIME:
        raise ValueError(
            f"Unsupported content type: {content_type!r}. "
            f"Allowed: {sorted(_ALLOWED_MIME)}"
        )
    if len(file_bytes) > MAX_BYTES:
        raise ValueError(
            f"File too large: {len(file_bytes)} bytes (max {MAX_BYTES})."
        )


def _write_file(
    file_bytes: bytes, content_type: str, user_id: int, slot: str
) -> str:
    """Resize if needed, save as PNG, delete previous slot files. Returns URL."""
    _delete_files_for_slot(user_id, slot)
    MOOD_PHOTO_DIR.mkdir(parents=True, exist_ok=True)

    img = Image.open(io.BytesIO(file_bytes)).convert("RGBA")
    if max(img.size) > _MAX_DIMENSION:
        img.thumbnail((_MAX_DIMENSION, _MAX_DIMENSION), Image.LANCZOS)

    ts       = int(time.time())
    filename = f"{user_id}_mood_{slot}_orig_{ts}.png"
    out_path = MOOD_PHOTO_DIR / filename
    img.save(out_path, "PNG", optimize=True)

    return f"/static/uploads/mood_photos/{filename}"


def _delete_files_for_slot(user_id: int, slot: str) -> None:
    if not MOOD_PHOTO_DIR.exists():
        return
    for f in MOOD_PHOTO_DIR.glob(f"{user_id}_mood_{slot}_*.png"):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass
