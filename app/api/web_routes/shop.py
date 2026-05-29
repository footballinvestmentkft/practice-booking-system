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

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["shop"])

_TYPE_TO_SHOP_PATH: dict[str, str] = {
    "player_card":    "/shop/cards/player",
    "welcome_card":   "/shop/cards/welcome",
    "challenge_card": "/shop/cards/challenge",
}


# ── Landing ────────────────────────────────────────────────────────────────────

@router.get("/shop", response_class=HTMLResponse)
async def shop_landing(
    request: Request,
    user: User = Depends(get_current_user_web),
):
    return templates.TemplateResponse(
        "shop_landing.html",
        {
            "request": request,
            "user":    user,
            "spec_dashboard_url":  "/dashboard/lfa-football-player",
            "spec_dashboard_icon": "⚽",
        },
    )


@router.get("/shop/cards", response_class=HTMLResponse)
async def shop_cards(
    request: Request,
    user: User = Depends(get_current_user_web),
):
    return templates.TemplateResponse(
        "shop_cards.html",
        {
            "request": request,
            "user":    user,
            "spec_dashboard_url":  "/dashboard/lfa-football-player",
            "spec_dashboard_icon": "⚽",
        },
    )


# ── Player Card shop ───────────────────────────────────────────────────────────

@router.get("/shop/cards/player", response_class=HTMLResponse)
async def shop_player_card(
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    all_designs = get_all_designs(db)
    credits = user.credit_balance

    def _state(design) -> str:
        if is_design_accessible(db, user.id, "player_card", design.id):
            return "owned"
        if design.credit_cost == 0:
            return "not_available"
        return "get_card" if credits >= design.credit_cost else "locked"

    design_rows = [
        {
            "id":          d.id,
            "label":       d.label,
            "description": d.description,
            "credit_cost": d.credit_cost,
            "is_premium":  d.is_premium,
            "state":       _state(d),
        }
        for d in all_designs
    ]

    owned_count = sum(1 for r in design_rows if r["state"] == "owned")
    total_count = len(design_rows)

    return templates.TemplateResponse(
        "shop_player_card.html",
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
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    credits = user.credit_balance

    def _wc_state(fmt) -> str:
        if is_design_accessible(db, user.id, "welcome_card", fmt.design_id):
            return "owned"
        return "get_card" if credits >= fmt.credit_cost else "locked"

    format_rows = [
        {
            "design_id":        fmt.design_id,
            "label":            fmt.label,
            "style_tag":        fmt.style_tag,
            "dims":             fmt.dims,
            "credit_cost":      fmt.credit_cost,
            "preview_platform": fmt.preview_platform,
            "state":            _wc_state(fmt),
            "preview_url":      f"/profile/onboarding-card?platform={fmt.preview_platform}",
            "export_url":       f"/profile/onboarding-card/export?platform={fmt.preview_platform}",
        }
        for fmt in WELCOME_CARD_FORMATS
    ]

    owned_count = sum(1 for r in format_rows if r["state"] == "owned")
    total_count = len(format_rows)

    return templates.TemplateResponse(
        "shop_welcome_card.html",
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


# ── Challenge Card shop ────────────────────────────────────────────────────────

@router.get("/shop/cards/challenge", response_class=HTMLResponse)
async def shop_challenge_card(
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    credits = user.credit_balance

    def _cc_state(fmt) -> str:
        if is_design_accessible(db, user.id, "challenge_card", fmt.design_id):
            return "owned"
        return "get_card" if credits >= fmt.credit_cost else "locked"

    format_rows = [
        {
            "design_id":   fmt.design_id,
            "label":       fmt.label,
            "style_tag":   fmt.style_tag,
            "dims":        fmt.dims,
            "credit_cost": fmt.credit_cost,
            "state":       _cc_state(fmt),
        }
        for fmt in CHALLENGE_CARD_FORMATS
    ]

    owned_count = sum(1 for r in format_rows if r["state"] == "owned")
    total_count = len(format_rows)

    return templates.TemplateResponse(
        "shop_challenge_card.html",
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
