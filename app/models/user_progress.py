from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, ForeignKey, Enum as SQLEnum, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid
import enum

from ..database import Base


# ========================================
# NEW: SPECIALIZATION LEVEL SYSTEM MODELS
# ========================================

class Specialization(Base):
    """
    Master table for specializations - MINIMAL HYBRID ARCHITECTURE

    HYBRID ARCHITECTURE:
    - DB: Maintains ONLY referential integrity (FK constraints)
    - JSON: Source of Truth for content (name, description, icon, levels)
    - Service: Bridge between DB validation and JSON content

    Columns:
    - id (PK): Unique identifier matching SpecializationType enum
    - is_active: Availability flag (can be toggled without code changes)
    - created_at: Audit trail

    ❌ NO CONTENT COLUMNS - use JSON configs via SpecializationConfigLoader
    """
    __tablename__ = "specializations"

    id = Column(String(50), primary_key=True,
                comment='Matches SpecializationType enum values')
    is_active = Column(Boolean, nullable=False, default=True,
                      comment='Controls availability without code changes')
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                       comment='Audit trail for when specialization was created')

    def __repr__(self):
        return f"<Specialization {self.id} (active={self.is_active})>"


class PlayerLevel(Base):
    """GanCuju Player belt levels (8 levels)"""
    __tablename__ = "player_levels"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    color = Column(String(50), nullable=False)
    required_xp = Column(Integer, nullable=False)
    required_sessions = Column(Integer, nullable=False)
    description = Column(Text)
    license_title = Column(String(255), nullable=False)

    def __repr__(self):
        return f"<PlayerLevel {self.id}: {self.name} ({self.color})>"


class CoachLevel(Base):
    """LFA Football Coach license levels (8 levels)"""
    __tablename__ = "coach_levels"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    required_xp = Column(Integer, nullable=False)
    required_sessions = Column(Integer, nullable=False)
    theory_hours = Column(Integer, nullable=False)
    practice_hours = Column(Integer, nullable=False)
    description = Column(Text)
    license_title = Column(String(255), nullable=False)

    def __repr__(self):
        return f"<CoachLevel {self.id}: {self.name}>"


class InternshipLevel(Base):
    """Startup Spirit Internship levels (3 levels)"""
    __tablename__ = "internship_levels"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    required_xp = Column(Integer, nullable=False)
    required_sessions = Column(Integer, nullable=False)
    total_hours = Column(Integer, nullable=False)
    description = Column(Text)
    license_title = Column(String(255), nullable=False)

    def __repr__(self):
        return f"<InternshipLevel {self.id}: {self.name}>"


