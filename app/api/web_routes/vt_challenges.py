"""
VT Challenge web routes — async + live friend-vs-friend challenge lifecycle.

Routes:
  GET  /challenges                    → challenge inbox (received/sent/active/completed)
  GET  /challenges/send               → send form (friends + compatible games)
  POST /challenges/send               → create challenge
  POST /challenges/{id}/accept        → accept incoming challenge (challenged only)
  POST /challenges/{id}/decline       → decline incoming challenge (challenged only)
  POST /challenges/{id}/cancel        → cancel pending/accepted challenge (challenger only)
  GET  /challenges/{id}/lobby         → live lobby page (both players)
  GET  /challenges/{id}/lobby-state   → JSON polling endpoint (2 s poll)
  POST /challenges/{id}/ready         → mark self as ready in lobby

Guards (send):
  - self-challenge blocked
  - target must be active user
  - must be friends (is_friends)
  - game must exist + in CHALLENGE_COMPATIBLE_GAMES
  - no duplicate active challenge between the pair on the same game
  - TT: difficulty_level must be valid; expert gated by expert_unlocked
  - live mode: completion_window_seconds ignored

Notifications:
  All notification links point to /challenges (or /challenges/{id}/lobby for live).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_optional, get_current_user_web
from ...models.friendship import Friendship, FriendshipStatus, is_friends
from ...models.license import UserLicense
from ...models.notification import NotificationType
from ...models.user_mood_photos import MoodPhotoStatus, UserMoodPhoto
from ...models.user import User
from ...models.virtual_training import VirtualTrainingAttempt, VirtualTrainingGame
from ...models.vt_challenge import (
    CHALLENGE_COMPATIBLE_GAMES,
    DEFAULT_COMPLETION_WINDOW,
    LOBBY_TIMEOUT_SECONDS,
    MAX_ACTIVE_PER_CATEGORY,
    ChallengeStatus,
    VirtualTrainingChallenge,
    count_active_challenges_in_category,
    get_active_challenge,
    make_completion_deadline,
    make_expires_at,
    validate_completion_window,
)
from ...core.redis_pubsub import publish_challenge_event
from ...services import card_export_service as _export_svc
from ...utils.football_positions import position_short
from ...skills_config import ALL_SKILLS, SKILL_CATEGORIES
from ...services.mood_photo_service import get_mood_photos_for_user as _mood_photos_for_user
from .card_editor import _MOOD_SLOT_META as _SEND_MOOD_SLOT_META
from ...services import notification_service
from ...services.challenge_completion_service import sweep_accepted_deadlines
from ...services.live_lobby_service import (
    apply_lobby_timeout_if_expired,
    apply_post_start_timeout_if_expired,
    get_lobby_state,
    set_ready,
    sweep_live_challenges,
)
from ...services.challenge_snapshot_service import (
    generate_snapshot,
    validate_challenge_mode,
)
from ...services.virtual_training_service import VirtualTrainingService
from .helpers import require_student_onboarding
from .student_features import _spec_ctx

router    = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# CC-DESIGN-1: skill_key → category display name (name_en from SKILL_CATEGORIES)
_SKILL_CATEGORY_LABEL: dict[str, str] = {
    skill["key"]: cat["name_en"]
    for cat in SKILL_CATEGORIES
    for skill in cat["skills"]
}

# CC-DESIGN-1 Phase-A: phase + outcome → preferred and alternative mood photo slot.
# Only the 6 existing slots are used; no DB migration required.
# is_winner=True  → player won / not forfeiter  → celebration/happy preferred
# is_winner=False → player lost / forfeited      → sad preferred
# is_winner=None  → pre-game or no winner context → phase-default
_PHASE_MOOD_MAP: dict[tuple[str, bool | None], tuple[str | None, str | None]] = {
    # Pre-game phases — Phase-B: focused_ready / confident where available
    ("challenge_sent",         None):  ("mood_focused_ready",      "mood_angry_competitive"),
    # challenge_received: surprise is authentic first reaction; focused as fallback
    ("challenge_received",     None):  ("mood_surprised_shocked",  "mood_focused_ready"),
    ("challenge_accepted",     None):  ("mood_confident",          "mood_happy_smile"),
    ("waiting_for_opponent",   None):  ("mood_focused_ready",      "mood_angry_competitive"),
    ("live_lobby_ready",       None):  ("mood_focused_ready",      "mood_angry_competitive"),
    ("live_in_progress",       None):  ("mood_focused_ready",      "mood_angry_competitive"),
    # Result phases — winner/loser aware (Phase-A logic unchanged)
    ("completed_score_win",    True):  ("mood_celebration",        "mood_happy_smile"),
    ("completed_score_win",    False): ("mood_sad_disappointed",   "mood_intro_neutral"),
    ("completed_draw",         None):  ("mood_surprised_shocked",  "mood_intro_neutral"),
    ("completed_forfeit_win",  True):  ("mood_celebration",        "mood_happy_smile"),
    ("completed_forfeit_win",  False): ("mood_sad_disappointed",   "mood_intro_neutral"),
    ("completed_forfeit_loss", True):  ("mood_celebration",        "mood_happy_smile"),
    ("completed_forfeit_loss", False): ("mood_sad_disappointed",   "mood_intro_neutral"),
    ("no_contest",             None):  ("mood_intro_neutral",      None),
    # Phase-B: proud preferred for skill progress
    ("skill_delta_result",     None):  ("mood_proud",              "mood_happy_smile"),
    # Terminal rejection phases
    ("challenge_cancelled",    None):  ("mood_intro_neutral",      None),
    ("challenge_declined",     None):  ("mood_sad_disappointed",   "mood_intro_neutral"),
}

_MAX_MSG         = 500
_COMPLETED_LIMIT = 20
_VALID_DIFFICULTIES = {"easy", "medium", "hard", "expert"}


def _trim_message(raw: str | None) -> str | None:
    if not raw:
        return None
    trimmed = raw.strip()[:_MAX_MSG]
    return trimmed or None



# ── Helpers for inbox ──────────────────────────────────────────────────────────

def _build_inbox_row(
    ch: VirtualTrainingChallenge,
    user_id: int,
    attempts_map: dict[int, "VirtualTrainingAttempt"],
    users_map: dict[int, User],
    games_map: dict[int, VirtualTrainingGame],
) -> dict[str, Any]:
    is_challenger = ch.challenger_id == user_id
    opponent_id   = ch.challenged_id if is_challenger else ch.challenger_id
    opponent      = users_map.get(opponent_id)
    game          = games_map.get(ch.game_id)

    my_attempt_id  = ch.challenger_attempt_id if is_challenger else ch.challenged_attempt_id
    opp_attempt_id = ch.challenged_attempt_id if is_challenger else ch.challenger_attempt_id

    my_attempt  = attempts_map.get(my_attempt_id)  if my_attempt_id  else None
    opp_attempt = attempts_map.get(opp_attempt_id) if opp_attempt_id else None

    game_code = game.code if game else ""
    diff      = ch.difficulty_level or "easy"

    if game_code == "memory_sequence":
        play_url = f"/virtual-training/memory-sequence?challenge_id={ch.id}"
    elif game_code == "target_tracking":
        play_url = f"/virtual-training/target-tracking?challenge_id={ch.id}&difficulty={diff}"
    else:
        play_url = "/virtual-training"

    # outcome
    outcome: str
    if ch.status == ChallengeStatus.COMPLETED:
        if ch.is_draw:
            outcome = "draw"
        elif ch.forfeit_user_id is not None:
            outcome = "forfeit_win" if ch.winner_id == user_id else "forfeit_loss"
        elif ch.winner_id == user_id:
            outcome = "won"
        else:
            outcome = "lost"
    elif ch.status == ChallengeStatus.ACCEPTED:
        if my_attempt_id is None:
            outcome = "play_now"
        elif opp_attempt_id is None:
            outcome = "waiting_for_opponent"
        else:
            outcome = "accepted"   # both submitted, awaiting completion write
    elif ch.status == ChallengeStatus.LIVE_LOBBY:
        outcome = "live_lobby"
    elif ch.status == ChallengeStatus.LIVE_IN_PROGRESS:
        if my_attempt_id is None:
            outcome = "live_play_now"
        else:
            outcome = "live_waiting_for_opponent"
    elif ch.status == ChallengeStatus.PENDING:
        outcome = "received" if not is_challenger else "sent"
    else:
        outcome = ch.status.value

    return {
        "id":                    ch.id,
        "status":                ch.status.value,
        "challenge_mode":        ch.challenge_mode,
        "challenge_category":    "virtual",
        "is_challenger":         is_challenger,
        "opponent_name":         (opponent.nickname or opponent.email) if opponent else "Unknown",
        "game_name":             game.name if game else "—",
        "game_code":             game_code,
        "difficulty_level":      ch.difficulty_level,
        "message":               ch.message,
        "expires_at":            ch.expires_at,
        "created_at":            ch.created_at,
        "completed_at":          ch.completed_at,
        "outcome":               outcome,
        "play_url":              play_url,
        "my_score":              my_attempt.score_normalized  if my_attempt  else None,
        "opp_score":             opp_attempt.score_normalized if opp_attempt else None,
        "winner_id":             ch.winner_id,
        "is_draw":               ch.is_draw,
        "completion_deadline":   ch.completion_deadline,
        "forfeit_user_id":       ch.forfeit_user_id,
        "forfeit_reason":        ch.forfeit_reason,
        "lobby_expires_at":      ch.lobby_expires_at,
        "live_start_at":         ch.live_start_at,
    }


# ── GET /challenges ────────────────────────────────────────────────────────────

@router.get("/challenges", response_class=HTMLResponse)
async def challenge_inbox(
    request: Request,
    db: Session  = Depends(get_db),
    user: User   = Depends(get_current_user_web),
):
    guard = require_student_onboarding(user)
    if guard:
        return guard

    all_challenges = (
        db.query(VirtualTrainingChallenge)
        .filter(
            or_(
                VirtualTrainingChallenge.challenger_id == user.id,
                VirtualTrainingChallenge.challenged_id == user.id,
            )
        )
        .order_by(VirtualTrainingChallenge.created_at.desc())
        .all()
    )

    _ACTIVE_STATUSES = {
        ChallengeStatus.PENDING,
        ChallengeStatus.ACCEPTED,
        ChallengeStatus.LIVE_LOBBY,
        ChallengeStatus.LIVE_IN_PROGRESS,
    }

    # Separate active vs completed/terminal
    active_chs   = [c for c in all_challenges if c.status in _ACTIVE_STATUSES]
    terminal_chs = [c for c in all_challenges if c.status not in _ACTIVE_STATUSES]

    # Lazy sweeps — deadlines (async) + lobby/post-start timeouts (live)
    swept = sweep_accepted_deadlines(db, active_chs) + sweep_live_challenges(db, active_chs)
    if swept:
        db.commit()
        # Re-partition after sweep
        active_chs   = [c for c in active_chs   if c.status in _ACTIVE_STATUSES]
        terminal_chs = [c for c in all_challenges if c.status not in _ACTIVE_STATUSES]

    terminal_chs  = terminal_chs[:_COMPLETED_LIMIT]

    shown = active_chs + terminal_chs

    # Batch-load opponents, games, attempts
    user_ids = set()
    game_ids = set()
    attempt_ids = set()
    for c in shown:
        user_ids.add(c.challenger_id)
        user_ids.add(c.challenged_id)
        if c.winner_id:
            user_ids.add(c.winner_id)
        game_ids.add(c.game_id)
        if c.challenger_attempt_id:
            attempt_ids.add(c.challenger_attempt_id)
        if c.challenged_attempt_id:
            attempt_ids.add(c.challenged_attempt_id)

    users_map: dict[int, User] = {}
    if user_ids:
        for u in db.query(User).filter(User.id.in_(user_ids)).all():
            users_map[u.id] = u

    games_map: dict[int, VirtualTrainingGame] = {}
    if game_ids:
        for g in db.query(VirtualTrainingGame).filter(VirtualTrainingGame.id.in_(game_ids)).all():
            games_map[g.id] = g

    attempts_map: dict[int, VirtualTrainingAttempt] = {}
    if attempt_ids:
        for a in db.query(VirtualTrainingAttempt).filter(
            VirtualTrainingAttempt.id.in_(attempt_ids)
        ).all():
            attempts_map[a.id] = a

    active_rows   = [_build_inbox_row(c, user.id, attempts_map, users_map, games_map)
                     for c in active_chs]
    terminal_rows = [_build_inbox_row(c, user.id, attempts_map, users_map, games_map)
                     for c in terminal_chs]

    return templates.TemplateResponse("vt_challenges.html", {
        "request":       request,
        "user":          user,
        **_spec_ctx(user, db),
        "active_rows":   active_rows,
        "terminal_rows": terminal_rows,
        "success":       request.query_params.get("success"),
        "error":         request.query_params.get("error"),
    })


# ── GET /challenges/send ───────────────────────────────────────────────────────

def _accepted_friends(db: Session, user_id: int) -> list[User]:
    rows = (
        db.query(Friendship)
        .filter(
            Friendship.status == FriendshipStatus.ACCEPTED,
            (Friendship.requester_id == user_id) | (Friendship.addressee_id == user_id),
        )
        .all()
    )
    friends = []
    for row in rows:
        other_id = row.addressee_id if row.requester_id == user_id else row.requester_id
        u = db.query(User).filter(User.id == other_id).first()
        if u:
            friends.append(u)
    return friends


@router.get("/challenges/send", response_class=HTMLResponse)
async def challenge_send_form(
    request: Request,
    friend_id: int  | None = None,
    game_code:  str | None = None,
    db: Session  = Depends(get_db),
    user: User   = Depends(get_current_user_web),
):
    guard = require_student_onboarding(user)
    if guard:
        return guard

    friends_rows = _accepted_friends(db, user.id)
    compatible_games = (
        db.query(VirtualTrainingGame)
        .filter(
            VirtualTrainingGame.code.in_(CHALLENGE_COMPATIBLE_GAMES),
            VirtualTrainingGame.is_active.is_(True),
        )
        .all()
    )

    # expert_unlocked — check for TT game if present
    expert_unlocked = False
    tt_game = next((g for g in compatible_games if g.code == "target_tracking"), None)
    if tt_game:
        expert_unlocked = VirtualTrainingService.is_expert_unlocked(db, user.id, tt_game.id)

    # Preselect friend/game from query params
    preselected_friend_id = friend_id
    preselected_game_code = game_code

    # CC-DESIGN-1: pass mood photos so the send form can show a card photo selector
    mood_photos = _mood_photos_for_user(user.id, db)

    return templates.TemplateResponse("vt_challenge_send.html", {
        "request":               request,
        "user":                  user,
        **_spec_ctx(user, db),
        "friends_rows":          friends_rows,
        "compatible_games":      compatible_games,
        "expert_unlocked":       expert_unlocked,
        "preselected_friend_id": preselected_friend_id,
        "preselected_game_code": preselected_game_code,
        "error":                 request.query_params.get("error"),
        "max_per_cat":           MAX_ACTIVE_PER_CATEGORY,
        "mood_photos":           mood_photos,
        "mood_slot_meta":        _SEND_MOOD_SLOT_META,
    })


# ── POST /challenges/send ──────────────────────────────────────────────────────

@router.post("/challenges/send")
async def send_challenge(
    challenged_user_id:        int        = Form(...),
    game_id:                   int        = Form(...),
    message:                   str | None = Form(default=None),
    difficulty_level:          str | None = Form(default=None),
    challenge_mode:            str | None = Form(default=None),
    completion_window_seconds: int | None = Form(default=None),
    challenge_category:        str | None = Form(default=None),
    card_photo_url:            str | None = Form(default=None),
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    # Category guard — only Virtual is active; On-site/Hybrid are Coming Soon
    # isinstance guard: when called directly in tests, challenge_category may be
    # the Form FieldInfo object rather than None (FastAPI only resolves it at
    # request time). Treat any non-str value as "use default".
    resolved_category = "virtual"
    if isinstance(challenge_category, str) and challenge_category:
        resolved_category = challenge_category.strip().lower()
    if resolved_category != "virtual":
        return RedirectResponse(
            url="/challenges/send?error=category_not_available", status_code=303
        )

    # Self-challenge guard
    if challenged_user_id == user.id:
        return RedirectResponse(url="/challenges/send?error=self_challenge", status_code=303)

    # Target must be active
    target = db.query(User).filter(
        User.id == challenged_user_id, User.is_active.is_(True)
    ).first()
    if not target:
        return RedirectResponse(url="/challenges/send?error=user_not_found", status_code=303)

    # Friendship guard
    if not is_friends(db, user.id, challenged_user_id):
        return RedirectResponse(url="/challenges/send?error=not_friends", status_code=303)

    # Game existence guard
    game = db.query(VirtualTrainingGame).filter(
        VirtualTrainingGame.id == game_id
    ).first()
    if not game:
        return RedirectResponse(url="/challenges/send?error=game_not_found", status_code=303)

    # Compatible game guard
    if game.code not in CHALLENGE_COMPATIBLE_GAMES:
        return RedirectResponse(url="/challenges/send?error=game_not_compatible", status_code=303)

    # Category-level active challenge limit (bidirectional, per game_type)
    active_in_cat = count_active_challenges_in_category(
        db, user.id, challenged_user_id, game.game_type
    )
    if active_in_cat >= MAX_ACTIVE_PER_CATEGORY:
        return RedirectResponse(
            url="/challenges/send?error=challenge_limit_reached", status_code=303
        )

    # Difficulty validation (TT only)
    resolved_difficulty: str | None = None
    if game.code == "target_tracking":
        lvl = (difficulty_level or "easy").strip().lower()
        if lvl not in _VALID_DIFFICULTIES:
            return RedirectResponse(url="/challenges/send?error=invalid_difficulty", status_code=303)
        if lvl == "expert":
            if not VirtualTrainingService.is_expert_unlocked(db, user.id, game.id):
                return RedirectResponse(url="/challenges/send?error=expert_locked", status_code=303)
        resolved_difficulty = lvl

    # Challenge mode validation — only async/live accepted; default async
    # isinstance guard: when called directly in tests, challenge_mode may be
    # the Form FieldInfo object rather than None (FastAPI only resolves it at
    # request time). Treat any non-str value as "use default".
    resolved_mode = "async"
    if isinstance(challenge_mode, str) and challenge_mode:
        try:
            resolved_mode = validate_challenge_mode(challenge_mode.strip().lower())
        except ValueError:
            return RedirectResponse(
                url="/challenges/send?error=invalid_challenge_mode", status_code=303
            )

    # Completion window validation — default 86400 (24h); completion_deadline set on accept
    resolved_window = DEFAULT_COMPLETION_WINDOW
    if isinstance(completion_window_seconds, int):
        try:
            resolved_window = validate_completion_window(completion_window_seconds)
        except ValueError:
            return RedirectResponse(
                url="/challenges/send?error=invalid_completion_window", status_code=303
            )

    # Snapshot generation — must succeed or challenge is NOT created
    try:
        snapshot = generate_snapshot(
            game_code        = game.code,
            game_config      = game.config or {},
            difficulty_level = resolved_difficulty,
        )
    except (ValueError, KeyError, TypeError) as exc:
        return RedirectResponse(
            url=f"/challenges/send?error=snapshot_generation_failed", status_code=303
        )

    now = datetime.now(timezone.utc)
    # CC-DESIGN-1 PHOTO SNAPSHOT: freeze the challenger's card photo at send time.
    # Priority: explicit form selection (card_photo_url) > auto-snapshot (neutral mood).
    # Ownership guard: card_photo_url must belong to this user's own mood photos.
    # isinstance guard: Form FieldInfo when called directly in tests
    resolved_photo: str | None = card_photo_url if isinstance(card_photo_url, str) and card_photo_url else None
    challenger_snapshot: str | None
    if resolved_photo:
        owns = db.query(UserMoodPhoto).filter(
            UserMoodPhoto.user_id == user.id,
            (UserMoodPhoto.processed_png_url == resolved_photo) |
            (UserMoodPhoto.original_url == resolved_photo),
        ).first()
        challenger_snapshot = resolved_photo if owns else None
    else:
        # No explicit selection → NULL snapshot so render-time phase/outcome
        # mood lookup (_get_participant_photo_for_phase) runs on every card view.
        # This allows challenge_sent → focused, completed_score_win → celebration/sad, etc.
        challenger_snapshot = None
    challenge = VirtualTrainingChallenge(
        challenger_id              = user.id,
        challenged_id              = challenged_user_id,
        game_id                    = game_id,
        status                     = ChallengeStatus.PENDING,
        message                    = _trim_message(message),
        difficulty_level           = resolved_difficulty,
        challenge_mode             = resolved_mode,
        challenge_config_snapshot  = snapshot,
        completion_window_seconds  = resolved_window,
        expires_at                 = make_expires_at(now),
        created_at                 = now,
        challenger_card_photo_url  = challenger_snapshot,
    )
    db.add(challenge)
    db.flush()

    notification_service.create_notification(
        db=db,
        user_id=challenged_user_id,
        title="Challenge Received",
        message=f"{user.nickname or user.email} challenged you to a VT game.",
        notification_type=NotificationType.VT_CHALLENGE_RECEIVED,
        link="/challenges",
    )

    db.commit()
    publish_challenge_event(
        [user.id, challenged_user_id],
        "challenge_sent",
        {"challenge_id": challenge.id, "challenger_id": user.id, "challenged_id": challenged_user_id},
    )
    return RedirectResponse(url="/challenges?success=challenge_sent", status_code=303)


# ── POST /challenges/{id}/accept ──────────────────────────────────────────────

@router.post("/challenges/{challenge_id}/accept")
async def accept_challenge(
    challenge_id: int,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    challenge = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()

    if not challenge or challenge.challenged_id != user.id:
        return RedirectResponse(url="/challenges?error=not_found", status_code=303)

    if challenge.status != ChallengeStatus.PENDING:
        return RedirectResponse(url="/challenges?error=not_pending", status_code=303)

    now = datetime.now(timezone.utc)
    if challenge.expires_at <= now:
        challenge.status     = ChallengeStatus.EXPIRED
        challenge.updated_at = now
        db.commit()
        return RedirectResponse(url="/challenges?error=challenge_expired", status_code=303)

    challenge.accepted_at = now
    challenge.updated_at  = now

    if challenge.challenge_mode == "live":
        challenge.status           = ChallengeStatus.LIVE_LOBBY
        challenge.lobby_expires_at = now + timedelta(seconds=LOBBY_TIMEOUT_SECONDS)
        notification_service.create_notification(
            db=db,
            user_id=challenge.challenger_id,
            title="Challenge Accepted — Live Lobby",
            message=f"{user.nickname or user.email} accepted your live challenge. Head to the lobby!",
            notification_type=NotificationType.VT_CHALLENGE_LIVE_LOBBY,
            link=f"/challenges/{challenge_id}/lobby",
        )
        db.commit()
        publish_challenge_event(
            [challenge.challenger_id, challenge.challenged_id],
            "challenge_accepted",
            {"challenge_id": challenge_id, "mode": "live"},
        )
        return RedirectResponse(url=f"/challenges/{challenge_id}/lobby", status_code=303)
    else:
        challenge.status = ChallengeStatus.ACCEPTED
        if challenge.completion_window_seconds is not None:
            challenge.completion_deadline = make_completion_deadline(
                now, challenge.completion_window_seconds
            )
        notification_service.create_notification(
            db=db,
            user_id=challenge.challenger_id,
            title="Challenge Accepted",
            message=f"{user.nickname or user.email} accepted your VT challenge.",
            notification_type=NotificationType.VT_CHALLENGE_ACCEPTED,
            link="/challenges",
        )
        db.commit()
        publish_challenge_event(
            [challenge.challenger_id, challenge.challenged_id],
            "challenge_accepted",
            {"challenge_id": challenge_id, "mode": "async"},
        )
        return RedirectResponse(url="/challenges?success=challenge_accepted", status_code=303)


# ── Outcome reason helper (view-layer only, no DB column) ─────────────────────

def _compute_outcome_reason(ch: VirtualTrainingChallenge) -> str:
    """Derive a human-readable outcome category from existing DB fields.

    Returns one of:
      score_win | draw |
      forfeit_post_start_timeout | forfeit_deadline | forfeit_no_show | forfeit |
      no_contest | waiting_for_acceptance | waiting_for_opponent | in_lobby |
      expired | declined | cancelled

    No DB migration required — fully derived from status + winner_id +
    forfeit_user_id + forfeit_reason.
    """
    s = ch.status

    if s == ChallengeStatus.PENDING:
        return "waiting_for_acceptance"
    if s == ChallengeStatus.LIVE_LOBBY:
        return "in_lobby"
    if s in (ChallengeStatus.ACCEPTED, ChallengeStatus.LIVE_IN_PROGRESS):
        return "waiting_for_opponent"
    if s == ChallengeStatus.DECLINED:
        return "declined"
    if s == ChallengeStatus.CANCELLED:
        return "cancelled"
    if s == ChallengeStatus.EXPIRED:
        return "no_contest" if ch.forfeit_reason == "no_contest" else "expired"

    # COMPLETED
    if ch.forfeit_user_id is not None:
        if ch.winner_id is None:
            return "no_contest"
        _reason_map = {
            "post_start_timeout": "forfeit_post_start_timeout",
            "deadline_expired":   "forfeit_deadline",
            "no_show":            "forfeit_no_show",
        }
        return _reason_map.get(ch.forfeit_reason or "", "forfeit")

    if ch.is_draw:
        return "draw"
    return "score_win"


# ── GET /challenges/results — Challenge result/history list ──────────────────

_RESULT_STATUS_MAP: dict[str, list[ChallengeStatus]] = {
    "completed": [ChallengeStatus.COMPLETED],
    "expired":   [ChallengeStatus.EXPIRED],
    "cancelled": [ChallengeStatus.CANCELLED, ChallengeStatus.DECLINED],
    "all":       [ChallengeStatus.COMPLETED, ChallengeStatus.EXPIRED,
                  ChallengeStatus.CANCELLED, ChallengeStatus.DECLINED],
}
_RESULTS_MAX_SIZE = 50


@router.get("/challenges/results", response_class=HTMLResponse)
async def challenge_results(
    request: Request,
    page:   int = Query(default=0, ge=0),
    size:   int = Query(default=20, ge=1, le=_RESULTS_MAX_SIZE),
    status: str = Query(default="all"),
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Challenge result/history list — terminal challenges only, paginated."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    statuses = _RESULT_STATUS_MAP.get(status, _RESULT_STATUS_MAP["completed"])

    # Fetch one extra to detect has_next without a COUNT query
    raw = (
        db.query(VirtualTrainingChallenge)
        .filter(
            or_(
                VirtualTrainingChallenge.challenger_id == user.id,
                VirtualTrainingChallenge.challenged_id == user.id,
            ),
            VirtualTrainingChallenge.status.in_(statuses),
        )
        .order_by(
            VirtualTrainingChallenge.completed_at.desc().nullslast(),
            VirtualTrainingChallenge.created_at.desc(),
        )
        .offset(page * size)
        .limit(size + 1)
        .all()
    )

    has_next = len(raw) > size
    challenges = raw[:size]

    def _result_row(ch: VirtualTrainingChallenge) -> dict:
        is_challenger = user.id == ch.challenger_id
        opponent = ch.challenged if is_challenger else ch.challenger
        return {
            "id":            ch.id,
            "status":        ch.status.value,
            "opponent_name": _display_name(opponent) if opponent else "Unknown",
            "game_name":     ch.game.name if ch.game else "—",
            "completed_at":  ch.completed_at,
            "created_at":    ch.created_at,
            "card_url":      f"/challenges/{ch.id}/card",
            "detail_url":    f"/challenges/{ch.id}",
        }

    return templates.TemplateResponse(
        "vt_challenge_results.html",
        {
            "request":       request,
            "user":          user,
            **_spec_ctx(user, db),
            "rows":          [_result_row(ch) for ch in challenges],
            "has_next":      has_next,
            "has_prev":      page > 0,
            "page":          page,
            "size":          size,
            "status_filter": status,
        },
    )


# ── GET /challenges/{id}/card — Challenge result card preview + download ──────

@router.get("/challenges/{challenge_id}/card", response_class=HTMLResponse)
async def challenge_result_card(
    challenge_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Challenge result card — style selector, phase selector, preview, download."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    ch = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()

    if ch is None or user.id not in (ch.challenger_id, ch.challenged_id):
        raise HTTPException(status_code=403, detail="Not a participant of this challenge.")

    # Load viewer's attempt for skill_delta phase detection
    is_challenger = user.id == ch.challenger_id
    my_attempt_id = ch.challenger_attempt_id if is_challenger else ch.challenged_attempt_id
    my_attempt = (
        db.query(VirtualTrainingAttempt)
        .filter(VirtualTrainingAttempt.id == my_attempt_id)
        .first()
    ) if my_attempt_id else None

    unlocked_phases = get_unlocked_challenge_card_phases(ch, user.id, my_attempt)
    locked_phases   = get_locked_challenge_card_phases(ch, user.id)

    # Owned formats — single bulk CDO query
    from ...services.card_design_service import (  # noqa: PLC0415
        CHALLENGE_CARD_FORMATS,
        get_owned_design_ids,
    )
    owned_format_ids = set(get_owned_design_ids(db, user.id, "challenge_card"))

    format_rows = [
        {
            "design_id":   fmt.design_id,
            "label":       fmt.label,
            "dims":        fmt.dims,
            "credit_cost": fmt.credit_cost,
            "owned":       fmt.design_id in owned_format_ids,
        }
        for fmt in CHALLENGE_CARD_FORMATS
    ]
    has_any_owned = any(r["owned"] for r in format_rows)

    opponent = ch.challenged if is_challenger else ch.challenger

    return templates.TemplateResponse(
        "vt_challenge_result_card.html",
        {
            "request":         request,
            "user":            user,
            **_spec_ctx(user, db),
            "challenge":       ch,
            "challenge_id":    ch.id,
            "opponent_name":   _display_name(opponent) if opponent else "Unknown",
            "game_name":       ch.game.name if ch.game else "—",
            "unlocked_phases": unlocked_phases,
            "locked_phases":   locked_phases,
            "format_rows":     format_rows,
            "has_any_owned":   has_any_owned,
            "phase_labels":    _PHASE_LABELS,
        },
    )


# ── GET /challenges/{id} — Virtual Challenge detail ───────────────────────────

@router.get("/challenges/{challenge_id}", response_class=HTMLResponse)
async def challenge_detail(
    challenge_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    """Virtual Challenge detail page — participants only."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    ch = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()

    if ch is None:
        return templates.TemplateResponse(
            "vt_challenges.html",
            {"request": request, "user": user, **_spec_ctx(user, db),
             "active_rows": [], "terminal_rows": [],
             "error": "challenge_not_found"},
            status_code=404,
        )

    if user.id not in (ch.challenger_id, ch.challenged_id):
        return templates.TemplateResponse(
            "vt_challenges.html",
            {"request": request, "user": user, **_spec_ctx(user, db),
             "active_rows": [], "terminal_rows": [],
             "error": "not_found"},
            status_code=403,
        )

    # Load attempts if linked
    challenger_attempt = (
        db.query(VirtualTrainingAttempt).filter(
            VirtualTrainingAttempt.id == ch.challenger_attempt_id
        ).first() if ch.challenger_attempt_id else None
    )
    challenged_attempt = (
        db.query(VirtualTrainingAttempt).filter(
            VirtualTrainingAttempt.id == ch.challenged_attempt_id
        ).first() if ch.challenged_attempt_id else None
    )

    # Return the stored skill_deltas directly — additive float values, not recomputed scores.
    # VTSkillScorer.score_all() returns performance scores (0-1) which are semantically wrong
    # for display as "Skill Impact"; the stored deltas (positive/negative) are the correct values.
    def _skill_scores(attempt: VirtualTrainingAttempt | None, _game=None) -> dict[str, float]:
        if attempt is None or not attempt.skill_deltas:
            return {}
        return {k: float(v) for k, v in attempt.skill_deltas.items()}

    game = ch.game  # ORM relationship

    is_forfeit    = ch.forfeit_user_id is not None
    is_no_contest = is_forfeit and ch.winner_id is None
    outcome_reason = _compute_outcome_reason(ch)

    return templates.TemplateResponse(
        "vt_challenge_detail.html",
        {
            "request":                  request,
            "user":                     user,
            **_spec_ctx(user, db),
            "challenge":                ch,
            "challenge_category":       "virtual",
            "game":                     game,
            "challenger":               ch.challenger,
            "challenged":               ch.challenged,
            "winner":                   ch.winner,
            "forfeit_user":             ch.forfeit_user,
            "challenger_attempt":       challenger_attempt,
            "challenged_attempt":       challenged_attempt,
            "challenger_skill_scores":  _skill_scores(challenger_attempt, game),
            "challenged_skill_scores":  _skill_scores(challenged_attempt, game),
            "is_challenger":            user.id == ch.challenger_id,
            "is_forfeit":               is_forfeit,
            "is_no_contest":            is_no_contest,
            "outcome_reason":           outcome_reason,
        },
    )


