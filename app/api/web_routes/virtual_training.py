"""Virtual Training web routes — Phase 2 Color Reaction MVP."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.notification import NotificationType
from ...models.user import User
from ...models.virtual_training import VirtualTrainingAttempt, VirtualTrainingGame
from ...models.vt_challenge import ChallengeStatus, VirtualTrainingChallenge
from ...core.redis_pubsub import publish_challenge_event
from ...services import notification_service
from ...services.challenge_completion_service import apply_forfeit_if_deadline_passed
from ...services.virtual_training_service import VirtualTrainingService
from ...services.training_day import (
    resolve_training_timezone,
    resolve_location_source,
    compute_training_local_date,
)
from .helpers import require_student_onboarding
from .student_features import _spec_ctx
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["virtual-training"])


# ── Training day helpers ──────────────────────────────────────────────────────

def _parse_location_captured_at(raw: str | None) -> "datetime | None":
    """Parse ISO datetime string from location payload, return None on failure."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


def _extract_training_ctx(body: dict) -> dict:
    """Extract browser timezone + location from submit body; compute training day.

    Returns dict with keys:
      training_local_date, training_tz, location_lat, location_lng,
      location_accuracy_m, location_captured_at, browser_timezone
    All fields are safe to pass directly to VirtualTrainingService.record_attempt().
    """
    browser_tz = body.get("browser_timezone")
    loc = body.get("location") or {}
    _now = datetime.now(timezone.utc)
    training_tz, _ = resolve_training_timezone(browser_tz)
    training_date = compute_training_local_date(_now, training_tz)
    cap_at = _parse_location_captured_at(loc.get("captured_at"))
    return {
        "training_local_date":   training_date,
        "training_tz":           training_tz,
        "browser_timezone":      browser_tz,
        "location_lat":          loc.get("lat"),
        "location_lng":          loc.get("lng"),
        "location_accuracy_m":   loc.get("accuracy_m"),
        "location_captured_at":  cap_at,
    }


# ── PR-C2 Challenge Submit Helpers ────────────────────────────────────────────

def _compute_winner(
    ch: VirtualTrainingChallenge,
    a_cr: VirtualTrainingAttempt,
    a_cd: VirtualTrainingAttempt,
) -> tuple[int | None, bool]:
    """Determine challenge winner. Returns (winner_id | None, is_draw)."""
    s_cr = a_cr.score_normalized or 0.0
    s_cd = a_cd.score_normalized or 0.0
    if s_cr != s_cd:
        return (ch.challenger_id if s_cr > s_cd else ch.challenged_id, False)

    def _acc(a: VirtualTrainingAttempt) -> float:
        if a.stimuli_count and a.stimuli_count > 0 and a.correct_count is not None:
            return a.correct_count / a.stimuli_count
        return 0.0

    acc_cr, acc_cd = _acc(a_cr), _acc(a_cd)
    if acc_cr != acc_cd:
        return (ch.challenger_id if acc_cr > acc_cd else ch.challenged_id, False)

    r_cr, r_cd = a_cr.avg_reaction_ms, a_cd.avg_reaction_ms
    if r_cr is not None and r_cd is not None and r_cr != r_cd:
        return (ch.challenger_id if r_cr < r_cd else ch.challenged_id, False)

    t_cr, t_cd = a_cr.completed_at, a_cd.completed_at
    if t_cr and t_cd and t_cr != t_cd:
        return (ch.challenger_id if t_cr < t_cd else ch.challenged_id, False)

    return (None, True)


def _validate_challenge_pre_submit(
    db: Session,
    challenge_id: int,
    user_id: int,
    game_id: int,
) -> tuple[VirtualTrainingChallenge | None, JSONResponse | None]:
    """
    Validate all challenge guards before the attempt is recorded.
    Returns (challenge, None) on success, (None, error_response) on failure.
    The caller must return the error_response immediately on failure.
    """
    challenge = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()
    if challenge is None:
        return None, JSONResponse({"error": "challenge_not_found"}, status_code=404)

    if user_id not in (challenge.challenger_id, challenge.challenged_id):
        return None, JSONResponse({"error": "not_participant"}, status_code=403)

    if challenge.game_id != game_id:
        return None, JSONResponse({"error": "wrong_game"}, status_code=400)

    # LIVE_LOBBY is not playable — players must wait for countdown
    if challenge.status == ChallengeStatus.LIVE_LOBBY:
        return None, JSONResponse(
            {"error": "challenge_not_started", "status": challenge.status.value},
            status_code=409,
        )

    if challenge.status not in (ChallengeStatus.ACCEPTED, ChallengeStatus.LIVE_IN_PROGRESS):
        return None, JSONResponse(
            {"error": "challenge_not_accepted", "status": challenge.status.value},
            status_code=409,
        )

    now = datetime.now(timezone.utc)
    if challenge.expires_at is not None and challenge.expires_at <= now:
        challenge.status = ChallengeStatus.EXPIRED
        challenge.updated_at = now
        db.commit()
        return None, JSONResponse({"error": "challenge_expired"}, status_code=410)

    # Idempotency: already submitted on this side
    side = "challenger" if user_id == challenge.challenger_id else "challenged"
    already_set = (
        challenge.challenger_attempt_id if side == "challenger"
        else challenge.challenged_attempt_id
    )
    if already_set is not None:
        return None, JSONResponse(
            {
                "error": "already_submitted",
                "challenge_context": {
                    "challenge_id": challenge_id,
                    "status": challenge.status.value,
                },
            },
            status_code=409,
        )

    # Async: late submit guard
    if challenge.status == ChallengeStatus.ACCEPTED:
        if challenge.completion_deadline is not None and challenge.completion_deadline <= now:
            apply_forfeit_if_deadline_passed(db, challenge, now)
            db.commit()
            return None, JSONResponse({"error": "challenge_deadline_passed"}, status_code=410)

    return challenge, None


def _send_completion_notifications(
    db: Session,
    challenge: VirtualTrainingChallenge,
    winner_id: int | None,
    is_draw: bool,
) -> None:
    """Send VT_CHALLENGE_COMPLETED to both participants."""
    def _msg(for_user_id: int) -> str:
        if is_draw:
            return "Your VT challenge ended in a draw!"
        if winner_id == for_user_id:
            return "You won the VT challenge!"
        return "You lost the VT challenge."

    for uid in (challenge.challenger_id, challenge.challenged_id):
        notification_service.create_notification(
            db=db,
            user_id=uid,
            title="VT Challenge Completed",
            message=_msg(uid),
            notification_type=NotificationType.VT_CHALLENGE_COMPLETED,
            link="/challenges",
        )


def _link_attempt_to_challenge(
    db: Session,
    challenge: VirtualTrainingChallenge,
    user_id: int,
    attempt: VirtualTrainingAttempt,
) -> dict:
    """
    Link a valid attempt to the challenge; complete if both sides have submitted.
    Runs inside the caller's open transaction (before db.commit()).
    Returns challenge_context dict for the submit response.
    """
    now = datetime.now(timezone.utc)
    side = "challenger" if user_id == challenge.challenger_id else "challenged"

    if side == "challenger":
        challenge.challenger_attempt_id = attempt.id
    else:
        challenge.challenged_attempt_id = attempt.id
    challenge.updated_at = now
    db.flush()

    winner_id: int | None = None
    is_draw = False

    if challenge.challenger_attempt_id and challenge.challenged_attempt_id:
        a_cr = db.query(VirtualTrainingAttempt).filter(
            VirtualTrainingAttempt.id == challenge.challenger_attempt_id
        ).first()
        a_cd = db.query(VirtualTrainingAttempt).filter(
            VirtualTrainingAttempt.id == challenge.challenged_attempt_id
        ).first()
        winner_id, is_draw = _compute_winner(challenge, a_cr, a_cd)
        challenge.status     = ChallengeStatus.COMPLETED
        challenge.winner_id  = winner_id
        challenge.is_draw    = is_draw
        challenge.completed_at = now
        challenge.updated_at   = now
        db.flush()
        _send_completion_notifications(db, challenge, winner_id, is_draw)
        publish_challenge_event(
            [challenge.challenger_id, challenge.challenged_id],
            "challenge_completed",
            {
                "challenge_id": challenge.id,
                "winner_id": winner_id,
                "is_draw": is_draw,
            },
        )

    is_winner = (winner_id == user_id) if challenge.status == ChallengeStatus.COMPLETED else None
    is_draw_ctx = is_draw if challenge.status == ChallengeStatus.COMPLETED else None

    return {
        "challenge_id": challenge.id,
        "status": challenge.status.value,
        "is_winner": is_winner,
        "is_draw": is_draw_ctx,
    }


