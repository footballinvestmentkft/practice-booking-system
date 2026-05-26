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

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.friendship import Friendship, FriendshipStatus, is_friends
from ...models.notification import NotificationType
from ...models.user import User
from ...models.virtual_training import VirtualTrainingAttempt, VirtualTrainingGame
from ...models.vt_challenge import (
    CHALLENGE_COMPATIBLE_GAMES,
    DEFAULT_COMPLETION_WINDOW,
    LOBBY_TIMEOUT_SECONDS,
    ChallengeStatus,
    VirtualTrainingChallenge,
    get_active_challenge,
    make_completion_deadline,
    make_expires_at,
    validate_completion_window,
)
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

    # Duplicate active challenge guard (bidirectional)
    if get_active_challenge(db, user.id, challenged_user_id, game_id):
        return RedirectResponse(url="/challenges/send?error=challenge_active", status_code=303)

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
        return RedirectResponse(url="/challenges?success=challenge_accepted", status_code=303)


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

    # Compute per-skill scores for each attempt (same pattern as individual result pages)
    def _skill_scores(attempt: VirtualTrainingAttempt | None, game) -> dict:
        if attempt is None or not attempt.skill_deltas or game is None:
            return {}
        try:
            from ...services.virtual_training_metrics import VTSignalExtractor, VTSkillScorer
            cfg          = game.config or {}
            phase_config = cfg.get("phases", []) if isinstance(cfg, dict) else []
            signals = VTSignalExtractor.extract(
                {
                    "stimuli_count":     attempt.stimuli_count,
                    "correct_count":     attempt.correct_count,
                    "wrong_click_count": attempt.wrong_click_count,
                    "error_count":       attempt.error_count,
                    "avg_reaction_ms":   attempt.avg_reaction_ms,
                    "raw_metrics":       attempt.raw_metrics,
                },
                phase_config,
            )
            return VTSkillScorer.score_all(signals, game.skill_targets or {})
        except Exception:
            return {}

    game = ch.game  # ORM relationship

    is_forfeit   = ch.forfeit_user_id is not None
    is_no_contest = is_forfeit and ch.winner_id is None

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
