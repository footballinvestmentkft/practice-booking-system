"""Canonical, version-bound assessment attempt and issued-question records."""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey, Integer,
    JSON, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class AssessmentAttempt(Base):
    __tablename__ = "assessment_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    track_release_id = Column(UUID(as_uuid=True), ForeignKey("track_releases.id", ondelete="RESTRICT"), nullable=False)
    lesson_id = Column(UUID(as_uuid=True), ForeignKey("lessons.id", ondelete="RESTRICT"), nullable=False)
    lesson_assessment_id = Column(UUID(as_uuid=True), ForeignKey("lesson_assessments.id", ondelete="RESTRICT"), nullable=False)
    quiz_id = Column(Integer, ForeignKey("quizzes.id", ondelete="RESTRICT"), nullable=False)
    quiz_revision = Column(Integer, nullable=False)
    locale = Column(String(35), nullable=False)
    delivery_mode = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="IN_PROGRESS")
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
    total_questions = Column(Integer, nullable=False, default=0)
    answered_questions = Column(Integer, nullable=False, default=0)
    correct_answers = Column(Integer, nullable=False, default=0)
    total_points = Column(Integer, nullable=False, default=0)
    earned_points = Column(Integer, nullable=False, default=0)
    score_percent = Column(Float, nullable=True)
    passed = Column(Boolean, nullable=False, default=False)
    xp_awarded = Column(Integer, nullable=False, default=0)
    completion_idempotency_key = Column(String(120), nullable=True)
    content_provenance = Column(JSON, nullable=False, default=dict)

    user = relationship("User")
    release = relationship("TrackRelease")
    lesson = relationship("Lesson")
    assessment = relationship("LessonAssessment")
    quiz = relationship("Quiz")
    issued_questions = relationship(
        "AssessmentIssuedQuestion",
        back_populates="attempt",
        cascade="all, delete-orphan",
        order_by="AssessmentIssuedQuestion.sequence",
    )

    __table_args__ = (
        CheckConstraint("status IN ('IN_PROGRESS','COMPLETED','VOIDED')", name="ck_assessment_attempt_status"),
        CheckConstraint("delivery_mode IN ('STANDARD','ADAPTIVE','EXAM')", name="ck_assessment_attempt_delivery"),
        UniqueConstraint("user_id", "completion_idempotency_key", name="uq_assessment_completion_key"),
    )


class AssessmentIssuedQuestion(Base):
    __tablename__ = "assessment_issued_questions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    attempt_id = Column(UUID(as_uuid=True), ForeignKey("assessment_attempts.id", ondelete="CASCADE"), nullable=False, index=True)
    source_question_id = Column(Integer, ForeignKey("quiz_questions.id", ondelete="RESTRICT"), nullable=False)
    sequence = Column(Integer, nullable=False)
    question_type = Column(String(40), nullable=False)
    points = Column(Integer, nullable=False)
    question_snapshot = Column(JSON, nullable=False)
    option_snapshot = Column(JSON, nullable=False, default=list)
    correct_option_id = Column(Integer, nullable=True)
    correct_answer_snapshot = Column(Text, nullable=True)
    answer_idempotency_key = Column(String(120), nullable=True)
    selected_option_id = Column(Integer, nullable=True)
    answer_text = Column(Text, nullable=True)
    is_correct = Column(Boolean, nullable=True)
    omitted = Column(Boolean, nullable=False, default=False)
    points_earned = Column(Integer, nullable=False, default=0)
    issued_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    answered_at = Column(DateTime(timezone=True), nullable=True)

    attempt = relationship("AssessmentAttempt", back_populates="issued_questions")
    source_question = relationship("QuizQuestion")

    __table_args__ = (
        UniqueConstraint("attempt_id", "source_question_id", name="uq_assessment_issued_source"),
        UniqueConstraint("attempt_id", "sequence", name="uq_assessment_issued_sequence"),
        UniqueConstraint("attempt_id", "answer_idempotency_key", name="uq_assessment_answer_key"),
        CheckConstraint("points > 0", name="ck_assessment_issued_points_positive"),
    )
