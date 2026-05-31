"""Card Editor routes — family-specific customizer/export pages.

Phase WCE-1:  Welcome Card Customizer (preview + export wrapper, no draft).
Phase CE-3.1: Card Studio landing — /card-editor entry point.
Phase CE-3.3: Welcome Card Studio — /card-editor/welcome (draft-free, query-param format).
Phase CE-3.4: Challenge Card Studio — /card-editor/challenge (draft-free, format gallery, no preview/export).
Future: /card-editor/player/{collection_id}
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User, UserRole
from ...models.license import UserLicense
from ...services.card_design_service import (
    CHALLENGE_CARD_FORMATS,
    WELCOME_CARD_FORMATS,
    get_owned_design_ids,
    is_design_accessible,
)
from ...services.mood_photo_service import get_mood_photos_for_user

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["card-editor"])

# Lookup map: design_id → NonPlayerCardFormatDefinition
_WC_FORMAT_BY_ID = {f.design_id: f for f in WELCOME_CARD_FORMATS}

# Ordered mood slot metadata for the Welcome Studio picker (CE-3.8).
# Mirrors _SLOT_META in mood_photos.py but kept local to avoid route→route coupling.
_MOOD_SLOT_META: list[dict] = [
    {"slot": "mood_intro_neutral",    "emoji": "😐", "label": "Neutral"},
    {"slot": "mood_happy_smile",      "emoji": "😊", "label": "Happy"},
    {"slot": "mood_celebration",      "emoji": "🎉", "label": "Celebration"},
    {"slot": "mood_sad_disappointed", "emoji": "😔", "label": "Sad"},
    {"slot": "mood_angry_competitive","emoji": "😤", "label": "Angry"},
    {"slot": "mood_surprised_shocked","emoji": "😲", "label": "Surprised"},
]

# Aspect-ratio CSS class per preview_platform
_WC_RATIO: dict[str, str] = {
    "instagram_portrait":  "mfg-ratio-45",
    "instagram_story":     "mfg-ratio-916",
    "instagram_square":    "mfg-ratio-11",
    "tiktok":              "mfg-ratio-916",
    "facebook_square":     "mfg-ratio-11",
    "facebook_landscape":  "mfg-ratio-169",
    "banner_custom":       "mfg-ratio-169",
}

# Valid design-ID sets for WC / CC (used in landing ownership count)
_WC_VALID_IDS: frozenset[str] = frozenset(f.design_id for f in WELCOME_CARD_FORMATS)
_CC_VALID_IDS: frozenset[str] = frozenset(f.design_id for f in CHALLENGE_CARD_FORMATS)


# ── Card Studio Landing ───────────────────────────────────────────────────────

@router.get("/card-editor", response_class=HTMLResponse)
async def card_studio_landing(
    user: User = Depends(get_current_user_web),
):
    """CS-S1: backward-compatible permanent redirect → canonical /card-studio."""
    return RedirectResponse(url="/card-studio", status_code=301)


# ── Welcome Card Studio ──────────────────────────────────────────────────────

@router.get("/card-editor/welcome", response_class=HTMLResponse)
async def card_studio_welcome(
    format_id: str | None = Query(default=None, alias="format"),
    user: User = Depends(get_current_user_web),
):
    """CS-S1: backward-compatible permanent redirect → canonical /card-studio/welcome."""
    if format_id:
        return RedirectResponse(
            url=f"/card-studio/welcome?format={format_id}", status_code=301
        )
    return RedirectResponse(url="/card-studio/welcome", status_code=301)


# ── Challenge Card Studio (CE-3.4 — format gallery, no preview/export) ───────

@router.get("/card-editor/challenge", response_class=HTMLResponse)
async def card_studio_challenge(
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Challenge Card Studio — draft-free entry point (CE-3.4).

    Displays owned Challenge Card formats with challenge-aware CTAs.
    No preview, no export, no latest-challenge auto-select, no challenge_id required.

    Guards (same pattern as card_studio_welcome / WCE-1):
      1. Authenticated (get_current_user_web)
      2. LFA_FOOTBALL_PLAYER license + onboarding complete
      3. No owned formats → redirect /shop/cards/challenge
    """
    # ── Guard 2: license + onboarding (identical to WCE-1 / card_studio_welcome) ─
    license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()
    if not license:
        return RedirectResponse(
            url="/dashboard?info=complete_lfa_onboarding_first", status_code=303
        )
    if not license.onboarding_completed:
        return RedirectResponse(
            url="/specialization/lfa-player/onboarding", status_code=303
        )

    # ── Guard 3: owned formats — CHALLENGE_CARD_FORMATS order preserved ───────
    owned_set = set(get_owned_design_ids(db, user.id, "challenge_card")) & _CC_VALID_IDS
    owned_formats_ordered = [
        f for f in CHALLENGE_CARD_FORMATS if f.design_id in owned_set
    ]
    if not owned_formats_ordered:
        return RedirectResponse(url="/shop/cards/challenge", status_code=303)

    # ── 200 path — format gallery, no preview/export context ─────────────────
    cc_format_rows = [
        {
            "design_id": f.design_id,
            "label":     f.label,
            "style_tag": f.style_tag,
            "dims":      f.dims,
        }
        for f in owned_formats_ordered
    ]

    return templates.TemplateResponse(
        "card_studio_challenge.html",
        {
            "request":         request,
            "user":            user,
            "cc_format_rows":  cc_format_rows,
            "spec_dashboard_url":  "/dashboard/lfa-football-player",
            "spec_dashboard_icon": "⚽",
            "spec_profile_url":    "/profile/lfa-football-player",
            "spec_profile_icon":   "🪪",
        },
    )


