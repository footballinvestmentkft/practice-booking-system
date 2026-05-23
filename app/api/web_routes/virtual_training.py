"""Virtual Training web routes — Phase 2 Color Reaction MVP."""
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_web
from ...models.user import User
from ...models.virtual_training import VirtualTrainingAttempt, VirtualTrainingGame
from ...services.virtual_training_service import VirtualTrainingService
from .helpers import require_student_onboarding
from .student_features import _spec_ctx
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(tags=["virtual-training"])


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
    return templates.TemplateResponse(
        "virtual_training_hub.html",
        {
            "request": request,
            "user": user,
            **_spec_ctx(user, db),
            "all_games": all_games,
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
            "is_admin":      is_admin,
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
