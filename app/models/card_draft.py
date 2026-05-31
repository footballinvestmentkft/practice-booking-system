"""CardDraft — per-user, per-card-type draft + published state.

One row per (user_id, card_type_id, instance_name) triplet.
Singleton cards (Player Card, Welcome Card) always use instance_name='default'.
Future multi-instance cards (Match Card, Event Card) use named instance keys.

Phase 4D-1: schema only.  Routes and services still read/write UserLicense
legacy columns.  Phase 4D-2 will switch the read/write paths.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
)

from ..database import Base


class CardDraft(Base):
    __tablename__ = "card_drafts"

    # ── Identity ──────────────────────────────────────────────────────────────
    id           = Column(Integer, primary_key=True)
    user_id      = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    card_type_id  = Column(
        String(50), nullable=False,
        comment="e.g. 'player_card', 'match_card', 'event_card'",
    )
    # 'default' for singleton cards; named key for multi-instance cards
    # (NOT NULL avoids Postgres NULL != NULL uniqueness edge case)
    instance_name = Column(
        String(100), nullable=False, server_default="default",
        comment="'default' for singleton cards; named key for multi-instance",
    )

    # ── Draft selection state (editor writes; public route never reads) ───────
    draft_theme    = Column(String(50), nullable=False, server_default="default")
    draft_variant  = Column(String(50), nullable=False, server_default="fclassic")
    draft_platform = Column(
        String(50), nullable=True,
        comment="NULL = platform default",
    )
    draft_data = Column(
        JSON, nullable=True,
        comment="Reserved for Phase 4E+ content fields",
    )

    # ── Published snapshot (public route reads; only publish action writes) ──
    published_theme    = Column(String(50), nullable=True,
                                comment="NULL = never published")
    published_variant  = Column(String(50), nullable=True)
    published_platform = Column(String(50), nullable=True)
    published_data     = Column(JSON, nullable=True,
                                comment="Reserved for Phase 4E+ content fields")
    published_at       = Column(
        DateTime(timezone=True), nullable=True,
        comment="Timestamp of last publish action; NULL = never published",
    )

    # ── Audit timestamps ──────────────────────────────────────────────────────
    created_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint(
            "user_id", "card_type_id", "instance_name",
            name="uq_card_drafts_user_type_instance",
        ),
        Index("ix_card_drafts_user_id", "user_id"),
    )

    def __repr__(self) -> str:
        return (
            f"<CardDraft user_id={self.user_id} "
            f"card_type_id={self.card_type_id!r} "
            f"instance_name={self.instance_name!r} "
            f"draft_theme={self.draft_theme!r}>"
        )
