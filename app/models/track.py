from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, JSON,
    String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from datetime import datetime
import uuid

from ..database import Base

class Track(Base):
    """
    Szakirány (Track) - Legfelső szintű oktatási egység
    Pl: LFA Internship, Coach, GānCuju
    """
    __tablename__ = "tracks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)  # "LFA Internship"
    code = Column(String(50), nullable=False, unique=True)  # "INT"
    description = Column(Text)
    duration_semesters = Column(Integer, default=1)
    prerequisites = Column(JSON, default=dict)  # Más track-ek előfeltételei
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    # PC3 canonical education identity. Nullable preserves legacy rows.
    specialization_id = Column(String(50), ForeignKey("specializations.id"), nullable=True)
    stable_key = Column(String(100), nullable=True)
    default_locale = Column(String(35), nullable=True)
    translations = Column(JSON, nullable=False, default=dict)
    current_release_id = Column(UUID(as_uuid=True), ForeignKey("track_releases.id", use_alter=True), nullable=True)

    # Relationships
    modules = relationship("Module", back_populates="track", cascade="all, delete-orphan", foreign_keys="Module.track_id")
    releases = relationship("TrackRelease", back_populates="track", cascade="all, delete-orphan", foreign_keys="TrackRelease.track_id")
    current_release = relationship("TrackRelease", foreign_keys=[current_release_id], post_update=True)
    certificate_template = relationship("CertificateTemplate", back_populates="track", uselist=False)
    user_progresses = relationship("UserTrackProgress", back_populates="track")

    def __repr__(self):
        return f"<Track {self.name} ({self.code})>"

    @property
    def total_modules(self):
        """Total number of modules in this track"""
        return len(self.modules)

    @property
    def mandatory_modules(self):
        """Number of mandatory modules"""
        return len([m for m in self.modules if m.is_mandatory])

    __table_args__ = (
        UniqueConstraint("specialization_id", "stable_key", name="uq_track_program_stable_key"),
    )


