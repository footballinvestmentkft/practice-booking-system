"""My Cards hub — cross-card-type navigation hub and detail redirects."""
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User
from ...models.virtual_training import VirtualTrainingAttempt
from ...models.vt_challenge import ChallengeStatus, VirtualTrainingChallenge
from ...services.card_draft_service import CardDraftService
from ...services.card_system import card_registry
from ...services.card_theme_service import get_all_themes
from .vt_challenges import (
    CHALLENGE_CARD_PLATFORMS,
    get_locked_challenge_card_phases,
    get_unlocked_challenge_card_phases,
    _display_name,
)

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
