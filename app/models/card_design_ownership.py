"""CardDesignOwnership — per-user entitlement record for card designs.

One row per (user_id, card_type_id, design_id) triplet.

Every design (including player_card / "fclassic" (legacy input sanitized via resolve_design_id)) requires an ownership row
before it can be exported. Use grant_design() to issue entitlements
without credit deduction (admin grants, backfill scripts, seed data).

source values:
  "purchase"    — user paid credits via purchase_design()
  "admin_grant" — explicit admin grant (no credit deduction)
  "promo"       — future promo/voucher flow (not MVP)
  "system"      — backfill script or system grant
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)

from ..database import Base


class CardDesignOwnership(Base):
    __tablename__ = "card_design_ownerships"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    card_type_id = Column(
        String(50),
        nullable=False,
        comment="'player_card' | 'welcome_card' | 'challenge_card'",
    )
    design_id = Column(
        String(50),
        nullable=False,
        comment=(
            "player_card: 'fclassic'|'compact'|… (maps to card_designs.id); "
            "welcome_card: platform format id e.g. 'instagram_portrait'; "
            "challenge_card: format id e.g. 'challenge_post_16_9'"
        ),
    )
    source = Column(
        String(20),
        nullable=False,
        server_default="purchase",
        comment="'purchase' | 'admin_grant' | 'promo' | 'system'",
    )
    credit_transaction_id = Column(
        Integer,
        ForeignKey("credit_transactions.id", ondelete="SET NULL"),
        nullable=True,
        comment="NULL for admin_grant / system source",
    )
    acquired_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "card_type_id", "design_id",
            name="uq_cdo_user_type_design",
        ),
        Index("ix_cdo_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<CardDesignOwnership user_id={self.user_id} "
            f"card_type_id={self.card_type_id!r} "
            f"design_id={self.design_id!r} "
            f"source={self.source!r}>"
        )