# ── PR-C3 Challenge Result Context ────────────────────────────────────────────

def _build_challenge_result_ctx(
    db: Session,
    challenge_id: int,
    user_id: int,
    attempt_id: int,
) -> dict | None:
    """Build challenge context block for result pages.

    Returns None if the challenge_id is invalid, the user is not a participant,
    or the given attempt_id doesn't belong to this user's challenge slot.
    """
    ch = db.query(VirtualTrainingChallenge).filter(
        VirtualTrainingChallenge.id == challenge_id
    ).first()
    if ch is None:
        return None
    if user_id not in (ch.challenger_id, ch.challenged_id):
        return None

    is_challenger   = ch.challenger_id == user_id
    my_attempt_id   = ch.challenger_attempt_id if is_challenger else ch.challenged_attempt_id
    opp_attempt_id  = ch.challenged_attempt_id if is_challenger else ch.challenger_attempt_id

    # Only bind the block if this attempt belongs to the user's challenge slot
    if my_attempt_id != attempt_id:
        return None

    opponent_id = ch.challenged_id if is_challenger else ch.challenger_id
    opponent = db.query(User).filter(User.id == opponent_id).first()
    opponent_name = (opponent.nickname or opponent.email) if opponent else "Unknown"

    ctx: dict = {
        "challenge_id":       ch.id,
        "status":             ch.status.value,
        "is_challenger":      is_challenger,
        "opponent_name":      opponent_name,
        "difficulty_level":   ch.difficulty_level,
        "my_score":           None,
        "opp_score":          None,
        "opp_attempt_id":     opp_attempt_id,
        "opp_skill_deltas":   None,
        "opp_per_round":      None,
        "outcome":            ch.status.value,
        "challenge_detail_url": f"/challenges/{challenge_id}",
        "challenge_category": "virtual",
    }

    if ch.status == ChallengeStatus.COMPLETED:
        # Load both attempt scores + opponent breakdown
        my_a  = db.query(VirtualTrainingAttempt).filter(
            VirtualTrainingAttempt.id == my_attempt_id
        ).first() if my_attempt_id else None
        opp_a = db.query(VirtualTrainingAttempt).filter(
            VirtualTrainingAttempt.id == opp_attempt_id
        ).first() if opp_attempt_id else None

        ctx["my_score"]       = my_a.score_normalized  if my_a  else None
        ctx["opp_score"]      = opp_a.score_normalized if opp_a else None
        ctx["opp_skill_deltas"] = opp_a.skill_deltas   if opp_a else None
        ctx["opp_per_round"]  = (
            (opp_a.raw_metrics or {}).get("per_round") if opp_a else None
        )

        if ch.is_draw:
            ctx["outcome"] = "draw"
        elif ch.winner_id == user_id:
            ctx["outcome"] = "won"
        else:
            ctx["outcome"] = "lost"

    elif ch.status == ChallengeStatus.ACCEPTED:
        if opp_attempt_id is None:
            ctx["outcome"] = "waiting_for_opponent"
        else:
            ctx["outcome"] = "waiting_for_resolution"

    return ctx


# ── Hub ───────────────────────────────────────────────────────────────────────

@router.get("/virtual-training", response_class=HTMLResponse)
async def virtual_training_hub(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Virtual Games hub — lists all hub-visible games (active + planned)."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    all_games = VirtualTrainingService.get_hub_games(db)

    today_start = datetime.combine(
        datetime.now(timezone.utc).date(),
        datetime.min.time(),
    ).replace(tzinfo=timezone.utc)
    game_attempts: dict[int, int] = {
        g.id: (
            db.query(VirtualTrainingAttempt)
            .filter(
                VirtualTrainingAttempt.user_id == user.id,
                VirtualTrainingAttempt.game_id == g.id,
                VirtualTrainingAttempt.started_at >= today_start,
                VirtualTrainingAttempt.is_valid == True,  # noqa: E712
            )
            .count()
        )
        for g in all_games
        if g.is_active
    }

    return templates.TemplateResponse(
        "virtual_training_hub.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "all_games": all_games,
            "game_attempts": game_attempts,
        },
    )


# ── Color Reaction game page ──────────────────────────────────────────────────

@router.get("/virtual-training/color-reaction", response_class=HTMLResponse)
async def virtual_training_color_reaction(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Color Reaction game page — instructions + Vanilla JS game loop."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    game = VirtualTrainingService.get_game(db, "color_reaction")
    if game is None or not game.is_active:
        return templates.TemplateResponse(
            "virtual_training_hub.html",
            {
                "request": request,
                "user": user,
                **_spec_ctx(user, db),
                "active_games": VirtualTrainingService.get_games(db),
                "error": "Color Reaction is not available at this time.",
            },
        )

    # Daily attempt count for the UI (show remaining attempts)
    today_start = datetime.combine(
        datetime.now(timezone.utc).date(),
        datetime.min.time(),
    ).replace(tzinfo=timezone.utc)
    attempts_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id == user.id,
            VirtualTrainingAttempt.game_id == game.id,
            VirtualTrainingAttempt.started_at >= today_start,
            VirtualTrainingAttempt.is_valid == True,  # noqa: E712
        )
        .count()
    )

    assigned_protocol = VirtualTrainingService.assign_protocol(db, user.id, game.id)

    return templates.TemplateResponse(
        "virtual_training_color_reaction.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "game": game,
            "attempts_today": attempts_today,
            "max_daily_attempts": game.max_daily_attempts,
            "attempts_remaining": max(0, game.max_daily_attempts - attempts_today),
            "assigned_protocol": assigned_protocol,
        },
    )


# ── Submit attempt (JSON API) ─────────────────────────────────────────────────

