"""
user_mood_photos — per-user optional "hangulatkép" slots.

Completely independent from player_card_photo_url, wc_photo_url,
and every other photo column on UserLicense.  No fallback in either
direction.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from app.database import Base

MOOD_PHOTO_SLOTS: frozenset[str] = frozenset(
    {
        "mood_intro_neutral",
        "mood_happy_smile",
        "mood_celebration",
        "mood_sad_disappointed",
        "mood_angry_competitive",
        "mood_surprised_shocked",
    }
)


class MoodPhotoStatus(str, enum.Enum):
    uploaded   = "uploaded"    # raw upload done; bg removal not yet run
    processing = "processing"  # bg removal in progress (future phase)
    ready      = "ready"       # processed_png_url populated (future phase)
    failed     = "failed"      # bg removal failed; original_url still usable


class UserMoodPhoto(Base):
    __tablename__ = "user_mood_photos"

    id    = Column(Integer, primary_key=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # nullable: allows mood photos to exist before a license is assigned,
    # and survives license row deletion without orphaning the photo record.
    license_id = Column(
        Integer,
        ForeignKey("user_licenses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    slot              = Column(String(30), nullable=False)
    original_url      = Column(String(512), nullable=False)
    processed_png_url = Column(String(512), nullable=True)  # NULL throughout MVP
    status            = Column(
        String(20), nullable=False, default=MoodPhotoStatus.uploaded.value
    )
    created_at  = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at  = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    processed_at = Column(DateTime(timezone=True), nullable=True)  # NULL in MVP

    __table_args__ = (
        UniqueConstraint("user_id", "slot", name="uq_mood_photo_user_slot"),
        CheckConstraint(
            "slot IN ('mood_intro_neutral','mood_happy_smile',"
            "'mood_celebration','mood_sad_disappointed',"
            "'mood_angry_competitive','mood_surprised_shocked')",
            name="ck_mood_photo_slot_valid",
        ),
    )

    def __repr__(self) -> str:
        return (
            f"<UserMoodPhoto user_id={self.user_id} slot={self.slot!r}"
            f" status={self.status!r}>"
        )
