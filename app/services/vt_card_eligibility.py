"""Virtual Training Card eligibility service.

Two eligibility checks:
  - check_single_game_eligibility: user completed all max_daily_attempts for a
    specific game today (standalone only — challenge attempts excluded).
  - check_reward_eligibility: user completed N distinct games today (tier 3/5/10).

"Standalone" means: is_valid=True AND (raw_metrics IS NULL OR
raw_metrics->>'attempt_source' IS DISTINCT FROM 'challenge').

No credit/CDO ownership check is performed here — VT cards are earned by playing,
not purchased.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.virtual_training import VirtualTrainingAttempt, VirtualTrainingGame

REWARD_TIERS: tuple[int, ...] = (3, 5, 10)
# Tier 10 requires at least this many active games to exist before it is enabled.
_TIER_10_MIN_ACTIVE_GAMES: int = 10


def _day_window(day: date) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def _standalone_count(db: Session, user_id: int, game_id: int, day: date) -> int:
    """Count valid standalone attempts for one user × game × day (UTC)."""
    day_start, day_end = _day_window(day)
    return (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id == user_id,
            VirtualTrainingAttempt.game_id == game_id,
            VirtualTrainingAttempt.is_valid == True,  # noqa: E712
            VirtualTrainingAttempt.completed_at >= day_start,
            VirtualTrainingAttempt.completed_at < day_end,
            or_(
                VirtualTrainingAttempt.raw_metrics.is_(None),
                VirtualTrainingAttempt.raw_metrics["attempt_source"].astext != "challenge",
            ),
        )
        .count()
    )


def check_single_game_eligibility(
    db: Session,
    user_id: int,
    game_id: int,
    day: date | None = None,
) -> tuple[bool, int, int]:
    """Return (eligible, completed_count, required_count).

    eligible is True when completed_count >= required_count (game.max_daily_attempts).
    Game must exist and be active; returns (False, 0, 0) if game is not found or inactive.
    """
    if day is None:
        day = datetime.now(timezone.utc).date()

    game = (
        db.query(VirtualTrainingGame)
        .filter(
            VirtualTrainingGame.id == game_id,
            VirtualTrainingGame.is_active == True,  # noqa: E712
        )
        .first()
    )
    if game is None:
        return (False, 0, 0)

    count = _standalone_count(db, user_id, game_id, day)
    required = game.max_daily_attempts
    return (count >= required, count, required)


def get_completed_game_ids(
    db: Session,
    user_id: int,
    day: date | None = None,
) -> list[int]:
    """Return IDs of active games the user has fully completed (standalone) today.

    "Completed" means _standalone_count >= game.max_daily_attempts.
    """
    if day is None:
        day = datetime.now(timezone.utc).date()

    active_games = (
        db.query(VirtualTrainingGame)
        .filter(VirtualTrainingGame.is_active == True)  # noqa: E712
        .all()
    )
    return [
        game.id
        for game in active_games
        if _standalone_count(db, user_id, game.id, day) >= game.max_daily_attempts
    ]


def check_reward_eligibility(
    db: Session,
    user_id: int,
    tier: int,
    day: date | None = None,
) -> tuple[bool, int]:
    """Return (eligible, completed_game_count).

    A game counts as "completed" when the user has done >= max_daily_attempts
    valid standalone attempts for that game today.

    Tier 10 is treated as disabled (eligible=False) when fewer than
    _TIER_10_MIN_ACTIVE_GAMES active games exist in the DB.

    Raises ValueError for unknown tier values.
    """
    if tier not in REWARD_TIERS:
        raise ValueError(f"Unknown reward tier: {tier!r}. Valid: {REWARD_TIERS}")

    if day is None:
        day = datetime.now(timezone.utc).date()

    # Tier-10 gate: not enough active games → always ineligible
    if tier == 10:
        active_count = (
            db.query(VirtualTrainingGame)
            .filter(VirtualTrainingGame.is_active == True)  # noqa: E712
            .count()
        )
        if active_count < _TIER_10_MIN_ACTIVE_GAMES:
            return (False, 0)

    active_games = (
        db.query(VirtualTrainingGame)
        .filter(VirtualTrainingGame.is_active == True)  # noqa: E712
        .all()
    )

    completed_game_count = sum(
        1
        for game in active_games
        if _standalone_count(db, user_id, game.id, day) >= game.max_daily_attempts
    )

    return (completed_game_count >= tier, completed_game_count)