@router.post("/virtual-training/color-reaction/submit")
async def virtual_training_color_reaction_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Record a Color Reaction attempt. Returns attempt_id, xp_awarded, is_valid."""
    guard = require_student_onboarding(user)
    if guard:
        return JSONResponse({"error": "onboarding required"}, status_code=403)

    game = VirtualTrainingService.get_game(db, "color_reaction")
    if game is None or not game.is_active:
        return JSONResponse({"error": "game not available"}, status_code=404)

    body = await request.json()
    _tctx = _extract_training_ctx(body)

    # Daily cap guard — training_local_date based (browser timezone aware)
    valid_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id             == user.id,
            VirtualTrainingAttempt.game_id              == game.id,
            VirtualTrainingAttempt.training_local_date  == _tctx["training_local_date"],
            VirtualTrainingAttempt.is_valid             == True,  # noqa: E712
        )
        .count()
    )
    if valid_today >= game.max_daily_attempts:
        return JSONResponse(
            {"error": "daily_cap", "message": "Daily attempt limit reached for this game."},
            status_code=429,
        )

    # Build idempotency key from client-supplied started_at so retries are safe
    started_at_raw = body.get("started_at", "")
    idem_key = f"vt_cr_u{user.id}_{started_at_raw}"

    attempt = VirtualTrainingService.record_attempt(
        db=db,
        user_id=user.id,
        game=game,
        data=body,
        idempotency_key=idem_key,
        browser_timezone=_tctx["browser_timezone"],
        location_lat=_tctx["location_lat"],
        location_lng=_tctx["location_lng"],
        location_accuracy_m=_tctx["location_accuracy_m"],
        location_captured_at=_tctx["location_captured_at"],
    )

    db.commit()

    return JSONResponse({
        "attempt_id": attempt.id,
        "is_valid": attempt.is_valid,
        "invalid_reason": attempt.invalid_reason,
        "xp_awarded": attempt.xp_awarded,
        "skill_deltas": attempt.skill_deltas,
        "attempt_index_today": attempt.attempt_index_today,
        "score_normalized": attempt.score_normalized,
    })


# ── Result page ───────────────────────────────────────────────────────────────

@router.get("/virtual-training/color-reaction/result/{attempt_id}",
            response_class=HTMLResponse)
async def virtual_training_color_reaction_result(
    attempt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Result screen for a completed Color Reaction attempt."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    attempt = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.id == attempt_id,
            VirtualTrainingAttempt.user_id == user.id,
        )
        .first()
    )
    if attempt is None:
        return templates.TemplateResponse(
            "virtual_training_hub.html",
            {
                "request": request,
                "user": user,
                **_spec_ctx(user, db),
                "active_games": VirtualTrainingService.get_games(db),
                "error": "Attempt not found.",
            },
        )

    game = db.query(VirtualTrainingGame).filter(
        VirtualTrainingGame.id == attempt.game_id
    ).first()

    # ── Skill delta breakdown (VH-04/05): recompute per-skill scores from stored fields ──
    skill_scores: dict = {}
    signals_ctx: dict = {}
    if attempt.skill_deltas and game is not None:
        from ...services.virtual_training_metrics import VTSignalExtractor, VTSkillScorer
        cfg          = game.config or {}
        phase_config = cfg.get("phases", []) if isinstance(cfg, dict) else []
        data_for_signals = {
            "stimuli_count":    attempt.stimuli_count,
            "correct_count":    attempt.correct_count,
            "wrong_click_count": attempt.wrong_click_count,
            "error_count":      attempt.error_count,
            "avg_reaction_ms":  attempt.avg_reaction_ms,
            "raw_metrics":      attempt.raw_metrics,
        }
        signals = VTSignalExtractor.extract(data_for_signals, phase_config)
        skill_scores = VTSkillScorer.score_all(signals, game.skill_targets or {})
        signals_ctx = {
            "hit_rate":        round(signals.hit_rate * 100, 1),
            "wrong_rate":      round(signals.wrong_rate * 100, 1),
            "miss_rate":       round(signals.miss_rate * 100, 1),
            "speed_score":     round(signals.speed_score * 100, 1),
            "completion_rate": round(signals.completion_rate * 100, 1),
            "avg_reaction_ms": signals.avg_reaction_ms,
        }

    # ── raw_metrics decomposition (VH-06/07/08/09): per-phase, per-color, per-stimulus ──
    per_phase: list = []
    per_color: dict = {}
    per_stimulus: list = []
    late_summary: dict | None = None
    raw = attempt.raw_metrics
    if isinstance(raw, dict) and raw.get("v", 1) >= 1:
        per_phase    = raw.get("per_phase")    or []
        per_color    = raw.get("per_color")    or {}
        per_stimulus = raw.get("per_stimulus") or []
    if isinstance(raw, dict) and raw.get("v", 1) >= 2:
        late_summary = raw.get("late_summary") or None

    hand_profile: dict | None = None
    if isinstance(raw, dict) and int(raw.get("v", 1)) >= 3:
        hand_profile = raw.get("hand_profile") or None

    from ...models.user import UserRole
    is_admin = user.role == UserRole.ADMIN

    return templates.TemplateResponse(
        "virtual_training_result.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "attempt": attempt,
            "game": game,
            "skill_scores": skill_scores,
            "signals_ctx":  signals_ctx,
            "per_phase":     per_phase,
            "per_color":     per_color,
            "per_stimulus":  per_stimulus,
            "late_summary":  late_summary,
            "hand_profile":  hand_profile,
            "is_admin":      is_admin,
        },
    )


# ── Go / No-Go Reaction game page ────────────────────────────────────────────

@router.get("/virtual-training/go-no-go", response_class=HTMLResponse)
async def virtual_training_go_no_go(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Go / No-Go Reaction game page — instructions + Vanilla JS game loop."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    game = VirtualTrainingService.get_game(db, "go_no_go")
    if game is None or not game.is_active:
        all_games = VirtualTrainingService.get_hub_games(db)
        return templates.TemplateResponse(
            "virtual_training_hub.html",
            {
                "request": request,
                "user": user,
                **_spec_ctx(user, db),
                "all_games": all_games,
                "error": "Go / No-Go Reaction is not available at this time.",
            },
        )

    today_start = datetime.combine(
        datetime.now(timezone.utc).date(),
        datetime.min.time(),
    ).replace(tzinfo=timezone.utc)
    attempts_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id == user.id,
            VirtualTrainingAttempt.game_id == game.id,
            VirtualTrainingAttempt.started_at >= today_start,
            VirtualTrainingAttempt.is_valid == True,  # noqa: E712
        )
        .count()
    )

    assigned_protocol = VirtualTrainingService.assign_protocol(db, user.id, game.id)

    return templates.TemplateResponse(
        "virtual_training_go_no_go.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "game": game,
            "attempts_today": attempts_today,
            "max_daily_attempts": game.max_daily_attempts,
            "attempts_remaining": max(0, game.max_daily_attempts - attempts_today),
            "assigned_protocol": assigned_protocol,
        },
    )


# ── Go / No-Go submit (JSON API) ──────────────────────────────────────────────

@router.post("/virtual-training/go-no-go/submit")
async def virtual_training_go_no_go_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Record a Go / No-Go Reaction attempt. Returns attempt_id, xp_awarded, is_valid."""
    guard = require_student_onboarding(user)
    if guard:
        return JSONResponse({"error": "onboarding required"}, status_code=403)

    game = VirtualTrainingService.get_game(db, "go_no_go")
    if game is None or not game.is_active:
        return JSONResponse({"error": "game not available"}, status_code=404)

    body = await request.json()
    _tctx = _extract_training_ctx(body)

    # Daily cap guard — training_local_date based (browser timezone aware)
    valid_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id             == user.id,
            VirtualTrainingAttempt.game_id              == game.id,
            VirtualTrainingAttempt.training_local_date  == _tctx["training_local_date"],
            VirtualTrainingAttempt.is_valid             == True,  # noqa: E712
        )
        .count()
    )
    if valid_today >= game.max_daily_attempts:
        return JSONResponse(
            {"error": "daily_cap", "message": "Daily attempt limit reached for this game."},
            status_code=429,
        )

    started_at_raw = body.get("started_at", "")
    idem_key = f"vt_gng_u{user.id}_{started_at_raw}"

    attempt = VirtualTrainingService.record_attempt(
        db=db,
        user_id=user.id,
        game=game,
        data=body,
        idempotency_key=idem_key,
        browser_timezone=_tctx["browser_timezone"],
        location_lat=_tctx["location_lat"],
        location_lng=_tctx["location_lng"],
        location_accuracy_m=_tctx["location_accuracy_m"],
        location_captured_at=_tctx["location_captured_at"],
    )

    db.commit()

    return JSONResponse({
        "attempt_id": attempt.id,
        "is_valid": attempt.is_valid,
        "invalid_reason": attempt.invalid_reason,
        "xp_awarded": attempt.xp_awarded,
        "skill_deltas": attempt.skill_deltas,
        "attempt_index_today": attempt.attempt_index_today,
        "score_normalized": attempt.score_normalized,
    })


