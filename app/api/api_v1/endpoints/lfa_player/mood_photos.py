"""
LFA Football Player — Native Mood Photos API
=============================================
Bearer-token equivalents of the web-session mood photo routes.
Delegates entirely to app.services.mood_photo_service — no logic duplication.

Endpoints:
  GET    /api/v1/lfa-player/mood-photos                      — list all 9 slots
  POST   /api/v1/lfa-player/mood-photos/{slot}/upload        — upload a slot
  DELETE /api/v1/lfa-player/mood-photos/{slot}               — delete a slot
  POST   /api/v1/lfa-player/mood-photos/{slot}/remove-bg     — trigger BG removal
  GET    /api/v1/lfa-player/mood-photos/{slot}/status        — poll slot status

Completion rule (iOS):
  phase_a_complete = True when all 6 Phase-A slots have original_url IS NOT NULL.
  Status 'uploaded', 'processing', 'ready', and 'failed' all count — only
  background removal outcome is excluded from completion gating.
"""

from __future__ import annotations

import io
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .....database import get_db
from .....dependencies import get_current_user
from .....models.user import User
from .....models.license import UserLicense
from .....models.user_mood_photos import MOOD_PHOTO_SLOTS, MoodPhotoStatus, UserMoodPhoto
from .....services.mood_photo_service import (
    save_mood_photo,
    delete_mood_photo,
    get_mood_photos_for_user,
    check_bg_removal_rate_limit,
    set_status_processing,
    apply_removal_result,
    apply_removal_failure,
    reset_processing,
    MOOD_PHOTO_DIR,
)
from .....config import settings

router = APIRouter()

# ── Slot metadata (mirrors web route _SLOT_META) ──────────────────────────────

_PHASE_A = (
    "mood_intro_neutral",
    "mood_happy_smile",
    "mood_celebration",
    "mood_sad_disappointed",
    "mood_angry_competitive",
    "mood_surprised_shocked",
)

_PHASE_B = (
    "mood_focused_ready",
    "mood_confident",
    "mood_proud",
)

_SLOT_META: dict[str, dict[str, str]] = {
    "mood_intro_neutral":     {"label": "Neutral",     "phase": "A"},
    "mood_happy_smile":       {"label": "Happy",       "phase": "A"},
    "mood_celebration":       {"label": "Celebration", "phase": "A"},
    "mood_sad_disappointed":  {"label": "Sad",         "phase": "A"},
    "mood_angry_competitive": {"label": "Angry",       "phase": "A"},
    "mood_surprised_shocked": {"label": "Surprised",   "phase": "A"},
    "mood_focused_ready":     {"label": "Focused",     "phase": "B"},
    "mood_confident":         {"label": "Confident",   "phase": "B"},
    "mood_proud":             {"label": "Proud",       "phase": "B"},
}

_ORDERED_SLOTS = list(_PHASE_A) + list(_PHASE_B)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_timed_out(record: UserMoodPhoto) -> bool:
    if record.status != MoodPhotoStatus.processing.value:
        return False
    if record.updated_at is None:
        return False
    elapsed = (datetime.now(timezone.utc) - record.updated_at).total_seconds()
    return elapsed > settings.PROCESSING_TIMEOUT_SECONDS


def _slot_dict(slot: str, record: UserMoodPhoto | None) -> dict[str, Any]:
    meta = _SLOT_META[slot]
    if record is None:
        return {
            "slot":               slot,
            "label":              meta["label"],
            "phase":              meta["phase"],
            "status":             None,
            "original_url":       None,
            "processed_png_url":  None,
            "processing_timed_out": False,
        }
    return {
        "slot":               slot,
        "label":              meta["label"],
        "phase":              meta["phase"],
        "status":             record.status,
        "original_url":       record.original_url,
        "processed_png_url":  record.processed_png_url,
        "processing_timed_out": _is_timed_out(record),
    }


def _validate_slot(slot: str) -> None:
    if slot not in MOOD_PHOTO_SLOTS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Invalid slot: {slot!r}")


def _get_license_id(user_id: int, db: Session) -> int | None:
    lic = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()
    return lic.id if lic else None


# ── In-process BG removal runner (same pattern as web route) ──────────────────

def _run_bg_removal(user_id: int, slot: str, original_url: str) -> None:
    """Run background removal in a FastAPI BackgroundTask thread."""
    from .....database import SessionLocal
    from .....services.background_removal import get_processor

    db = SessionLocal()
    try:
        processor = get_processor()
        orig_path = MOOD_PHOTO_DIR / Path(original_url).name
        if not orig_path.exists():
            apply_removal_failure(user_id, slot, db)
            return

        output_bytes = processor.remove(orig_path.read_bytes())
        ts = int(time.time())
        proc_filename = f"{user_id}_mood_{slot}_proc_{ts}.png"
        (MOOD_PHOTO_DIR / proc_filename).write_bytes(output_bytes)
        processed_url = f"/static/uploads/mood_photos/{proc_filename}"
        apply_removal_result(user_id, slot, processed_url, db)
    except Exception:
        apply_removal_failure(user_id, slot, db)
    finally:
        db.close()


