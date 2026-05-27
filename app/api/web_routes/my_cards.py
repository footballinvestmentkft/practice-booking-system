"""My Cards hub — cross-card-type navigation hub and detail redirects."""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.license import UserLicense
from ...models.user import User
from ...models.virtual_training import VirtualTrainingAttempt
from ...models.vt_challenge import ChallengeStatus, VirtualTrainingChallenge
from ...services.card_design_service import (
    AlreadyOwnedError,
    FreeDesignError,
    _NON_PLAYER_CARD_PRICES,
    get_all_designs,
    is_design_accessible,
    purchase_design,
)
from ...services.card_draft_service import CardDraftService
from ...services.card_system import card_registry
from ...services.card_theme_service import get_all_themes
from ...services.credit_service import InsufficientCreditsError
from .vt_challenges import (
    CHALLENGE_CARD_PLATFORMS,
    get_locked_challenge_card_phases,
    get_unlocked_challenge_card_phases,
    _display_name,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["my-cards"])

_TYPE_TO_TAB: dict[str, str] = {
    "player_card":    "player",
    "welcome_card":   "welcome",
    "challenge_card": "challenge",
}


@router.get("/my-cards", response_class=HTMLResponse)
async def my_cards_hub(
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Hub — entitlement-aware central page for all card families."""
    license = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
        UserLicense.is_active == True,
    ).first()

    card_variant    = (license.card_variant if license else None) or "fifa"
    designs_by_id   = {d.id: d for d in get_all_designs(db)}
    design_obj      = designs_by_id.get(card_variant)
    pc_price        = design_obj.credit_cost if design_obj else 0
    pc_free         = not design_obj.is_premium if design_obj else True
    pc_design_label = design_obj.label if design_obj else "FIFA Classic"
    pc_owned        = is_design_accessible(db, user.id, "player_card", card_variant)

    if pc_free:
        pc_state = "free"
    elif pc_owned:
        pc_state = "owned"
    elif user.credit_balance >= pc_price:
        pc_state = "get_card"
    else:
        pc_state = "locked"

    wc_price = _NON_PLAYER_CARD_PRICES[("welcome_card",   "default")]
    wc_owned = is_design_accessible(db, user.id, "welcome_card", "default")
    if wc_owned:
        wc_state = "owned"
    elif user.credit_balance >= wc_price:
        wc_state = "get_card"
    else:
        wc_state = "locked"

    cc_price = _NON_PLAYER_CARD_PRICES[("challenge_card", "challenge")]
    cc_owned = is_design_accessible(db, user.id, "challenge_card", "challenge")
    if cc_owned:
        cc_state = "owned"
    elif user.credit_balance >= cc_price:
        cc_state = "get_card"
    else:
        cc_state = "locked"

    return templates.TemplateResponse(
        "my_cards_hub.html",
        {
            "request":         request,
            "user":            user,
            # Player Card
            "pc_state":        pc_state,
            "pc_price":        pc_price,
            "pc_design":       card_variant,
            "pc_design_label": pc_design_label,
            # Welcome Card
            "wc_state":        wc_state,
            "wc_price":        wc_price,
            # Challenge Card
            "cc_state":        cc_state,
            "cc_price":        cc_price,
            # Flash
            "flash_purchased": request.query_params.get("purchased"),
            # Explicit LFA spec context — multi-spec safe
            "spec_dashboard_url":  "/dashboard/lfa-football-player",
            "spec_dashboard_icon": "⚽",
            "spec_profile_url":    "/profile/lfa-football-player",
            "spec_profile_icon":   "🪪",
        },
    )


@router.get("/my-cards/shop", response_class=HTMLResponse)
async def my_cards_shop(
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Card Design Shop — browse and purchase card design entitlements."""
    player_designs = get_all_designs(db)
    wc_price = _NON_PLAYER_CARD_PRICES[("welcome_card",   "default")]
    cc_price = _NON_PLAYER_CARD_PRICES[("challenge_card", "challenge")]
    credits  = user.credit_balance

    def _state(card_type_id: str, design_id: str, credit_cost: int, is_premium: bool) -> str:
        if not is_premium and card_type_id == "player_card":
            return "free"
        if is_design_accessible(db, user.id, card_type_id, design_id):
            return "owned"
        return "purchasable" if credits >= credit_cost else "locked"

    player_design_rows = [
        {
            "id":          d.id,
            "label":       d.label,
            "description": d.description,
            "credit_cost": d.credit_cost,
            "is_premium":  d.is_premium,
            "state":       _state("player_card", d.id, d.credit_cost, d.is_premium),
        }
        for d in player_designs
    ]

    wc_state = _state("welcome_card", "default", wc_price, True)
    cc_state = _state("challenge_card", "challenge", cc_price, True)

    return templates.TemplateResponse(
        "my_cards_shop.html",
        {
            "request":             request,
            "user":                user,
            "player_design_rows":  player_design_rows,
            "wc_price":            wc_price,
            "cc_price":            cc_price,
            "wc_state":            wc_state,
            "cc_state":            cc_state,
            "flash_purchased":     request.query_params.get("purchased"),
            "flash_error":         request.query_params.get("error"),
            "spec_dashboard_url":  "/dashboard/lfa-football-player",
        },
    )


@router.post("/my-cards/designs/{card_type_id}/{design_id}/get")
async def get_card(
    card_type_id: str,
    design_id: str,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Purchase a card design entitlement (credit deduction + ownership row)."""
    tab = _TYPE_TO_TAB.get(card_type_id, "player")
    try:
        purchase_design(db, user, card_type_id, design_id)
        return RedirectResponse(
            f"/my-cards?purchased={card_type_id}:{design_id}",
            status_code=303,
        )
    except FreeDesignError:
        return RedirectResponse(f"/my-cards/shop?error=free&tab={tab}",    status_code=303)
    except AlreadyOwnedError:
        return RedirectResponse(f"/my-cards/shop?error=owned&tab={tab}",   status_code=303)
    except InsufficientCreditsError:
        return RedirectResponse(f"/my-cards/shop?error=credits&tab={tab}", status_code=303)
    except ValueError:
        return RedirectResponse(f"/my-cards/shop?error=invalid&tab={tab}", status_code=303)


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


_CHALLENGE_CARD_FORMATS = [
    {"id": "challenge_post_16_9",  "label": "Post (16:9)",  "dims": "1280×720",  "platform": "Facebook / Instagram"},
    {"id": "challenge_story_9_16", "label": "Story (9:16)", "dims": "1080×1920", "platform": "TikTok / Instagram Story"},
]

_COLLECTION_LIMIT = 5

_PHASE_LABELS = {
    "challenge_sent":         "Challenge Sent",
    "challenge_received":     "Challenge Received",
    "challenge_accepted":     "Challenge Accepted",
    "waiting_for_opponent":   "Waiting for Opponent",
    "live_lobby_ready":       "Live Lobby",
    "live_in_progress":       "Live — In Progress",
    "completed_score_win":    "Result — Score",
    "completed_draw":         "Result — Draw",
    "completed_forfeit_win":  "Result — Forfeit Win",
    "completed_forfeit_loss": "Result — Forfeit Loss",
    "no_contest":             "No Contest",
    "skill_delta_result":     "Skill Progress",
}


def _build_challenge_card_row(ch: VirtualTrainingChallenge, viewer_id: int, my_attempt) -> dict:
    """Build a single challenge row for the collection page."""
    is_challenger  = viewer_id == ch.challenger_id
    opponent       = ch.challenged if is_challenger else ch.challenger
    unlocked       = get_unlocked_challenge_card_phases(ch, viewer_id, my_attempt)
    locked         = get_locked_challenge_card_phases(ch, viewer_id)

    def _phase_cards(phases: list[str], is_locked: bool) -> list[dict]:
        cards = []
        for phase in phases:
            preview_urls = {
                fmt["id"]: f"/challenges/{ch.id}/card/preview?phase={phase}&platform={fmt['id']}"
                for fmt in _CHALLENGE_CARD_FORMATS
            }
            export_urls = (
                {
                    fmt["id"]: f"/challenges/{ch.id}/card/export?phase={phase}&platform={fmt['id']}"
                    for fmt in _CHALLENGE_CARD_FORMATS
                }
                if not is_locked else {}
            )
            cards.append({
                "phase":        phase,
                "label":        _PHASE_LABELS.get(phase, phase.replace("_", " ").title()),
                "is_locked":    is_locked,
                "preview_urls": preview_urls,
                "export_urls":  export_urls,
                "formats":      _CHALLENGE_CARD_FORMATS,
            })
        return cards

    phase_cards = _phase_cards(unlocked, False) + _phase_cards(locked, True)

    return {
        "id":            ch.id,
        "status":        ch.status.value,
        "challenge_mode": ch.challenge_mode,
        "opponent_name": _display_name(opponent) if opponent else "Unknown",
        "game_name":     ch.game.name if ch.game else "—",
        "created_at":    ch.created_at,
        "completed_at":  ch.completed_at,
        "phase_cards":   phase_cards,
        "unlocked_count": len(unlocked),
    }


@router.get("/my-cards/challenge-card", response_class=HTMLResponse)
async def my_cards_challenge_card(
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Challenge Card Collection — phase-based card manager with inline preview."""
    draft  = CardDraftService.get_or_create_singleton(db, user.id, "challenge_card")
    themes = get_all_themes(db=db)

    # Fetch last N challenges where user is participant
    recent_challenges = (
        db.query(VirtualTrainingChallenge)
        .filter(
            or_(
                VirtualTrainingChallenge.challenger_id == user.id,
                VirtualTrainingChallenge.challenged_id == user.id,
            )
        )
        .order_by(VirtualTrainingChallenge.created_at.desc())
        .limit(_COLLECTION_LIMIT)
        .all()
    )

    # Batch-load attempts for skill_delta check
    attempt_ids = set()
    for ch in recent_challenges:
        is_challenger = user.id == ch.challenger_id
        my_id = ch.challenger_attempt_id if is_challenger else ch.challenged_attempt_id
        if my_id:
            attempt_ids.add(my_id)

    attempts_map = {}
    if attempt_ids:
        for a in db.query(VirtualTrainingAttempt).filter(
            VirtualTrainingAttempt.id.in_(attempt_ids)
        ).all():
            attempts_map[a.id] = a

    challenge_rows = []
    for ch in recent_challenges:
        is_challenger = user.id == ch.challenger_id
        my_attempt_id = ch.challenger_attempt_id if is_challenger else ch.challenged_attempt_id
        my_attempt    = attempts_map.get(my_attempt_id) if my_attempt_id else None
        challenge_rows.append(_build_challenge_card_row(ch, user.id, my_attempt))

    return templates.TemplateResponse(
        "my_cards_challenge_card.html",
        {
            "request":          request,
            "user":             user,
            "draft":            draft,
            "themes":           themes,
            "formats":          _CHALLENGE_CARD_FORMATS,
            "challenge_rows":   challenge_rows,
            "has_challenges":   bool(challenge_rows),
        },
    )
