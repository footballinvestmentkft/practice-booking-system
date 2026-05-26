"""
mood_photos — web routes for the mood photo feature.

Routes
------
GET  /profile/my-mood-photos            — management page
POST /profile/my-mood-photos/{slot}/upload  — upload / replace a slot
POST /profile/my-mood-photos/{slot}/delete  — HTML-form delete fallback
DELETE /profile/my-mood-photos/{slot}       — JS-fetch delete endpoint
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User
from ...models.license import UserLicense
from ...models.user_mood_photos import MOOD_PHOTO_SLOTS
from ...services.mood_photo_service import (
    delete_mood_photo,
    get_mood_photos_for_user,
    save_mood_photo,
)

logger = logging.getLogger(__name__)

BASE_DIR  = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter()

# Ordered for consistent display in the template
_SLOT_META: list[dict] = [
    {"slot": "mood_intro_neutral",    "label": "Neutral",      "emoji": "😐"},
    {"slot": "mood_happy_smile",      "label": "Happy",        "emoji": "😊"},
    {"slot": "mood_celebration",      "label": "Celebration",  "emoji": "🎉"},
    {"slot": "mood_sad_disappointed", "label": "Sad",          "emoji": "😔"},
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
            "request":     request,
            "user":        user,
            "mood_photos": mood_photos,
            "slots_meta":  _SLOT_META,
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
