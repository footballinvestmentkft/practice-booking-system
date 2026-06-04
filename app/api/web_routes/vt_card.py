"""Virtual Training Card routes.

Routes:
  GET /virtual-training/card/preview          → single-game card HTML (eligibility-gated)
  GET /virtual-training/card/export           → single-game card PNG (eligibility-gated)
  GET /virtual-training/card/reward/preview   → reward card HTML (tier eligibility-gated)
  GET /virtual-training/card/reward/export    → reward card PNG (tier eligibility-gated)

Eligibility:
  - Single-game: user must have completed >= game.max_daily_attempts valid standalone
    attempts for the requested game on the requested date.
  - Reward: user must have completed >= tier distinct games today (each game fully
    completed, standalone only).
  - No credit/CDO ownership required — cards are earned by playing.

Platforms:
  - Single-game: vt_landscape (1280×720) | vt_portrait (1080×1920)
  - Reward:      vt_reward_landscape (1280×720) | vt_reward_portrait (1080×1920)
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ...database import get_db
from ...dependencies import get_current_user_optional, get_current_user_web
from ...models.user import User
from ...models.virtual_training import VirtualTrainingAttempt, VirtualTrainingGame
from ...services import card_export_service as _export_svc
from ...services.card_constants import VT_CARD_PLATFORMS, VT_REWARD_CARD_PLATFORMS
from ...services.card_design_service import is_design_accessible as _is_accessible
from ...services.vt_card_eligibility import (
    REWARD_TIERS,
    check_reward_eligibility,
    check_single_game_eligibility,
    get_completed_game_ids,
)

router = APIRouter()
templates = Jinja2Templates(
    directory="app/templates",
)

# ── Template map ───────────────────────────────────────────────────────────────

_VT_TEMPLATE: dict[str, str] = {
    "vt_landscape": "public/export/vt/landscape.html",
    "vt_portrait":  "public/export/vt/portrait.html",
}
_VT_REWARD_TEMPLATE: dict[str, str] = {
    "vt_reward_landscape": "public/export/vt_reward/landscape.html",
    "vt_reward_portrait":  "public/export/vt_reward/portrait.html",
}


# ── Context helpers ────────────────────────────────────────────────────────────

def _display_name(user: User) -> str:
    return user.nickname if (user and getattr(user, "nickname", None)) else user.email


def _player_display(db: Session, user: User) -> dict:
    """Return player identity: name, photo_url, overall OVR, primary position."""
    from ...models.license import UserLicense  # noqa: PLC0415
    from ...utils.football_positions import position_short  # noqa: PLC0415

    name = _display_name(user)
    lic = db.query(UserLicense).filter(
        UserLicense.user_id == user.id,
        UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
    ).first()
    if lic is None:
        return {"name": name, "photo_url": None, "overall": None, "primary_pos": None}

    football_skills = lic.football_skills or {}
    levels = [
        v["current_level"]
        for v in football_skills.values()
        if isinstance(v, dict) and v.get("current_level") is not None
    ]
    overall = round(sum(levels) / len(levels), 1) if levels else None
    positions = (lic.motivation_scores or {}).get("positions", [])
    return {
        "name":        name,
        "photo_url":   lic.player_card_photo_url,
        "overall":     overall,
        "primary_pos": position_short(positions[0]) if positions else None,
    }


def _get_standalone_attempts(
    db: Session, user_id: int, game_id: int, day: date, limit: int = 5,
) -> list[VirtualTrainingAttempt]:
    """Return ordered standalone attempts for a user+game+training day.

    Uses training_local_date (browser-timezone-aware) as the day boundary.
    Ordering: attempt_index_today (nulls last) → completed_at → started_at.
    Only valid, non-challenge attempts for the exact game are returned.
    Capped at `limit` (= game.max_daily_attempts) so a 6th attempt is excluded.
    """
    return (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id             == user_id,
            VirtualTrainingAttempt.game_id              == game_id,
            VirtualTrainingAttempt.is_valid             == True,           # noqa: E712
            VirtualTrainingAttempt.training_local_date  == day,
            or_(
                VirtualTrainingAttempt.raw_metrics.is_(None),
                VirtualTrainingAttempt.raw_metrics["attempt_source"].astext != "challenge",
            ),
        )
        .order_by(
            VirtualTrainingAttempt.attempt_index_today.asc().nullslast(),
            VirtualTrainingAttempt.completed_at.asc().nullslast(),
            VirtualTrainingAttempt.started_at.asc(),
        )
        .limit(limit)
        .all()
    )


def _compute_vtc_stats(attempts: list[VirtualTrainingAttempt]) -> dict:
    """Pure function: compute all derived stats from an ordered attempt list."""
    _empty = {
        "best_score": None, "avg_score": None,
        "score_trend": None, "score_consistency": None,
        "avg_reaction_ms": None, "xp_earned": 0, "top_skill_delta": None,
        "attempt_chart_points": [], "attempts": [],
    }
    if not attempts:
        return _empty

    scores    = [a.score_normalized for a in attempts if a.score_normalized is not None]
    reactions = [a.avg_reaction_ms   for a in attempts if a.avg_reaction_ms   is not None]

    best_score = round(max(scores), 1)          if scores else None
    avg_score  = round(sum(scores) / len(scores), 1) if scores else None

    if len(scores) >= 4:
        score_trend = round((scores[-2] + scores[-1]) / 2 - (scores[0] + scores[1]) / 2, 1)
    elif len(scores) >= 2:
        score_trend = round(scores[-1] - scores[0], 1)
    else:
        score_trend = 0.0

    score_consistency = (
        round(100.0 - (max(scores) - min(scores)), 1) if len(scores) >= 2 else 100.0
    )

    avg_reaction_ms = round(sum(reactions) / len(reactions)) if reactions else None
    xp_earned       = sum(a.xp_awarded or 0 for a in attempts)

    agg_deltas: dict[str, float] = {}
    for a in attempts:
        for skill, delta in (a.skill_deltas or {}).items():
            agg_deltas[skill] = agg_deltas.get(skill, 0.0) + float(delta)
    top_delta = None
    if agg_deltas:
        top_key   = max(agg_deltas, key=lambda k: abs(agg_deltas[k]))
        top_delta = {"name": top_key.replace("_", " ").title(), "delta": agg_deltas[top_key]}

    chart_points = []
    attempt_list = []
    for i, a in enumerate(attempts, start=1):
        s   = a.score_normalized
        idx = a.attempt_index_today if a.attempt_index_today else i
        chart_points.append({
            "index":       idx,
            "score":       round(s, 1) if s is not None else None,
            "label":       str(round(s)) if s is not None else "—",
            "reaction_ms": a.avg_reaction_ms,
        })
        attempt_list.append({
            "index":       idx,
            "score":       round(s, 1) if s is not None else None,
            "reaction_ms": a.avg_reaction_ms,
            "xp":          a.xp_awarded or 0,
            "correct":     a.correct_count,
            "errors":      a.error_count,
        })

    return {
        "best_score":           best_score,
        "avg_score":            avg_score,
        "score_trend":          score_trend,
        "score_consistency":    score_consistency,
        "avg_reaction_ms":      avg_reaction_ms,
        "xp_earned":            xp_earned,
        "top_skill_delta":      top_delta,
        "attempt_chart_points": chart_points,
        "attempts":             attempt_list,
    }


def _vtc_mood_slots(
    avg_score: float | None,
    score_consistency: float | None,
    score_trend: float | None,
) -> tuple[str, str, str]:
    """Return (primary_slot, alt_slot, reason) based on multi-factor performance.

    Thresholds (evaluated in order):
      avg>=80, consistency>=70 → celebration / happy_smile
      avg>=80, consistency<70  → proud / happy_smile
      avg 65-79, trend>5       → proud / confident
      avg 65-79, trend<=5      → confident / proud
      avg 45-64, trend>=-5     → focused_ready / confident
      avg 45-64, trend<-5      → focused_ready / neutral
      avg<45, trend>5          → focused_ready / neutral
      avg<45, trend<=5         → sad_disappointed / neutral
      no score                 → neutral / neutral
    """
    if avg_score is None:
        return ("mood_intro_neutral", "mood_intro_neutral", "no_score_data")

    cons  = score_consistency if score_consistency is not None else 100.0
    trend = score_trend       if score_trend       is not None else 0.0

    if avg_score >= 80:
        if cons >= 70:
            return ("mood_celebration",      "mood_happy_smile",   "high_avg_consistent")
        return     ("mood_proud",            "mood_happy_smile",   "high_avg_inconsistent")
    if avg_score >= 65:
        if trend > 5:
            return ("mood_proud",            "mood_confident",     "good_avg_improving")
        return     ("mood_confident",        "mood_proud",         "good_avg_stable")
    if avg_score >= 45:
        if trend >= -5:
            return ("mood_focused_ready",    "mood_confident",     "mid_avg_stable")
        return     ("mood_focused_ready",    "mood_intro_neutral", "mid_avg_declining")
    if trend > 5:
        return     ("mood_focused_ready",    "mood_intro_neutral", "low_avg_improving")
    return         ("mood_sad_disappointed", "mood_intro_neutral", "low_avg_declining")


def _get_vtc_mood_photo_url(
    db: Session,
    user_id: int,
    primary_slot: str,
    alt_slot: str,
    player_photo_url: str | None,
) -> str | None:
    """Return mood photo URL via 6-step fallback chain:
    1. primary processed_png_url
    2. primary original_url
    3. alt processed_png_url
    4. alt original_url
    5. player_card_photo_url
    6. None → template renders initials
    """
    from ...models.user_mood_photos import UserMoodPhoto  # noqa: PLC0415

    for slot in (primary_slot, alt_slot):
        photo = db.query(UserMoodPhoto).filter_by(user_id=user_id, slot=slot).first()
        if photo:
            if photo.processed_png_url:
                return photo.processed_png_url
            if photo.original_url:
                return photo.original_url
    return player_photo_url


def _daily_attempt_stats(db: Session, user_id: int, game_id: int, day: date) -> dict:
    """Aggregation helper for reward card — uses training_local_date boundary."""
    attempts = (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id             == user_id,
            VirtualTrainingAttempt.game_id              == game_id,
            VirtualTrainingAttempt.is_valid             == True,  # noqa: E712
            VirtualTrainingAttempt.training_local_date  == day,
            or_(
                VirtualTrainingAttempt.raw_metrics.is_(None),
                VirtualTrainingAttempt.raw_metrics["attempt_source"].astext != "challenge",
            ),
        )
        .all()
    )

    if not attempts:
        return {"best_score": None, "avg_reaction_ms": None, "xp_earned": 0, "top_skill_delta": None}

    scores    = [a.score_normalized for a in attempts if a.score_normalized is not None]
    reactions = [a.avg_reaction_ms   for a in attempts if a.avg_reaction_ms   is not None]
    xp_total  = sum(a.xp_awarded for a in attempts)

    agg_deltas: dict[str, float] = {}
    for a in attempts:
        for skill, delta in (a.skill_deltas or {}).items():
            agg_deltas[skill] = agg_deltas.get(skill, 0.0) + float(delta)

    top_delta = None
    if agg_deltas:
        top_key   = max(agg_deltas, key=lambda k: abs(agg_deltas[k]))
        top_delta = {"name": top_key.replace("_", " ").title(), "delta": agg_deltas[top_key]}

    return {
        "best_score":      round(max(scores), 1) if scores else None,
        "avg_reaction_ms": round(sum(reactions) / len(reactions)) if reactions else None,
        "xp_earned":       xp_total,
        "top_skill_delta": top_delta,
    }


def _reward_daily_stats(db: Session, user_id: int, completed_game_ids: list[int], day: date) -> dict:
    """Return completed game names and total XP for the reward card context."""
    games = (
        db.query(VirtualTrainingGame)
        .filter(VirtualTrainingGame.id.in_(completed_game_ids))
        .all()
    ) if completed_game_ids else []
    game_name_map = {g.id: g.name for g in games}
    completed_game_names = [game_name_map[gid] for gid in completed_game_ids if gid in game_name_map]

    # Total XP: sum from ALL standalone attempts on this training day (across all completed games)
    if completed_game_ids:
        total_xp = (
            db.query(VirtualTrainingAttempt.xp_awarded)
            .filter(
                VirtualTrainingAttempt.user_id             == user_id,
                VirtualTrainingAttempt.game_id.in_(completed_game_ids),
                VirtualTrainingAttempt.is_valid             == True,  # noqa: E712
                VirtualTrainingAttempt.training_local_date  == day,
                or_(
                    VirtualTrainingAttempt.raw_metrics.is_(None),
                    VirtualTrainingAttempt.raw_metrics["attempt_source"].astext != "challenge",
                ),
            )
            .all()
        )
        xp_sum = sum(row[0] for row in total_xp if row[0])
    else:
        xp_sum = 0

    return {"completed_game_names": completed_game_names, "total_xp": xp_sum}


# ── Render token auth helpers ─────────────────────────────────────────────────

def _resolve_vt_render_token(token: str, db: Session) -> "User | None":
    """Validate a vtc_render JWT and return the corresponding User, or None."""
    try:
        from jose import jwt as _jwt  # noqa: PLC0415
        from ...config import settings  # noqa: PLC0415
        payload = _jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        if payload.get("purpose") != "vtc_render":
            return None
        user_id = int(payload.get("sub") or 0)
        if not user_id:
            return None
        return db.query(User).filter(User.id == user_id, User.is_active == True).first()  # noqa: E712
    except Exception:  # noqa: BLE001
        return None


def _parse_date(date_str: str | None) -> date:
    if date_str is None:
        return datetime.now(timezone.utc).date()
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid date format: {date_str!r}. Expected YYYY-MM-DD.",
        )


# ── Single-game preview ───────────────────────────────────────────────────────

@router.get("/virtual-training/card/preview", response_class=HTMLResponse)
async def vt_card_preview(
    request: Request,
    game_id: int           = Query(..., description="VirtualTrainingGame.id"),
    platform: str          = Query(..., description="vt_landscape | vt_portrait"),
    date_str: str | None   = Query(default=None, alias="date", description="YYYY-MM-DD"),
    render_token: str | None = Query(default=None),
    db: Session            = Depends(get_db),
    user: "User | None"    = Depends(get_current_user_optional),
):
    if render_token is not None:
        token_user = _resolve_vt_render_token(render_token, db)
        if token_user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired render token")
        user = token_user
    elif user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if platform not in VT_CARD_PLATFORMS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported platform: {platform!r}. Valid: {sorted(VT_CARD_PLATFORMS)}",
        )

    day = _parse_date(date_str)
    eligible, count, required = check_single_game_eligibility(db, user.id, game_id, day)
    if not eligible:
        raise HTTPException(
            status_code=403,
            detail=f"Not eligible: {count}/{required} standalone attempts completed for game {game_id}.",
        )

    game = db.query(VirtualTrainingGame).filter(
        VirtualTrainingGame.id == game_id,
        VirtualTrainingGame.is_active == True,  # noqa: E712
    ).first()
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")

    player   = _player_display(db, user)
    attempts = _get_standalone_attempts(db, user.id, game_id, day, limit=required)
    stats    = _compute_vtc_stats(attempts)

    primary_slot, alt_slot, mood_reason = _vtc_mood_slots(
        avg_score=stats["avg_score"],
        score_consistency=stats["score_consistency"],
        score_trend=stats["score_trend"],
    )
    mood_photo_url = _get_vtc_mood_photo_url(
        db, user.id, primary_slot, alt_slot, player["photo_url"],
    )

    ctx = {
        "request":            request,
        "game":               game,
        "attempt_date":       day.isoformat(),
        "completed_count":    count,
        "max_attempts":       required,
        "platform":           platform,
        # player identity
        "player_name":        player["name"],
        "player_overall":     player["overall"],
        "player_photo_url":   player["photo_url"],
        "player_primary_pos": player["primary_pos"],
        # mood photo
        "mood_photo_url":     mood_photo_url,
        "mood_slot":          primary_slot,
        "mood_reason":        mood_reason,
        # aggregated stats (backward-compat keys)
        "best_score":         stats["best_score"],
        "avg_reaction_ms":    stats["avg_reaction_ms"],
        "xp_earned":          stats["xp_earned"],
        "top_skill_delta":    stats["top_skill_delta"],
        # new stats
        "avg_score":          stats["avg_score"],
        "score_trend":        stats["score_trend"],
        "score_consistency":  stats["score_consistency"],
        # chart + attempt list
        "attempt_chart_points": stats["attempt_chart_points"],
        "attempts":             stats["attempts"],
    }
    return templates.TemplateResponse(_VT_TEMPLATE[platform], ctx)


# ── Single-game export ────────────────────────────────────────────────────────

@router.get("/virtual-training/card/export")
async def vt_card_export(
    request: Request,
    game_id: int         = Query(..., description="VirtualTrainingGame.id"),
    platform: str        = Query(..., description="vt_landscape | vt_portrait"),
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD"),
    db: Session          = Depends(get_db),
    user: User           = Depends(get_current_user_web),
):
    from ...config import settings  # noqa: PLC0415
    from ...core.auth import create_vt_card_render_token  # noqa: PLC0415

    if platform not in VT_CARD_PLATFORMS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported platform: {platform!r}. Valid: {sorted(VT_CARD_PLATFORMS)}",
        )

    # Ownership guard — CDO row required for this format (same pattern as Challenge Card).
    # Preview is performance-only gated; export requires both ownership AND performance.
    if not _is_accessible(db, user.id, "virtual_training_card", platform):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Virtual Training Card format {platform!r} not owned. "
                "Purchase it at /shop?type=virtual_training_card"
            ),
        )

    day = _parse_date(date_str)
    eligible, count, required = check_single_game_eligibility(db, user.id, game_id, day)
    if not eligible:
        raise HTTPException(
            status_code=403,
            detail=f"Not eligible: {count}/{required} standalone attempts completed for game {game_id}.",
        )

    client_ip = request.client.host if request.client else "unknown"
    rate_key  = f"vt_card:{game_id}:{user.id}:{client_ip}"
    if not _export_svc.check_export_rate_limit(rate_key):
        raise HTTPException(
            status_code=429,
            detail="Export rate limit exceeded (5 per minute). Please wait before exporting again.",
        )

    token = create_vt_card_render_token(user.id)
    date_param = f"&date={date_str}" if date_str else ""
    render_url = (
        f"http://127.0.0.1:{settings.APP_INTERNAL_PORT}"
        f"/virtual-training/card/preview"
        f"?game_id={game_id}&platform={platform}&render_token={token}{date_param}"
    )

    try:
        png_bytes = await asyncio.to_thread(
            _export_svc._sync_take_screenshot, render_url, platform
        )
    except _export_svc.CardExportTimeoutError:
        raise HTTPException(status_code=504, detail="Card render timed out")

    filename = f"lfa_vt_{game_id}_{day.isoformat()}_{platform}.png"
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control":       "no-store",
            "X-Export-Platform":   platform,
            "X-Export-Game-Id":    str(game_id),
        },
    )


# ── Reward preview ────────────────────────────────────────────────────────────

@router.get("/virtual-training/card/reward/preview", response_class=HTMLResponse)
async def vt_reward_card_preview(
    request: Request,
    tier: int              = Query(..., description="Reward tier: 3 | 5 | 10"),
    platform: str          = Query(..., description="vt_reward_landscape | vt_reward_portrait"),
    date_str: str | None   = Query(default=None, alias="date", description="YYYY-MM-DD"),
    render_token: str | None = Query(default=None),
    db: Session            = Depends(get_db),
    user: "User | None"    = Depends(get_current_user_optional),
):
    if render_token is not None:
        token_user = _resolve_vt_render_token(render_token, db)
        if token_user is None:
            raise HTTPException(status_code=401, detail="Invalid or expired render token")
        user = token_user
    elif user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")

    if platform not in VT_REWARD_CARD_PLATFORMS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported platform: {platform!r}. Valid: {sorted(VT_REWARD_CARD_PLATFORMS)}",
        )

    if tier not in REWARD_TIERS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid tier: {tier!r}. Valid: {list(REWARD_TIERS)}",
        )

    day = _parse_date(date_str)
    eligible, completed_games_count = check_reward_eligibility(db, user.id, tier, day)
    if not eligible:
        raise HTTPException(
            status_code=403,
            detail=f"Not eligible for tier-{tier} reward: {completed_games_count}/{tier} games completed.",
        )

    player          = _player_display(db, user)
    completed_ids   = get_completed_game_ids(db, user.id, day)
    reward_stats    = _reward_daily_stats(db, user.id, completed_ids[:tier], day)

    ctx = {
        "request":               request,
        "tier":                  tier,
        "completed_games":       completed_games_count,
        "attempt_date":          day.isoformat(),
        "platform":              platform,
        # player identity
        "player_name":           player["name"],
        "player_overall":        player["overall"],
        "player_photo_url":      player["photo_url"],
        "player_primary_pos":    player["primary_pos"],
        # reward stats
        "completed_game_names":  reward_stats["completed_game_names"],
        "total_xp":              reward_stats["total_xp"],
    }
    return templates.TemplateResponse(_VT_REWARD_TEMPLATE[platform], ctx)


# ── Reward export ─────────────────────────────────────────────────────────────

@router.get("/virtual-training/card/reward/export")
async def vt_reward_card_export(
    request: Request,
    tier: int            = Query(..., description="Reward tier: 3 | 5 | 10"),
    platform: str        = Query(..., description="vt_reward_landscape | vt_reward_portrait"),
    date_str: str | None = Query(default=None, alias="date", description="YYYY-MM-DD"),
    db: Session          = Depends(get_db),
    user: User           = Depends(get_current_user_web),
):
    from ...config import settings  # noqa: PLC0415
    from ...core.auth import create_vt_card_render_token  # noqa: PLC0415

    if platform not in VT_REWARD_CARD_PLATFORMS:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported platform: {platform!r}. Valid: {sorted(VT_REWARD_CARD_PLATFORMS)}",
        )

    if tier not in REWARD_TIERS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid tier: {tier!r}. Valid: {list(REWARD_TIERS)}",
        )

    # Ownership guard — CDO row required for this reward format.
    if not _is_accessible(db, user.id, "virtual_training_card", platform):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Virtual Training Card format {platform!r} not owned. "
                "Purchase it at /shop?type=virtual_training_card"
            ),
        )

    day = _parse_date(date_str)
    eligible, completed_games_count = check_reward_eligibility(db, user.id, tier, day)
    if not eligible:
        raise HTTPException(
            status_code=403,
            detail=f"Not eligible for tier-{tier} reward: {completed_games_count}/{tier} games completed.",
        )

    client_ip = request.client.host if request.client else "unknown"
    rate_key  = f"vt_reward_card:{tier}:{user.id}:{client_ip}"
    if not _export_svc.check_export_rate_limit(rate_key):
        raise HTTPException(
            status_code=429,
            detail="Export rate limit exceeded (5 per minute). Please wait before exporting again.",
        )

    token = create_vt_card_render_token(user.id)
    date_param = f"&date={date_str}" if date_str else ""
    render_url = (
        f"http://127.0.0.1:{settings.APP_INTERNAL_PORT}"
        f"/virtual-training/card/reward/preview"
        f"?tier={tier}&platform={platform}&render_token={token}{date_param}"
    )

    try:
        png_bytes = await asyncio.to_thread(
            _export_svc._sync_take_screenshot, render_url, platform
        )
    except _export_svc.CardExportTimeoutError:
        raise HTTPException(status_code=504, detail="Card render timed out")

    filename = f"lfa_vt_reward_{tier}_{day.isoformat()}_{platform}.png"
    return Response(
        content=png_bytes,
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control":       "no-store",
            "X-Export-Platform":   platform,
            "X-Export-Tier":       str(tier),
        },
    )
