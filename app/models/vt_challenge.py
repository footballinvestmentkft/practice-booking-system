"""VirtualTrainingChallenge model — async friend-vs-friend VT challenge.

Lifecycle (PR-C1):
  PENDING  → ACCEPTED   (challenged_id accepts, expires_at not passed)
  PENDING  → DECLINED   (challenged_id declines)
  PENDING  → CANCELLED  (challenger_id cancels)
  ACCEPTED → CANCELLED  (challenger_id cancels)

PR-C2 adds:
  ACCEPTED → COMPLETED  (both attempts submitted, winner_id / is_draw set)

Expiry:
  expires_at = created_at + 7 days (set at creation).
  Accept guard checks expires_at <= now() → rejects with EXPIRED status.

Game compatibility:
  Only games in CHALLENGE_COMPATIBLE_GAMES (by game.code) are allowed.
  Expandable to a DB config field later.
"""
from __future__ import annotations

import enum
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Enum,
    ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session, relationship

from ..database import Base

# ── Game allowlist ─────────────────────────────────────────────────────────────
CHALLENGE_COMPATIBLE_GAMES: frozenset[str] = frozenset({
    "memory_sequence",
    "target_tracking",
})


# ── Enum ───────────────────────────────────────────────────────────────────────

class ChallengeStatus(enum.Enum):
    PENDING   = "pending"
    ACCEPTED  = "accepted"
    DECLINED  = "declined"
    EXPIRED   = "expired"
    CANCELLED = "cancelled"
    COMPLETED = "completed"   # set by PR-C2 submit hook


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
    difficulty_level           = Column(String(20), nullable=True)   # TT only; NULL for MS
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

    challenger = relationship("User", foreign_keys=[challenger_id])
    challenged = relationship("User", foreign_keys=[challenged_id])
    winner     = relationship("User", foreign_keys=[winner_id])
    game       = relationship("VirtualTrainingGame")


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_expires_at(created_at: datetime | None = None) -> datetime:
    base = created_at or datetime.now(timezone.utc)
    return base + timedelta(days=7)


def get_active_challenge(
    db: Session,
    user_a_id: int,
    user_b_id: int,
    game_id: int,
) -> VirtualTrainingChallenge | None:
    """Return PENDING or ACCEPTED challenge between two users on a game (either direction)."""
    return (
        db.query(VirtualTrainingChallenge)
        .filter(
            VirtualTrainingChallenge.game_id == game_id,
            VirtualTrainingChallenge.status.in_(
                [ChallengeStatus.PENDING, ChallengeStatus.ACCEPTED]
            ),
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