# ── Go / No-Go result page ────────────────────────────────────────────────────

@router.get("/virtual-training/go-no-go/result/{attempt_id}",
            response_class=HTMLResponse)
async def virtual_training_go_no_go_result(
    attempt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Result screen for a completed Go / No-Go Reaction attempt."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    attempt = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.id == attempt_id,
            VirtualTrainingAttempt.user_id == user.id,
        )
        .first()
    )
    if attempt is None:
        all_games = VirtualTrainingService.get_hub_games(db)
        return templates.TemplateResponse(
            "virtual_training_hub.html",
            {
                "request": request,
                "user": user,
                **_spec_ctx(user, db),
                "all_games": all_games,
                "error": "Attempt not found.",
            },
        )

    game = db.query(VirtualTrainingGame).filter(
        VirtualTrainingGame.id == attempt.game_id
    ).first()

    # Skill delta breakdown — recompute per-skill scores from stored fields
    skill_scores: dict = {}
    signals_ctx: dict = {}
    if attempt.skill_deltas and game is not None:
        from ...services.virtual_training_metrics import VTSignalExtractor, VTSkillScorer
        cfg          = game.config or {}
        phase_config = cfg.get("phases", []) if isinstance(cfg, dict) else []
        data_for_signals = {
            "stimuli_count":     attempt.stimuli_count,
            "correct_count":     attempt.correct_count,
            "wrong_click_count": attempt.wrong_click_count,
            "error_count":       attempt.error_count,
            "avg_reaction_ms":   attempt.avg_reaction_ms,
            "raw_metrics":       attempt.raw_metrics,
        }
        signals = VTSignalExtractor.extract(data_for_signals, phase_config)
        skill_scores = VTSkillScorer.score_all(signals, game.skill_targets or {})
        signals_ctx = {
            "hit_rate":        round(signals.hit_rate * 100, 1),
            "wrong_rate":      round(signals.wrong_rate * 100, 1),
            "miss_rate":       round(signals.miss_rate * 100, 1),
            "speed_score":     round(signals.speed_score * 100, 1),
            "completion_rate": round(signals.completion_rate * 100, 1),
            "avg_reaction_ms": signals.avg_reaction_ms,
        }

    # Decompose raw_metrics — per_phase and per_stimulus (no per_color for Go/No-Go)
    per_phase: list = []
    per_stimulus: list = []
    late_summary: dict | None = None
    raw = attempt.raw_metrics
    if isinstance(raw, dict) and raw.get("v", 1) >= 1:
        per_phase    = raw.get("per_phase")    or []
        per_stimulus = raw.get("per_stimulus") or []
    if isinstance(raw, dict) and raw.get("v", 1) >= 2:
        late_summary = raw.get("late_summary") or None

    hand_profile: dict | None = None
    if isinstance(raw, dict) and int(raw.get("v", 1)) >= 3:
        hand_profile = raw.get("hand_profile") or None

    from ...models.user import UserRole
    is_admin = user.role == UserRole.ADMIN

    return templates.TemplateResponse(
        "virtual_training_go_no_go_result.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "attempt": attempt,
            "game": game,
            "skill_scores": skill_scores,
            "signals_ctx":  signals_ctx,
            "per_phase":     per_phase,
            "per_stimulus":  per_stimulus,
            "late_summary":  late_summary,
            "hand_profile":  hand_profile,
            "is_admin":      is_admin,
        },
    )


# ── Target Tracking game page ─────────────────────────────────────────────────

@router.get("/virtual-training/target-tracking", response_class=HTMLResponse)
async def virtual_training_target_tracking(
    request: Request,
    challenge_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Target Tracking game page — instruction + MOT arena (moving objects)."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    game = VirtualTrainingService.get_game(db, "target_tracking")
    if game is None or not game.is_active:
        all_games = VirtualTrainingService.get_hub_games(db)
        return templates.TemplateResponse(
            "virtual_training_hub.html",
            {
                "request": request,
                "user": user,
                **_spec_ctx(user, db),
                "all_games": all_games,
                "error": "Target Tracking is not available at this time.",
            },
        )

    # Challenge mode: load snapshot, guard NULL snapshot (no random fallback)
    challenge_snapshot: dict | None = None
    live_start_at: str | None = None
    if challenge_id is not None:
        ch = db.query(VirtualTrainingChallenge).filter(
            VirtualTrainingChallenge.id == challenge_id
        ).first()
        if ch is None or user.id not in (ch.challenger_id, ch.challenged_id):
            return RedirectResponse(
                url="/challenges?error=challenge_not_found", status_code=303
            )
        if ch.challenge_config_snapshot is None:
            return RedirectResponse(
                url="/challenges?error=challenge_snapshot_missing", status_code=303
            )
        challenge_snapshot = ch.challenge_config_snapshot
        if ch.live_start_at is not None:
            live_start_at = ch.live_start_at.isoformat()

    today_start = datetime.combine(
        datetime.now(timezone.utc).date(),
        datetime.min.time(),
    ).replace(tzinfo=timezone.utc)
    attempts_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id == user.id,
            VirtualTrainingAttempt.game_id == game.id,
            VirtualTrainingAttempt.started_at >= today_start,
            VirtualTrainingAttempt.is_valid == True,  # noqa: E712
        )
        .count()
    )

    expert_unlocked = VirtualTrainingService.is_expert_unlocked(db, user.id, game.id)

    return templates.TemplateResponse(
        "virtual_training_target_tracking.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "game": game,
            "attempts_today": attempts_today,
            "max_daily_attempts": game.max_daily_attempts,
            "attempts_remaining": max(0, game.max_daily_attempts - attempts_today),
            "expert_unlocked": expert_unlocked,
            "challenge_snapshot": challenge_snapshot,
            "live_start_at": live_start_at,
        },
    )


# ── Target Tracking submit (JSON API) ─────────────────────────────────────────

