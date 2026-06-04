"""Virtual Training Card eligibility service.

Two eligibility checks:
  - check_single_game_eligibility: user completed all max_daily_attempts for a
    specific game on a given training day (standalone only — challenge excluded).
  - check_reward_eligibility: user completed N distinct games on a given training
    day (tier 3/5/10).

Training day (Phase 1):
  Uses training_local_date stored on each attempt — computed at submit time
  from the browser IANA timezone. This replaces the former UTC completed_at
  window approach.

Fallback for legacy attempts (backfill):
  Old attempts without training_local_date were backfilled at migration time
  with completed_at::date in UTC, so training_local_date is always non-NULL
  after the 2026_06_05_1000 migration.

"Standalone" means: is_valid=True AND (raw_metrics IS NULL OR
raw_metrics->>'attempt_source' IS DISTINCT FROM 'challenge').
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.virtual_training import VirtualTrainingAttempt, VirtualTrainingGame
from app.services.training_day import current_training_date_utc

REWARD_TIERS: tuple[int, ...] = (3, 5, 10)
_TIER_10_MIN_ACTIVE_GAMES: int = 10


def _standalone_count(
    db: Session, user_id: int, game_id: int, training_local_date: date
) -> int:
    """Count valid standalone attempts for one user × game × training day."""
    return (
        db.query(VirtualTrainingAttempt)
        .filter(
            VirtualTrainingAttempt.user_id             == user_id,
            VirtualTrainingAttempt.game_id              == game_id,
            VirtualTrainingAttempt.is_valid             == True,  # noqa: E712
            VirtualTrainingAttempt.training_local_date  == training_local_date,
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
    training_local_date: date | None = None,
) -> tuple[bool, int, int]:
    """Return (eligible, completed_count, required_count).

    eligible is True when completed_count >= required_count (game.max_daily_attempts).
    Game must exist and be active; returns (False, 0, 0) if not found or inactive.

    training_local_date: the training day to check. Defaults to UTC today
    (server-side fallback) when not provided.
    """
    if training_local_date is None:
        training_local_date = current_training_date_utc()

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

    count = _standalone_count(db, user_id, game_id, training_local_date)
    required = game.max_daily_attempts
    return (count >= required, count, required)


def get_completed_game_ids(
    db: Session,
    user_id: int,
    training_local_date: date | None = None,
) -> list[int]:
    """Return IDs of active games the user has fully completed (standalone) today.

    "Completed" means _standalone_count >= game.max_daily_attempts.
    """
    if training_local_date is None:
        training_local_date = current_training_date_utc()

    active_games = (
        db.query(VirtualTrainingGame)
        .filter(VirtualTrainingGame.is_active == True)  # noqa: E712
        .all()
    )
    return [
        game.id
        for game in active_games
        if _standalone_count(db, user_id, game.id, training_local_date) >= game.max_daily_attempts
    ]


def check_reward_eligibility(
    db: Session,
    user_id: int,
    tier: int,
    training_local_date: date | None = None,
) -> tuple[bool, int]:
    """Return (eligible, completed_game_count).

    A game counts as "completed" when the user has >= max_daily_attempts
    valid standalone attempts on the given training day.

    Tier 10 is treated as disabled when fewer than _TIER_10_MIN_ACTIVE_GAMES
    active games exist in the DB.

    Raises ValueError for unknown tier values.
    """
    if tier not in REWARD_TIERS:
        raise ValueError(f"Unknown reward tier: {tier!r}. Valid: {REWARD_TIERS}")

    if training_local_date is None:
        training_local_date = current_training_date_utc()

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
        if _standalone_count(db, user_id, game.id, training_local_date) >= game.max_daily_attempts
    )

    return (completed_game_count >= tier, completed_game_count)
