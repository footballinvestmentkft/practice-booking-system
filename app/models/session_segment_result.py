"""
SessionSegmentResult model — one row per (segment, attendance) pair.

Persists the resolved training skill deltas and per-segment XP for a
student completing a drill.  Written once; immutable after creation.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from ..database import Base


class SessionSegmentResult(Base):
    """
    Resolved result for one student completing one segment.

    ``skill_deltas`` is computed once by segment_reward_service at write time.
    Values are raw additive deltas (not EMA values); the EMA engine is separate
    and reads TournamentParticipation exclusively.

    Idempotency: two unique guards prevent double-counting:
      - uq_segment_result_seg_att   (composite on segment_id + attendance_id)
      - uq_segment_result_idempotency (partial unique on idempotency_key)
    """
    __tablename__ = "session_segment_results"
    __table_args__ = (
        UniqueConstraint("segment_id", "attendance_id", name="uq_segment_result_seg_att"),
        Index(
            "uq_segment_result_idempotency",
            "idempotency_key",
            unique=True,
            postgresql_where="idempotency_key IS NOT NULL",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    segment_id = Column(
        Integer,
        ForeignKey("session_segments.id", name="fk_ssr_segment_id", ondelete="CASCADE"),
        nullable=False,
    )
    attendance_id = Column(
        Integer,
        ForeignKey("attendance.id", name="fk_ssr_attendance_id", ondelete="CASCADE"),
        nullable=False,
    )
    session_id = Column(
        Integer,
        ForeignKey("sessions.id", name="fk_ssr_session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", name="fk_ssr_user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    skill_deltas = Column(
        JSONB,
        nullable=False,
        server_default="{}",
        comment="Resolved per-skill additive deltas at write time. Immutable after creation.",
    )
    xp_awarded = Column(Integer, nullable=False, server_default="0")
    idempotency_key = Column(
        String(255),
        nullable=False,
        comment='Format: "seg_{segment_id}_att_{attendance_id}"',
    )
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    # Relationships
    segment = relationship("SessionSegment", back_populates="results")
    attendance = relationship("Attendance")
    session = relationship("Session", back_populates="segment_results")
    user = relationship("User")