@router.post("/virtual-training/target-tracking/submit")
async def virtual_training_target_tracking_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Record a Target Tracking attempt. Returns attempt_id, xp_awarded, is_valid."""
    guard = require_student_onboarding(user)
    if guard:
        return JSONResponse({"error": "onboarding required"}, status_code=403)

    game = VirtualTrainingService.get_game(db, "target_tracking")
    if game is None or not game.is_active:
        return JSONResponse({"error": "game not available"}, status_code=404)

    body = await request.json()
    _tctx = _extract_training_ctx(body)

    # Challenge pre-validation (before daily cap / attempt recording)
    raw_challenge_id = body.get("challenge_id")
    challenge: VirtualTrainingChallenge | None = None
    if raw_challenge_id is not None:
        try:
            cid = int(raw_challenge_id)
        except (ValueError, TypeError):
            return JSONResponse({"error": "invalid_challenge_id"}, status_code=400)
        challenge, err = _validate_challenge_pre_submit(db, cid, user.id, game.id)
        if err is not None:
            return err

    # Difficulty guard — Expert requires unlock
    difficulty_level = str(body.get("difficulty_level", "easy")).lower()
    if difficulty_level not in ("easy", "medium", "hard", "expert"):
        difficulty_level = "easy"
    if difficulty_level == "expert":
        if not VirtualTrainingService.is_expert_unlocked(db, user.id, game.id):
            return JSONResponse(
                {"error": "expert_locked",
                 "message": "Expert requires 3 Hard attempts with 70%+ score."},
                status_code=403,
            )

    # Daily cap — training_local_date based (browser timezone aware)
    # Challenge attempts bypass the standalone daily cap.
    valid_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id             == user.id,
            VirtualTrainingAttempt.game_id              == game.id,
            VirtualTrainingAttempt.training_local_date  == _tctx["training_local_date"],
            VirtualTrainingAttempt.is_valid             == True,  # noqa: E712
        )
        .count()
    )
    if challenge is None and valid_today >= game.max_daily_attempts:
        return JSONResponse(
            {"error": "daily_cap", "message": "Daily attempt limit reached for this game."},
            status_code=429,
        )

    # Inject difficulty metadata into raw_metrics so record_attempt can read it
    diff_cfg = VirtualTrainingService.get_difficulty_config(game, difficulty_level)
    diff_mult = float(diff_cfg.get("difficulty_multiplier", 1.00))
    raw = body.get("raw_metrics")
    if isinstance(raw, dict):
        raw["difficulty_level"]      = difficulty_level
        raw["difficulty_multiplier"] = diff_mult
        raw["v"]                     = 3
        body["raw_metrics"]          = raw

    # Tag challenge attempts in raw_metrics for auditability (no DB column needed).
    # ALWAYS create a dict if raw_metrics is absent — ensures the tag is written
    # even when the client sends no raw_metrics payload.  VTC eligibility relies on
    # raw_metrics->>'attempt_source' = 'challenge' to exclude these from standalone counts.
    if challenge is not None:
        raw = body.get("raw_metrics")
        if not isinstance(raw, dict):
            raw = {}
        raw["attempt_source"] = "challenge"
        body["raw_metrics"]   = raw

    started_at_raw = body.get("started_at", "")
    idem_key = f"vt_tt_u{user.id}_{started_at_raw}"

    attempt = VirtualTrainingService.record_attempt(
        db=db,
        user_id=user.id,
        game=game,
        data=body,
        idempotency_key=idem_key,
        is_challenge=(challenge is not None),
        browser_timezone=_tctx["browser_timezone"],
        location_lat=_tctx["location_lat"],
        location_lng=_tctx["location_lng"],
        location_accuracy_m=_tctx["location_accuracy_m"],
        location_captured_at=_tctx["location_captured_at"],
    )

    # Challenge linking — only for valid attempts, inside the same transaction
    challenge_context: dict | None = None
    if challenge is not None and attempt.is_valid:
        challenge_context = _link_attempt_to_challenge(db, challenge, user.id, attempt)
    elif challenge is not None and not attempt.is_valid:
        challenge_context = {
            "challenge_id":   challenge.id,
            "status":         challenge.status.value,
            "note":           "invalid_attempt_not_linked",
            "retry_required": True,
            "invalid_reason": attempt.invalid_reason,
        }

    db.commit()

    resp: dict = {
        "attempt_id": attempt.id,
        "is_valid": attempt.is_valid,
        "invalid_reason": attempt.invalid_reason,
        "xp_awarded": attempt.xp_awarded,
        "skill_deltas": attempt.skill_deltas,
        "attempt_index_today": attempt.attempt_index_today,
        "score_normalized": attempt.score_normalized,
    }
    if challenge_context is not None:
        resp["challenge_context"] = challenge_context
    return JSONResponse(resp)


# ── Target Tracking result page ───────────────────────────────────────────────

@router.get("/virtual-training/target-tracking/result/{attempt_id}",
            response_class=HTMLResponse)
async def virtual_training_target_tracking_result(
    attempt_id: int,
    request: Request,
    challenge_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Result screen for a completed Target Tracking attempt."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    attempt = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.id == attempt_id,
            VirtualTrainingAttempt.user_id == user.id,
        )
        .first()
    )
    if attempt is None:
        all_games = VirtualTrainingService.get_hub_games(db)
        return templates.TemplateResponse(
            "virtual_training_hub.html",
            {
                "request": request,
                "user": user,
                **_spec_ctx(user, db),
                "all_games": all_games,
                "error": "Attempt not found.",
            },
        )

    game = db.query(VirtualTrainingGame).filter(
        VirtualTrainingGame.id == attempt.game_id
    ).first()

    # Skill delta breakdown — recompute per-skill scores from stored fields
    skill_scores: dict = {}
    signals_ctx: dict = {}
    if attempt.skill_deltas and game is not None:
        from ...services.virtual_training_metrics import VTSignalExtractor, VTSkillScorer
        cfg          = game.config or {}
        phase_config = cfg.get("phases", []) if isinstance(cfg, dict) else []
        data_for_signals = {
            "stimuli_count":     attempt.stimuli_count,
            "correct_count":     attempt.correct_count,
            "wrong_click_count": attempt.wrong_click_count,
            "error_count":       attempt.error_count,
            "avg_reaction_ms":   attempt.avg_reaction_ms,
            "raw_metrics":       attempt.raw_metrics,
        }
        signals = VTSignalExtractor.extract(data_for_signals, phase_config)
        skill_scores = VTSkillScorer.score_all(signals, game.skill_targets or {})
        signals_ctx = {
            "hit_rate":        round(signals.hit_rate * 100, 1),
            "wrong_rate":      round(signals.wrong_rate * 100, 1),
            "miss_rate":       round(signals.miss_rate * 100, 1),
            "speed_score":     round(signals.speed_score * 100, 1),
            "completion_rate": round(signals.completion_rate * 100, 1),
            "avg_reaction_ms": signals.avg_reaction_ms,
        }

    # Decompose raw_metrics — per_round, per_phase, difficulty info, flash summary
    per_phase: list = []
    per_round: list = []
    difficulty_level      = "easy"
    difficulty_multiplier = 1.00
    flash_summary: dict   = {}
    raw = attempt.raw_metrics
    if isinstance(raw, dict) and raw.get("v", 1) >= 1:
        per_phase = raw.get("per_phase") or []
        per_round = raw.get("per_round") or []
    if isinstance(raw, dict) and raw.get("v", 1) >= 3:
        difficulty_level      = raw.get("difficulty_level", "easy")
        difficulty_multiplier = float(raw.get("difficulty_multiplier", 1.00))
        ls = raw.get("late_summary") or {}
        if ls.get("total_flashes_shown"):
            flash_summary = {
                "total_flashes_shown":  ls.get("total_flashes_shown", 0),
                "taps_during_flash":    ls.get("taps_during_flash", 0),
                "flash_distraction_rate": ls.get("flash_distraction_rate", 0.0),
            }

    from ...models.user import UserRole
    is_admin = user.role == UserRole.ADMIN

    challenge_ctx = None
    if challenge_id is not None:
        challenge_ctx = _build_challenge_result_ctx(db, challenge_id, user.id, attempt_id)

    return templates.TemplateResponse(
        "virtual_training_target_tracking_result.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "attempt": attempt,
            "game": game,
            "skill_scores":          skill_scores,
            "signals_ctx":           signals_ctx,
            "per_phase":             per_phase,
            "per_round":             per_round,
            "is_admin":              is_admin,
            "difficulty_level":      difficulty_level,
            "difficulty_multiplier": difficulty_multiplier,
            "flash_summary":         flash_summary,
            "challenge_ctx":         challenge_ctx,
        },
    )


# ── History ───────────────────────────────────────────────────────────────────

@router.get("/virtual-training/history", response_class=HTMLResponse)
async def virtual_training_history(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Attempt history — last 20 valid attempts across all VT games."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    attempts = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id == user.id,
            VirtualTrainingAttempt.is_valid == True,  # noqa: E712
        )
        .order_by(VirtualTrainingAttempt.completed_at.desc())
        .limit(20)
        .all()
    )

    # Build game lookup map to avoid N+1 in template
    game_ids = {a.game_id for a in attempts}
    games = {
        g.id: g
        for g in db.query(VirtualTrainingGame)
        .filter(VirtualTrainingGame.id.in_(game_ids))
        .all()
    } if game_ids else {}

    return templates.TemplateResponse(
        "virtual_training_history.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "attempts": attempts,
            "games": games,
        },
    )


# ── Memory Sequence game page ─────────────────────────────────────────────────

@router.get("/virtual-training/memory-sequence", response_class=HTMLResponse)
async def virtual_training_memory_sequence(
    request: Request,
    challenge_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Memory Sequence game page — instruction + 3×4 grid recall game loop."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    game = VirtualTrainingService.get_game(db, "memory_sequence")
    if game is None or not game.is_active:
        all_games = VirtualTrainingService.get_hub_games(db)
        return templates.TemplateResponse(
            "virtual_training_hub.html",
            {
                "request": request,
                "user": user,
                **_spec_ctx(user, db),
                "all_games": all_games,
                "error": "Memory Sequence is not available at this time.",
            },
        )

    # Challenge mode: load snapshot, guard NULL snapshot (no random fallback)
    challenge_snapshot: dict | None = None
    live_start_at: str | None = None
    if challenge_id is not None:
        ch = db.query(VirtualTrainingChallenge).filter(
            VirtualTrainingChallenge.id == challenge_id
        ).first()
        if ch is None or user.id not in (ch.challenger_id, ch.challenged_id):
            return RedirectResponse(
                url="/challenges?error=challenge_not_found", status_code=303
            )
        if ch.challenge_config_snapshot is None:
            return RedirectResponse(
                url="/challenges?error=challenge_snapshot_missing", status_code=303
            )
        challenge_snapshot = ch.challenge_config_snapshot
        if ch.live_start_at is not None:
            live_start_at = ch.live_start_at.isoformat()

    today_start = datetime.combine(
        datetime.now(timezone.utc).date(),
        datetime.min.time(),
    ).replace(tzinfo=timezone.utc)
    attempts_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id == user.id,
            VirtualTrainingAttempt.game_id == game.id,
            VirtualTrainingAttempt.started_at >= today_start,
            VirtualTrainingAttempt.is_valid == True,  # noqa: E712
        )
        .count()
    )

    return templates.TemplateResponse(
        "virtual_training_memory_sequence.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "game": game,
            "attempts_today": attempts_today,
            "max_daily_attempts": game.max_daily_attempts,
            "attempts_remaining": max(0, game.max_daily_attempts - attempts_today),
            "challenge_snapshot": challenge_snapshot,
            "live_start_at": live_start_at,
        },
    )


# ── Memory Sequence submit (JSON API) ─────────────────────────────────────────

@router.post("/virtual-training/memory-sequence/submit")
async def virtual_training_memory_sequence_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Record a Memory Sequence attempt. Returns attempt_id, xp_awarded, is_valid."""
    guard = require_student_onboarding(user)
    if guard:
        return JSONResponse({"error": "onboarding required"}, status_code=403)

    game = VirtualTrainingService.get_game(db, "memory_sequence")
    if game is None or not game.is_active:
        return JSONResponse({"error": "game not available"}, status_code=404)

    body = await request.json()
    _tctx = _extract_training_ctx(body)

    # Challenge pre-validation (before daily cap / attempt recording)
    raw_challenge_id = body.get("challenge_id")
    challenge: VirtualTrainingChallenge | None = None
    if raw_challenge_id is not None:
        try:
            cid = int(raw_challenge_id)
        except (ValueError, TypeError):
            return JSONResponse({"error": "invalid_challenge_id"}, status_code=400)
        challenge, err = _validate_challenge_pre_submit(db, cid, user.id, game.id)
        if err is not None:
            return err

    # Daily cap — training_local_date based (browser timezone aware)
    # Challenge attempts bypass the standalone daily cap.
    valid_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id             == user.id,
            VirtualTrainingAttempt.game_id              == game.id,
            VirtualTrainingAttempt.training_local_date  == _tctx["training_local_date"],
            VirtualTrainingAttempt.is_valid             == True,  # noqa: E712
        )
        .count()
    )
    if challenge is None and valid_today >= game.max_daily_attempts:
        return JSONResponse(
            {"error": "daily_cap", "message": "Daily attempt limit reached for this game."},
            status_code=429,
        )

    # Tag challenge attempts in raw_metrics for auditability (no DB column needed).
    # ALWAYS create a dict if raw_metrics is absent — ensures the tag is written
    # even when the client sends no raw_metrics payload.  VTC eligibility relies on
    # raw_metrics->>'attempt_source' = 'challenge' to exclude these from standalone counts.
    if challenge is not None:
        raw = body.get("raw_metrics")
        if not isinstance(raw, dict):
            raw = {}
        raw["attempt_source"] = "challenge"
        body["raw_metrics"]   = raw

    started_at_raw = body.get("started_at", "")
    idem_key = f"vt_ms_u{user.id}_{started_at_raw}"

    attempt = VirtualTrainingService.record_attempt(
        db=db,
        user_id=user.id,
        game=game,
        data=body,
        idempotency_key=idem_key,
        is_challenge=(challenge is not None),
        browser_timezone=_tctx["browser_timezone"],
        location_lat=_tctx["location_lat"],
        location_lng=_tctx["location_lng"],
        location_accuracy_m=_tctx["location_accuracy_m"],
        location_captured_at=_tctx["location_captured_at"],
    )

    # Challenge linking — only for valid attempts, inside the same transaction
    challenge_context: dict | None = None
    if challenge is not None and attempt.is_valid:
        challenge_context = _link_attempt_to_challenge(db, challenge, user.id, attempt)
    elif challenge is not None and not attempt.is_valid:
        challenge_context = {
            "challenge_id":   challenge.id,
            "status":         challenge.status.value,
            "note":           "invalid_attempt_not_linked",
            "retry_required": True,
            "invalid_reason": attempt.invalid_reason,
        }

    db.commit()

    resp: dict = {
        "attempt_id": attempt.id,
        "is_valid": attempt.is_valid,
        "invalid_reason": attempt.invalid_reason,
        "xp_awarded": attempt.xp_awarded,
        "skill_deltas": attempt.skill_deltas,
        "attempt_index_today": attempt.attempt_index_today,
        "score_normalized": attempt.score_normalized,
    }
    if challenge_context is not None:
        resp["challenge_context"] = challenge_context
    return JSONResponse(resp)


