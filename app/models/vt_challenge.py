"""VirtualTrainingChallenge model — async and live friend-vs-friend VT challenge.

Async lifecycle (PR-C1 / PR-C2 / PR-P1):
  PENDING  → ACCEPTED        (challenged accepts, expires_at not passed)
  PENDING  → DECLINED        (challenged declines)
  PENDING  → CANCELLED       (challenger cancels)
  ACCEPTED → CANCELLED       (challenger cancels)
  ACCEPTED → COMPLETED       (both submitted, winner_id / is_draw set)
  ACCEPTED → COMPLETED       (one played before deadline → forfeit win)
  ACCEPTED → EXPIRED         (neither played before deadline → no_contest)

Live lobby lifecycle (PR-L1):
  PENDING     → LIVE_LOBBY       (challenged accepts a live challenge)
  LIVE_LOBBY  → LIVE_IN_PROGRESS (both mark ready, countdown fires)
  LIVE_LOBBY  → EXPIRED          (lobby_expires_at passed, neither/one ready)
  LIVE_IN_PROGRESS → COMPLETED   (both submitted within post-start window)
  LIVE_IN_PROGRESS → COMPLETED   (one submitted, other missed → forfeit win / no_show)
  LIVE_IN_PROGRESS → EXPIRED     (neither submitted in post-start window → no_contest)

Expiry:
  expires_at = created_at + 7 days (set at creation).
  Accept guard checks expires_at <= now() → rejects with EXPIRED status.

Completion deadline (PR-P1, async only):
  accepted_at set when challenged accepts.
  completion_deadline = accepted_at + completion_window_seconds.
  NULL completion_deadline (legacy challenges) → deadline logic skipped entirely.

Live lobby (PR-L1):
  lobby_expires_at  = accepted_at + LOBBY_TIMEOUT_SECONDS (900 s).
  challenger_ready_at / challenged_ready_at set when each side POSTs /ready.
  live_start_at set server-side when both are ready.
  Post-start submit window = POST_START_SUBMIT_WINDOW_SECONDS (300 s).

Forfeit (PR-P1 + PR-L1):
  forfeit_user_id = user who did not play in time.
  forfeit_reason  = 'deadline_expired' | 'no_contest' | 'no_show' | 'post_start_timeout'.

Game compatibility:
  Only games in CHALLENGE_COMPATIBLE_GAMES (by game.code) are allowed.
"""
from __future__ import annotations

import enum
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Enum,
    ForeignKey, Integer, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, relationship

from ..database import Base

# ── Game allowlist ─────────────────────────────────────────────────────────────
CHALLENGE_COMPATIBLE_GAMES: frozenset[str] = frozenset({
    "memory_sequence",
    "target_tracking",
})

# ── Per-category active challenge limit ────────────────────────────────────────
# Maximum number of simultaneously active challenges between two users within
# the same game category (VirtualTrainingGame.game_type).  Application-level
# only — no DB constraint backs this. See count_active_challenges_in_category().
#
# Race-condition note: because this is a read-then-write guard without a DB lock,
# concurrent requests from the same user pair in the same category could
# theoretically exceed this limit by 1 in a multi-worker deployment.  The risk
# is negligible for single-worker production; add SELECT FOR UPDATE or a Redis
# advisory lock before scaling horizontally.
MAX_ACTIVE_PER_CATEGORY: int = 3

# ── Completion window options (seconds, async mode) ───────────────────────────
VALID_COMPLETION_WINDOWS: frozenset[int] = frozenset({
    1800,    # 30 minutes
    3600,    # 1 hour
    86400,   # 24 hours (default)
    259200,  # 3 days
    604800,  # 7 days
})
DEFAULT_COMPLETION_WINDOW: int = 86400

# ── Live lobby constants ───────────────────────────────────────────────────────
LOBBY_TIMEOUT_SECONDS: int        = 900   # 15 min to both mark ready
LIVE_COUNTDOWN_SECONDS: int       = 5     # client-side countdown before game starts
POST_START_SUBMIT_WINDOW_SECONDS: int = 300  # 5 min to submit after live_start_at


# ── Enum ───────────────────────────────────────────────────────────────────────

class ChallengeStatus(enum.Enum):
    PENDING         = "pending"
    ACCEPTED        = "accepted"
    DECLINED        = "declined"
    EXPIRED         = "expired"
    CANCELLED       = "cancelled"
    COMPLETED       = "completed"
    LIVE_LOBBY      = "live_lobby"
    LIVE_IN_PROGRESS = "live_in_progress"


# ── Model ──────────────────────────────────────────────────────────────────────

