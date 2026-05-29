"""CardColorOwnership — per-user, per-family color entitlement record.

One row per (user_id, card_type_id, color_id) triplet.

Ownership key:
  user_id + card_type_id + color_id

The same color_id in different families is a distinct product — e.g.
"gold" for "player_card" is independent of "gold" for "welcome_card".

pack_id: nullable, reserved for TS-3 bundle logic. When a color is
purchased individually, pack_id is NULL. When purchased as part of a
named bundle, pack_id carries the bundle identifier (e.g.
"player_color_bundle_v1") for audit purposes.
"""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint

from ..database import Base


class CardColorOwnership(Base):
    __tablename__ = "card_color_ownership"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    card_type_id = Column(
        String(64),
        nullable=False,
        comment="'player_card' | 'welcome_card' | 'challenge_card'",
    )
    color_id = Column(
        String(64),
        nullable=False,
        comment="e.g. 'gold', 'emerald', 'crimson'",
    )
    pack_id = Column(
        String(128),
        nullable=True,
        comment="NULL for individual purchase; bundle id for TS-3 bundle logic",
    )
    purchased_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "card_type_id", "color_id",
            name="uq_cco_user_type_color",
        ),
        Index("ix_cco_user_id", "user_id"),
        Index("ix_cco_family_color", "card_type_id", "color_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<CardColorOwnership user_id={self.user_id} "
            f"card_type_id={self.card_type_id!r} "
            f"color_id={self.color_id!r}>"
        )
