"""My Cards hub — owned-card collection and purchase compat wrapper."""
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User
from ...services.card_design_service import (
    CHALLENGE_CARD_FORMATS,
    WELCOME_CARD_FORMATS,
    AlreadyOwnedError,
    FreeDesignError,
    get_all_designs,
    get_owned_design_ids,
    is_design_accessible,
    purchase_design,
)
from ...services.card_system import card_registry
from ...services.credit_service import InsufficientCreditsError

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["my-cards"])

# Maps card_type_id → canonical collection path (used for purchase redirect targets)
_TYPE_TO_FAMILY_PATH: dict[str, str] = {
    "player_card":    "/my-cards/player",
    "welcome_card":   "/my-cards/welcome",
    "challenge_card": "/my-cards/challenge",
}


# ── Hub ───────────────────────────────────────────────────────────────────────

@router.get("/my-cards", response_class=HTMLResponse)
async def my_cards_hub(
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Hub — owned-count tiles for all three card families."""
    all_designs = get_all_designs(db)
    pc_total    = len(all_designs)
    pc_owned_ids = set(get_owned_design_ids(db, user.id, "player_card"))
    pc_owned_count = len(pc_owned_ids)

    wc_total      = len(WELCOME_CARD_FORMATS)
    _wc_valid_ids = {f.design_id for f in WELCOME_CARD_FORMATS}
    wc_owned_ids  = set(get_owned_design_ids(db, user.id, "welcome_card"))
    wc_owned_count = len(wc_owned_ids & _wc_valid_ids)

    cc_total      = len(CHALLENGE_CARD_FORMATS)
    _cc_valid_ids = {f.design_id for f in CHALLENGE_CARD_FORMATS}
    cc_owned_ids  = set(get_owned_design_ids(db, user.id, "challenge_card"))
    cc_owned_count = len(cc_owned_ids & _cc_valid_ids)

    return templates.TemplateResponse(
        "my_cards_hub.html",
        {
            "request":    request,
            "user":       user,
            # Player Card
            "pc_owned_count": pc_owned_count,
            "pc_total":       pc_total,
            # Welcome Card
            "wc_owned_count": wc_owned_count,
            "wc_total":       wc_total,
            # Challenge Card
            "cc_owned_count": cc_owned_count,
            "cc_total":       cc_total,
            # Flash
            "flash_purchased": request.query_params.get("purchased"),
            # Explicit LFA spec context — multi-spec safe
            "spec_dashboard_url":  "/dashboard/lfa-football-player",
            "spec_dashboard_icon": "⚽",
            "spec_profile_url":    "/profile/lfa-football-player",
            "spec_profile_icon":   "🪪",
        },
    )


# ── /my-cards/shop — retired, redirect to central shop ───────────────────────

@router.get("/my-cards/shop")
async def my_cards_shop(tab: str | None = Query(default=None)):
    """Retired shop page — redirects to the card shop."""
    destinations = {
        "player":    "/shop?type=player_card",
        "welcome":   "/shop?type=welcome_card",
        "challenge": "/shop?type=challenge_card",
    }
    return RedirectResponse(
        url=destinations.get(tab or "", "/shop"),
        status_code=301,
    )


# ── Legacy hyphenated paths → canonical short paths (301) ────────────────────

@router.get("/my-cards/player-card")
async def my_cards_player_card_legacy():
    return RedirectResponse(url="/my-cards/player", status_code=301)


@router.get("/my-cards/welcome-card")
async def my_cards_welcome_card_legacy():
    return RedirectResponse(url="/my-cards/welcome", status_code=301)


@router.get("/my-cards/challenge-card")
async def my_cards_challenge_card_legacy():
    return RedirectResponse(url="/my-cards/challenge", status_code=301)


# ── Purchase POST compat wrapper ──────────────────────────────────────────────

@router.post("/my-cards/designs/{card_type_id}/{design_id}/get")
async def get_card(
    card_type_id: str,
    design_id: str,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Compat purchase endpoint — delegates to purchase_design(), redirects to collection."""
    family_path = _TYPE_TO_FAMILY_PATH.get(card_type_id, "/my-cards")
    try:
        purchase_design(db, user, card_type_id, design_id)
        return RedirectResponse(
            f"{family_path}?purchased={design_id}",
            status_code=303,
        )
    except FreeDesignError:
        return RedirectResponse(f"{family_path}?error=free",    status_code=303)
    except AlreadyOwnedError:
        return RedirectResponse(f"{family_path}?error=owned",   status_code=303)
    except InsufficientCreditsError:
        return RedirectResponse(f"{family_path}?error=credits", status_code=303)
    except ValueError:
        return RedirectResponse(f"{family_path}?error=invalid", status_code=303)


# ── Player Card collection (owned-only) — canonical: /my-cards/player ─────────

@router.get("/my-cards/player", response_class=HTMLResponse)
async def my_cards_player_card(
    request: Request,
    db: Session     = Depends(get_db),
    user: User      = Depends(get_current_user_web),
):
    """Player Card owned-only collection."""
    all_designs = get_all_designs(db)

    design_rows = [
        {
            "id":          d.id,
            "label":       d.label,
            "description": d.description,
            "credit_cost": d.credit_cost,
            "is_premium":  d.is_premium,
            "state":       "owned",
        }
        for d in all_designs
        if is_design_accessible(db, user.id, "player_card", d.id)
    ]

    owned_count = len(design_rows)
    total_count = owned_count

    return templates.TemplateResponse(
        "my_cards_player_card.html",
        {
            "request":         request,
            "user":            user,
            "design_rows":     design_rows,
            "owned_count":     owned_count,
            "total_count":     total_count,
            "flash_purchased": request.query_params.get("purchased"),
            "flash_error":     request.query_params.get("error"),
            "spec_dashboard_url": "/dashboard/lfa-football-player",
        },
    )


# ── Welcome Card collection (owned-only) — canonical: /my-cards/welcome ───────

@router.get("/my-cards/welcome", response_class=HTMLResponse)
async def my_cards_welcome_card(
    request: Request,
    db: Session     = Depends(get_db),
    user: User      = Depends(get_current_user_web),
):
    """Welcome Card owned-only format collection."""
    format_rows = [
        {
            "design_id":        fmt.design_id,
            "label":            fmt.label,
            "style_tag":        fmt.style_tag,
            "dims":             fmt.dims,
            "credit_cost":      fmt.credit_cost,
            "preview_platform": fmt.preview_platform,
            "state":            "owned",
            "preview_url":      f"/profile/onboarding-card?platform={fmt.preview_platform}",
            "export_url":       f"/profile/onboarding-card/export?platform={fmt.preview_platform}",
        }
        for fmt in WELCOME_CARD_FORMATS
        if is_design_accessible(db, user.id, "welcome_card", fmt.design_id)
    ]

    owned_count = len(format_rows)
    total_count = owned_count

    return templates.TemplateResponse(
        "my_cards_welcome_card.html",
        {
            "request":         request,
            "user":            user,
            "format_rows":     format_rows,
            "owned_count":     owned_count,
            "total_count":     total_count,
            "flash_purchased": request.query_params.get("purchased"),
            "flash_error":     request.query_params.get("error"),
            "spec_dashboard_url": "/dashboard/lfa-football-player",
        },
    )


# ── Challenge Card collection (owned-only) — canonical: /my-cards/challenge ───

@router.get("/my-cards/challenge", response_class=HTMLResponse)
async def my_cards_challenge_card(
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Challenge Card owned-only format collection."""
    cc_format_rows = [
        {
            "design_id":   fmt.design_id,
            "label":       fmt.label,
            "style_tag":   fmt.style_tag,
            "dims":        fmt.dims,
            "credit_cost": fmt.credit_cost,
            "state":       "owned",
        }
        for fmt in CHALLENGE_CARD_FORMATS
        if is_design_accessible(db, user.id, "challenge_card", fmt.design_id)
    ]
    cc_owned_count = len(cc_format_rows)
    cc_total       = cc_owned_count

    return templates.TemplateResponse(
        "my_cards_challenge_card.html",
        {
            "request":         request,
            "user":            user,
            "cc_format_rows":  cc_format_rows,
            "cc_owned_count":  cc_owned_count,
            "cc_total":        cc_total,
            "flash_purchased": request.query_params.get("purchased"),
            "flash_error":     request.query_params.get("error"),
            "spec_dashboard_url": "/dashboard/lfa-football-player",
        },
    )