# ── Welcome Card Customizer (WCE-1 — per-format, unchanged) ──────────────────

@router.get("/card-editor/welcome/{format_id}", response_class=HTMLResponse)
async def welcome_card_editor(
    format_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Welcome Card Customizer — preview + export wrapper for an owned format.

    Guards (in order):
      1. Authenticated (get_current_user_web)
      2. LFA_FOOTBALL_PLAYER license + onboarding complete
      3. format_id in WELCOME_CARD_FORMATS → 404 otherwise
      4. CDO ownership (is_design_accessible) → redirect shop if not owned
         Admin bypass: skipped for UserRole.ADMIN
    """
    # ── Guard 2: license + onboarding ────────────────────────────────────────
    license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()
    if not license:
        return RedirectResponse(
            url="/dashboard?info=complete_lfa_onboarding_first", status_code=303
        )
    if not license.onboarding_completed:
        return RedirectResponse(
            url="/specialization/lfa-player/onboarding", status_code=303
        )

    # ── Guard 3: known format ─────────────────────────────────────────────────
    fmt = _WC_FORMAT_BY_ID.get(format_id)
    if fmt is None:
        raise HTTPException(status_code=404, detail=f"Unknown Welcome Card format: {format_id!r}")

    # ── Guard 4: ownership ────────────────────────────────────────────────────
    if user.role != UserRole.ADMIN:
        if not is_design_accessible(db, user.id, "welcome_card", format_id):
            return RedirectResponse(
                url="/shop/cards/welcome?error=not_owned", status_code=303
            )

    ratio_class = _WC_RATIO.get(fmt.preview_platform, "mfg-ratio-11")
    preview_url = f"/profile/onboarding-card?platform={fmt.preview_platform}"
    export_url  = f"/profile/onboarding-card/export?platform={fmt.preview_platform}"

    return templates.TemplateResponse(
        "card_editor_welcome.html",
        {
            "request":     request,
            "user":        user,
            "fmt":         fmt,
            "format_id":   format_id,
            "ratio_class": ratio_class,
            "preview_url": preview_url,
            "export_url":  export_url,
        },
    )
