"""Card Studio unified shell routes (CS-S0).

Single unified studio shell with card-type switcher.
CS-S0 MVP: Welcome Card mode fully functional.
Player and Challenge modes shown as Coming Soon.

Canonical routes:
  GET /card-studio              → shell (Welcome default for CS-S0)
  GET /card-studio/welcome      → shell, Welcome mode
  GET /card-studio/welcome?format=X → shell, Welcome mode, specific format

Backward-compat routes remain in card_editor.py unchanged.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.license import UserLicense
from ...models.user import User
from ...services.card_design_service import get_owned_design_ids
from ...services.card_design_service import (
    WELCOME_CARD_FORMATS,
    get_card_family,
)
from ...services.mood_photo_service import get_mood_photos_for_user
from .card_editor import (
    _WC_FORMAT_BY_ID,
    _WC_RATIO,
    _WC_VALID_IDS,
    _MOOD_SLOT_META,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["card-studio"])

# ── Helpers ───────────────────────────────────────────────────────────────────

_STUDIO_NAV_CTX = {
    "spec_dashboard_url":  "/dashboard/lfa-football-player",
    "spec_dashboard_icon": "⚽",
    "spec_profile_url":    "/profile/lfa-football-player",
    "spec_profile_icon":   "🪪",
}

_WELCOME_FORMATS_ORDERED = WELCOME_CARD_FORMATS  # 7 formats, canonical order


def _license_guard(db: Session, user_id: int):
    """Return active LFA license or None."""
    return db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()


def _resolve_welcome_context(db: Session, user, format_id: str | None):
    """
    Resolve all context needed for Welcome mode in the unified shell.
    Returns (context_dict, redirect_url_or_None).
    redirect_url: caller must issue 303 redirect if set.
    """
    lic = _license_guard(db, user.id)
    if not lic:
        return None, "/dashboard?info=complete_lfa_onboarding_first"
    if not lic.onboarding_completed:
        return None, "/specialization/lfa-player/onboarding"

    owned_set = set(get_owned_design_ids(db, user.id, "welcome_card")) & _WC_VALID_IDS
    owned_formats_ordered = [f for f in _WELCOME_FORMATS_ORDERED if f.design_id in owned_set]
    if not owned_formats_ordered:
        return None, "/shop/cards/welcome"

    first_owned_id = owned_formats_ordered[0].design_id

    if format_id is None or format_id not in owned_set:
        return None, f"/card-studio/welcome?format={first_owned_id}"

    fmt = _WC_FORMAT_BY_ID[format_id]
    ratio_class = _WC_RATIO.get(fmt.preview_platform, "mfg-ratio-11")
    preview_url = f"/profile/onboarding-card?platform={fmt.preview_platform}"
    export_url  = f"/profile/onboarding-card/export?platform={fmt.preview_platform}"

    owned_format_rows = [
        {
            "design_id":   f.design_id,
            "label":       f.label,
            "style_tag":   f.style_tag,
            "dims":        f.dims,
            "preview_url": f"/profile/onboarding-card?platform={f.preview_platform}",
            "active":      f.design_id == format_id,
        }
        for f in owned_formats_ordered
    ]

    mood_photos = get_mood_photos_for_user(user.id, db)

    ctx = {
        "active_type":        "welcome",
        "active_format":      format_id,
        "fmt":                fmt,
        "ratio_class":        ratio_class,
        "preview_url":        preview_url,
        "export_url":         export_url,
        "owned_format_rows":  owned_format_rows,
        "wc_photo_url":           lic.wc_photo_url,
        "wc_photo_portrait_url":  lic.wc_photo_portrait_url,
        "wc_photo_landscape_url": lic.wc_photo_landscape_url,
        "mood_photos":            mood_photos,
        "mood_slot_meta":         _MOOD_SLOT_META,
        **_STUDIO_NAV_CTX,
    }
    return ctx, None


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/card-studio", response_class=HTMLResponse)
async def card_studio_default(
    request: Request,
    db:   Session = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    """Card Studio landing — defaults to Welcome mode for CS-S0.

    Renders the unified shell with Welcome Card active.
    Player and Challenge shown as Coming Soon.
    """
    # CS-S0: default to Welcome — redirect to canonical with first owned format
    lic = _license_guard(db, user.id)
    if not lic or not lic.onboarding_completed:
        target = "/dashboard?info=complete_lfa_onboarding_first" if not lic else "/specialization/lfa-player/onboarding"
        return RedirectResponse(url=target, status_code=303)

    owned_set = set(get_owned_design_ids(db, user.id, "welcome_card")) & _WC_VALID_IDS
    if owned_set:
        first = next((f.design_id for f in _WELCOME_FORMATS_ORDERED if f.design_id in owned_set), None)
        if first:
            return RedirectResponse(url=f"/card-studio/welcome?format={first}", status_code=303)

    return RedirectResponse(url="/shop/cards/welcome", status_code=303)


@router.get("/card-studio/welcome", response_class=HTMLResponse)
async def card_studio_welcome(
    request:   Request,
    format_id: str | None = Query(default=None, alias="format"),
    db:        Session    = Depends(get_db),
    user:      User       = Depends(get_current_user_web),
):
    """Unified Studio shell — Welcome Card mode (CS-S0 fully functional)."""
    ctx, redirect = _resolve_welcome_context(db, user, format_id)
    if redirect:
        return RedirectResponse(url=redirect, status_code=303)

    return templates.TemplateResponse(
        "card_studio_shell.html",
        {"request": request, "user": user, **ctx},
    )