class TrackRelease(Base):
    """Immutable publish boundary for one version of a canonical Track."""
    __tablename__ = "track_releases"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    track_id = Column(UUID(as_uuid=True), ForeignKey("tracks.id", ondelete="CASCADE"), nullable=False)
    version = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="DRAFT")
    content_hash = Column(String(64), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    published_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    track = relationship("Track", back_populates="releases", foreign_keys=[track_id])
    modules = relationship("Module", back_populates="track_release", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("track_id", "version", name="uq_track_release_version"),
        CheckConstraint("status IN ('DRAFT','PUBLISHED','ARCHIVED')", name="ck_track_release_status"),
    )

class Module(Base):
    """
    Modul - Track-en belüli tanulási egység
    Pl: "Taktikai Alapok", "Megyeri Gyakorlat"
    """
    __tablename__ = "modules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    track_id = Column(UUID(as_uuid=True), ForeignKey("tracks.id"), nullable=False)
    semester_id = Column(Integer, ForeignKey("semesters.id"))  # Fixed: Integer type to match semesters.id
    name = Column(String(255), nullable=False)
    description = Column(Text)
    order_in_track = Column(Integer, default=0)
    learning_objectives = Column(JSON, default=list)
    estimated_hours = Column(Integer, default=0)
    is_mandatory = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    track_release_id = Column(UUID(as_uuid=True), ForeignKey("track_releases.id", ondelete="CASCADE"), nullable=True)
    stable_key = Column(String(100), nullable=True)
    translations = Column(JSON, nullable=False, default=dict)

    # Relationships
    track = relationship("Track", back_populates="modules", foreign_keys=[track_id])
    track_release = relationship("TrackRelease", back_populates="modules")
    semester = relationship("Semester")
    components = relationship("ModuleComponent", back_populates="module", cascade="all, delete-orphan")
    user_progresses = relationship("UserModuleProgress", back_populates="module")

    def __repr__(self):
        return f"<Module {self.name} in {self.track.name}>"

    @property
    def total_components(self):
        """Total number of components in this module"""
        return len(self.components)

    @property
    def mandatory_components(self):
        """Number of mandatory components"""
        return len([c for c in self.components if c.is_mandatory])

    lessons = relationship("Lesson", back_populates="module", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("track_release_id", "stable_key", name="uq_module_release_stable_key"),
        UniqueConstraint("track_release_id", "order_in_track", name="uq_module_release_order"),
    )


class Lesson(Base):
    """Canonical Player curriculum lesson within one versioned module."""
    __tablename__ = "lessons"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    module_id = Column(UUID(as_uuid=True), ForeignKey("modules.id", ondelete="CASCADE"), nullable=False)
    stable_key = Column(String(100), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    topic_key = Column(String(150), nullable=True)
    order_in_module = Column(Integer, nullable=False)
    is_mandatory = Column(Boolean, nullable=False, default=True)
    translations = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    module = relationship("Module", back_populates="lessons")
    components = relationship("ModuleComponent", back_populates="lesson")
    assessments = relationship("LessonAssessment", back_populates="lesson", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("module_id", "stable_key", name="uq_lesson_module_stable_key"),
        UniqueConstraint("module_id", "order_in_module", name="uq_lesson_module_order"),
    )

class ModuleComponent(Base):
    """
    Modul Komponens - Konkrét tanulási elemek
    Típusok: theory, quiz, project, assignment, video
    """
    __tablename__ = "module_components"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    module_id = Column(UUID(as_uuid=True), ForeignKey("modules.id"), nullable=False)
    type = Column(String(50), nullable=False)  # 'theory', 'quiz', 'project', 'assignment', 'video'
    name = Column(String(255), nullable=False)
    description = Column(Text)
    order_in_module = Column(Integer, default=0)
    estimated_minutes = Column(Integer, default=0)
    is_mandatory = Column(Boolean, default=True)
    component_data = Column(JSON, default=dict)  # Type-specific data
    created_at = Column(DateTime, default=datetime.utcnow)
    lesson_id = Column(UUID(as_uuid=True), ForeignKey("lessons.id", ondelete="CASCADE"), nullable=True)
    stable_key = Column(String(100), nullable=True)
    translations = Column(JSON, nullable=False, default=dict)
    payload_schema_version = Column(String(20), nullable=False, default="1.0")

    # Relationships
    module = relationship("Module", back_populates="components")
    lesson = relationship("Lesson", back_populates="components")

    def __repr__(self):
        return f"<ModuleComponent {self.name} ({self.type})>"

    @property
    def estimated_hours(self):
        """Convert minutes to hours"""
        return round(self.estimated_minutes / 60, 1) if self.estimated_minutes else 0

    __table_args__ = (
        UniqueConstraint("lesson_id", "stable_key", name="uq_component_lesson_stable_key"),
        UniqueConstraint("lesson_id", "order_in_module", name="uq_component_lesson_order"),
    )


class LessonAssessment(Base):
    """Logical assessment placement; localized Quiz rows are its variants."""
    __tablename__ = "lesson_assessments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lesson_id = Column(UUID(as_uuid=True), ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False)
    track_release_id = Column(UUID(as_uuid=True), ForeignKey("track_releases.id", ondelete="CASCADE"), nullable=False)
    stable_key = Column(String(100), nullable=False)
    delivery_mode = Column(String(20), nullable=False)
    purpose = Column(String(20), nullable=False)
    order_in_lesson = Column(Integer, nullable=False)
    difficulty = Column(String(20), nullable=True)
    topic_key = Column(String(150), nullable=True)
    is_required = Column(Boolean, nullable=False, default=False)
    max_attempts = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)

    lesson = relationship("Lesson", back_populates="assessments")
    variants = relationship("LessonAssessmentQuiz", back_populates="assessment", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("lesson_id", "stable_key", name="uq_assessment_lesson_stable_key"),
        UniqueConstraint("lesson_id", "order_in_lesson", name="uq_assessment_lesson_order"),
        CheckConstraint("delivery_mode IN ('STANDARD','ADAPTIVE','EXAM')", name="ck_lesson_assessment_delivery"),
        CheckConstraint("purpose IN ('FORMATIVE','SUMMATIVE')", name="ck_lesson_assessment_purpose"),
    )


class LessonAssessmentQuiz(Base):
    __tablename__ = "lesson_assessment_quizzes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lesson_assessment_id = Column(UUID(as_uuid=True), ForeignKey("lesson_assessments.id", ondelete="CASCADE"), nullable=False)
    quiz_id = Column(Integer, ForeignKey("quizzes.id", ondelete="RESTRICT"), nullable=False)
    locale = Column(String(35), nullable=False)

    assessment = relationship("LessonAssessment", back_populates="variants")
    quiz = relationship("Quiz")

    __table_args__ = (
        UniqueConstraint("lesson_assessment_id", "locale", name="uq_assessment_locale"),
        UniqueConstraint("lesson_assessment_id", "quiz_id", name="uq_assessment_quiz"),
    )


class EducationProgressEvent(Base):
    """Immutable provenance record for an outcome against an exact release."""
    __tablename__ = "education_progress_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    track_id = Column(UUID(as_uuid=True), ForeignKey("tracks.id", ondelete="RESTRICT"), nullable=False)
    track_release_id = Column(UUID(as_uuid=True), ForeignKey("track_releases.id", ondelete="RESTRICT"), nullable=False)
    lesson_id = Column(UUID(as_uuid=True), ForeignKey("lessons.id", ondelete="RESTRICT"), nullable=False)
    lesson_assessment_id = Column(UUID(as_uuid=True), ForeignKey("lesson_assessments.id", ondelete="RESTRICT"), nullable=False)
    quiz_id = Column(Integer, ForeignKey("quizzes.id", ondelete="RESTRICT"), nullable=False)
    quiz_attempt_id = Column(Integer, ForeignKey("quiz_attempts.id", ondelete="RESTRICT"), nullable=False)
    quiz_revision = Column(Integer, nullable=False)
    locale = Column(String(35), nullable=False)
    event_type = Column(String(30), nullable=False, default="ASSESSMENT_COMPLETED")
    idempotency_key = Column(String(120), nullable=False, unique=True)
    provenance = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow, nullable=False)
