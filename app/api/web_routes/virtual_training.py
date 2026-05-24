"""Virtual Training web routes — Phase 2 Color Reaction MVP."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.notification import NotificationType
from ...models.user import User
from ...models.virtual_training import VirtualTrainingAttempt, VirtualTrainingGame
from ...models.vt_challenge import ChallengeStatus, VirtualTrainingChallenge
from ...services import notification_service
from ...services.virtual_training_service import VirtualTrainingService
from .helpers import require_student_onboarding
from .student_features import _spec_ctx
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["virtual-training"])


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

    if challenge.status != ChallengeStatus.ACCEPTED:
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
            link="/friends",
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

    is_winner = (winner_id == user_id) if challenge.status == ChallengeStatus.COMPLETED else None
    is_draw_ctx = is_draw if challenge.status == ChallengeStatus.COMPLETED else None

    return {
        "challenge_id": challenge.id,
        "status": challenge.status.value,
        "is_winner": is_winner,
        "is_draw": is_draw_ctx,
    }


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

    # Daily cap guard
    today_start = datetime.combine(
        datetime.now(timezone.utc).date(),
        datetime.min.time(),
    ).replace(tzinfo=timezone.utc)
    valid_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id == user.id,
            VirtualTrainingAttempt.game_id == game.id,
            VirtualTrainingAttempt.started_at >= today_start,
            VirtualTrainingAttempt.is_valid == True,  # noqa: E712
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

    today_start = datetime.combine(
        datetime.now(timezone.utc).date(),
        datetime.min.time(),
    ).replace(tzinfo=timezone.utc)
    valid_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id == user.id,
            VirtualTrainingAttempt.game_id == game.id,
            VirtualTrainingAttempt.started_at >= today_start,
            VirtualTrainingAttempt.is_valid == True,  # noqa: E712
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

    today_start = datetime.combine(
        datetime.now(timezone.utc).date(),
        datetime.min.time(),
    ).replace(tzinfo=timezone.utc)
    valid_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id == user.id,
            VirtualTrainingAttempt.game_id == game.id,
            VirtualTrainingAttempt.started_at >= today_start,
            VirtualTrainingAttempt.is_valid == True,  # noqa: E712
        )
        .count()
    )
    if valid_today >= game.max_daily_attempts:
        # challenge not mutated — guard returned before any write
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

    started_at_raw = body.get("started_at", "")
    idem_key = f"vt_tt_u{user.id}_{started_at_raw}"

    attempt = VirtualTrainingService.record_attempt(
        db=db,
        user_id=user.id,
        game=game,
        data=body,
        idempotency_key=idem_key,
    )

    # Challenge linking — only for valid attempts, inside the same transaction
    challenge_context: dict | None = None
    if challenge is not None and attempt.is_valid:
        challenge_context = _link_attempt_to_challenge(db, challenge, user.id, attempt)
    elif challenge is not None and not attempt.is_valid:
        challenge_context = {
            "challenge_id": challenge.id,
            "status": challenge.status.value,
            "note": "invalid_attempt_not_linked",
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

    today_start = datetime.combine(
        datetime.now(timezone.utc).date(),
        datetime.min.time(),
    ).replace(tzinfo=timezone.utc)
    valid_today = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id == user.id,
            VirtualTrainingAttempt.game_id == game.id,
            VirtualTrainingAttempt.started_at >= today_start,
            VirtualTrainingAttempt.is_valid == True,  # noqa: E712
        )
        .count()
    )
    if valid_today >= game.max_daily_attempts:
        # challenge not mutated — guard returned before any write
        return JSONResponse(
            {"error": "daily_cap", "message": "Daily attempt limit reached for this game."},
            status_code=429,
        )

    started_at_raw = body.get("started_at", "")
    idem_key = f"vt_ms_u{user.id}_{started_at_raw}"

    attempt = VirtualTrainingService.record_attempt(
        db=db,
        user_id=user.id,
        game=game,
        data=body,
        idempotency_key=idem_key,
    )

    # Challenge linking — only for valid attempts, inside the same transaction
    challenge_context: dict | None = None
    if challenge is not None and attempt.is_valid:
        challenge_context = _link_attempt_to_challenge(db, challenge, user.id, attempt)
    elif challenge is not None and not attempt.is_valid:
        challenge_context = {
            "challenge_id": challenge.id,
            "status": challenge.status.value,
            "note": "invalid_attempt_not_linked",
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
        },
    )