class SpecializationProgress(Base):
    """Tracks student progress in a specialization"""
    __tablename__ = "specialization_progress"

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(Integer, ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    specialization_id = Column(String(50), ForeignKey('specializations.id', ondelete='CASCADE'), nullable=False)
    current_level = Column(Integer, default=1)
    total_xp = Column(Integer, default=0)
    completed_sessions = Column(Integer, default=0)
    completed_projects = Column(Integer, default=0)
    theory_hours_completed = Column(Integer, default=0)  # For COACH specialization
    practice_hours_completed = Column(Integer, default=0)  # For COACH specialization
    last_activity = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    student = relationship("User", foreign_keys=[student_id])
    specialization = relationship("Specialization", foreign_keys=[specialization_id])

    def __repr__(self):
        return f"<SpecializationProgress student={self.student_id} spec={self.specialization_id} level={self.current_level}>"


# ========================================
# OLD: TRACK SYSTEM MODELS (DEPRECATED)
# ========================================

class TrackProgressStatus(enum.Enum):
    """Track progress status enumeration"""
    ENROLLED = "enrolled"
    ACTIVE = "active"
    COMPLETED = "completed"
    SUSPENDED = "suspended"
    DROPPED = "dropped"

class ModuleProgressStatus(enum.Enum):
    """Module progress status enumeration"""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

class UserTrackProgress(Base):
    """
    Hallgató Track Progress - Szakirány haladás nyomon követése
    Támogatja a párhuzamos track-eket
    """
    __tablename__ = "user_track_progresses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)  # Fixed: Integer to match users.id
    track_id = Column(UUID(as_uuid=True), ForeignKey("tracks.id"), nullable=False)
    track_release_id = Column(UUID(as_uuid=True), ForeignKey("track_releases.id", ondelete="RESTRICT"), nullable=True)
    enrollment_date = Column(DateTime, default=datetime.utcnow)
    current_semester = Column(Integer, default=1)
    status = Column(SQLEnum(TrackProgressStatus), default=TrackProgressStatus.ENROLLED)
    completion_percentage = Column(Float, default=0.0)
    certificate_id = Column(UUID(as_uuid=True), ForeignKey("issued_certificates.id"), nullable=True)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User")
    track = relationship("Track", back_populates="user_progresses")
    certificate = relationship("IssuedCertificate")
    module_progresses = relationship("UserModuleProgress", back_populates="track_progress", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<UserTrackProgress {self.user.name} in {self.track.name}>"

    def start(self):
        """Start the track progress"""
        self.status = TrackProgressStatus.ACTIVE
        self.started_at = datetime.utcnow()

    def complete(self):
        """Mark track as completed"""
        self.status = TrackProgressStatus.COMPLETED
        self.completed_at = datetime.utcnow()
        self.completion_percentage = 100.0

    def suspend(self):
        """Suspend track progress"""
        self.status = TrackProgressStatus.SUSPENDED

    def calculate_completion_percentage(self) -> float:
        """Calculate completion percentage based on module progresses"""
        if not self.module_progresses:
            return 0.0
        
        total_modules = len(self.track.modules)
        if total_modules == 0:
            return 0.0
        
        completed_modules = len([mp for mp in self.module_progresses 
                               if mp.status == ModuleProgressStatus.COMPLETED])
        
        percentage = (completed_modules / total_modules) * 100
        self.completion_percentage = round(percentage, 2)
        return self.completion_percentage

    @property
    def is_ready_for_certificate(self) -> bool:
        """Check if user is ready to receive certificate"""
        return (
            self.status == TrackProgressStatus.COMPLETED and
            self.completion_percentage >= 100.0 and
            self.certificate_id is None
        )

    @property
    def duration_days(self) -> int:
        """Calculate duration in days"""
        if not self.started_at:
            return 0
        
        end_date = self.completed_at or datetime.utcnow()
        return (end_date - self.started_at).days

class UserModuleProgress(Base):
    """
    Hallgató Modul Progress - Modul szintű haladás nyomon követése
    """
    __tablename__ = "user_module_progresses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_track_progress_id = Column(UUID(as_uuid=True), ForeignKey("user_track_progresses.id"), nullable=False)
    module_id = Column(UUID(as_uuid=True), ForeignKey("modules.id"), nullable=False)
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    grade = Column(Float)  # 0.0 - 100.0
    status = Column(SQLEnum(ModuleProgressStatus), default=ModuleProgressStatus.NOT_STARTED)
    attempts = Column(Integer, default=0)
    time_spent_minutes = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    track_progress = relationship("UserTrackProgress", back_populates="module_progresses")
    module = relationship("Module", back_populates="user_progresses")

    def __repr__(self):
        return f"<UserModuleProgress {self.module.name} - {self.status.value}>"

    def start(self):
        """Start the module"""
        self.status = ModuleProgressStatus.IN_PROGRESS
        self.started_at = datetime.utcnow()
        self.attempts += 1

    def complete(self, grade: float = None):
        """Complete the module"""
        self.status = ModuleProgressStatus.COMPLETED
        self.completed_at = datetime.utcnow()
        if grade is not None:
            self.grade = grade

    def fail(self):
        """Mark module as failed"""
        self.status = ModuleProgressStatus.FAILED

    def reset(self):
        """Reset module progress for retry"""
        self.status = ModuleProgressStatus.NOT_STARTED
        self.started_at = None
        self.completed_at = None
        self.grade = None

    @property
    def duration_hours(self) -> float:
        """Calculate duration in hours"""
        if not self.started_at:
            return 0.0
        
        end_date = self.completed_at or datetime.utcnow()
        duration_seconds = (end_date - self.started_at).total_seconds()
        return round(duration_seconds / 3600, 2)

    @property
    def is_passed(self) -> bool:
        """Check if module is passed (grade >= 60%)"""
        return self.grade is not None and self.grade >= 60.0

    @property
    def grade_letter(self) -> str:
        """Convert numeric grade to letter grade"""
        if self.grade is None:
            return "N/A"
        
        if self.grade >= 90:
            return "A"
        elif self.grade >= 80:
            return "B" 
        elif self.grade >= 70:
            return "C"
        elif self.grade >= 60:
            return "D"
        else:
            return "F"