# ── Challenge Social Card routes ─────────────────────────────────────────────

CHALLENGE_CARD_PLATFORMS = frozenset({"challenge_post_16_9", "challenge_story_9_16"})

VALID_CHALLENGE_CARD_PHASES = frozenset({
    "challenge_sent",
    "challenge_received",
    "challenge_accepted",
    "waiting_for_opponent",
    "live_lobby_ready",
    "live_in_progress",
    "completed_score_win",
    "completed_draw",
    "completed_forfeit_win",
    "completed_forfeit_loss",
    "no_contest",
    "skill_delta_result",
    # Terminal rejection phases (CC-DESIGN-1 extension)
    "challenge_cancelled",  # challenger withdrew
    "challenge_declined",   # challenged refused
})

# Phases that are exportable — all require format ownership (CDO row).
# CC-DESIGN-1 social moment export: challenge_sent / challenge_received are
# the first shareable social moments of a challenge lifecycle and must be
# downloadable. Historical does NOT mean preview-only.
_EXPORTABLE_PHASES = frozenset({
    # Social moment phases (CC-DESIGN-1 addition)
    "challenge_sent",
    "challenge_received",
    # Acceptance moment
    "challenge_accepted",
    # Viewer submitted, waiting for opponent
    "waiting_for_opponent",
    # Terminal rejection phases
    "challenge_cancelled",
    "challenge_declined",
    # Result phases (unchanged)
    "completed_score_win",
    "completed_draw",
    "completed_forfeit_win",
    "completed_forfeit_loss",
    "no_contest",
    "skill_delta_result",
})