# ── GET /mood-photos ──────────────────────────────────────────────────────────

@router.get("/mood-photos")
def list_mood_photos(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return all 9 mood photo slots for the current user.

    phase_a_complete is True when all 6 Phase-A slots have original_url set
    (status may be uploaded / processing / ready / failed — all count).
    """
    by_slot = get_mood_photos_for_user(current_user.id, db)

    slots = [_slot_dict(s, by_slot.get(s)) for s in _ORDERED_SLOTS]

    phase_a_count = sum(
        1 for s in _PHASE_A if by_slot.get(s) is not None
    )

    return {
        "slots":                slots,
        "phase_a_uploaded_count": phase_a_count,
        "phase_a_complete":       phase_a_count == len(_PHASE_A),
    }


# ── POST /mood-photos/{slot}/upload ──────────────────────────────────────────

@router.post("/mood-photos/{slot}/upload")
async def upload_mood_photo(
    slot:             str,
    background_tasks: BackgroundTasks,
    photo:            UploadFile = File(...),
    db:               Session    = Depends(get_db),
    current_user:     User       = Depends(get_current_user),
):
    """
    Upload (or replace) a mood photo slot.

    Accepts JPEG, PNG, or WebP ≤ 5 MB.
    If BG_REMOVAL_PROCESSOR is not 'null', auto-triggers background removal.
    Returns the updated slot dict.
    """
    _validate_slot(slot)

    file_bytes   = await photo.read()
    content_type = photo.content_type or "image/jpeg"
    license_id   = _get_license_id(current_user.id, db)

    try:
        record = save_mood_photo(
            file_bytes=file_bytes,
            content_type=content_type,
            user_id=current_user.id,
            slot=slot,
            db=db,
            license_id=license_id,
        )
        db.commit()
        db.refresh(record)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    # Auto-trigger BG removal if processor is active
    if settings.BG_REMOVAL_PROCESSOR != "null":
        if check_bg_removal_rate_limit(current_user.id):
            set_status_processing(current_user.id, slot, db)
            db.commit()
            db.refresh(record)
            background_tasks.add_task(
                _run_bg_removal, current_user.id, slot, record.original_url
            )

    return _slot_dict(slot, record)


# ── DELETE /mood-photos/{slot} ────────────────────────────────────────────────

@router.delete("/mood-photos/{slot}", status_code=status.HTTP_204_NO_CONTENT)
def delete_mood_photo_endpoint(
    slot:         str,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """Delete a mood photo slot. Idempotent — no-op if slot has no photo."""
    _validate_slot(slot)
    delete_mood_photo(current_user.id, slot, db)
    db.commit()


# ── POST /mood-photos/{slot}/remove-bg ───────────────────────────────────────

@router.post("/mood-photos/{slot}/remove-bg")
def trigger_bg_removal(
    slot:             str,
    background_tasks: BackgroundTasks,
    db:               Session = Depends(get_db),
    current_user:     User    = Depends(get_current_user),
):
    """
    Manually trigger background removal for a slot.

    No-op if slot is already processing or ready.
    Rate-limited: 3 triggers per 60 s per user.
    """
    _validate_slot(slot)

    record = (
        db.query(UserMoodPhoto)
        .filter_by(user_id=current_user.id, slot=slot)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="No uploaded photo for this slot.")

    if record.status in (MoodPhotoStatus.processing.value, MoodPhotoStatus.ready.value):
        return _slot_dict(slot, record)

    if not check_bg_removal_rate_limit(current_user.id):
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="Too many background removal requests. Try again shortly.")

    orig_path = MOOD_PHOTO_DIR / Path(record.original_url).name
    if not orig_path.exists():
        apply_removal_failure(current_user.id, slot, db)
        db.commit()
        db.refresh(record)
        return _slot_dict(slot, record)

    set_status_processing(current_user.id, slot, db)
    db.commit()
    db.refresh(record)
    background_tasks.add_task(
        _run_bg_removal, current_user.id, slot, record.original_url
    )
    return _slot_dict(slot, record)


# ── GET /mood-photos/{slot}/status ───────────────────────────────────────────

@router.get("/mood-photos/{slot}/status")
def get_slot_status(
    slot:         str,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """
    Poll the processing status of a single slot.

    Returns processing_timed_out=True when status='processing' and the
    record has not been updated within PROCESSING_TIMEOUT_SECONDS.
    """
    _validate_slot(slot)

    record = (
        db.query(UserMoodPhoto)
        .filter_by(user_id=current_user.id, slot=slot)
        .first()
    )
    if record is None:
        return {
            "status":               "not_uploaded",
            "processed_png_url":    None,
            "updated_at":           None,
            "processing_timed_out": False,
        }
    return {
        "status":               record.status,
        "processed_png_url":    record.processed_png_url,
        "updated_at":           record.updated_at.isoformat() if record.updated_at else None,
        "processing_timed_out": _is_timed_out(record),
    }