# ── Memory Sequence result page ───────────────────────────────────────────────

@router.get("/virtual-training/memory-sequence/result/{attempt_id}",
            response_class=HTMLResponse)
async def virtual_training_memory_sequence_result(
    attempt_id: int,
    request: Request,
    challenge_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Result screen for a completed Memory Sequence attempt."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    attempt = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.id == attempt_id,
            VirtualTrainingAttempt.user_id == user.id,
        )
        .first()
    )
    if attempt is None:
        all_games = VirtualTrainingService.get_hub_games(db)
        return templates.TemplateResponse(
            "virtual_training_hub.html",
            {
                "request": request,
                "user": user,
                **_spec_ctx(user, db),
                "all_games": all_games,
                "error": "Attempt not found.",
            },
        )

    game = db.query(VirtualTrainingGame).filter(
        VirtualTrainingGame.id == attempt.game_id
    ).first()

    # Skill delta breakdown — recompute per-skill scores from stored fields
    skill_scores: dict = {}
    signals_ctx: dict = {}
    if attempt.skill_deltas and game is not None:
        from ...services.virtual_training_metrics import VTSignalExtractor, VTSkillScorer
        cfg          = game.config or {}
        phase_config = cfg.get("phases", []) if isinstance(cfg, dict) else []
        data_for_signals = {
            "stimuli_count":     attempt.stimuli_count,
            "correct_count":     attempt.correct_count,
            "wrong_click_count": attempt.wrong_click_count,
            "error_count":       attempt.error_count,
            "avg_reaction_ms":   attempt.avg_reaction_ms,
            "raw_metrics":       attempt.raw_metrics,
        }
        signals = VTSignalExtractor.extract(data_for_signals, phase_config)
        skill_scores = VTSkillScorer.score_all(signals, game.skill_targets or {})
        signals_ctx = {
            "hit_rate":        round(signals.hit_rate * 100, 1),
            "wrong_rate":      round(signals.wrong_rate * 100, 1),
            "miss_rate":       round(signals.miss_rate * 100, 1),
            "speed_score":     round(signals.speed_score * 100, 1),
            "completion_rate": round(signals.completion_rate * 100, 1),
            "avg_reaction_ms": signals.avg_reaction_ms,
        }

    # Decompose raw_metrics — per_round and per_phase (v=2, no hand_profile)
    per_phase: list = []
    per_round: list = []
    raw = attempt.raw_metrics
    if isinstance(raw, dict) and raw.get("v", 1) >= 1:
        per_phase = raw.get("per_phase") or []
        per_round = raw.get("per_round") or []

    # Best sequence reached
    best_sequence_length = 0
    if per_round:
        completed = [r for r in per_round if r.get("outcome") == "correct"]
        if completed:
            best_sequence_length = max(r.get("sequence_length", 0) for r in completed)
    elif game is not None:
        cfg = game.config or {}
        phases = cfg.get("phases", []) if isinstance(cfg, dict) else []
        if phases:
            best_sequence_length = phases[0].get("sequence_length", 3)

    from ...models.user import UserRole
    is_admin = user.role == UserRole.ADMIN

    challenge_ctx = None
    if challenge_id is not None:
        challenge_ctx = _build_challenge_result_ctx(db, challenge_id, user.id, attempt_id)

    return templates.TemplateResponse(
        "virtual_training_memory_sequence_result.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "attempt": attempt,
            "game": game,
            "skill_scores": skill_scores,
            "signals_ctx":  signals_ctx,
            "per_phase":    per_phase,
            "per_round":    per_round,
            "best_sequence_length": best_sequence_length,
            "is_admin":     is_admin,
            "challenge_ctx": challenge_ctx,
        },
    )