_TERMINAL_STATUSES = frozenset({
    ChallengeStatus.COMPLETED, ChallengeStatus.EXPIRED,
    ChallengeStatus.DECLINED,  ChallengeStatus.CANCELLED,
})

# CC-DESIGN-1: central phase → emoji mapping used by both export templates.
# challenge_sent (⚔️) and challenge_received (🛡️) are intentionally different:
#   ⚔️ = challenger/attacker perspective
#   🛡️ = challenged/defender perspective
_PHASE_EMOJI: dict[str, str] = {
    "challenge_sent":         "⚔️",
    "challenge_received":     "🛡️",
    "challenge_accepted":     "✅",
    "challenge_cancelled":    "🚫",
    "challenge_declined":     "👎",
    "waiting_for_opponent":   "⏳",
    "live_lobby_ready":       "⚡",
    "live_in_progress":       "🔥",
    "completed_score_win":    "🏆",
    "completed_draw":         "⚖️",
    "completed_forfeit_win":  "🏆",
    "completed_forfeit_loss": "💔",
    "no_contest":             "🔄",
    "skill_delta_result":     "📈",
}

_PHASE_LABELS = {
    "challenge_sent":         "Challenge Sent",
    "challenge_received":     "You've Been Challenged",
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
    "challenge_cancelled":    "Cancelled",
    "challenge_declined":     "Declined",
}

