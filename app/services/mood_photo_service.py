"""
mood_photo_service — save / replace / delete / list hangulatkép slots.

Completely independent from player_photo_service.  Never reads or writes
player_card_photo_url, wc_photo_url, or any other UserLicense photo field.
No fallback in either direction.

Background removal pipeline (Phase 1):
  set_status_processing / apply_removal_result / apply_removal_failure /
  reset_processing handle DB state transitions.  The actual image processing
  is done by app.services.background_removal (NullProcessor in Phase 1).
  Real background removal remains Phase 2 (rembg + onnxruntime-cpu).
"""
from __future__ import annotations

import io
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

from PIL import Image
from sqlalchemy.orm import Session

from app.models.user_mood_photos import MOOD_PHOTO_SLOTS, MoodPhotoStatus, UserMoodPhoto

MOOD_PHOTO_DIR: Path = Path("app/static/uploads/mood_photos")
MAX_BYTES: int       = 5 * 1024 * 1024   # 5 MB
_ALLOWED_MIME: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)
_MAX_DIMENSION: int = 2048   # pixels; larger images are resized down

# ── Background removal rate limiter — 3 triggers / 60 s per user ─────────────
# Mirrors the pattern from card_export_service.check_export_rate_limit.
# Guards against duplicate clicks and rapid uploaded→failed→retry loops.
# Keyed by user_id (int); reset_bg_removal_rate_counters() is a test helper.
_BG_RATE_LIMIT:    int                    = 3
_BG_RATE_WINDOW:   int                    = 60  # seconds
_bg_rate_counters: dict[int, deque]       = {}
_bg_rate_lock:     Lock                   = Lock()


def check_bg_removal_rate_limit(user_id: int) -> bool:
    """Return True if within 3 remove-bg triggers/60s for this user, False if exceeded."""
    now = time.monotonic()
    with _bg_rate_lock:
        if user_id not in _bg_rate_counters:
            _bg_rate_counters[user_id] = deque()
        dq     = _bg_rate_counters[user_id]
        cutoff = now - _BG_RATE_WINDOW
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= _BG_RATE_LIMIT:
            return False
        dq.append(now)
        return True


def reset_bg_removal_rate_counters() -> None:
    """Test helper — clears all in-memory remove-bg rate counters."""
    with _bg_rate_lock:
        _bg_rate_counters.clear()


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


# ── Background removal pipeline — Phase 1 ────────────────────────────────────
# These functions manage DB state transitions only.  Actual image processing
# is handled by the Celery task in app.tasks.mood_photo_tasks.
# Real background removal remains Phase 2 (rembg + onnxruntime-cpu).

def set_status_processing(user_id: int, slot: str, db: Session) -> UserMoodPhoto:
    """Set status='processing' before the Celery task is enqueued."""
    record = (
        db.query(UserMoodPhoto)
        .filter_by(user_id=user_id, slot=slot)
        .first()
    )
    if record is None:
        raise ValueError(
            f"No mood photo record for user_id={user_id} slot={slot!r}"
        )
    record.status = MoodPhotoStatus.processing.value
    db.flush()
    return record


def apply_removal_result(
    user_id: int, slot: str, processed_url: str, db: Session
) -> None:
    """Called by the Celery task on success. Commits the DB session."""
    record = (
        db.query(UserMoodPhoto)
        .filter_by(user_id=user_id, slot=slot)
        .first()
    )
    if record is None:
        return
    record.processed_png_url = processed_url
    record.status            = MoodPhotoStatus.ready.value
    record.processed_at      = datetime.now(timezone.utc)
    db.commit()


def apply_removal_failure(user_id: int, slot: str, db: Session) -> None:
    """Called by the Celery task on failure (including max retries). Commits."""
    record = (
        db.query(UserMoodPhoto)
        .filter_by(user_id=user_id, slot=slot)
        .first()
    )
    if record is None:
        return
    record.status       = MoodPhotoStatus.failed.value
    record.processed_at = datetime.now(timezone.utc)
    db.commit()


def reset_processing(user_id: int, slot: str, db: Session) -> None:
    """
    Reset a stuck 'processing' record back to 'uploaded'.

    Idempotent: no-op if the record does not exist or status != 'processing'.
    Only operates on the record belonging to user_id (own record guard).
    """
    record = (
        db.query(UserMoodPhoto)
        .filter_by(user_id=user_id, slot=slot)
        .first()
    )
    if record is None or record.status != MoodPhotoStatus.processing.value:
        return
    record.status            = MoodPhotoStatus.uploaded.value
    record.processed_png_url = None
    record.processed_at      = None
    db.flush()
