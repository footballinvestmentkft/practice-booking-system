"""My Cards hub — cross-card-type navigation hub and detail redirects."""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ...dependencies import get_current_user_web
from ...models.user import User
from ...services.card_system import card_registry

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["my-cards"])


@router.get("/my-cards", response_class=HTMLResponse)
async def my_cards_hub(
    request: Request,
    user: User = Depends(get_current_user_web),
):
    """Hub page listing all card types — active (v≥1) and coming-soon (v0)."""
    card_specs = [
        card_registry.get_card_type_spec(tid)
        for tid in card_registry.list_card_type_ids()
    ]
    return templates.TemplateResponse(
        "my_cards_hub.html",
        {
            "request": request,
            "user": user,
            "card_specs": card_specs,
        },
    )


@router.get("/my-cards/player-card")
async def my_cards_player_card(
    user: User = Depends(get_current_user_web),
):
    """Detail entry point for Player Card — redirects to the card editor."""
    return RedirectResponse(
        url="/dashboard/lfa-football-player/card-editor",
        status_code=303,
    )


@router.get("/my-cards/welcome-card")
async def my_cards_welcome_card(
    user: User = Depends(get_current_user_web),
):
    """Detail entry point for Welcome Card — redirects to the onboarding card."""
    return RedirectResponse(
        url="/profile/onboarding-card",
        status_code=303,
    )
