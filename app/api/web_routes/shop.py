"""Card shop — browse and purchase card designs and platform formats."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
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
    is_design_accessible,
    purchase_design,
)
from ...services.card_constants import PC_FORMAT_META
from ...services.credit_service import InsufficientCreditsError
from ...services.card_color_service import (
    get_colors_for_family as _get_colors_for_family,
    get_owned_color_ids as _get_owned_color_ids,
)
from ...services.shop_catalog_service import (
    build_shop_catalog as _build_catalog,
    resolve_type_filter as _resolve_type,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["shop"])

_TYPE_TO_SHOP_PATH: dict[str, str] = {
    "player_card":           "/shop/cards/player",
    "welcome_card":          "/shop/cards/welcome",
    "challenge_card":        "/shop/cards/challenge",
    "virtual_training_card": "/shop?type=virtual_training_card",
}


# ── Landing ────────────────────────────────────────────────────────────────────

@router.get("/shop", response_class=HTMLResponse)
async def shop_landing(
    request: Request,
    db: Session  = Depends(get_db),
    user: User   = Depends(get_current_user_web),
    type: str | None = None,
):
    """SHOP-1: Unified shop listing — all card products in one page with filter."""
    type_filter  = _resolve_type(type)
    shop_items   = _build_catalog(db, user.id, user.credit_balance, type_filter)
    active_label = {
        "player_card":           "Player Cards",
        "welcome_card":          "Welcome Cards",
        "challenge_card":        "Challenge Cards",
        "virtual_training_card": "VT Cards",
    }.get(type_filter or "", "All Cards")

    return templates.TemplateResponse(
        "shop_unified.html",
        {
            "request":             request,
            "user":                user,
            "shop_items":          shop_items,
            "type_filter":         type_filter or "",
            "active_filter_label": active_label,
            "total_count":         len(shop_items),
            "owned_count":         sum(1 for i in shop_items if i.is_owned),
            "spec_dashboard_url":  "/dashboard/lfa-football-player",
            "spec_dashboard_icon": "⚽",
        },
    )


def _flash_params(request: Request) -> str:
    """Extract allowed flash params (error, purchased) for redirect passthrough."""
    parts = []
    for key in ("error", "purchased"):
        val = request.query_params.get(key)
        if val:
            parts.append(f"{key}={val}")
    return ("&" + "&".join(parts)) if parts else ""


@router.get("/shop/cards", response_class=HTMLResponse)
async def shop_cards(
    request: Request,
    user: User = Depends(get_current_user_web),
):
    """SHOP-2: 302 redirect → /shop (unified listing)."""
    return RedirectResponse(url=f"/shop{_flash_params(request)}", status_code=302)


# ── Player Card shop ───────────────────────────────────────────────────────────

@router.get("/shop/cards/player", response_class=HTMLResponse)
async def shop_player_card(
    request: Request,
    user: User = Depends(get_current_user_web),
):
    """SHOP-2: 302 redirect → /shop?type=player_card."""
    return RedirectResponse(
        url=f"/shop?type=player_card{_flash_params(request)}", status_code=302
    )


# ── Player Card Color Shop ─────────────────────────────────────────────────────

@router.get("/shop/cards/player/colors", response_class=HTMLResponse)
async def shop_player_card_colors(
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Player Card Color Shop — browse and unlock premium color packs (TS-1)."""
    raw_colors = _get_colors_for_family("player_card")
    owned_ids  = _get_owned_color_ids(db, user.id, "player_card")

    player_colors = [
        {
            "id":          c.id,
            "label":       c.label,
            "dot_color":   c.dot_color,
            "is_premium":  c.is_premium,
            "credit_cost": c.credit_cost,
            "is_owned":    (not c.is_premium) or (c.id in owned_ids),
        }
        for c in raw_colors
    ]

    return templates.TemplateResponse(
        "shop_card_player_colors.html",
        {
            "request":       request,
            "user":          user,
            "player_colors": player_colors,
            "credit_balance": user.credit_balance,
        },
    )


# ── Player Card collection detail ─────────────────────────────────────────────

@router.get("/shop/cards/player/{collection_id}", response_class=HTMLResponse)
async def shop_player_card_detail(
    collection_id: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    all_designs = get_all_designs(db)
    design = next((d for d in all_designs if d.id == collection_id), None)
    if design is None:
        raise HTTPException(status_code=404)

    credits = user.credit_balance
    owned = is_design_accessible(db, user.id, "player_card", collection_id)

    if not design.available or design.credit_cost == 0:
        state = "not_available"
    elif owned:
        state = "owned"
    elif credits >= design.credit_cost:
        state = "get_card"
    else:
        state = "locked"

    meta_by_bucket = {m["bucket"]: m for m in PC_FORMAT_META}
    format_rows = [
        meta_by_bucket[b]
        for b in design.supported_export_buckets
        if b in meta_by_bucket
    ]

    return templates.TemplateResponse(
        "shop_player_card_detail.html",
        {
            "request":         request,
            "user":            user,
            "design":          design,
            "collection_id":   collection_id,
            "state":           state,
            "format_rows":     format_rows,
            "flash_purchased": request.query_params.get("purchased"),
            "flash_error":     request.query_params.get("error"),
        },
    )


# ── Welcome Card shop ──────────────────────────────────────────────────────────

@router.get("/shop/cards/welcome", response_class=HTMLResponse)
async def shop_welcome_card(
    request: Request,
    user: User = Depends(get_current_user_web),
):
    """SHOP-2: 302 redirect → /shop?type=welcome_card."""
    return RedirectResponse(
        url=f"/shop?type=welcome_card{_flash_params(request)}", status_code=302
    )


# ── Challenge Card shop ────────────────────────────────────────────────────────

@router.get("/shop/cards/challenge", response_class=HTMLResponse)
async def shop_challenge_card(
    request: Request,
    user: User = Depends(get_current_user_web),
):
    """SHOP-2: 302 redirect → /shop?type=challenge_card."""
    return RedirectResponse(
        url=f"/shop?type=challenge_card{_flash_params(request)}", status_code=302
    )


# ── Purchase POST ──────────────────────────────────────────────────────────────

@router.post("/shop/cards/{card_type_id}/buy/{design_id}")
async def shop_buy(
    card_type_id: str,
    design_id: str,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Purchase a card design/format from the shop (credit deduction + ownership row)."""
    shop_path = _TYPE_TO_SHOP_PATH.get(card_type_id, "/shop/cards")
    try:
        purchase_design(db, user, card_type_id, design_id)
        return RedirectResponse(f"{shop_path}?purchased={design_id}", status_code=303)
    except FreeDesignError:
        return RedirectResponse(f"{shop_path}?error=free",    status_code=303)
    except AlreadyOwnedError:
        return RedirectResponse(f"{shop_path}?error=owned",   status_code=303)
    except InsufficientCreditsError:
        return RedirectResponse(f"{shop_path}?error=credits", status_code=303)
    except ValueError:
        return RedirectResponse(f"{shop_path}?error=invalid", status_code=303)
