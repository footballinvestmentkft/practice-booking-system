"""Stable learner-facing contract for canonical Player education."""
from typing import Any
from pydantic import BaseModel, Field


class EducationTrackSummary(BaseModel):
    id: str
    stable_key: str
    code: str
    name: str
    default_locale: str
    release_id: str


class AssessmentVariantResponse(BaseModel):
    quiz_id: int
    locale: str


class LessonAssessmentResponse(BaseModel):
    id: str
    stable_key: str
    delivery_mode: str
    purpose: str
    difficulty: str | None = None
    variants: list[AssessmentVariantResponse] = Field(default_factory=list)


class EducationComponentResponse(BaseModel):
    id: str
    stable_key: str
    type: str
    name: str
    payload: dict[str, Any] = Field(default_factory=dict)


class EducationLessonResponse(BaseModel):
    id: str
    stable_key: str
    title: str
    topic_key: str | None = None
    components: list[EducationComponentResponse] = Field(default_factory=list)
    assessments: list[LessonAssessmentResponse] = Field(default_factory=list)


class EducationModuleResponse(BaseModel):
    id: str
    stable_key: str
    name: str
    lessons: list[EducationLessonResponse] = Field(default_factory=list)


class EducationCurriculumResponse(BaseModel):
    program_id: str
    track_id: str
    release_id: str
    release_version: int
    locale: str
    modules: list[EducationModuleResponse] = Field(default_factory=list)
