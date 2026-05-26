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
from ...models.notification import NotificationType
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
})

# Phases that are exportable (unlocked) — remaining phases are preview-only when relevant
_EXPORTABLE_PHASES = frozenset({
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

    # For non-pending challenges, the initial send/receive phase is historical
    if s not in (ChallengeStatus.PENDING, ChallengeStatus.DECLINED,
                 ChallengeStatus.CANCELLED, ChallengeStatus.EXPIRED):
        locked.append(initial)

    # For completed challenges, the accepted phase is also historical
    if s == ChallengeStatus.COMPLETED:
        locked.append("challenge_accepted")

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
    if for_export and phase not in unlocked:
        raise HTTPException(
            status_code=403,
            detail=f"Phase {phase!r} is locked — export not available.",
        )


def _build_challenge_card_context(
    ch: VirtualTrainingChallenge,
    viewer: User,
    challenger_attempt: Any,
    challenged_attempt: Any,
    phase: str,
    my_attempt: Any = None,
) -> dict:
    def _skill_scores_map(attempt: Any) -> dict[str, float]:
        if attempt is None or not attempt.skill_deltas:
            return {}
        return {k: float(v) for k, v in attempt.skill_deltas.items()}

    is_challenger = viewer.id == ch.challenger_id
    if my_attempt is None:
        my_attempt  = challenger_attempt if is_challenger else challenged_attempt
    opp_attempt = challenged_attempt if is_challenger else challenger_attempt

    my_score  = float(my_attempt.score_normalized)  if my_attempt  else None
    opp_score = float(opp_attempt.score_normalized) if opp_attempt else None

    outcome_reason = _compute_outcome_reason(ch)
    unlocked = get_unlocked_challenge_card_phases(ch, viewer.id, my_attempt)
    is_locked = phase not in unlocked

    return {
        "challenge_id":     ch.id,
        "phase":            phase,
        "challenger_name":  _display_name(ch.challenger),
        "challenged_name":  _display_name(ch.challenged),
        "game_name":        ch.game.name if ch.game else "Unknown Game",
        "challenge_mode":   ch.challenge_mode or "async",
        "outcome_reason":   outcome_reason,
        "challenger_score": float(challenger_attempt.score_normalized) if challenger_attempt else None,
        "challenged_score": float(challenged_attempt.score_normalized) if challenged_attempt else None,
        "winner_name":      _display_name(ch.winner) if ch.winner else None,
        "is_draw":          bool(ch.is_draw),
        "my_score":         my_score,
        "opp_score":        opp_score,
        "my_skill_scores":  _skill_scores_map(my_attempt),
        "is_viewer_winner": ch.winner_id is not None and ch.winner_id == viewer.id,
        "cta_label":        _PHASE_CTA.get(phase, "View challenge"),
        "completed_at":     ch.completed_at if ch.status == ChallengeStatus.COMPLETED else None,
        "is_locked":        is_locked,
        "unlocked_phases":  unlocked,
    }


@router.get("/challenges/{challenge_id}/card/preview", response_class=HTMLResponse)
async def challenge_card_preview(
    challenge_id: int,
    request: Request,
    platform: str           = Query(...),
    phase: str              = Query(...),
    render_token: str | None = Query(default=None),
    export: bool            = Query(default=False),
    db: Session             = Depends(get_db),
    user: "User | None"     = Depends(get_current_user_optional),
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

    ctx = _build_challenge_card_context(
        ch, user, challenger_attempt, challenged_attempt, phase, my_attempt
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