_PHASE_CTA = {
    "challenge_sent":          "View challenge",
    "challenge_received":      "Accept challenge",
    "challenge_accepted":      "Play now",
    "waiting_for_opponent":    "Waiting…",
    "live_lobby_ready":        "Join lobby",
    "live_in_progress":        "Playing now",
    "completed_score_win":     "Play again",
    "completed_draw":          "Play again",
    "completed_forfeit_win":   "Play again",
    "completed_forfeit_loss":  "Play again",
    "no_contest":              "Challenge again",
    "skill_delta_result":      "View profile",
    "challenge_cancelled":     "Challenge again",
    "challenge_declined":      "Challenge again",
}


def _display_name(user: Any) -> str:
    return user.nickname if (user and user.nickname) else (user.email if user else "Unknown")


def _resolve_challenge_render_token(token: str, challenge_id: int, db: Session) -> "User | None":
    """Validate a vt_card_render JWT and return the corresponding User, or None.

    Rejects tokens with wrong purpose, mismatched challenge_id, expired tokens,
    and invalid signatures.
    """
    try:
        from jose import JWTError, jwt as _jwt
        from ...config import settings
        payload = _jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("purpose") != "vt_card_render":
            return None
        if int(payload.get("cid") or -1) != challenge_id:
            return None
        user_id = int(payload.get("sub") or 0)
        if not user_id:
            return None
        return db.query(User).filter(User.id == user_id, User.is_active == True).first()
    except Exception:
        return None