# ── Direction Swipe game page ──────────────────────────────────────────────────

@router.get("/virtual-training/direction-swipe", response_class=HTMLResponse)
async def virtual_training_direction_swipe(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Direction Swipe game page — swipe or press arrow keys to match the shown direction."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    game = VirtualTrainingService.get_game(db, "direction_swipe")
    all_games = VirtualTrainingService.get_hub_games(db)
    if game is None or not game.is_active:
        return templates.TemplateResponse(
            "virtual_training_hub.html",
            {
                "request": request,
                "user": user,
                **_spec_ctx(user, db),
                "all_games": all_games,
                "error": "Direction Swipe is not available at this time.",
            },
        )

    today_start = datetime.combine(
        datetime.now(timezone.utc).date(),
        datetime.min.time(),
    ).replace(tzinfo=timezone.utc)
    attempts_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id == user.id,
            VirtualTrainingAttempt.game_id == game.id,
            VirtualTrainingAttempt.started_at >= today_start,
            VirtualTrainingAttempt.is_valid == True,  # noqa: E712
        )
        .count()
    )

    return templates.TemplateResponse(
        "virtual_training_direction_swipe.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "game": game,
            "attempts_today": attempts_today,
            "max_daily_attempts": game.max_daily_attempts,
            "attempts_remaining": max(0, game.max_daily_attempts - attempts_today),
        },
    )


# ── Direction Swipe submit (JSON API) ─────────────────────────────────────────

