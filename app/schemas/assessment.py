"""Public contract for the canonical Player assessment lifecycle."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AssessmentStartRequest(BaseModel):
    locale: str = "en"


class IssuedOptionResponse(BaseModel):
    id: int
    text: str
    order: int


class IssuedQuestionResponse(BaseModel):
    id: UUID
    sequence: int
    question_type: str
    points: int
    text: str
    options: list[IssuedOptionResponse]


class AssessmentAttemptResponse(BaseModel):
    id: UUID
    track_release_id: UUID
    lesson_id: UUID
    lesson_assessment_id: UUID
    quiz_revision: int
    locale: str
    delivery_mode: str
    status: str
    questions: list[IssuedQuestionResponse]


class AssessmentAnswerRequest(BaseModel):
    issued_question_id: UUID
    selected_option_id: int | None = None
    answer_text: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=120)


class AssessmentAnswerResponse(BaseModel):
    issued_question_id: UUID
    accepted: bool
    is_correct: bool
    points_earned: int


class AssessmentCompleteRequest(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=120)


class AssessmentOutcomeResponse(BaseModel):
    attempt_id: UUID
    status: str
    total_questions: int
    answered_questions: int
    correct_answers: int
    total_points: int
    earned_points: int
    score_percent: float
    passed: bool
    xp_awarded: int
    track_release_id: UUID
    lesson_id: UUID
    lesson_assessment_id: UUID
    quiz_revision: int
    locale: str
    provenance: dict[str, Any]