def get_unlocked_challenge_card_phases(
    ch: VirtualTrainingChallenge,
    viewer_id: int,
    my_attempt: Any = None,
) -> list[str]:
    """Return phases that are available and exportable for this viewer."""
    s = ch.status
    is_challenger = viewer_id == ch.challenger_id
    has_my_attempt = (
        (ch.challenger_attempt_id if is_challenger else ch.challenged_attempt_id) is not None
    )

    phases: list[str] = []

    if s == ChallengeStatus.PENDING:
        phases.append("challenge_sent" if is_challenger else "challenge_received")

    elif s in (ChallengeStatus.ACCEPTED, ChallengeStatus.LIVE_IN_PROGRESS):
        phases.append("challenge_accepted")
        if has_my_attempt:
            phases.append("waiting_for_opponent")

    elif s == ChallengeStatus.LIVE_LOBBY:
        phases.append("live_lobby_ready")

    elif s == ChallengeStatus.COMPLETED:
        outcome = _compute_outcome_reason(ch)
        if outcome == "score_win":
            phases.append("completed_score_win")
        elif outcome == "draw":
            phases.append("completed_draw")
        elif outcome in ("forfeit_post_start_timeout", "forfeit_deadline",
                         "forfeit_no_show", "forfeit"):
            phases.append(
                "completed_forfeit_win" if ch.winner_id == viewer_id
                else "completed_forfeit_loss"
            )
        elif outcome == "no_contest":
            phases.append("no_contest")
        if my_attempt is not None and my_attempt.skill_deltas:
            phases.append("skill_delta_result")

    elif s == ChallengeStatus.EXPIRED:
        outcome = _compute_outcome_reason(ch)
        if outcome == "no_contest":
            phases.append("no_contest")

    elif s == ChallengeStatus.CANCELLED:
        phases.append("challenge_cancelled")

    elif s == ChallengeStatus.DECLINED:
        phases.append("challenge_declined")

    return phases


def get_locked_challenge_card_phases(
    ch: VirtualTrainingChallenge,
    viewer_id: int,
) -> list[str]:
    """Return historical phases that are shown as locked (preview only, no export)."""
    s = ch.status
    is_challenger = viewer_id == ch.challenger_id
    initial = "challenge_sent" if is_challenger else "challenge_received"

    locked: list[str] = []

    # For non-pending challenges, the initial send/receive phase is historical.
    # CANCELLED and DECLINED: the challenge_sent/received event DID happen (the
    # challenge reached PENDING before being cancelled or declined), so the initial
    # phase must appear as a historical locked chip alongside the terminal phase.
    # EXPIRED: excluded here — handled via _CC_STATUSES_WITH_IMPLICIT_INITIAL in the
    # Card Studio layer (tech debt: should be unified in a future pass).
    if s not in (ChallengeStatus.PENDING, ChallengeStatus.EXPIRED):
        locked.append(initial)

    # For completed challenges, the accepted phase is also historical
    if s == ChallengeStatus.COMPLETED:
        locked.append("challenge_accepted")
        # waiting_for_opponent is historical if the viewer had submitted an attempt
        # before the other side did — check directly on the FK so this works without
        # loading the attempt object (used by both challenge_card and preview routes).
        has_my_attempt = (
            ch.challenger_attempt_id if is_challenger else ch.challenged_attempt_id
        ) is not None
        if has_my_attempt:
            locked.append("waiting_for_opponent")

    return locked


def validate_challenge_card_phase(
    ch: VirtualTrainingChallenge,
    viewer_id: int,
    phase: str,
    for_export: bool,
    my_attempt: Any = None,
) -> None:
    """Raise HTTPException if the phase is invalid, not applicable, or locked for export."""
    if phase not in VALID_CHALLENGE_CARD_PHASES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid phase: {phase!r}. Valid: {sorted(VALID_CHALLENGE_CARD_PHASES)}",
        )
    unlocked = get_unlocked_challenge_card_phases(ch, viewer_id, my_attempt)
    locked   = get_locked_challenge_card_phases(ch, viewer_id)
    all_relevant = set(unlocked) | set(locked)

    if phase not in all_relevant:
        raise HTTPException(
            status_code=403,
            detail=f"Phase {phase!r} is not applicable to this challenge.",
        )
    # CC-DESIGN-1: export allowed if phase is in _EXPORTABLE_PHASES.
    # Social moment phases (challenge_sent/received) are in locked (historical)
    # but must be exportable — use _EXPORTABLE_PHASES as the authoritative list.
    if for_export and phase not in _EXPORTABLE_PHASES:
        raise HTTPException(
            status_code=403,
            detail=f"Phase {phase!r} is locked — export not available.",
        )


def _get_participant_photo(db: Session, user_id: int) -> str | None:
    """Return the neutral mood default photo for a challenge card participant.

    CC-DESIGN-1 NEUTRAL fallback priority (mood_intro_neutral required):
    1. mood_intro_neutral.processed_png_url (bg-removed transparent PNG, if ready)
    2. mood_intro_neutral.original_url
    3. player_card_photo_url
    4. wc_photo_url
    5. None (template renders initials)

    Only mood_intro_neutral is used — never happy/angry/celebration/sad.
    This ensures consistent, non-random default photo selection.
    """
    neutral = db.query(UserMoodPhoto).filter_by(
        user_id=user_id, slot="mood_intro_neutral"
    ).first()
    if neutral is not None:
        if neutral.processed_png_url:
            return neutral.processed_png_url
        if neutral.original_url:
            return neutral.original_url

    lic = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()
    if lic is None:
        return None
    return lic.player_card_photo_url or lic.wc_photo_url or None


def _winner_ctx(ch: Any, user_id: int) -> bool | None:
    """Return winner context for a participant in a challenge.

    True  → this user won  (or is the non-forfeiter in forfeit)
    False → this user lost (or is the forfeiter)
    None  → no winner set (draw, no_contest, or pre-game phase)
    """
    if ch.winner_id is None:
        return None
    return ch.winner_id == user_id


def _get_participant_photo_for_phase(
    db: Session,
    user_id: int,
    phase: str,
    is_winner: bool | None,
) -> str | None:
    """Phase- and outcome-aware mood photo selection (Phase-A, 6 existing slots only).

    Lookup order per player:
      1. preferred mood slot for (phase, is_winner) from _PHASE_MOOD_MAP
      2. alternative mood slot
      3. mood_intro_neutral (final mood fallback)
      4. UserLicense.player_card_photo_url
      5. UserLicense.wc_photo_url
      6. None (template renders initials)

    Called only when the per-challenge frozen snapshot is NULL.
    Never performs DB writes.
    """
    preferred, alt = _PHASE_MOOD_MAP.get(
        (phase, is_winner),
        _PHASE_MOOD_MAP.get((phase, None), (None, "mood_intro_neutral")),
    )
    seen: set[str] = set()
    for slot in filter(None, [preferred, alt, "mood_intro_neutral"]):
        if slot in seen:
            continue
        seen.add(slot)
        photo = db.query(UserMoodPhoto).filter_by(user_id=user_id, slot=slot).first()
        if photo:
            return photo.processed_png_url or photo.original_url

    lic = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()
    return (lic.player_card_photo_url or lic.wc_photo_url) if lic else None


