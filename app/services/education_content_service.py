"""Canonical Player education hierarchy and release boundary.

This service is deliberately narrow: PC3 supports LFA Football Player content
only. Other specializations may share low-level platform capabilities later,
but must opt into their own validated curriculum policy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.quiz import (
    ContentStatus, OptionType, QuestionMetadata, QuestionType, Quiz,
    QuizAnswerOption, QuizAttempt, QuizCategory, QuizDifficulty, QuizQuestion,
)
from app.models.track import (
    EducationProgressEvent, Lesson, LessonAssessment, LessonAssessmentQuiz,
    Module, ModuleComponent, Track, TrackRelease,
)
from app.models.user import User
from app.models.user_progress import Specialization
from app.services.player_identity_service import get_authorized_football_player_context


PLAYER_PROGRAM_ID = "LFA_FOOTBALL_PLAYER"
_LOCALE_RE = re.compile(
    r"^[A-Za-z]{2,3}(?:-[A-Za-z]{4})?(?:-(?:[A-Za-z]{2}|[0-9]{3}))?(?:-[A-Za-z0-9]{5,8})*$"
)


class EducationContentError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def normalize_bcp47_locale(value: str) -> str:
    """Validate and canonicalize the supported structural subset of BCP-47."""
    if not isinstance(value, str) or not _LOCALE_RE.fullmatch(value):
        raise EducationContentError("INVALID_LOCALE")
    parts = value.split("-")
    normalized = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            normalized.append(part.title())
        elif (len(part) == 2 and part.isalpha()) or (len(part) == 3 and part.isdigit()):
            normalized.append(part.upper())
        else:
            normalized.append(part.lower())
    return "-".join(normalized)


def _uuid(value: UUID | str) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _translations(value: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for locale, payload in (value or {}).items():
        normalized = normalize_bcp47_locale(locale)
        if not isinstance(payload, dict):
            raise EducationContentError("INVALID_TRANSLATION")
        result[normalized] = payload
    return result


def _localized(base: str, translations: dict[str, Any], locale: str, key: str) -> str:
    return translations.get(locale, {}).get(key) or base


class EducationContentService:
    """Single authority for canonical Player hierarchy writes and reads."""

    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _assert_player_program(program_id: str) -> None:
        if program_id != PLAYER_PROGRAM_ID:
            raise EducationContentError("UNSUPPORTED_EDUCATION_PROGRAM")

    @staticmethod
    def _assert_draft(release: TrackRelease) -> None:
        if release.status != "DRAFT":
            raise EducationContentError("RELEASE_IMMUTABLE")

    def create_track(
        self, *, program_id: str, stable_key: str, code: str, name: str,
        default_locale: str = "en", description: str | None = None,
        translations: dict[str, Any] | None = None,
    ) -> Track:
        self._assert_player_program(program_id)
        if self.db.get(Specialization, program_id) is None:
            raise EducationContentError("PROGRAM_NOT_FOUND")
        track = Track(
            specialization_id=program_id, stable_key=stable_key, code=code,
            name=name, description=description,
            default_locale=normalize_bcp47_locale(default_locale),
            translations=_translations(translations), is_active=True,
        )
        self.db.add(track)
        self.db.flush()
        return track

    def create_release(self, *, track_id: UUID | str, version: int) -> TrackRelease:
        track = self.db.get(Track, _uuid(track_id))
        if track is None:
            raise EducationContentError("TRACK_NOT_FOUND")
        self._assert_player_program(track.specialization_id)
        if version < 1:
            raise EducationContentError("INVALID_RELEASE_VERSION")
        release = TrackRelease(track_id=track.id, version=version, status="DRAFT")
        self.db.add(release)
        self.db.flush()
        return release

    def add_module(
        self, *, release_id: UUID | str, stable_key: str, name: str, order: int,
        description: str | None = None, translations: dict[str, Any] | None = None,
    ) -> Module:
        release = self.db.get(TrackRelease, _uuid(release_id))
        if release is None:
            raise EducationContentError("RELEASE_NOT_FOUND")
        self._assert_draft(release)
        module = Module(
            track_id=release.track_id, track_release_id=release.id,
            stable_key=stable_key, name=name, description=description,
            order_in_track=order, translations=_translations(translations),
        )
        self.db.add(module)
        self.db.flush()
        return module

    def add_lesson(
        self, *, module_id: UUID | str, stable_key: str, title: str, order: int,
        description: str | None = None, topic_key: str | None = None,
        translations: dict[str, Any] | None = None,
    ) -> Lesson:
        module = self.db.get(Module, _uuid(module_id))
        if module is None or module.track_release is None:
            raise EducationContentError("CANONICAL_MODULE_NOT_FOUND")
        self._assert_draft(module.track_release)
        lesson = Lesson(
            module_id=module.id, stable_key=stable_key, title=title,
            description=description, topic_key=topic_key, order_in_module=order,
            translations=_translations(translations),
        )
        self.db.add(lesson)
        self.db.flush()
        return lesson

    def add_component(
        self, *, lesson_id: UUID | str, stable_key: str, component_type: str,
        name: str, order: int, payload: dict[str, Any],
        description: str | None = None, translations: dict[str, Any] | None = None,
        payload_schema_version: str = "1.0",
    ) -> ModuleComponent:
        lesson = self.db.get(Lesson, _uuid(lesson_id))
        if lesson is None:
            raise EducationContentError("LESSON_NOT_FOUND")
        self._assert_draft(lesson.module.track_release)
        component = ModuleComponent(
            module_id=lesson.module_id, lesson_id=lesson.id, stable_key=stable_key,
            type=component_type, name=name, description=description,
            order_in_module=order, component_data=payload,
            translations=_translations(translations),
            payload_schema_version=payload_schema_version,
        )
        self.db.add(component)
        self.db.flush()
        return component

    def add_translation(self, entity: Any, locale: str, payload: dict[str, Any]) -> Any:
        if isinstance(entity, Module):
            release = entity.track_release
        elif isinstance(entity, Lesson):
            release = entity.module.track_release
        elif isinstance(entity, ModuleComponent):
            release = entity.lesson.module.track_release if entity.lesson else None
        elif isinstance(entity, Track):
            release = None
            if entity.releases and any(row.status != "DRAFT" for row in entity.releases):
                raise EducationContentError("RELEASE_IMMUTABLE")
        else:
            raise EducationContentError("UNSUPPORTED_TRANSLATION_ENTITY")
        if release is not None:
            self._assert_draft(release)
        normalized = normalize_bcp47_locale(locale)
        translations = dict(entity.translations or {})
        translations[normalized] = payload
        entity.translations = translations
        self.db.flush()
        return entity

    def attach_assessment(
        self, *, lesson_id: UUID | str, stable_key: str, delivery_mode: str,
        purpose: str, order: int, difficulty: str | None = None,
        topic_key: str | None = None, is_required: bool = False,
        max_attempts: int | None = None,
    ) -> LessonAssessment:
        lesson = self.db.get(Lesson, _uuid(lesson_id))
        if lesson is None:
            raise EducationContentError("LESSON_NOT_FOUND")
        release = lesson.module.track_release
        self._assert_draft(release)
        assessment = LessonAssessment(
            lesson_id=lesson.id, track_release_id=release.id,
            stable_key=stable_key, delivery_mode=delivery_mode.upper(),
            purpose=purpose.upper(), order_in_lesson=order,
            difficulty=difficulty.upper() if difficulty else None,
            topic_key=topic_key, is_required=is_required, max_attempts=max_attempts,
        )
        self.db.add(assessment)
        self.db.flush()
        return assessment

    def add_assessment_variant(
        self, assessment_id: UUID | str, quiz_id: int, *, locale: str,
    ) -> LessonAssessmentQuiz:
        assessment = self.db.get(LessonAssessment, _uuid(assessment_id))
        quiz = self.db.get(Quiz, quiz_id)
        if assessment is None or quiz is None:
            raise EducationContentError("ASSESSMENT_OR_QUIZ_NOT_FOUND")
        self._assert_draft(assessment.lesson.module.track_release)
        normalized = normalize_bcp47_locale(locale)
        if normalize_bcp47_locale(quiz.language) != normalized:
            raise EducationContentError("QUIZ_LOCALE_MISMATCH")
        variant = LessonAssessmentQuiz(
            lesson_assessment_id=assessment.id, quiz_id=quiz.id, locale=normalized,
        )
        self.db.add(variant)
        self.db.flush()
        return variant

    def _release_graph(self, release: TrackRelease) -> dict[str, Any]:
        modules = []
        for module in sorted(release.modules, key=lambda row: row.order_in_track):
            lessons = []
            for lesson in sorted(module.lessons, key=lambda row: row.order_in_module):
                components = [
                    {"key": c.stable_key, "type": c.type, "name": c.name,
                     "description": c.description, "order": c.order_in_module,
                     "mandatory": c.is_mandatory,
                     "payload_schema_version": c.payload_schema_version,
                     "payload": c.component_data, "translations": c.translations}
                    for c in sorted(lesson.components, key=lambda row: row.order_in_module)
                ]
                assessments = [
                    {"key": a.stable_key, "mode": a.delivery_mode,
                     "purpose": a.purpose, "order": a.order_in_lesson,
                     "difficulty": a.difficulty, "topic_key": a.topic_key,
                     "required": a.is_required, "max_attempts": a.max_attempts,
                     "variants": [
                         {
                             "locale": v.locale,
                             "quiz_id": v.quiz_id,
                             "content_key": v.quiz.content_key,
                             "revision": v.quiz.revision,
                             "source_checksum": v.quiz.source_checksum,
                         }
                         for v in sorted(a.variants, key=lambda row: (row.locale, row.quiz_id))
                     ]}
                    for a in sorted(lesson.assessments, key=lambda row: row.order_in_lesson)
                ]
                lessons.append({"key": lesson.stable_key, "title": lesson.title,
                                "description": lesson.description,
                                "topic_key": lesson.topic_key,
                                "order": lesson.order_in_module,
                                "mandatory": lesson.is_mandatory,
                                "translations": lesson.translations,
                                "components": components, "assessments": assessments})
            modules.append({"key": module.stable_key, "name": module.name,
                            "description": module.description,
                            "order": module.order_in_track,
                            "objectives": module.learning_objectives,
                            "estimated_hours": module.estimated_hours,
                            "mandatory": module.is_mandatory,
                            "translations": module.translations,
                            "lessons": lessons})
        return {
            "program_id": release.track.specialization_id,
            "track": str(release.track_id),
            "track_key": release.track.stable_key,
            "track_name": release.track.name,
            "default_locale": release.track.default_locale,
            "track_translations": release.track.translations,
            "version": release.version,
            "modules": modules,
        }

    def publish_release(self, release_id: UUID | str, *, actor_user_id: int | None = None) -> TrackRelease:
        release = (
            self.db.query(TrackRelease).filter(TrackRelease.id == _uuid(release_id))
            .with_for_update().one_or_none()
        )
        if release is None:
            raise EducationContentError("RELEASE_NOT_FOUND")
        self._assert_draft(release)
        self._assert_player_program(release.track.specialization_id)
        for module in release.modules:
            for lesson in module.lessons:
                for assessment in lesson.assessments:
                    if assessment.track_release_id != release.id:
                        raise EducationContentError("ASSESSMENT_RELEASE_MISMATCH")
                    for variant in assessment.variants:
                        if variant.quiz.content_status != ContentStatus.PUBLISHED.value or not variant.quiz.is_active:
                            raise EducationContentError("ASSESSMENT_CONTENT_NOT_PUBLISHED")
        graph = self._release_graph(release)
        release.content_hash = hashlib.sha256(
            json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        release.status = "PUBLISHED"
        release.published_at = datetime.now(timezone.utc)
        release.published_by_user_id = actor_user_id
        release.track.current_release_id = release.id
        self.db.flush()
        return release

    def list_published_tracks(self, program_id: str) -> list[dict[str, Any]]:
        self._assert_player_program(program_id)
        rows = self.db.query(Track).filter(
            Track.specialization_id == program_id,
            Track.current_release_id.isnot(None), Track.is_active.is_(True),
        ).order_by(Track.code).all()
        return [{"id": str(row.id), "stable_key": row.stable_key, "code": row.code,
                 "name": row.name, "default_locale": row.default_locale,
                 "release_id": str(row.current_release_id)} for row in rows]

    def get_published_curriculum(self, track_id: UUID | str, *, locale: str) -> dict[str, Any]:
        locale = normalize_bcp47_locale(locale)
        track = self.db.get(Track, _uuid(track_id))
        if track is None or track.current_release_id is None:
            raise EducationContentError("PUBLISHED_RELEASE_NOT_FOUND")
        self._assert_player_program(track.specialization_id)
        release = self.db.get(TrackRelease, track.current_release_id)
        if release is None or release.status != "PUBLISHED":
            raise EducationContentError("PUBLISHED_RELEASE_NOT_FOUND")
        modules = []
        for module in sorted(release.modules, key=lambda row: row.order_in_track):
            lessons = []
            for lesson in sorted(module.lessons, key=lambda row: row.order_in_module):
                assessments = []
                for assessment in sorted(lesson.assessments, key=lambda row: row.order_in_lesson):
                    variants = [{"quiz_id": row.quiz_id, "locale": row.locale}
                                for row in assessment.variants]
                    assessments.append({"id": str(assessment.id), "stable_key": assessment.stable_key,
                                        "delivery_mode": assessment.delivery_mode,
                                        "purpose": assessment.purpose, "difficulty": assessment.difficulty,
                                        "variants": variants})
                components = [{"id": str(row.id), "stable_key": row.stable_key,
                               "type": row.type,
                               "name": _localized(row.name, row.translations or {}, locale, "name"),
                               "payload": (row.translations or {}).get(locale, {}).get("payload", row.component_data)}
                              for row in sorted(lesson.components, key=lambda item: item.order_in_module)]
                lessons.append({"id": str(lesson.id), "stable_key": lesson.stable_key,
                                "title": _localized(lesson.title, lesson.translations or {}, locale, "title"),
                                "topic_key": lesson.topic_key, "components": components,
                                "assessments": assessments})
            modules.append({"id": str(module.id), "stable_key": module.stable_key,
                            "name": _localized(module.name, module.translations or {}, locale, "name"),
                            "lessons": lessons})
        return {"program_id": track.specialization_id, "track_id": str(track.id),
                "release_id": str(release.id), "release_version": release.version,
                "locale": locale, "modules": modules}

    def record_assessment_outcome(
        self, *, user: User, assessment_id: UUID | str, quiz_attempt_id: int,
        idempotency_key: str, on_date: date | None = None,
    ) -> EducationProgressEvent:
        existing = self.db.query(EducationProgressEvent).filter_by(idempotency_key=idempotency_key).one_or_none()
        if existing is not None:
            if existing.user_id != user.id:
                raise EducationContentError("IDEMPOTENCY_KEY_CONFLICT")
            return existing
        get_authorized_football_player_context(self.db, user=user, on_date=on_date)
        assessment = self.db.get(LessonAssessment, _uuid(assessment_id))
        attempt = self.db.get(QuizAttempt, quiz_attempt_id)
        if assessment is None or attempt is None or attempt.user_id != user.id:
            raise EducationContentError("ASSESSMENT_ATTEMPT_NOT_FOUND")
        release = self.db.get(TrackRelease, assessment.track_release_id)
        if release.status != "PUBLISHED":
            raise EducationContentError("ASSESSMENT_RELEASE_NOT_PUBLISHED")
        variants = {row.quiz_id: row for row in assessment.variants}
        if attempt.quiz_id not in variants:
            raise EducationContentError("ASSESSMENT_QUIZ_MISMATCH")
        variant = variants[attempt.quiz_id]
        event = EducationProgressEvent(
            user_id=user.id, track_id=release.track_id, track_release_id=release.id,
            lesson_id=assessment.lesson_id, lesson_assessment_id=assessment.id,
            quiz_id=attempt.quiz_id, quiz_attempt_id=attempt.id,
            quiz_revision=attempt.quiz.revision, locale=variant.locale,
            idempotency_key=idempotency_key,
            provenance={"release_version": release.version,
                        "release_content_hash": release.content_hash,
                        "assessment_key": assessment.stable_key,
                        "quiz_content_key": attempt.quiz.content_key,
                        "quiz_source_checksum": attempt.quiz.source_checksum},
        )
        self.db.add(event)
        self.db.flush()
        return event

    def get_outcome(self, event_id: UUID | str, *, user_id: int) -> EducationProgressEvent:
        event = self.db.get(EducationProgressEvent, _uuid(event_id))
        if event is None or event.user_id != user_id:
            raise EducationContentError("OUTCOME_NOT_FOUND")
        return event


def load_player_sample_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("program_id") != PLAYER_PROGRAM_ID:
        raise EducationContentError("UNSUPPORTED_EDUCATION_PROGRAM")
    if data.get("release", {}).get("status") != "DRAFT":
        raise EducationContentError("SAMPLE_RELEASE_MUST_BE_DRAFT")
    paths: list[str] = []
    for module in data.get("modules", []):
        for lesson in module.get("lessons", []):
            for assessment in lesson.get("assessments", []):
                for variant in assessment.get("variants", []):
                    source = variant.get("source_path")
                    if not source or not Path(source).is_file():
                        raise EducationContentError("SAMPLE_SOURCE_NOT_FOUND")
                    paths.append(source)
    if len(paths) != len(set(paths)):
        raise EducationContentError("DUPLICATE_SAMPLE_SOURCE")
    return data


@dataclass
class SampleImportResult:
    track: Track
    release: TrackRelease
    quizzes: list[Quiz]
    files: int
    questions: int
    modules: int
    lessons: int
    assessments: int


def _seed_sample_quiz(db: Session, data: dict[str, Any], *, key: str, checksum: str) -> Quiz:
    locale = normalize_bcp47_locale(data.get("language", "en"))
    quiz = Quiz(
        title=data["quiz_title"].strip(),
        description=f"{data.get('topic', '')} — {data.get('module', '')}",
        category=QuizCategory[data["category"]], difficulty=QuizDifficulty[data["difficulty"]],
        language=locale, time_limit_minutes=20, xp_reward=50, passing_score=70.0,
        is_active=False, content_status=ContentStatus.DRAFT.value,
        content_key=key, revision=1, source_checksum=checksum,
        source_schema_version=data.get("schema_version", "1.0"),
    )
    db.add(quiz)
    db.flush()
    for index, q_data in enumerate(data["questions"]):
        question = QuizQuestion(
            quiz_id=quiz.id, question_text=q_data["text"].strip(),
            question_type=QuestionType[q_data["type"]], points=q_data.get("points", 1),
            order_index=index, explanation=q_data["explanation"].strip(),
        )
        db.add(question)
        db.flush()
        options = q_data.get("options", [])
        for option_index, option in enumerate(options):
            db.add(QuizAnswerOption(
                question_id=question.id, option_text=option["text"].strip(),
                is_correct=bool(option["is_correct"]), order_index=option_index,
                option_type=OptionType.FIXED,
            ))
        metadata = q_data["metadata"]
        tags = metadata.get("concept_tags", [])
        db.add(QuestionMetadata(
            question_id=question.id,
            estimated_difficulty=metadata["estimated_difficulty"],
            cognitive_load=metadata["cognitive_load"],
            concept_tags=json.dumps(tags) if isinstance(tags, list) else tags,
            average_time_seconds=metadata["average_time_seconds"],
        ))
    return quiz


def _import_player_sample_draft(db: Session, *, manifest_path: Path) -> SampleImportResult:
    manifest = load_player_sample_manifest(manifest_path)
    service = EducationContentService(db)
    track_data = manifest["track"]
    track = service.create_track(program_id=manifest["program_id"], **track_data)
    release = service.create_release(track_id=track.id, version=manifest["release"]["version"])
    quizzes: list[Quiz] = []
    question_count = assessment_count = lesson_count = 0
    for module_index, module_data in enumerate(manifest["modules"], 1):
        module = service.add_module(
            release_id=release.id, stable_key=module_data["stable_key"],
            name=module_data["name"], order=module_index,
            translations=module_data.get("translations"),
        )
        for lesson_index, lesson_data in enumerate(module_data["lessons"], 1):
            lesson_count += 1
            lesson = service.add_lesson(
                module_id=module.id, stable_key=lesson_data["stable_key"],
                title=lesson_data["title"], order=lesson_index,
                topic_key=lesson_data.get("topic_key"),
                translations=lesson_data.get("translations"),
            )
            for assessment_index, assessment_data in enumerate(lesson_data.get("assessments", []), 1):
                assessment_count += 1
                assessment = service.attach_assessment(
                    lesson_id=lesson.id, stable_key=assessment_data["stable_key"],
                    delivery_mode=assessment_data.get("delivery_mode", "ADAPTIVE"),
                    purpose=assessment_data.get("purpose", "FORMATIVE"),
                    difficulty=assessment_data.get("difficulty"), order=assessment_index,
                    topic_key=lesson_data.get("topic_key"),
                )
                for variant in assessment_data["variants"]:
                    raw = Path(variant["source_path"]).read_bytes()
                    data = json.loads(raw.decode("utf-8"))
                    checksum = hashlib.sha256(raw).hexdigest()
                    locale = normalize_bcp47_locale(data.get("language", variant["locale"]))
                    quiz = _seed_sample_quiz(
                        db, data, key=f"{assessment_data['stable_key']}:{locale}", checksum=checksum,
                    )
                    quizzes.append(quiz)
                    question_count += len(data["questions"])
                    service.add_assessment_variant(assessment.id, quiz.id, locale=locale)
    db.flush()
    return SampleImportResult(
        track=track, release=release, quizzes=quizzes, files=len(quizzes),
        questions=question_count, modules=len(manifest["modules"]),
        lessons=lesson_count, assessments=assessment_count,
    )


def import_player_sample_draft(db: Session, *, manifest_path: Path) -> SampleImportResult:
    """Atomically map the unchanged 31-file corpus to one DRAFT release."""
    savepoint = db.begin_nested()
    try:
        result = _import_player_sample_draft(db, manifest_path=manifest_path)
        savepoint.commit()
        return result
    except Exception:
        if savepoint.is_active:
            savepoint.rollback()
        raise
