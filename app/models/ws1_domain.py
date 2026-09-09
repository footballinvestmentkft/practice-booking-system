"""Additive WS1 domain records for consent and football category truth."""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import relationship

from ..database import Base


class UserGuardianConsent(Base):
    __tablename__ = "user_guardian_consents"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    guardian_name = Column(String(200), nullable=False)
    guardian_relationship = Column(String(100), nullable=True)
    evidence_reference = Column(String(500), nullable=True)
    granted_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    granted_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    revoked_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revocation_reason = Column(Text, nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    granted_by = relationship("User", foreign_keys=[granted_by_user_id])
    revoked_by = relationship("User", foreign_keys=[revoked_by_user_id])

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    __table_args__ = (
        Index(
            "uq_user_guardian_consents_one_active",
            "user_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )


class FootballSeasonCategoryAssignment(Base):
    __tablename__ = "football_season_category_assignments"

    id = Column(Integer, primary_key=True)
    player_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    user_license_id = Column(Integer, ForeignKey("user_licenses.id", ondelete="RESTRICT"), nullable=False, index=True)
    season_start = Column(Date, nullable=False, index=True)
    season_end = Column(Date, nullable=False)
    season_base_category = Column(String(20), nullable=False)
    effective_category = Column(String(20), nullable=False)
    base_participation_retained = Column(Boolean, nullable=False, default=False)
    version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    player = relationship("User", foreign_keys=[player_user_id])
    user_license = relationship("UserLicense", foreign_keys=[user_license_id])
    movement_events = relationship(
        "FootballCategoryMovementEvent",
        back_populates="assignment",
        order_by="FootballCategoryMovementEvent.created_at",
    )

    __table_args__ = (
        UniqueConstraint("player_user_id", "season_start", name="uq_football_assignment_player_season"),
        UniqueConstraint("user_license_id", "season_start", name="uq_football_assignment_license_season"),
        CheckConstraint("season_end > season_start", name="ck_football_assignment_season_dates"),
        CheckConstraint(
            "season_base_category IN ('PRE','YOUTH','AMATEUR')",
            name="ck_football_assignment_base_category",
        ),
        CheckConstraint(
            "effective_category IN ('PRE','YOUTH','AMATEUR','PRO')",
            name="ck_football_assignment_effective_category",
        ),
        CheckConstraint("version >= 1", name="ck_football_assignment_version"),
    )


class FootballCategoryMovementEvent(Base):
    __tablename__ = "football_category_movement_events"

    id = Column(Integer, primary_key=True)
    assignment_id = Column(
        Integer,
        ForeignKey("football_season_category_assignments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    player_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    season_start = Column(Date, nullable=False)
    season_end = Column(Date, nullable=False)
    season_base_category = Column(String(20), nullable=False)
    previous_effective_category = Column(String(20), nullable=False)
    target_effective_category = Column(String(20), nullable=False)
    base_participation_retained = Column(Boolean, nullable=False)
    event_type = Column(String(30), nullable=False, default="ASSIGNMENT")
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    actor_role = Column(String(30), nullable=False)
    authorization_basis = Column(String(100), nullable=False)
    source = Column(String(100), nullable=False)
    instructor_assignment_id = Column(
        Integer, ForeignKey("instructor_assignments.id", ondelete="SET NULL"), nullable=True
    )
    source_enrollment_id = Column(
        Integer, ForeignKey("semester_enrollments.id", ondelete="SET NULL"), nullable=True
    )
    reason = Column(Text, nullable=False)
    context = Column(JSON, nullable=True)
    idempotency_key = Column(String(255), nullable=False, unique=True, index=True)
    assignment_version = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    assignment = relationship("FootballSeasonCategoryAssignment", back_populates="movement_events")
    player = relationship("User", foreign_keys=[player_user_id])
    actor = relationship("User", foreign_keys=[actor_user_id])
    instructor_assignment = relationship("InstructorAssignment", foreign_keys=[instructor_assignment_id])
    source_enrollment = relationship("SemesterEnrollment", foreign_keys=[source_enrollment_id])

    __table_args__ = (
        CheckConstraint(
            "season_base_category IN ('PRE','YOUTH','AMATEUR')",
            name="ck_football_movement_base_category",
        ),
        CheckConstraint(
            "previous_effective_category IN ('PRE','YOUTH','AMATEUR','PRO')",
            name="ck_football_movement_previous_category",
        ),
        CheckConstraint(
            "target_effective_category IN ('PRE','YOUTH','AMATEUR','PRO')",
            name="ck_football_movement_target_category",
        ),
    )


@event.listens_for(FootballCategoryMovementEvent, "before_update")
@event.listens_for(FootballCategoryMovementEvent, "before_delete")
def _movement_history_is_append_only(mapper, connection, target):
    raise ValueError("Football category movement history is append-only")