def _get_participant_stats(db: Session, user_id: int) -> dict:
    """Return overall skill + primary/secondary position for a challenge card participant.

    overall:       float | None  — average of football_skills current_level values (1dp)
    primary_pos:   str   | None  — position_short of motivation_scores["positions"][0]
    secondary_pos: str   | None  — position_short of motivation_scores["positions"][1]
    Returns all-None dict when no LFA_FOOTBALL_PLAYER license exists.
    """
    lic = db.query(UserLicense).filter(
        UserLicense.user_id == user_id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()
    if lic is None:
        return {"overall": None, "primary_pos": None, "secondary_pos": None}

    football_skills = lic.football_skills or {}
    levels = [
        v["current_level"]
        for v in football_skills.values()
        if isinstance(v, dict) and v.get("current_level") is not None
    ]
    overall = round(sum(levels) / len(levels), 1) if levels else None
    skill_levels = {
        k: float(v["current_level"])
        for k, v in football_skills.items()
        if isinstance(v, dict) and v.get("current_level") is not None
    }

    positions = (lic.motivation_scores or {}).get("positions", [])
    return {
        "overall":       overall,
        "primary_pos":   position_short(positions[0]) if positions else None,
        "secondary_pos": position_short(positions[1]) if len(positions) > 1 else None,
        "skill_levels":  skill_levels,
    }


def _build_skill_progress_rows(
    skill_deltas: dict[str, float],
    skill_levels: dict[str, float],
    max_rows: int = 8,
) -> list[dict]:
    """Build Player Card-style skill progress rows for skill_delta_result card.

    Returns rows sorted by abs(delta) desc, capped at max_rows.
    category uses SKILL_CATEGORIES name_en (Outfield/Set Pieces/Mental/Physical).
    fill_pct is None when current_level is unavailable — bar renders as empty.
    """
    rows = []
    for key, delta in skill_deltas.items():
        skill_def = ALL_SKILLS.get(key)
        level = skill_levels.get(key)
        rows.append({
            "key":           key,
            "name":          skill_def["name_en"] if skill_def else key.replace("_", " ").title(),
            "category":      _SKILL_CATEGORY_LABEL.get(key, ""),
            "current_level": level,
            "delta":         delta,
            "fill_pct":      min(round(level), 100) if level is not None else None,
            "is_positive":   delta > 0,
            "is_negative":   delta < 0,
        })
    rows.sort(key=lambda r: abs(r["delta"]), reverse=True)
    return rows[:max_rows]


def _build_result_summary(attempt: Any, game_code: str | None) -> dict:
    """Build game-aware result summary for challenge card rendering (CC-DESIGN-1).

    Returns a dict with primary_value (score_normalized) and up to 2 game-specific
    secondary items.  primary_value is None only when attempt or score_normalized is
    missing — if game_code is None/unknown the primary score is still surfaced.
    """
    primary_value: float | None = None
    if attempt is not None and attempt.score_normalized is not None:
        primary_value = round(float(attempt.score_normalized), 1)

    secondary: list[dict] = []

    if attempt is not None and game_code == "memory_sequence":
        raw = attempt.raw_metrics or {}
        per_round = raw.get("per_round") or []
        completed = [r for r in per_round if r.get("outcome") == "correct"]
        best_seq = max((r.get("sequence_length", 0) for r in completed), default=0)
        if best_seq > 0:
            secondary.append({"label": "Sequence", "value": str(best_seq)})
        stim = attempt.stimuli_count
        corr = attempt.correct_count
        if isinstance(stim, (int, float)) and stim > 0 and isinstance(corr, (int, float)):
            secondary.append({"label": "Accuracy", "value": f"{round(corr / stim * 100)}%"})

    elif attempt is not None and game_code == "target_tracking":
        raw = attempt.raw_metrics or {}
        diff = raw.get("difficulty_level")
        if diff:
            secondary.append({"label": "Difficulty", "value": diff.title()})
        stim = attempt.stimuli_count
        corr = attempt.correct_count
        if isinstance(stim, (int, float)) and stim > 0 and isinstance(corr, (int, float)):
            secondary.append({"label": "Hit Rate", "value": f"{round(corr / stim * 100)}%"})

    return {
        "game_code":       game_code,
        "primary_label":   "Score",
        "primary_value":   primary_value,
        "secondary_items": secondary[:2],
    }


def _build_challenge_card_context(
    ch: VirtualTrainingChallenge,
    viewer: User,
    challenger_attempt: Any,
    challenged_attempt: Any,
    phase: str,
    my_attempt: Any = None,
    challenger_photo_url: str | None = None,
    challenged_photo_url: str | None = None,
    selected_photo_url: str | None = None,
    challenger_stats: dict | None = None,
    challenged_stats: dict | None = None,
) -> dict:
    """Build rendering context for challenge card templates.

    CC-DESIGN-1 additions:
      challenger_photo_url / challenged_photo_url — player photos for VS layouts.
      selected_photo_url — viewer-chosen mood/hero photo (query-param MVP, no DB write).
      challenger_stats / challenged_stats — {overall, primary_pos, secondary_pos} dicts
        from _get_participant_stats(); all values may be None.
    """
    def _skill_scores_map(attempt: Any) -> dict[str, float]:
        if attempt is None or not attempt.skill_deltas:
            return {}
        return {k: float(v) for k, v in attempt.skill_deltas.items()}

    is_challenger = viewer.id == ch.challenger_id
    if my_attempt is None:
        my_attempt  = challenger_attempt if is_challenger else challenged_attempt
    opp_attempt = challenged_attempt if is_challenger else challenger_attempt
    viewer_stats = challenger_stats if is_challenger else challenged_stats

    my_score  = float(my_attempt.score_normalized)  if my_attempt  else None
    opp_score = float(opp_attempt.score_normalized) if opp_attempt else None

    # CC-DESIGN-1: viewer_photo / opponent_photo derived from participant roles
    viewer_photo   = challenger_photo_url if is_challenger else challenged_photo_url
    opponent_photo = challenged_photo_url if is_challenger else challenger_photo_url

    outcome_reason = _compute_outcome_reason(ch)
    unlocked = get_unlocked_challenge_card_phases(ch, viewer.id, my_attempt)
    is_locked = phase not in unlocked

    # CC-DESIGN-1: viewer_action_text — two-participant invitation narrative line
    _challenged_dn = _display_name(ch.challenged)
    _challenger_dn = _display_name(ch.challenger)
    if phase == "challenge_sent":
        viewer_action_text = f"You challenged {_challenged_dn}"
    elif phase == "challenge_received":
        viewer_action_text = f"{_challenger_dn} challenged you"
    elif phase == "challenge_accepted":
        viewer_action_text = f"{_challenged_dn} accepted" if is_challenger else "accepted by you"
    elif phase == "waiting_for_opponent":
        opp = _challenged_dn if is_challenger else _challenger_dn
        viewer_action_text = f"Waiting for {opp}"
    elif phase == "challenge_cancelled":
        viewer_action_text = "cancelled by you" if is_challenger else f"{_challenger_dn} cancelled"
    elif phase == "challenge_declined":
        viewer_action_text = f"{_challenged_dn} declined" if is_challenger else "declined by you"
    elif phase in ("completed_forfeit_win", "completed_forfeit_loss"):
        _forfeiter_dn = _display_name(ch.forfeit_user) if ch.forfeit_user else None
        if phase == "completed_forfeit_loss" and ch.forfeit_user_id and (
            (is_challenger and ch.forfeit_user_id == ch.challenger_id) or
            (not is_challenger and ch.forfeit_user_id == ch.challenged_id)
        ):
            viewer_action_text = "you forfeited"
        elif _forfeiter_dn:
            viewer_action_text = f"{_forfeiter_dn} forfeited"
        else:
            viewer_action_text = "opponent forfeited"
    elif phase == "no_contest":
        viewer_action_text = "neither player completed"
    else:
        viewer_action_text = ""

    return {
        "challenge_id":          ch.id,
        "phase":                 phase,
        "challenger_name":       _challenger_dn,
        "challenged_name":       _challenged_dn,
        "game_name":             ch.game.name if ch.game else "Unknown Game",
        "challenge_mode":        ch.challenge_mode or "async",
        "outcome_reason":        outcome_reason,
        "challenger_score":      float(challenger_attempt.score_normalized) if challenger_attempt else None,
        "challenged_score":      float(challenged_attempt.score_normalized) if challenged_attempt else None,
        "winner_name":           _display_name(ch.winner) if ch.winner else None,
        "is_draw":               bool(ch.is_draw),
        "my_score":              my_score,
        "opp_score":             opp_score,
        "my_skill_scores":       _skill_scores_map(my_attempt),
        "is_viewer_winner":      ch.winner_id is not None and ch.winner_id == viewer.id,
        "cta_label":             _PHASE_CTA.get(phase, "View challenge"),
        "completed_at":          ch.completed_at if ch.status == ChallengeStatus.COMPLETED else None,
        "is_locked":             is_locked,
        "unlocked_phases":       unlocked,
        # CC-DESIGN-1: photo fields
        "challenger_photo_url":  challenger_photo_url,
        "challenged_photo_url":  challenged_photo_url,
        "viewer_photo_url":      viewer_photo,
        "opponent_photo_url":    opponent_photo,
        "selected_photo_url":    selected_photo_url,
        "viewer_is_challenger":  is_challenger,
        "forfeit_reason":        ch.forfeit_reason,
        # CC-DESIGN-1: two-participant invitation narrative
        "viewer_action_text":    viewer_action_text,
        # CC-DESIGN-1: central emoji for this phase (both templates use this)
        "phase_emoji":           _PHASE_EMOJI.get(phase, ""),
        # CC-DESIGN-1: game-aware result summaries
        # my_result_summary / viewer_result_summary — viewer's own attempt (waiting + result)
        # opponent_result_summary — for result layout (viewer-relative)
        # challenger/challenged — absolute role keys for Archetype D (both columns always shown)
        "my_result_summary":           _build_result_summary(
                                           my_attempt, ch.game.code if ch.game else None
                                       ),
        "viewer_result_summary":       _build_result_summary(
                                           my_attempt, ch.game.code if ch.game else None
                                       ),
        "opponent_result_summary":     _build_result_summary(
                                           opp_attempt, ch.game.code if ch.game else None
                                       ),
        "challenger_result_summary":   _build_result_summary(
                                           challenger_attempt, ch.game.code if ch.game else None
                                       ),
        "challenged_result_summary":   _build_result_summary(
                                           challenged_attempt, ch.game.code if ch.game else None
                                       ),
        # CC-DESIGN-1: forfeit/no-contest display helpers
        "forfeiter_name":          _display_name(ch.forfeit_user) if ch.forfeit_user else None,
        "forfeit_sublabel":        {
            "forfeit_post_start_timeout": "Post-start timeout",
            "forfeit_deadline":           "Deadline expired",
            "forfeit_no_show":            "No show",
            "forfeit":                    "Forfeited",
            "no_contest":                 "Challenge expired",
        }.get(outcome_reason, "Forfeited") if outcome_reason in (
            "forfeit_post_start_timeout", "forfeit_deadline",
            "forfeit_no_show", "forfeit", "no_contest"
        ) else None,
        # CC-DESIGN-1: participant stats (overall skill + position)
        "challenger_overall":       (challenger_stats or {}).get("overall"),
        "challenger_primary_pos":   (challenger_stats or {}).get("primary_pos"),
        "challenger_secondary_pos": (challenger_stats or {}).get("secondary_pos"),
        "challenged_overall":       (challenged_stats or {}).get("overall"),
        "challenged_primary_pos":   (challenged_stats or {}).get("primary_pos"),
        "challenged_secondary_pos": (challenged_stats or {}).get("secondary_pos"),
        # CC-DESIGN-1: skill_delta_result — viewer skill levels + progress rows
        "viewer_skill_levels":      (viewer_stats or {}).get("skill_levels", {}),
        "my_skill_progress":        _build_skill_progress_rows(
            {k: float(v) for k, v in (my_attempt.skill_deltas or {}).items()}
            if my_attempt and my_attempt.skill_deltas else {},
            (viewer_stats or {}).get("skill_levels", {}),
        ),
    }


@router.get("/challenges/{challenge_id}/card/preview", response_class=HTMLResponse)
async def challenge_card_preview(
    challenge_id: int,
    request: Request,
    platform: str            = Query(...),
    phase: str               = Query(...),
    render_token: str | None = Query(default=None),
    export: bool             = Query(default=False),
    photo_url: str | None    = Query(default=None),
    db: Session              = Depends(get_db),
    user: "User | None"      = Depends(get_current_user_optional),
):
    """Render a challenge social card as HTML.

    Auth: session cookie (browser) OR short-lived render JWT (Playwright export).
    render_token is generated by challenge_card_export, expires in 60 s,
    and is only accepted when purpose=='vt_card_render' and cid==challenge_id.
    """
    if render_token is not None:
        token_user = _resolve_challenge_render_token(render_token, challenge_id, db)
        if token_user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired render token")
        user = token_user
    elif user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if platform not in CHALLENGE_CARD_PLATFORMS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported platform: {platform!r}. Valid: {sorted(CHALLENGE_CARD_PLATFORMS)}",
        )
    if phase not in VALID_CHALLENGE_CARD_PHASES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid phase: {phase!r}. Valid: {sorted(VALID_CHALLENGE_CARD_PHASES)}",
        )

    ch = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()
    if ch is None:
        raise HTTPException(status_code=404, detail="Challenge not found")
    if user.id not in (ch.challenger_id, ch.challenged_id):
        raise HTTPException(status_code=403, detail="Participants only")

    challenger_attempt = (
        db.query(VirtualTrainingAttempt).filter(
            VirtualTrainingAttempt.id == ch.challenger_attempt_id
        ).first() if ch.challenger_attempt_id else None
    )
    challenged_attempt = (
        db.query(VirtualTrainingAttempt).filter(
            VirtualTrainingAttempt.id == ch.challenged_attempt_id
        ).first() if ch.challenged_attempt_id else None
    )

    is_challenger = user.id == ch.challenger_id
    my_attempt = challenger_attempt if is_challenger else challenged_attempt

    # Validate phase is relevant (locked phases are allowed for preview)
    unlocked = get_unlocked_challenge_card_phases(ch, user.id, my_attempt)
    locked   = get_locked_challenge_card_phases(ch, user.id)
    if phase not in set(unlocked) | set(locked):
        raise HTTPException(
            status_code=403,
            detail=f"Phase {phase!r} is not applicable to this challenge.",
        )

    # CC-DESIGN-1 Phase-A SNAPSHOT: frozen snapshot has absolute priority.
    # If NULL, phase/outcome-aware mood lookup runs (_PHASE_MOOD_MAP).
    # photo_url query param overrides the VIEWER's own slot only (not the opponent's).
    ch_is_winner = _winner_ctx(ch, ch.challenger_id)
    cd_is_winner = _winner_ctx(ch, ch.challenged_id)

    def _photo(uid: int, snapshot_url: str | None, is_winner: bool | None) -> str | None:
        return snapshot_url or _get_participant_photo_for_phase(db, uid, phase, is_winner)

    challenger_photo = _photo(ch.challenger_id, ch.challenger_card_photo_url, ch_is_winner)
    challenged_photo = _photo(ch.challenged_id, ch.challenged_card_photo_url, cd_is_winner)

    # CC-DESIGN-1: participant stats (overall skill + position overlay)
    ch_stats = _get_participant_stats(db, ch.challenger_id)
    cd_stats = _get_participant_stats(db, ch.challenged_id)

    # photo_url query param is the viewer's in-session override for their own slot
    selected = photo_url  # applied per viewer_is_challenger in the template

    ctx = _build_challenge_card_context(
        ch, user, challenger_attempt, challenged_attempt, phase, my_attempt,
        challenger_photo_url=challenger_photo,
        challenged_photo_url=challenged_photo,
        selected_photo_url=selected,
        challenger_stats=ch_stats,
        challenged_stats=cd_stats,
    )
    template_name = (
        "public/export/challenge/post_16_9.html"
        if platform == "challenge_post_16_9"
        else "public/export/challenge/story_9_16.html"
    )
    return templates.TemplateResponse(template_name, {"request": request, **ctx})


