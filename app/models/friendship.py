"""Friendship model — minimal social graph for friend-gated features.

A Friendship row represents a directed request from requester → addressee.
The accepted state is bidirectional: is_friends() checks both directions.

Status transitions:
  (new) → PENDING  → ACCEPTED  (addressee accepted)
                   → DECLINED  (addressee declined)
  ACCEPTED         → (deleted) via remove endpoint
  PENDING/ACCEPTED → BLOCKED   (future: block flow, not exposed in PR-F1)
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Enum,
    ForeignKey, Integer, UniqueConstraint,
)
from sqlalchemy.orm import Session, relationship

from ..database import Base


class FriendshipStatus(enum.Enum):
    PENDING  = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    BLOCKED  = "blocked"


class Friendship(Base):
    __tablename__ = "friendships"
    __table_args__ = (
        UniqueConstraint("requester_id", "addressee_id", name="uq_friendship_pair"),
        CheckConstraint("requester_id != addressee_id", name="ck_no_self_friendship"),
    )

    id           = Column(Integer, primary_key=True, index=True)
    requester_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    addressee_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    status       = Column(
                      Enum(FriendshipStatus,
                           values_callable=lambda obj: [e.value for e in obj]),
                      nullable=False,
                      default=FriendshipStatus.PENDING,
                  )
    created_at   = Column(DateTime(timezone=True), nullable=False,
                          default=lambda: datetime.now(timezone.utc))
    updated_at   = Column(DateTime(timezone=True), nullable=True)

    requester = relationship("User", foreign_keys=[requester_id])
    addressee = relationship("User", foreign_keys=[addressee_id])


# ── Helpers ───────────────────────────────────────────────────────────────────

def is_friends(db: Session, user_a_id: int, user_b_id: int) -> bool:
    """Return True if an ACCEPTED friendship exists in either direction."""
    return db.query(Friendship).filter(
        Friendship.status == FriendshipStatus.ACCEPTED,
        (
            (Friendship.requester_id == user_a_id) & (Friendship.addressee_id == user_b_id)
        ) | (
            (Friendship.requester_id == user_b_id) & (Friendship.addressee_id == user_a_id)
        ),
    ).first() is not None


def get_friendship(db: Session, user_a_id: int, user_b_id: int) -> Friendship | None:
    """Return the friendship row between two users (either direction)."""
    return db.query(Friendship).filter(
        (
            (Friendship.requester_id == user_a_id) & (Friendship.addressee_id == user_b_id)
        ) | (
            (Friendship.requester_id == user_b_id) & (Friendship.addressee_id == user_a_id)
        ),
    ).first()
