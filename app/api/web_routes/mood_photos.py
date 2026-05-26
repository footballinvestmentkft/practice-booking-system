"""
mood_photos — web routes for the mood photo feature.

Routes
------
GET    /profile/my-mood-photos                        — management page
POST   /profile/my-mood-photos/{slot}/upload          — upload / replace a slot
POST   /profile/my-mood-photos/{slot}/delete          — HTML-form delete fallback
DELETE /profile/my-mood-photos/{slot}                 — JS-fetch delete endpoint
POST   /profile/my-mood-photos/{slot}/remove-bg       — trigger background removal
GET    /profile/my-mood-photos/{slot}/status          — JSON status + timeout flag
POST   /profile/my-mood-photos/{slot}/reset-processing — reset stuck processing
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ...config import settings
from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User
from ...models.license import UserLicense
from ...models.user_mood_photos import MOOD_PHOTO_SLOTS, MoodPhotoStatus, UserMoodPhoto
from ...services.mood_photo_service import (
    MOOD_PHOTO_DIR,
    apply_removal_failure,
    delete_mood_photo,
    get_mood_photos_for_user,
    reset_processing,
    save_mood_photo,
    set_status_processing,
)
from ...tasks.mood_photo_tasks import remove_background_task

logger = logging.getLogger(__name__)

BASE_DIR  = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()

# Ordered for consistent display in the template.
# description is rendered verbatim in the card; keep it to one short sentence.
_SLOT_META: list[dict] = [
    {
        "slot":        "mood_intro_neutral",
        "label":       "Neutral",
        "emoji":       "😐",
        "description": "Your default neutral expression — shown when no other mood applies.",
    },
    {
        "slot":        "mood_happy_smile",
        "label":       "Happy",
        "emoji":       "😊",
        "description": "A happy or smiling photo — after a good training session.",
    },
    {
        "slot":        "mood_celebration",
        "label":       "Celebration",
        "emoji":       "🎉",
        "description": "A celebration shot — winning a match or achieving a goal.",
    },
    {
        "slot":        "mood_sad_disappointed",
        "label":       "Sad",
        "emoji":       "😔",
        "description": "A disappointed expression — after a tough defeat.",
    },
    {
        "slot":        "mood_angry_competitive",
        "label":       "Angry",
        "emoji":       "😤",
        "description": "A fired-up, competitive look — before a big match.",
    },
    {
        "slot":        "mood_surprised_shocked",
        "label":       "Surprised",
        "emoji":       "😲",
        "description": "A shocked or surprised reaction — an unexpected result.",
    },
]


# ── GET /profile/my-mood-photos ───────────────────────────────────────────────

@router.get("/profile/my-mood-photos", response_class=HTMLResponse)
async def mood_photos_page(
    request: Request,
    user:    User    = Depends(get_current_user_web),
    db:      Session = Depends(get_db),
) -> HTMLResponse:
    mood_photos = get_mood_photos_for_user(user.id, db)
    return templates.TemplateResponse(
        "lfa_player_mood_photos.html",
        {
            "request":            request,
            "user":               user,
            "mood_photos":        mood_photos,
            "slots_meta":         _SLOT_META,
            # Drives processor-aware UI:
            #   "null"  → Remove Background button hidden; no removal claims shown
            #   "rembg" → Remove Background / Retry / Background Removed enabled (Phase 2)
            "bg_processor_mode":  settings.BG_REMOVAL_PROCESSOR,
            # Explicit LFA spec context — mood photos is an LFA Football Player
            # feature; do not rely on user.specialization which can be any active
            # spec (e.g. GANCUJU_PLAYER) on multi-spec accounts.
            "spec_dashboard_url":  "/dashboard/lfa-football-player",
            "spec_dashboard_icon": "⚽",
            "spec_profile_url":    "/profile/lfa-football-player",
            "spec_profile_icon":   "🪪",
        },
    )


# ── POST /profile/my-mood-photos/{slot}/upload ────────────────────────────────

@router.post("/profile/my-mood-photos/{slot}/upload")
async def mood_photo_upload(
    slot:    str,
    request: Request,
    photo:   UploadFile = File(...),
    user:    User       = Depends(get_current_user_web),
    db:      Session    = Depends(get_db),
):
    if slot not in MOOD_PHOTO_SLOTS:
        raise HTTPException(status_code=422, detail=f"Invalid slot: {slot!r}")

    file_bytes   = await photo.read()
    content_type = photo.content_type or ""

    license_id: int | None = None
    lic = db.query(UserLicense).filter_by(user_id=user.id).first()
    if lic:
        license_id = lic.id

    try:
        save_mood_photo(
            file_bytes   = file_bytes,
            content_type = content_type,
            user_id      = user.id,
            slot         = slot,
            db           = db,
            license_id   = license_id,
        )
        db.commit()
        logger.info(
            "mood_photo_uploaded",
            extra={"user": user.email, "slot": slot},
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    accepts_json = "application/json" in (request.headers.get("accept", ""))
    if accepts_json:
        row = get_mood_photos_for_user(user.id, db).get(slot)
        return JSONResponse(
            {
                "slot":         slot,
                "original_url": row.original_url if row else None,
                "status":       row.status if row else "uploaded",
            }
        )
    return RedirectResponse(url="/profile/my-mood-photos", status_code=303)


# ── POST /profile/my-mood-photos/{slot}/delete  (HTML-form fallback) ─────────

@router.post("/profile/my-mood-photos/{slot}/delete")
async def mood_photo_delete_form(
    slot: str,
    user: User    = Depends(get_current_user_web),
    db:   Session = Depends(get_db),
):
    if slot not in MOOD_PHOTO_SLOTS:
        raise HTTPException(status_code=422, detail=f"Invalid slot: {slot!r}")
    delete_mood_photo(user.id, slot, db)
    db.commit()
    logger.info("mood_photo_deleted", extra={"user": user.email, "slot": slot})
    return RedirectResponse(url="/profile/my-mood-photos", status_code=303)


# ── DELETE /profile/my-mood-photos/{slot}  (JS fetch endpoint) ───────────────

@router.delete("/profile/my-mood-photos/{slot}", status_code=204)
async def mood_photo_delete_api(
    slot: str,
    user: User    = Depends(get_current_user_web),
    db:   Session = Depends(get_db),
):
    if slot not in MOOD_PHOTO_SLOTS:
        raise HTTPException(status_code=422, detail=f"Invalid slot: {slot!r}")
    delete_mood_photo(user.id, slot, db)
    db.commit()
    logger.info("mood_photo_deleted_api", extra={"user": user.email, "slot": slot})


# ── POST /profile/my-mood-photos/{slot}/remove-bg ────────────────────────────

@router.post("/profile/my-mood-photos/{slot}/remove-bg")
async def mood_photo_remove_bg(
    slot: str,
    user: User    = Depends(get_current_user_web),
    db:   Session = Depends(get_db),
):
    """
    Trigger background removal for a slot.

    State machine:
      uploaded | failed → set processing → enqueue task → 303
      processing | ready → 303 no-op (no double enqueue / no re-trigger)

    Fast-fail: if the original file is missing from disk, status is set to
    'failed' immediately without enqueueing the task.

    Auth: get_current_user_web; filter_by(user_id=user.id) enforces own record only.
    CSRF: caller must supply X-CSRF-Token header (JS fetch, same as delete).
    """
    if slot not in MOOD_PHOTO_SLOTS:
        raise HTTPException(status_code=422, detail=f"Invalid slot: {slot!r}")

    record = (
        db.query(UserMoodPhoto)
        .filter_by(user_id=user.id, slot=slot)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="No uploaded photo for this slot")

    if record.status in (
        MoodPhotoStatus.processing.value,
        MoodPhotoStatus.ready.value,
    ):
        return RedirectResponse(url="/profile/my-mood-photos", status_code=303)

    # Fast-fail: original file missing from disk
    orig_path = MOOD_PHOTO_DIR / Path(record.original_url).name
    if not orig_path.exists():
        apply_removal_failure(user.id, slot, db)
        logger.warning(
            "bg_removal_missing_original",
            extra={"user": user.email, "slot": slot},
        )
        return RedirectResponse(url="/profile/my-mood-photos", status_code=303)

    set_status_processing(user.id, slot, db)
    db.commit()
    remove_background_task.delay(user.id, slot, record.original_url)
    logger.info("bg_removal_triggered", extra={"user": user.email, "slot": slot})
    return RedirectResponse(url="/profile/my-mood-photos", status_code=303)


# ── GET /profile/my-mood-photos/{slot}/status ────────────────────────────────

@router.get("/profile/my-mood-photos/{slot}/status")
async def mood_photo_status(
    slot: str,
    user: User    = Depends(get_current_user_web),
    db:   Session = Depends(get_db),
):
    """
    Return JSON status for a slot.  Used by the template JS polling loop when
    status='processing'.

    Response shape:
      {status, processed_png_url, updated_at, processing_timed_out}

    processing_timed_out=True when status='processing' and updated_at is older
    than settings.PROCESSING_TIMEOUT_SECONDS — signals a stuck worker to the UI.

    Auth: filter_by(user_id=user.id) — own record only; no cross-user leakage.
    """
    if slot not in MOOD_PHOTO_SLOTS:
        raise HTTPException(status_code=422, detail=f"Invalid slot: {slot!r}")

    record = (
        db.query(UserMoodPhoto)
        .filter_by(user_id=user.id, slot=slot)
        .first()
    )
    if record is None:
        return JSONResponse({
            "status":               "not_uploaded",
            "processed_png_url":    None,
            "updated_at":           None,
            "processing_timed_out": False,
        })

    timed_out = (
        record.status == MoodPhotoStatus.processing.value
        and record.updated_at is not None
        and (
            datetime.now(timezone.utc) - record.updated_at
        ).total_seconds() > settings.PROCESSING_TIMEOUT_SECONDS
    )
    return JSONResponse({
        "status":               record.status,
        "processed_png_url":    record.processed_png_url,
        "updated_at":           record.updated_at.isoformat() if record.updated_at else None,
        "processing_timed_out": timed_out,
    })


# ── POST /profile/my-mood-photos/{slot}/reset-processing ─────────────────────

@router.post("/profile/my-mood-photos/{slot}/reset-processing")
async def mood_photo_reset_processing(
    slot: str,
    user: User    = Depends(get_current_user_web),
    db:   Session = Depends(get_db),
):
    """
    Reset a stuck 'processing' record back to 'uploaded'.

    Idempotent: no-op if status != 'processing' or no record exists.
    Auth: filter_by(user_id=user.id) in reset_processing — own record only.
    CSRF: caller must supply X-CSRF-Token header (JS fetch).
    """
    if slot not in MOOD_PHOTO_SLOTS:
        raise HTTPException(status_code=422, detail=f"Invalid slot: {slot!r}")

    record = (
        db.query(UserMoodPhoto)
        .filter_by(user_id=user.id, slot=slot)
        .first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="No mood photo record for this slot")

    reset_processing(user.id, slot, db)
    db.commit()
    logger.info("bg_processing_reset", extra={"user": user.email, "slot": slot})
    return RedirectResponse(url="/profile/my-mood-photos", status_code=303)