@router.get("/challenges/{challenge_id}/card/export")
async def challenge_card_export(
    challenge_id: int,
    request: Request,
    platform: str = Query(...),
    phase: str    = Query(...),
    db: Session   = Depends(get_db),
    user: User    = Depends(get_current_user_web),
):
    """Export a challenge social card as PNG. Participants only. Rate-limited 5/60s.

    Only exportable (unlocked) phases are accepted — locked phases return 403.
    """
    from app.config import settings  # noqa: PLC0415
    from ...core.auth import create_challenge_render_token  # noqa: PLC0415

    if platform not in CHALLENGE_CARD_PLATFORMS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported platform: {platform!r}. Valid: {sorted(CHALLENGE_CARD_PLATFORMS)}",
        )
    if phase not in VALID_CHALLENGE_CARD_PHASES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid phase: {phase!r}. Valid: {sorted(VALID_CHALLENGE_CARD_PHASES)}",
        )

    ch = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()
    if ch is None:
        raise HTTPException(status_code=404, detail="Challenge not found")
    if user.id not in (ch.challenger_id, ch.challenged_id):
        raise HTTPException(status_code=403, detail="Participants only")

    # Challenge Card ownership guard — every format requires a CDO row.
    # Admin bypass: admins may export any format regardless of ownership.
    from app.models.user import UserRole as _UserRole  # noqa: PLC0415
    if user.role != _UserRole.ADMIN:
        from app.services.card_design_service import is_design_accessible as _is_accessible  # noqa: PLC0415
        if not _is_accessible(db, user.id, "challenge_card", platform):
            raise HTTPException(
                status_code=403,
                detail="Challenge Card not owned. Purchase it at /my-cards/challenge-card",
            )

    is_challenger = user.id == ch.challenger_id
    my_attempt_id = ch.challenger_attempt_id if is_challenger else ch.challenged_attempt_id
    my_attempt = (
        db.query(VirtualTrainingAttempt).filter(
            VirtualTrainingAttempt.id == my_attempt_id
        ).first() if my_attempt_id else None
    )

    # Enforce export only for unlocked phases
    validate_challenge_card_phase(ch, user.id, phase, for_export=True, my_attempt=my_attempt)

    client_ip = request.client.host if request.client else "unknown"
    rate_key  = f"vt_card:{challenge_id}:{user.id}:{client_ip}"
    if not _export_svc.check_export_rate_limit(rate_key):
        raise HTTPException(
            status_code=429,
            detail="Export rate limit exceeded (5 per minute). Please wait before exporting again.",
        )

    token = create_challenge_render_token(user.id, challenge_id)
    render_url = (
        f"http://127.0.0.1:{settings.APP_INTERNAL_PORT}"
        f"/challenges/{challenge_id}/card/preview"
        f"?platform={platform}&phase={phase}&export=1&render_token={token}"
    )

    try:
        png_bytes = await asyncio.to_thread(
            _export_svc._sync_take_screenshot, render_url, platform
        )
    except _export_svc.CardExportTimeoutError:
        raise HTTPException(status_code=504, detail="Card render timed out")

    filename = f"lfa_challenge_{challenge_id}_{phase}_{platform}.png"
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control":       "no-store",
            "X-Export-Platform":   platform,
            "X-Export-Phase":      phase,
        },
    )


