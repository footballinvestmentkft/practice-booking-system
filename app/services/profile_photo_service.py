"""
profile_photo_service — save / delete / background-removal for the user profile photo.

Mirrors mood_photo_service patterns but operates on User model columns directly
(no separate DB table).  One photo per user: original + optional processed (BG-removed).

Status flow:
  NULL / "none"  → uploaded  → processing  → ready
                                           → failed
                → deleted  → NULL

Background removal re-uses the same BackgroundProcessor interface as mood photos.
With BG_REMOVAL_PROCESSOR="null" (default) the processor is a passthrough —
processed_url will equal original_url in content, but the pipeline is fully wired
so switching to "rembg" activates real removal without code changes.
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

from app.models.user import User
from app.services.background_removal import get_processor
from app.services.mood_photo_service import check_bg_removal_rate_limit

# ── Constants ─────────────────────────────────────────────────────────────────

PROFILE_PHOTO_DIR: Path = Path("app/static/uploads/profile_photos")
_MAX_BYTES:     int     = 5 * 1024 * 1024   # 5 MB — same limit as mood photos
_MAX_DIMENSION: int     = 2048              # px; larger images resized down
_ALLOWED_MIME: frozenset[str] = frozenset(
    {"image/jpeg", "image/png", "image/webp"}
)

# Status constants — kept as plain strings (not Enum) to match mood photo pattern.
STATUS_NONE:       str = "none"
STATUS_UPLOADED:   str = "uploaded"
STATUS_PROCESSING: str = "processing"
STATUS_READY:      str = "ready"
STATUS_FAILED:     str = "failed"


# ── Public API ────────────────────────────────────────────────────────────────

def save_profile_photo(
    file_bytes:   bytes,
    content_type: str,
    user:         User,
    db:           Session,
) -> User:
    """
    Validate, resize to PNG, save to disk, update User columns.

    - Deletes previous profile photo files for this user.
    - Sets profile_photo_url, clears processed_url, sets status="uploaded".
    - Does NOT trigger background removal — caller decides.
    - Returns the refreshed User object.
    """
    _validate(file_bytes, content_type)
    url = _write_file(file_bytes, content_type, user.id)

    user.profile_photo_url           = url
    user.profile_photo_processed_url = None
    user.profile_photo_status        = STATUS_UPLOADED
    db.flush()
    db.refresh(user)
    return user


def delete_profile_photo(user: User, db: Session) -> None:
    """
    Delete disk files and NULL all three profile photo columns.  Idempotent.
    """
    _delete_files(user.id)
    user.profile_photo_url           = None
    user.profile_photo_processed_url = None
    user.profile_photo_status        = None
    db.flush()


def trigger_bg_removal(user: User, db: Session) -> bool:
    """
    Rate-limited in-process background removal (3 triggers / 60 s per user).

    Returns True if the removal was triggered, False if rate-limited or no photo.
    Caller is responsible for running this inside a FastAPI BackgroundTask.
    """
    if not user.profile_photo_url:
        return False
    if not check_bg_removal_rate_limit(user.id):
        return False

    user.profile_photo_status = STATUS_PROCESSING
    db.flush()
    return True


def run_bg_removal(user_id: int, original_url: str, db: Session) -> None:
    """
    Execute background removal synchronously (called from FastAPI BackgroundTasks).

    Reads the original PNG from disk, runs get_processor().remove(), saves result.
    Updates status to "ready" or "failed".  Safe to call on any thread.
    """
    try:
        # Resolve disk path from URL string
        # URL is like "/static/uploads/profile_photos/{filename}"
        rel_path = original_url.lstrip("/").replace("static/", "app/static/", 1)
        in_path  = Path(rel_path)
        if not in_path.exists():
            _apply_failure(user_id, db)
            return

        in_bytes  = in_path.read_bytes()
        processor = get_processor()
        out_bytes = processor.remove(in_bytes)

        # Save processed PNG
        PROFILE_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
        ts       = int(time.time())
        filename = f"{user_id}_profile_proc_{ts}.png"
        out_path = PROFILE_PHOTO_DIR / filename
        out_path.write_bytes(out_bytes)

        processed_url = f"/static/uploads/profile_photos/{filename}"
        _apply_success(user_id, processed_url, db)

    except Exception:
        _apply_failure(user_id, db)


# ── Internal state transitions ────────────────────────────────────────────────

def _apply_success(user_id: int, processed_url: str, db: Session) -> None:
    user = db.query(User).filter_by(id=user_id).first()
    if user:
        user.profile_photo_processed_url = processed_url
        user.profile_photo_status        = STATUS_READY
        db.commit()


def _apply_failure(user_id: int, db: Session) -> None:
    user = db.query(User).filter_by(id=user_id).first()
    if user:
        user.profile_photo_status = STATUS_FAILED
        db.commit()


# ── Private helpers ────────────────────────────────────────────────────────────

def _validate(file_bytes: bytes, content_type: str) -> None:
    if content_type not in _ALLOWED_MIME:
        raise ValueError(
            f"Unsupported content type: {content_type!r}. "
            f"Allowed: {sorted(_ALLOWED_MIME)}"
        )
    if len(file_bytes) > _MAX_BYTES:
        raise ValueError(
            f"File too large: {len(file_bytes):,} bytes (max {_MAX_BYTES:,})."
        )


def _write_file(file_bytes: bytes, content_type: str, user_id: int) -> str:
    """Resize if needed, convert to PNG, delete previous files, save. Returns URL."""
    _delete_files(user_id)
    PROFILE_PHOTO_DIR.mkdir(parents=True, exist_ok=True)

    img = Image.open(io.BytesIO(file_bytes)).convert("RGBA")
    if max(img.size) > _MAX_DIMENSION:
        img.thumbnail((_MAX_DIMENSION, _MAX_DIMENSION), Image.LANCZOS)

    ts       = time.time_ns() // 1_000_000
    filename = f"{user_id}_profile_orig_{ts}.png"
    out_path = PROFILE_PHOTO_DIR / filename
    img.save(out_path, "PNG", optimize=True)
    return f"/static/uploads/profile_photos/{filename}"


def _delete_files(user_id: int) -> None:
    """Delete all profile_photo files (orig + proc) for this user."""
    if not PROFILE_PHOTO_DIR.exists():
        return
    for f in PROFILE_PHOTO_DIR.glob(f"{user_id}_profile_*.png"):
        try:
            f.unlink(missing_ok=True)
        except OSError:
            pass
