"""Virtual Training data models — Phase 2 (Color Reaction MVP)."""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, SmallInteger, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database import Base


class VirtualTrainingGame(Base):
    """
    A virtual training mini-game preset.

    Phase 1: only seeded as is_active=False — no active user routes yet.
    Activated per-game by an admin toggle (is_active=True) in a later phase.
    """
    __tablename__ = "virtual_training_games"

    id          = Column(Integer, primary_key=True, index=True)
    code        = Column(String(50), nullable=False, unique=True, index=True)
    name        = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    game_type   = Column(String(50), nullable=False)   # reaction_time | cognitive_inhibition | go_no_go
    is_active   = Column(Boolean, nullable=False, default=False)

    base_xp             = Column(Integer, nullable=False, default=10)
    max_daily_attempts  = Column(Integer, nullable=False, default=5)

    # {skill_key: weight}  — reused by compute_skill_deltas()
    skill_targets = Column(JSONB, nullable=False, default=dict)
    # game-specific runtime config (stimulus timings, colour sets, etc.)
    config        = Column(JSONB, nullable=False, default=dict)

    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))

    attempts = relationship("VirtualTrainingAttempt", back_populates="game",
                            cascade="all, delete-orphan", lazy="dynamic")


class VirtualTrainingAttempt(Base):
    """
    One completed (or invalid) attempt at a VirtualTrainingGame by a user.

    Idempotency key prevents double-writes from retry storms.
    Bot filter: avg_reaction_ms < 100 → is_valid=False, invalid_reason="bot_suspected".
    Diminishing-returns multiplier baked into xp_awarded at write time.
    """
    __tablename__ = "virtual_training_attempts"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_vt_attempts_idempotency_key"),
    )

    id      = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    game_id = Column(Integer, ForeignKey("virtual_training_games.id", ondelete="CASCADE"),
                     nullable=False, index=True)

    started_at   = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    is_valid       = Column(Boolean, nullable=False, default=True)
    invalid_reason = Column(String(100), nullable=True)  # "bot_suspected"

    score_raw        = Column(Float, nullable=True)   # game-native score
    score_normalized = Column(Float, nullable=True)   # 0–100
    avg_reaction_ms  = Column(Float, nullable=True)   # reaction-time games

    xp_awarded        = Column(Integer, nullable=False, default=0)
    skill_deltas      = Column(JSONB, nullable=False, default=dict)
    attempt_index_today = Column(SmallInteger, nullable=False, default=1)  # 1-based

    # Phase 2 gameplay columns (anti-farming + result display)
    duration_seconds  = Column(Float, nullable=True)         # elapsed seconds first stim → last click
    stimuli_count     = Column(SmallInteger, nullable=True)  # stimuli presented
    correct_count     = Column(SmallInteger, nullable=True)  # clicked within window
    error_count       = Column(SmallInteger, nullable=True)  # window expired before click (miss)
    min_reaction_ms   = Column(Float, nullable=True)         # fastest single reaction
    wrong_click_count = Column(SmallInteger, nullable=True)  # wrong-color clicks (Phase 2.1)
    raw_metrics       = Column(JSONB, nullable=True)          # per-stimulus/color/phase (Phase 2.2)

    idempotency_key = Column(String(100), nullable=True, unique=True)

    game = relationship("VirtualTrainingGame", back_populates="attempts")
