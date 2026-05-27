"""My Cards hub — cross-card-type navigation hub and family shop routes."""
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User
from ...models.virtual_training import VirtualTrainingAttempt
from ...models.vt_challenge import ChallengeStatus, VirtualTrainingChallenge
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

# Maps card_type_id → family page path (used for purchase redirect targets)
_TYPE_TO_FAMILY_PATH: dict[str, str] = {
    "player_card":    "/my-cards/player-card",
    "welcome_card":   "/my-cards/welcome-card",
    "challenge_card": "/my-cards/challenge-card",
}


# ── Hub ───────────────────────────────────────────────────────────────────────

@router.get("/my-cards", response_class=HTMLResponse)
async def my_cards_hub(
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Hub — format-count tiles for all three card families."""
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


# ── /my-cards/shop — retired, redirect by tab param ──────────────────────────

@router.get("/my-cards/shop")
async def my_cards_shop(tab: str | None = Query(default=None)):
    """Retired shop page — redirects to the appropriate family shop."""
    destinations = {
        "player":    "/my-cards/player-card",
        "welcome":   "/my-cards/welcome-card",
        "challenge": "/my-cards/challenge-card",
    }
    return RedirectResponse(
        url=destinations.get(tab or "", "/my-cards"),
        status_code=302,
    )


# ── Purchase POST ─────────────────────────────────────────────────────────────

@router.post("/my-cards/designs/{card_type_id}/{design_id}/get")
async def get_card(
    card_type_id: str,
    design_id: str,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Purchase a card design entitlement (credit deduction + ownership row)."""
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


# ── Player Card family shop ───────────────────────────────────────────────────

@router.get("/my-cards/player-card", response_class=HTMLResponse)
async def my_cards_player_card(
    request: Request,
    db: Session     = Depends(get_db),
    user: User      = Depends(get_current_user_web),
):
    """Player Card format shop — browse and purchase Player Card designs."""
    all_designs = get_all_designs(db)
    credits     = user.credit_balance

    def _state(design) -> str:
        if not design.is_premium:
            return "free"
        if is_design_accessible(db, user.id, "player_card", design.id):
            return "owned"
        return "purchasable" if credits >= design.credit_cost else "locked"

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

    owned_count = sum(1 for r in design_rows if r["state"] in ("free", "owned"))
    total_count = len(design_rows)

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


# ── Welcome Card family shop ──────────────────────────────────────────────────

@router.get("/my-cards/welcome-card", response_class=HTMLResponse)
async def my_cards_welcome_card(
    request: Request,
    db: Session     = Depends(get_db),
    user: User      = Depends(get_current_user_web),
):
    """Welcome Card format shop — browse and purchase Welcome Card platform formats."""
    credits = user.credit_balance

    def _wc_state(fmt) -> str:
        if is_design_accessible(db, user.id, "welcome_card", fmt.design_id):
            return "owned"
        return "purchasable" if credits >= fmt.credit_cost else "locked"

    format_rows = [
        {
            "design_id":       fmt.design_id,
            "label":           fmt.label,
            "style_tag":       fmt.style_tag,
            "dims":            fmt.dims,
            "credit_cost":     fmt.credit_cost,
            "preview_platform": fmt.preview_platform,
            "state":           _wc_state(fmt),
            "preview_url":     f"/profile/onboarding-card?platform={fmt.preview_platform}",
            "export_url":      f"/profile/onboarding-card/export?platform={fmt.preview_platform}",
        }
        for fmt in WELCOME_CARD_FORMATS
    ]

    owned_count = sum(1 for r in format_rows if r["state"] == "owned")
    total_count = len(format_rows)

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


# ── Challenge Card collection + format shop ───────────────────────────────────

_CHALLENGE_CARD_FORMATS_DICT = [
    {"id": "challenge_post_16_9",  "label": "Post (16:9)",  "dims": "1280×720",  "platform": "Facebook / Instagram"},
    {"id": "challenge_story_9_16", "label": "Story (9:16)", "dims": "1080×1920", "platform": "TikTok / Instagram Story"},
]
# Backward-compat alias used by tests/unit/api/web_routes/test_vt_social_cards.py
_CHALLENGE_CARD_FORMATS = _CHALLENGE_CARD_FORMATS_DICT

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
                for fmt in _CHALLENGE_CARD_FORMATS_DICT
            }
            export_urls = (
                {
                    fmt["id"]: f"/challenges/{ch.id}/card/export?phase={phase}&platform={fmt['id']}"
                    for fmt in _CHALLENGE_CARD_FORMATS_DICT
                }
                if not is_locked else {}
            )
            cards.append({
                "phase":        phase,
                "label":        _PHASE_LABELS.get(phase, phase.replace("_", " ").title()),
                "is_locked":    is_locked,
                "preview_urls": preview_urls,
                "export_urls":  export_urls,
                "formats":      _CHALLENGE_CARD_FORMATS_DICT,
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
    """Challenge Card Collection — format shop header + phase-based card manager."""
    draft  = CardDraftService.get_or_create_singleton(db, user.id, "challenge_card")
    themes = get_all_themes(db=db)
    credits = user.credit_balance

    # Format header section — ownership state per CC format
    def _cc_fmt_state(fmt) -> str:
        if is_design_accessible(db, user.id, "challenge_card", fmt.design_id):
            return "owned"
        return "purchasable" if credits >= fmt.credit_cost else "locked"

    cc_format_rows = [
        {
            "design_id":   fmt.design_id,
            "label":       fmt.label,
            "style_tag":   fmt.style_tag,
            "dims":        fmt.dims,
            "credit_cost": fmt.credit_cost,
            "state":       _cc_fmt_state(fmt),
        }
        for fmt in CHALLENGE_CARD_FORMATS
    ]
    cc_owned_count = sum(1 for r in cc_format_rows if r["state"] == "owned")
    cc_total       = len(cc_format_rows)

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
            "formats":          _CHALLENGE_CARD_FORMATS_DICT,
            "cc_format_rows":   cc_format_rows,
            "cc_owned_count":   cc_owned_count,
            "cc_total":         cc_total,
            "challenge_rows":   challenge_rows,
            "has_challenges":   bool(challenge_rows),
            "flash_purchased":  request.query_params.get("purchased"),
            "flash_error":      request.query_params.get("error"),
        },
    )