# ── POST /challenges/{id}/card/photo — save per-challenge card photo snapshot ──

@router.post("/challenges/{challenge_id}/card/photo")
async def challenge_card_photo_save(
    challenge_id: int,
    photo_url: str = Form(...),
    db: Session    = Depends(get_db),
    user: User     = Depends(get_current_user_web),
):
    """Save the viewer's chosen mood photo as the per-challenge card snapshot.

    Only participants may call this endpoint.
    Each user can only write their OWN slot:
      - challenger → challenger_card_photo_url
      - challenged → challenged_card_photo_url
    The opponent's slot is never modified.

    The photo_url must belong to the requesting user's mood photos or be empty
    (empty string clears the snapshot, reverting to neutral mood default).
    """
    ch = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()
    if ch is None:
        raise HTTPException(status_code=404, detail="Challenge not found")
    if user.id not in (ch.challenger_id, ch.challenged_id):
        raise HTTPException(status_code=403, detail="Participants only")

    # Validate photo_url ownership: must be empty (clear) or belong to this user
    if photo_url:
        owns = db.query(UserMoodPhoto).filter(
            UserMoodPhoto.user_id == user.id,
            (UserMoodPhoto.processed_png_url == photo_url) |
            (UserMoodPhoto.original_url == photo_url),
        ).first()
        if owns is None:
            raise HTTPException(
                status_code=403,
                detail="photo_url must belong to your own mood photos",
            )

    effective_url = photo_url or None  # empty string → clear snapshot

    if user.id == ch.challenger_id:
        ch.challenger_card_photo_url = effective_url
    else:
        ch.challenged_card_photo_url = effective_url

    db.commit()
    return {"ok": True, "role": "challenger" if user.id == ch.challenger_id else "challenged"}


# ── POST /challenges/{id}/decline ─────────────────────────────────────────────

@router.post("/challenges/{challenge_id}/decline")
async def decline_challenge(
    challenge_id: int,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    challenge = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()

    if not challenge or challenge.challenged_id != user.id:
        return RedirectResponse(url="/challenges?error=not_found", status_code=303)

    if challenge.status != ChallengeStatus.PENDING:
        return RedirectResponse(url="/challenges?error=not_pending", status_code=303)

    now = datetime.now(timezone.utc)
    challenge.status     = ChallengeStatus.DECLINED
    challenge.updated_at = now

    notification_service.create_notification(
        db=db,
        user_id=challenge.challenger_id,
        title="Challenge Declined",
        message=f"{user.nickname or user.email} declined your VT challenge.",
        notification_type=NotificationType.VT_CHALLENGE_DECLINED,
        link="/challenges",
    )

    db.commit()
    publish_challenge_event(
        [challenge.challenger_id, challenge.challenged_id],
        "challenge_declined",
        {"challenge_id": challenge_id},
    )
    return RedirectResponse(url="/challenges?success=challenge_declined", status_code=303)


# ── POST /challenges/{id}/cancel ──────────────────────────────────────────────

@router.post("/challenges/{challenge_id}/cancel")
async def cancel_challenge(
    challenge_id: int,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    challenge = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()

    if not challenge or challenge.challenger_id != user.id:
        return RedirectResponse(url="/challenges?error=not_found", status_code=303)

    if challenge.status not in (ChallengeStatus.PENDING, ChallengeStatus.ACCEPTED):
        return RedirectResponse(url="/challenges?error=cannot_cancel", status_code=303)

    now = datetime.now(timezone.utc)
    challenge.status     = ChallengeStatus.CANCELLED
    challenge.updated_at = now

    notification_service.create_notification(
        db=db,
        user_id=challenge.challenged_id,
        title="Challenge Cancelled",
        message=f"{user.nickname or user.email} cancelled their VT challenge.",
        notification_type=NotificationType.VT_CHALLENGE_CANCELLED,
        link="/challenges",
    )

    db.commit()
    publish_challenge_event(
        [challenge.challenger_id, challenge.challenged_id],
        "challenge_cancelled",
        {"challenge_id": challenge_id},
    )
    return RedirectResponse(url="/challenges?success=challenge_cancelled", status_code=303)


# ── GET /challenges/{id}/lobby ────────────────────────────────────────────────

@router.get("/challenges/{challenge_id}/lobby", response_class=HTMLResponse)
async def challenge_lobby(
    challenge_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    guard = require_student_onboarding(user)
    if guard:
        return guard

    challenge = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()

    if not challenge or user.id not in (challenge.challenger_id, challenge.challenged_id):
        return RedirectResponse(url="/challenges?error=not_found", status_code=303)

    if challenge.status not in (ChallengeStatus.LIVE_LOBBY, ChallengeStatus.LIVE_IN_PROGRESS):
        return RedirectResponse(url="/challenges?error=not_live", status_code=303)

    now = datetime.now(timezone.utc)
    # Apply timeouts lazily before rendering
    changed = apply_lobby_timeout_if_expired(db, challenge, now) or \
              apply_post_start_timeout_if_expired(db, challenge, now)
    if changed:
        db.commit()
        return RedirectResponse(url="/challenges?error=lobby_expired", status_code=303)

    game = db.query(VirtualTrainingGame).filter(
        VirtualTrainingGame.id == challenge.game_id
    ).first()
    diff = challenge.difficulty_level or "easy"
    if game and game.code == "memory_sequence":
        play_url = f"/virtual-training/memory-sequence?challenge_id={challenge.id}"
    elif game and game.code == "target_tracking":
        play_url = f"/virtual-training/target-tracking?challenge_id={challenge.id}&difficulty={diff}"
    else:
        play_url = "/virtual-training"

    challenger = db.query(User).filter(User.id == challenge.challenger_id).first()
    challenged = db.query(User).filter(User.id == challenge.challenged_id).first()

    is_challenger = user.id == challenge.challenger_id
    my_ready_at  = challenge.challenger_ready_at if is_challenger else challenge.challenged_ready_at
    opp_ready_at = challenge.challenged_ready_at if is_challenger else challenge.challenger_ready_at

    return templates.TemplateResponse("vt_challenge_lobby.html", {
        "request":        request,
        "user":           user,
        **_spec_ctx(user, db),
        "challenge":      challenge,
        "challenger":     challenger,
        "challenged":     challenged,
        "game":           game,
        "play_url":       play_url,
        "is_challenger":  is_challenger,
        "my_ready":       my_ready_at is not None,
        "opp_ready":      opp_ready_at is not None,
        "live_start_at":  challenge.live_start_at,
        "lobby_expires_at": challenge.lobby_expires_at,
        "server_now":     now,
        "error":          request.query_params.get("error"),
    })


# ── GET /challenges/{id}/lobby-state ─────────────────────────────────────────

@router.get("/challenges/{challenge_id}/lobby-state")
async def challenge_lobby_state(
    challenge_id: int,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    challenge = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()

    if not challenge or user.id not in (challenge.challenger_id, challenge.challenged_id):
        return JSONResponse({"error": "not_found"}, status_code=404)

    now = datetime.now(timezone.utc)
    # Apply timeouts lazily
    apply_lobby_timeout_if_expired(db, challenge, now)
    apply_post_start_timeout_if_expired(db, challenge, now)
    db.commit()

    state = get_lobby_state(challenge, now)

    # Compute game_url so the frontend can redirect without relying on
    # the template-rendered PLAY_URL (defensive — also used by tests).
    game = db.query(VirtualTrainingGame).filter(
        VirtualTrainingGame.id == challenge.game_id
    ).first()
    diff = challenge.difficulty_level or "easy"
    if game and game.code == "memory_sequence":
        state["game_url"] = f"/virtual-training/memory-sequence?challenge_id={challenge.id}"
    elif game and game.code == "target_tracking":
        state["game_url"] = f"/virtual-training/target-tracking?challenge_id={challenge.id}&difficulty={diff}"
    else:
        state["game_url"] = "/virtual-training"

    return JSONResponse(state)


# ── POST /challenges/{id}/ready ───────────────────────────────────────────────

@router.post("/challenges/{challenge_id}/ready")
async def challenge_ready(
    challenge_id: int,
    db: Session = Depends(get_db),
    user: User  = Depends(get_current_user_web),
):
    challenge = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()

    if not challenge or user.id not in (challenge.challenger_id, challenge.challenged_id):
        return JSONResponse({"error": "not_found"}, status_code=404)

    if challenge.status != ChallengeStatus.LIVE_LOBBY:
        return JSONResponse({"error": "not_in_lobby"}, status_code=409)

    now = datetime.now(timezone.utc)
    state = set_ready(db, challenge, user.id, now)
    db.commit()
    return JSONResponse(state)