@router.post("/virtual-training/direction-swipe/submit")
async def virtual_training_direction_swipe_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Record a Direction Swipe attempt. Returns attempt_id, xp_awarded, is_valid."""
    guard = require_student_onboarding(user)
    if guard:
        return JSONResponse({"error": "onboarding required"}, status_code=403)

    game = VirtualTrainingService.get_game(db, "direction_swipe")
    if game is None or not game.is_active:
        return JSONResponse({"error": "game not available"}, status_code=404)

    body = await request.json()
    _tctx = _extract_training_ctx(body)

    # Daily cap guard — training_local_date based (browser timezone aware)
    valid_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id             == user.id,
            VirtualTrainingAttempt.game_id              == game.id,
            VirtualTrainingAttempt.training_local_date  == _tctx["training_local_date"],
            VirtualTrainingAttempt.is_valid             == True,  # noqa: E712
        )
        .count()
    )
    if valid_today >= game.max_daily_attempts:
        return JSONResponse(
            {"error": "daily_cap", "message": "Daily attempt limit reached for this game."},
            status_code=429,
        )

    started_at_raw = body.get("started_at", "")
    idem_key = f"vt_ds_u{user.id}_{started_at_raw}"

    attempt = VirtualTrainingService.record_attempt(
        db=db,
        user_id=user.id,
        game=game,
        data=body,
        idempotency_key=idem_key,
        browser_timezone=_tctx["browser_timezone"],
        location_lat=_tctx["location_lat"],
        location_lng=_tctx["location_lng"],
        location_accuracy_m=_tctx["location_accuracy_m"],
        location_captured_at=_tctx["location_captured_at"],
    )

    db.commit()

    return JSONResponse({
        "attempt_id": attempt.id,
        "is_valid": attempt.is_valid,
        "invalid_reason": attempt.invalid_reason,
        "xp_awarded": attempt.xp_awarded,
        "skill_deltas": attempt.skill_deltas,
        "attempt_index_today": attempt.attempt_index_today,
        "score_normalized": attempt.score_normalized,
    })


# ── Direction Swipe result page ───────────────────────────────────────────────

@router.get("/virtual-training/direction-swipe/result/{attempt_id}",
            response_class=HTMLResponse)
async def virtual_training_direction_swipe_result(
    attempt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Result screen for a completed Direction Swipe attempt."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    attempt = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.id == attempt_id,
            VirtualTrainingAttempt.user_id == user.id,
        )
        .first()
    )
    if attempt is None:
        all_games = VirtualTrainingService.get_hub_games(db)
        return templates.TemplateResponse(
            "virtual_training_hub.html",
            {
                "request": request,
                "user": user,
                **_spec_ctx(user, db),
                "all_games": all_games,
                "error": "Attempt not found.",
            },
        )

    game = db.query(VirtualTrainingGame).filter(
        VirtualTrainingGame.id == attempt.game_id
    ).first()

    skill_scores: dict = {}
    signals_ctx: dict = {}
    if attempt.skill_deltas and game is not None:
        from ...services.virtual_training_metrics import VTSignalExtractor, VTSkillScorer
        cfg          = game.config or {}
        phase_config = cfg.get("phases", []) if isinstance(cfg, dict) else []
        data_for_signals = {
            "stimuli_count":     attempt.stimuli_count,
            "correct_count":     attempt.correct_count,
            "wrong_click_count": attempt.wrong_click_count,
            "error_count":       attempt.error_count,
            "avg_reaction_ms":   attempt.avg_reaction_ms,
            "raw_metrics":       attempt.raw_metrics,
        }
        signals = VTSignalExtractor.extract(data_for_signals, phase_config)
        skill_scores = VTSkillScorer.score_all(signals, game.skill_targets or {})
        signals_ctx = {
            "hit_rate":        round(signals.hit_rate * 100, 1),
            "wrong_rate":      round(signals.wrong_rate * 100, 1),
            "miss_rate":       round(signals.miss_rate * 100, 1),
            "speed_score":     round(signals.speed_score * 100, 1),
            "completion_rate": round(signals.completion_rate * 100, 1),
            "avg_reaction_ms": signals.avg_reaction_ms,
        }

    per_phase: list = []
    per_stimulus: list = []
    late_summary: dict | None = None
    raw = attempt.raw_metrics
    if isinstance(raw, dict) and raw.get("v", 1) >= 1:
        per_phase    = raw.get("per_phase")    or []
        per_stimulus = raw.get("per_stimulus") or []
    if isinstance(raw, dict) and raw.get("v", 1) >= 2:
        late_summary = raw.get("late_summary") or None

    from ...models.user import UserRole
    is_admin = user.role == UserRole.ADMIN

    return templates.TemplateResponse(
        "virtual_training_direction_swipe_result.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "attempt": attempt,
            "game": game,
            "skill_scores": skill_scores,
            "signals_ctx":  signals_ctx,
            "per_phase":    per_phase,
            "per_stimulus": per_stimulus,
            "late_summary": late_summary,
            "is_admin":     is_admin,
        },
    )


# ── Number-Color Conflict game page ──────────────────────────────────────────

@router.get("/virtual-training/number-color-conflict", response_class=HTMLResponse)
async def virtual_training_number_color_conflict(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Number-Color Conflict game page — instructions + Vanilla JS game loop."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    game = VirtualTrainingService.get_game(db, "number_color_conflict")
    if game is None or not game.is_active:
        all_games = VirtualTrainingService.get_hub_games(db)
        return templates.TemplateResponse(
            "virtual_training_hub.html",
            {
                "request": request,
                "user": user,
                **_spec_ctx(user, db),
                "all_games": all_games,
                "error": "Number-Color Conflict is not available at this time.",
            },
        )

    today_start = datetime.combine(
        datetime.now(timezone.utc).date(),
        datetime.min.time(),
    ).replace(tzinfo=timezone.utc)
    attempts_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id == user.id,
            VirtualTrainingAttempt.game_id == game.id,
            VirtualTrainingAttempt.started_at >= today_start,
            VirtualTrainingAttempt.is_valid == True,  # noqa: E712
        )
        .count()
    )

    assigned_protocol = VirtualTrainingService.assign_protocol(db, user.id, game.id)

    return templates.TemplateResponse(
        "virtual_training_number_color_conflict.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "game": game,
            "attempts_today": attempts_today,
            "max_daily_attempts": game.max_daily_attempts,
            "attempts_remaining": max(0, game.max_daily_attempts - attempts_today),
            "assigned_protocol": assigned_protocol,
        },
    )


# ── Number-Color Conflict submit (JSON API) ───────────────────────────────────

@router.post("/virtual-training/number-color-conflict/submit")
async def virtual_training_number_color_conflict_submit(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Record a Number-Color Conflict attempt. Returns attempt_id, xp_awarded, is_valid."""
    guard = require_student_onboarding(user)
    if guard:
        return JSONResponse({"error": "onboarding required"}, status_code=403)

    game = VirtualTrainingService.get_game(db, "number_color_conflict")
    if game is None or not game.is_active:
        return JSONResponse({"error": "game not available"}, status_code=404)

    body = await request.json()
    _tctx = _extract_training_ctx(body)

    # Daily cap guard — training_local_date based (browser timezone aware)
    valid_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id             == user.id,
            VirtualTrainingAttempt.game_id              == game.id,
            VirtualTrainingAttempt.training_local_date  == _tctx["training_local_date"],
            VirtualTrainingAttempt.is_valid             == True,  # noqa: E712
        )
        .count()
    )
    if valid_today >= game.max_daily_attempts:
        return JSONResponse(
            {"error": "daily_cap", "message": "Daily attempt limit reached for this game."},
            status_code=429,
        )

    started_at_raw = body.get("started_at", "")
    idem_key = f"vt_ncc_u{user.id}_{started_at_raw}"

    attempt = VirtualTrainingService.record_attempt(
        db=db,
        user_id=user.id,
        game=game,
        data=body,
        idempotency_key=idem_key,
        browser_timezone=_tctx["browser_timezone"],
        location_lat=_tctx["location_lat"],
        location_lng=_tctx["location_lng"],
        location_accuracy_m=_tctx["location_accuracy_m"],
        location_captured_at=_tctx["location_captured_at"],
    )

    db.commit()

    return JSONResponse({
        "attempt_id": attempt.id,
        "is_valid": attempt.is_valid,
        "invalid_reason": attempt.invalid_reason,
        "xp_awarded": attempt.xp_awarded,
        "skill_deltas": attempt.skill_deltas,
        "attempt_index_today": attempt.attempt_index_today,
        "score_normalized": attempt.score_normalized,
    })


# ── Number-Color Conflict result page ────────────────────────────────────────

@router.get("/virtual-training/number-color-conflict/result/{attempt_id}",
            response_class=HTMLResponse)
async def virtual_training_number_color_conflict_result(
    attempt_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_web),
):
    """Result screen for a completed Number-Color Conflict attempt."""
    guard = require_student_onboarding(user)
    if guard:
        return guard

    attempt = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.id == attempt_id,
            VirtualTrainingAttempt.user_id == user.id,
        )
        .first()
    )
    if attempt is None:
        all_games = VirtualTrainingService.get_hub_games(db)
        return templates.TemplateResponse(
            "virtual_training_hub.html",
            {
                "request": request,
                "user": user,
                **_spec_ctx(user, db),
                "all_games": all_games,
                "error": "Attempt not found.",
            },
        )

    game = db.query(VirtualTrainingGame).filter(
        VirtualTrainingGame.id == attempt.game_id
    ).first()

    skill_scores: dict = {}
    signals_ctx: dict = {}
    if attempt.skill_deltas and game is not None:
        from ...services.virtual_training_metrics import VTSignalExtractor, VTSkillScorer
        cfg          = game.config or {}
        phase_config = cfg.get("phases", []) if isinstance(cfg, dict) else []
        data_for_signals = {
            "stimuli_count":     attempt.stimuli_count,
            "correct_count":     attempt.correct_count,
            "wrong_click_count": attempt.wrong_click_count,
            "error_count":       attempt.error_count,
            "avg_reaction_ms":   attempt.avg_reaction_ms,
            "raw_metrics":       attempt.raw_metrics,
        }
        signals = VTSignalExtractor.extract(data_for_signals, phase_config)
        skill_scores = VTSkillScorer.score_all(signals, game.skill_targets or {})
        signals_ctx = {
            "hit_rate":        round(signals.hit_rate * 100, 1),
            "wrong_rate":      round(signals.wrong_rate * 100, 1),
            "miss_rate":       round(signals.miss_rate * 100, 1),
            "speed_score":     round(signals.speed_score * 100, 1),
            "completion_rate": round(signals.completion_rate * 100, 1),
            "avg_reaction_ms": signals.avg_reaction_ms,
        }

    per_phase: list = []
    per_stimulus: list = []
    late_summary: dict | None = None
    raw = attempt.raw_metrics
    if isinstance(raw, dict) and raw.get("v", 1) >= 1:
        per_phase    = raw.get("per_phase")    or []
        per_stimulus = raw.get("per_stimulus") or []
    if isinstance(raw, dict) and raw.get("v", 1) >= 2:
        late_summary = raw.get("late_summary") or None

    hand_profile: dict | None = None
    if isinstance(raw, dict) and int(raw.get("v", 1)) >= 3:
        hand_profile = raw.get("hand_profile") or None

    from ...models.user import UserRole
    is_admin = user.role == UserRole.ADMIN

    return templates.TemplateResponse(
        "virtual_training_number_color_conflict_result.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "attempt": attempt,
            "game": game,
            "skill_scores": skill_scores,
            "signals_ctx":  signals_ctx,
            "per_phase":     per_phase,
            "per_stimulus":  per_stimulus,
            "late_summary":  late_summary,
            "hand_profile":  hand_profile,
            "is_admin":      is_admin,
        },
    )