class VirtualTrainingChallenge(Base):
    __tablename__ = "vt_challenges"
    __table_args__ = (
        CheckConstraint(
            "challenger_id != challenged_id",
            name="ck_challenge_no_self",
        ),
        CheckConstraint(
            "challenge_mode IN ('async', 'live')",
            name="ck_vt_challenge_mode_valid",
        ),
        CheckConstraint(
            "forfeit_reason IS NULL OR forfeit_reason IN "
            "('deadline_expired', 'no_contest', 'no_show', 'post_start_timeout')",
            name="ck_vt_forfeit_reason_valid",
        ),
    )

    id                    = Column(Integer, primary_key=True, index=True)
    challenger_id         = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                                   nullable=False, index=True)
    challenged_id         = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                                   nullable=False, index=True)
    game_id               = Column(Integer,
                                   ForeignKey("virtual_training_games.id", ondelete="CASCADE"),
                                   nullable=False, index=True)
    status                = Column(
                                Enum(ChallengeStatus,
                                     values_callable=lambda obj: [e.value for e in obj]),
                                nullable=False,
                                default=ChallengeStatus.PENDING,
                            )
    message               = Column(Text, nullable=True)
    challenger_attempt_id = Column(Integer,
                                   ForeignKey("virtual_training_attempts.id", ondelete="SET NULL"),
                                   nullable=True)
    challenged_attempt_id = Column(Integer,
                                   ForeignKey("virtual_training_attempts.id", ondelete="SET NULL"),
                                   nullable=True)
    difficulty_level           = Column(String(20), nullable=True)
    challenge_mode             = Column(String(10), nullable=False, default="async",
                                        server_default="async")
    challenge_config_snapshot  = Column(JSONB, nullable=True)
    winner_id             = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"),
                                   nullable=True)
    is_draw               = Column(Boolean, nullable=False, default=False)
    completed_at          = Column(DateTime(timezone=True), nullable=True)
    expires_at            = Column(DateTime(timezone=True), nullable=False)
    created_at            = Column(DateTime(timezone=True), nullable=False,
                                   default=lambda: datetime.now(timezone.utc))
    updated_at            = Column(DateTime(timezone=True), nullable=True)

    # PR-P1 — completion deadline + forfeit
    accepted_at                = Column(DateTime(timezone=True), nullable=True)
    completion_window_seconds  = Column(Integer, nullable=True)
    completion_deadline        = Column(DateTime(timezone=True), nullable=True)
    forfeit_user_id            = Column(Integer,
                                        ForeignKey("users.id", ondelete="SET NULL"),
                                        nullable=True, index=True)
    forfeit_reason             = Column(String(30), nullable=True)

    # PR-L1 — live lobby
    challenger_ready_at  = Column(DateTime(timezone=True), nullable=True)
    challenged_ready_at  = Column(DateTime(timezone=True), nullable=True)
    live_start_at        = Column(DateTime(timezone=True), nullable=True)
    lobby_expires_at     = Column(DateTime(timezone=True), nullable=True)

    # CC-DESIGN-1: per-challenge card photo snapshot (NULL = fallback to neutral mood)
    challenger_card_photo_url = Column(String(512), nullable=True)
    challenged_card_photo_url = Column(String(512), nullable=True)

    challenger   = relationship("User", foreign_keys=[challenger_id])
    challenged   = relationship("User", foreign_keys=[challenged_id])
    winner       = relationship("User", foreign_keys=[winner_id])
    game         = relationship("VirtualTrainingGame")
    forfeit_user = relationship("User", foreign_keys=[forfeit_user_id])


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_expires_at(created_at: datetime | None = None) -> datetime:
    base = created_at or datetime.now(timezone.utc)
    return base + timedelta(days=7)


def validate_completion_window(seconds: int) -> int:
    if seconds not in VALID_COMPLETION_WINDOWS:
        raise ValueError(
            f"Invalid completion_window_seconds {seconds!r}. "
            f"Must be one of: {sorted(VALID_COMPLETION_WINDOWS)}"
        )
    return seconds


def make_completion_deadline(accepted_at: datetime, window_seconds: int) -> datetime:
    return accepted_at + timedelta(seconds=window_seconds)


def get_active_challenge(
    db: Session,
    user_a_id: int,
    user_b_id: int,
    game_id: int,
) -> VirtualTrainingChallenge | None:
    """Return active challenge between two users on a specific game (either direction).

    Kept for backward compatibility and direct per-game lookups.
    The send_challenge guard now uses count_active_challenges_in_category().
    """
    return (
        db.query(VirtualTrainingChallenge)
        .filter(
            VirtualTrainingChallenge.game_id == game_id,
            VirtualTrainingChallenge.status.in_([
                ChallengeStatus.PENDING,
                ChallengeStatus.ACCEPTED,
                ChallengeStatus.LIVE_LOBBY,
                ChallengeStatus.LIVE_IN_PROGRESS,
            ]),
            (
                (VirtualTrainingChallenge.challenger_id == user_a_id) &
                (VirtualTrainingChallenge.challenged_id == user_b_id)
            ) | (
                (VirtualTrainingChallenge.challenger_id == user_b_id) &
                (VirtualTrainingChallenge.challenged_id == user_a_id)
            ),
        )
        .first()
    )


def count_active_challenges_in_category(
    db: Session,
    user_a_id: int,
    user_b_id: int,
    game_type: str,
) -> int:
    """Count active challenges between two users within a game category (bidirectional).

    Used by send_challenge to enforce MAX_ACTIVE_PER_CATEGORY.
    ``game_type`` is VirtualTrainingGame.game_type (e.g. "memory_span", "tracking").
    """
    from sqlalchemy import func
    from app.models.virtual_training import VirtualTrainingGame

    _ACTIVE = [
        ChallengeStatus.PENDING,
        ChallengeStatus.ACCEPTED,
        ChallengeStatus.LIVE_LOBBY,
        ChallengeStatus.LIVE_IN_PROGRESS,
    ]
    return (
        db.query(func.count(VirtualTrainingChallenge.id))
        .join(VirtualTrainingGame, VirtualTrainingChallenge.game_id == VirtualTrainingGame.id)
        .filter(
            VirtualTrainingGame.game_type == game_type,
            VirtualTrainingChallenge.status.in_(_ACTIVE),
            (
                (VirtualTrainingChallenge.challenger_id == user_a_id) &
                (VirtualTrainingChallenge.challenged_id == user_b_id)
            ) | (
                (VirtualTrainingChallenge.challenger_id == user_b_id) &
                (VirtualTrainingChallenge.challenged_id == user_a_id)
            ),
        )
        .scalar()
    )
