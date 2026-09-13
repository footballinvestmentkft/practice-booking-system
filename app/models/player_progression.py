"""PC5 canonical Player progression outcome, skill state and immutable skill history."""
import uuid

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, JSON, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class PlayerProgressionOutcome(Base):
    __tablename__ = "player_progression_outcomes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    specialization_id = Column(String(50), ForeignKey("specializations.id", ondelete="RESTRICT"), nullable=False)
    source_type = Column(String(50), nullable=False)
    source_id = Column(String(120), nullable=False)
    idempotency_key = Column(String(160), nullable=False, unique=True)
    payload_hash = Column(String(64), nullable=False)
    xp_delta = Column(Integer, nullable=False, default=0)
    provenance = Column(JSON, nullable=False, default=dict)
    context = Column(JSON, nullable=False, default=dict)
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    applied_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    skill_changes = relationship("PlayerSkillChange", back_populates="outcome")

    __table_args__ = (
        UniqueConstraint("player_user_id", "source_type", "source_id", name="uq_player_progression_source"),
        CheckConstraint("specialization_id = 'LFA_FOOTBALL_PLAYER'", name="ck_player_progression_specialization"),
        CheckConstraint("xp_delta >= 0", name="ck_player_progression_xp_nonnegative"),
    )


class PlayerSkillState(Base):
    __tablename__ = "player_skill_states"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    player_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    skill_key = Column(String(50), nullable=False)
    current_value = Column(Float, nullable=False)
    last_outcome_id = Column(UUID(as_uuid=True), ForeignKey("player_progression_outcomes.id", ondelete="RESTRICT"), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("player_user_id", "skill_key", name="uq_player_skill_state"),
        CheckConstraint("current_value >= 0 AND current_value <= 100", name="ck_player_skill_state_range"),
    )


class PlayerSkillChange(Base):
    __tablename__ = "player_skill_changes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    outcome_id = Column(UUID(as_uuid=True), ForeignKey("player_progression_outcomes.id", ondelete="RESTRICT"), nullable=False, index=True)
    player_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    skill_key = Column(String(50), nullable=False)
    previous_value = Column(Float, nullable=False)
    new_value = Column(Float, nullable=False)
    delta = Column(Float, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    outcome = relationship("PlayerProgressionOutcome", back_populates="skill_changes")

    __table_args__ = (
        UniqueConstraint("outcome_id", "skill_key", name="uq_player_skill_change_outcome"),
        CheckConstraint("new_value >= 0 AND new_value <= 100", name="ck_player_skill_change_range"),
    )
