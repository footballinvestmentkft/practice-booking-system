"""
SessionSegment model — ordered drill/exercise records within a training session.
"""
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, SmallInteger, String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..database import Base


class SessionSegment(Base):
    """
    One drill/exercise within a training session.

    Created by the instructor before or during the session.
    ``skill_targets`` declares intent — which skills this segment develops
    and at what relative weight.

    Priority chain at result time (resolved by segment_reward_service):
      1. segment.skill_targets                (instructor explicit override)
      2. session.session_reward_config["skill_areas"]  (session-level override)
      3. session.game_preset.game_config["skill_config"]["skill_weights"]  (preset)
      4. {}                                   (no skills → no delta written)
    """
    __tablename__ = "session_segments"
    __table_args__ = (
        UniqueConstraint("session_id", "position", name="uq_segment_session_position"),
    )

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(
        Integer,
        ForeignKey("sessions.id", name="fk_session_segments_session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position = Column(SmallInteger, nullable=False)
    label = Column(String(200), nullable=False)
    duration_minutes = Column(SmallInteger, nullable=True)
    skill_targets = Column(
        JSONB,
        nullable=True,
        comment=(
            "JSONB map of skill_key → weight (instructor explicit override). "
            "NULL = inherit from session.game_preset at result time."
        ),
    )
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    # Relationships
    session = relationship("Session", back_populates="segments")
    results = relationship(
        "SessionSegmentResult",
        back_populates="segment",
        cascade="all, delete-orphan",
    )
