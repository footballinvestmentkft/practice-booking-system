"""
⚽ Football Skill Assessment Model
Tracks individual skill assessments for LFA Player specializations

LIFECYCLE STATE MACHINE (Phase 1):
  NOT_ASSESSED → ASSESSED → VALIDATED → ARCHIVED
                     ↓           ↓
                 ARCHIVED    ARCHIVED (when new assessment created)

State Definitions:
  - NOT_ASSESSED: No assessment exists (initial state)
  - ASSESSED: Instructor created assessment, pending validation (if required)
  - VALIDATED: Admin validated assessment (optional, per business rule)
  - ARCHIVED: Old assessment replaced by newer one (terminal state)
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from ..database import Base


class FootballSkillAssessment(Base):
    """
    Individual skill assessment record with lifecycle state machine.

    Each row represents ONE assessment of ONE skill at ONE point in time.
    Multiple assessments are averaged to calculate the current skill level.

    Lifecycle:
        - Created with status='ASSESSED'
        - Optionally validated (status='VALIDATED') if requires_validation=True
        - Archived (status='ARCHIVED') when new assessment created for same skill

    Concurrency Protection:
        - UniqueConstraint: Only 1 active (ASSESSED/VALIDATED) per (user_license_id, skill_name)
        - Prevents duplicate assessments during concurrent creation

    Example:
        Heading assessments for Junior Intern:
        - 2025-11-20: 7/10 = 70% (status=ASSESSED)
        - 2025-11-25: 8/10 = 80% (status=ASSESSED, previous archived)
        - 2025-12-01: 6/10 = 60% (status=VALIDATED, previous archived)
        → Current average: 60% (only active assessment counted)
    """
    __tablename__ = "football_skill_assessments"

    id = Column(Integer, primary_key=True, index=True)

    # Which license/student?
    user_license_id = Column(Integer, ForeignKey("user_licenses.id", ondelete="CASCADE"), nullable=False)

    # Which skill? (44 skills from skills_config.py — Phase 3 expansion)
    skill_name = Column(String(50), nullable=False)

    # Assessment scores
    points_earned = Column(Integer, nullable=False)  # e.g., 7
    points_total = Column(Integer, nullable=False)   # e.g., 10
    percentage = Column(Float, nullable=False)       # e.g., 70.0 (auto-calculated)

    # Who assessed and when?
    assessed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=False)
    assessed_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))

    # Optional notes from instructor
    notes = Column(Text, nullable=True)

    # ========================================================================
    # LIFECYCLE STATE MACHINE (Phase 1 - Migration: 2026_02_24_1200)
    # ========================================================================

    # Lifecycle status (NOT_ASSESSED, ASSESSED, VALIDATED, ARCHIVED)
    status = Column(String(20), nullable=False, default='ASSESSED', index=True)

    # Validation tracking (nullable — optional validation per business rule)
    validated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    validated_at = Column(DateTime, nullable=True)
    requires_validation = Column(Boolean, nullable=False, default=False)

    # Archive tracking
    archived_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    archived_at = Column(DateTime, nullable=True)
    archived_reason = Column(Text, nullable=True)

    # State transition audit trail
    previous_status = Column(String(20), nullable=True)
    status_changed_at = Column(DateTime, nullable=True)
    status_changed_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    progression_outcome_id = Column(UUID(as_uuid=True), ForeignKey("player_progression_outcomes.id", ondelete="RESTRICT"), nullable=True, unique=True)

    # Relationships
    user_license = relationship("UserLicense", foreign_keys=[user_license_id])
    assessor = relationship("User", foreign_keys=[assessed_by])
    validator = relationship("User", foreign_keys=[validated_by])
    archiver = relationship("User", foreign_keys=[archived_by])
    status_changer = relationship("User", foreign_keys=[status_changed_by])

    def to_dict(self, include_lifecycle: bool = True) -> Dict[str, Any]:
        """
        Convert to dictionary for API responses.

        Args:
            include_lifecycle: Include lifecycle fields (status, validation, archive)

        Returns:
            Dictionary representation of assessment
        """
        result = {
            "id": self.id,
            "user_license_id": self.user_license_id,
            "skill_name": self.skill_name,
            "points_earned": self.points_earned,
            "points_total": self.points_total,
            "percentage": round(self.percentage, 1),
            "assessed_by": self.assessed_by,
            "assessed_at": self.assessed_at.isoformat() if self.assessed_at else None,
            "notes": self.notes
        }

        # Include lifecycle fields if requested
        if include_lifecycle:
            result.update({
                # Lifecycle status
                "status": self.status,

                # Validation tracking
                "validated_by": self.validated_by,
                "validated_at": self.validated_at.isoformat() if self.validated_at else None,
                "requires_validation": self.requires_validation,

                # Archive tracking
                "archived_by": self.archived_by,
                "archived_at": self.archived_at.isoformat() if self.archived_at else None,
                "archived_reason": self.archived_reason,

                # State transition audit
                "previous_status": self.previous_status,
                "status_changed_at": self.status_changed_at.isoformat() if self.status_changed_at else None,
                "status_changed_by": self.status_changed_by
            })

        return result

    @classmethod
    def calculate_percentage(cls, points_earned: int, points_total: int) -> float:
        """Calculate percentage from points"""
        if points_total == 0:
            return 0.0
        return round((points_earned / points_total) * 100, 1)

    def __repr__(self):
        return f"<FootballSkillAssessment {self.skill_name}: {self.points_earned}/{self.points_total} ({self.percentage}%)>"
