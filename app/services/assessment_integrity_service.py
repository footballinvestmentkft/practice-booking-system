"""PC4 canonical Player assessment lifecycle.

The service owns issue, answer, scoring and immutable outcome creation.  Callers
own commit/rollback so every operation can participate in one request transaction.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.assessment import AssessmentAttempt, AssessmentIssuedQuestion
from app.models.quiz import (
    AdaptiveLearningSession, ALSessionStatus, ContentStatus, QuestionType,
    QuizAnswerOption, QuizAttempt, QuizQuestion, QuizUserAnswer,
)
from app.models.gamification import UserStats
from app.models.xp_transaction import XPTransaction
from app.models.track import (
    EducationProgressEvent, LessonAssessment, LessonAssessmentQuiz, TrackRelease,
)
from app.models.user import User
from app.services.education_content_service import normalize_bcp47_locale
from app.services.player_identity_service import (
    PlayerIdentityError, get_authorized_football_player_context,
)


PLAYER_PROGRAM_ID = "LFA_FOOTBALL_PLAYER"


class AssessmentIntegrityError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _uuid(value: UUID | str) -> UUID:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise AssessmentIntegrityError("INVALID_IDENTIFIER") from exc


def _enum_value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


class AssessmentIntegrityService:
    def __init__(self, db: Session):
        self.db = db

    def _authorize(self, user: User) -> None:
        try:
            get_authorized_football_player_context(self.db, user=user)
        except PlayerIdentityError as exc:
            raise AssessmentIntegrityError(exc.code) from exc

    def _attempt(self, attempt_id: UUID | str, user: User, *, lock: bool = False) -> AssessmentAttempt:
        query = self.db.query(AssessmentAttempt).filter(
            AssessmentAttempt.id == _uuid(attempt_id),
            AssessmentAttempt.user_id == user.id,
        )
        if lock:
            query = query.with_for_update()
        attempt = query.one_or_none()
        if attempt is None:
            raise AssessmentIntegrityError("ATTEMPT_NOT_FOUND")
        return attempt

    def _resolve_assessment(self, assessment_id: UUID | str, locale: str):
        assessment = (
            self.db.query(LessonAssessment)
            .filter(LessonAssessment.id == _uuid(assessment_id))
            .with_for_update(of=LessonAssessment)
            .one_or_none()
        )
        if assessment is None:
            raise AssessmentIntegrityError("ASSESSMENT_NOT_FOUND")
        lesson = assessment.lesson
        module = lesson.module
        release = self.db.get(TrackRelease, assessment.track_release_id)
        if release is None or module.track_release_id != release.id:
            raise AssessmentIntegrityError("ASSESSMENT_RELEASE_MISMATCH")
        if (
            release.status != "PUBLISHED"
            or release.track.current_release_id != release.id
        ):
            raise AssessmentIntegrityError("ASSESSMENT_NOT_PUBLISHED")
        if release.track.specialization_id != PLAYER_PROGRAM_ID:
            raise AssessmentIntegrityError("PLAYER_SPECIALIZATION_REQUIRED")
        variant = next((row for row in assessment.variants if row.locale == locale), None)
        if variant is None:
            raise AssessmentIntegrityError("ASSESSMENT_LOCALE_NOT_FOUND")
        quiz = variant.quiz
        if quiz.content_status != ContentStatus.PUBLISHED.value or not quiz.is_active:
            raise AssessmentIntegrityError("ASSESSMENT_CONTENT_NOT_PUBLISHED")
        return assessment, release, lesson, quiz

    def start_attempt(self, *, user: User, assessment_id: UUID | str, locale: str = "en") -> AssessmentAttempt:
        self._authorize(user)
        try:
            locale = normalize_bcp47_locale(locale)
        except ValueError as exc:
            raise AssessmentIntegrityError("INVALID_LOCALE") from exc
        assessment, release, lesson, quiz = self._resolve_assessment(assessment_id, locale)
        active = (
            self.db.query(AssessmentAttempt)
            .filter_by(
                user_id=user.id,
                lesson_assessment_id=assessment.id,
                locale=locale,
                status="IN_PROGRESS",
            )
            .with_for_update()
            .one_or_none()
        )
        if active is not None:
            return active
        if assessment.max_attempts is not None:
            completed = self.db.query(AssessmentAttempt).filter_by(
                user_id=user.id,
                lesson_assessment_id=assessment.id,
                status="COMPLETED",
            ).count()
            if completed >= assessment.max_attempts:
                raise AssessmentIntegrityError("MAX_ATTEMPTS_REACHED")
        provenance = {
            "track_id": str(release.track_id),
            "release_version": release.version,
            "release_content_hash": release.content_hash,
            "lesson_id": str(lesson.id),
            "assessment_key": assessment.stable_key,
            "quiz_content_key": quiz.content_key,
            "quiz_source_checksum": quiz.source_checksum,
            "quiz_source_schema_version": quiz.source_schema_version,
            "passing_score_percent": (
                (quiz.passing_score or 0) * 100
                if (quiz.passing_score or 0) <= 1 else (quiz.passing_score or 0)
            ),
            "xp_reward": max(0, quiz.xp_reward or 0),
        }
        attempt = AssessmentAttempt(
            user_id=user.id,
            track_release_id=release.id,
            lesson_id=lesson.id,
            lesson_assessment_id=assessment.id,
            quiz_id=quiz.id,
            quiz_revision=quiz.revision,
            locale=locale,
            delivery_mode=assessment.delivery_mode,
            status="IN_PROGRESS",
            content_provenance=provenance,
        )
        self.db.add(attempt)
        self.db.flush()
        legacy = QuizAttempt(
            user_id=user.id,
            quiz_id=quiz.id,
            total_questions=0,
            assessment_attempt_id=attempt.id,
        )
        self.db.add(legacy)
        if assessment.delivery_mode == "ADAPTIVE":
            self.db.add(AdaptiveLearningSession(
                user_id=user.id,
                category=quiz.category,
                language=locale,
                lesson_assessment_id=assessment.id,
                assessment_attempt_id=attempt.id,
                status=ALSessionStatus.IN_PROGRESS.value,
            ))
        else:
            for question in sorted(quiz.questions, key=lambda row: (row.order_index, row.id)):
                self._snapshot_question(attempt, question)
        self.db.flush()
        return attempt

    def _snapshot_question(self, attempt: AssessmentAttempt, question: QuizQuestion) -> AssessmentIssuedQuestion:
        options = sorted(question.answer_options, key=lambda row: (row.order_index, row.id))
        correct = next((row for row in options if row.is_correct), None)
        row = AssessmentIssuedQuestion(
            attempt_id=attempt.id,
            source_question_id=question.id,
            sequence=attempt.total_questions + 1,
            question_type=_enum_value(question.question_type),
            points=max(1, question.points or 1),
            question_snapshot={
                "text": question.question_text,
                "explanation": question.explanation,
                "source_order": question.order_index,
            },
            option_snapshot=[
                {"id": option.id, "text": option.option_text, "order": option.order_index}
                for option in options
            ],
            correct_option_id=correct.id if correct else None,
            correct_answer_snapshot=correct.option_text if correct else None,
        )
        self.db.add(row)
        attempt.total_questions += 1
        attempt.total_points += row.points
        self.db.flush()
        return row

    def issue_next_question(self, *, user: User, attempt_id: UUID | str) -> AssessmentIssuedQuestion | None:
        self._authorize(user)
        attempt = self._attempt(attempt_id, user, lock=True)
        if attempt.status != "IN_PROGRESS":
            raise AssessmentIntegrityError("ATTEMPT_COMPLETED")
        if attempt.delivery_mode != "ADAPTIVE":
            raise AssessmentIntegrityError("NOT_ADAPTIVE_ATTEMPT")
        pending = next((row for row in attempt.issued_questions if row.answered_at is None), None)
        if pending is not None:
            return pending
        issued_ids = {row.source_question_id for row in attempt.issued_questions}
        question = (
            self.db.query(QuizQuestion)
            .filter(QuizQuestion.quiz_id == attempt.quiz_id, ~QuizQuestion.id.in_(issued_ids))
            .order_by(QuizQuestion.order_index, QuizQuestion.id)
            .with_for_update()
            .first()
        )
        if question is None:
            return None
        return self._snapshot_question(attempt, question)

    def answer_source_question(
        self, *, user: User, attempt_id: UUID | str, question_id: int,
        selected_option_id: int | None = None, answer_text: str | None = None,
        idempotency_key: str,
    ) -> AssessmentIssuedQuestion:
        attempt = self._attempt(attempt_id, user)
        issued = next((row for row in attempt.issued_questions if row.source_question_id == question_id), None)
        if issued is None:
            raise AssessmentIntegrityError("QUESTION_NOT_ISSUED")
        return self.answer_question(
            user=user, attempt_id=attempt.id, issued_question_id=issued.id,
            selected_option_id=selected_option_id, answer_text=answer_text,
            idempotency_key=idempotency_key,
        )

    def answer_question(
        self, *, user: User, attempt_id: UUID | str,
        issued_question_id: UUID | str, selected_option_id: int | None = None,
        answer_text: str | None = None, idempotency_key: str,
    ) -> AssessmentIssuedQuestion:
        if not idempotency_key:
            raise AssessmentIntegrityError("IDEMPOTENCY_KEY_REQUIRED")
        self._authorize(user)
        attempt = self._attempt(attempt_id, user, lock=True)
        if attempt.status != "IN_PROGRESS":
            raise AssessmentIntegrityError("ATTEMPT_COMPLETED")
        issued = (
            self.db.query(AssessmentIssuedQuestion)
            .filter_by(id=_uuid(issued_question_id), attempt_id=attempt.id)
            .with_for_update().one_or_none()
        )
        if issued is None:
            raise AssessmentIntegrityError("ISSUED_QUESTION_NOT_FOUND")
        collision = self.db.query(AssessmentIssuedQuestion).filter_by(
            attempt_id=attempt.id, answer_idempotency_key=idempotency_key,
        ).one_or_none()
        if collision is not None and collision.id != issued.id:
            raise AssessmentIntegrityError("IDEMPOTENCY_KEY_CONFLICT")
        if issued.answered_at is not None:
            return issued
        option_ids = {item["id"] for item in (issued.option_snapshot or [])}
        if selected_option_id is not None and selected_option_id not in option_ids:
            raise AssessmentIntegrityError("OPTION_NOT_ISSUED")
        question_type = issued.question_type
        if question_type in {QuestionType.MULTIPLE_CHOICE.value, QuestionType.TRUE_FALSE.value}:
            correct = selected_option_id is not None and selected_option_id == issued.correct_option_id
        elif question_type == QuestionType.FILL_IN_BLANK.value:
            correct = bool(answer_text) and answer_text.strip().casefold() == (issued.correct_answer_snapshot or "").strip().casefold()
        else:
            correct = False
        issued.answer_idempotency_key = idempotency_key
        issued.selected_option_id = selected_option_id
        issued.answer_text = answer_text
        issued.is_correct = correct
        issued.omitted = False
        issued.points_earned = issued.points if correct else 0
        issued.answered_at = datetime.now(timezone.utc)
        attempt.answered_questions += 1
        attempt.correct_answers += int(correct)
        attempt.earned_points += issued.points_earned
        self.db.flush()
        return issued

    def complete_attempt(self, *, user: User, attempt_id: UUID | str, idempotency_key: str) -> AssessmentAttempt:
        if not idempotency_key:
            raise AssessmentIntegrityError("IDEMPOTENCY_KEY_REQUIRED")
        self._authorize(user)
        attempt = self._attempt(attempt_id, user, lock=True)
        if attempt.status == "COMPLETED":
            if attempt.completion_idempotency_key != idempotency_key:
                raise AssessmentIntegrityError("ATTEMPT_COMPLETED")
            return attempt
        if attempt.status != "IN_PROGRESS":
            raise AssessmentIntegrityError("ATTEMPT_NOT_ACTIVE")
        collision = self.db.query(AssessmentAttempt).filter_by(
            user_id=user.id, completion_idempotency_key=idempotency_key,
        ).one_or_none()
        if collision is not None and collision.id != attempt.id:
            raise AssessmentIntegrityError("IDEMPOTENCY_KEY_CONFLICT")
        now = datetime.now(timezone.utc)
        for row in attempt.issued_questions:
            if row.answered_at is None:
                row.answered_at = now
                row.is_correct = False
                row.omitted = True
                row.points_earned = 0
        attempt.answered_questions = attempt.total_questions
        attempt.correct_answers = sum(1 for row in attempt.issued_questions if row.is_correct)
        attempt.earned_points = sum(row.points_earned for row in attempt.issued_questions)
        attempt.score_percent = round(
            (attempt.earned_points / attempt.total_points) * 100, 6
        ) if attempt.total_points else 0.0
        threshold = float((attempt.content_provenance or {}).get("passing_score_percent", 0))
        attempt.passed = attempt.score_percent >= threshold
        attempt.status = "COMPLETED"
        attempt.completed_at = now
        attempt.completion_idempotency_key = idempotency_key

        legacy = self.db.query(QuizAttempt).filter_by(assessment_attempt_id=attempt.id).one()
        legacy.completed_at = now
        legacy.score = attempt.score_percent
        legacy.total_questions = attempt.total_questions
        legacy.correct_answers = attempt.correct_answers
        legacy.passed = attempt.passed
        for row in attempt.issued_questions:
            self.db.add(QuizUserAnswer(
                attempt_id=legacy.id,
                question_id=row.source_question_id,
                selected_option_id=row.selected_option_id,
                answer_text=row.answer_text,
                is_correct=bool(row.is_correct),
            ))

        xp_amount = int((attempt.content_provenance or {}).get("xp_reward", 0)) if attempt.passed else 0
        if xp_amount:
            xp_key = f"assessment:{attempt.id}:xp"
            existing_xp = self.db.query(XPTransaction).filter_by(idempotency_key=xp_key).one_or_none()
            if existing_xp is None:
                locked_user = self.db.query(User).filter(User.id == user.id).with_for_update().one()
                locked_user.xp_balance = (locked_user.xp_balance or 0) + xp_amount
                self.db.add(XPTransaction(
                    user_id=user.id, transaction_type="ASSESSMENT_COMPLETION",
                    amount=xp_amount, balance_after=locked_user.xp_balance,
                    description=f"Assessment completed: {attempt.quiz.title}",
                    idempotency_key=xp_key,
                ))
                stats = self.db.query(UserStats).filter_by(user_id=user.id).with_for_update().one_or_none()
                if stats is None:
                    stats = UserStats(user_id=user.id, total_xp=xp_amount, level=max(1, xp_amount // 1000))
                    self.db.add(stats)
                else:
                    stats.total_xp = (stats.total_xp or 0) + xp_amount
                    stats.level = max(1, stats.total_xp // 1000)
            attempt.xp_awarded = xp_amount
        legacy.xp_awarded = attempt.xp_awarded

        adaptive = self.db.query(AdaptiveLearningSession).filter_by(assessment_attempt_id=attempt.id).one_or_none()
        if adaptive is not None:
            adaptive.ended_at = now
            adaptive.status = ALSessionStatus.COMPLETED.value
            adaptive.questions_presented = attempt.total_questions
            adaptive.questions_correct = attempt.correct_answers

        snapshot_payload = [
            {
                "id": str(row.id), "source_question_id": row.source_question_id,
                "question": row.question_snapshot, "options": row.option_snapshot,
                "selected_option_id": row.selected_option_id, "answer_text": row.answer_text,
                "is_correct": row.is_correct, "omitted": row.omitted,
            }
            for row in attempt.issued_questions
        ]
        snapshot_hash = hashlib.sha256(
            json.dumps(snapshot_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        provenance = dict(attempt.content_provenance or {})
        provenance.update({
            "issued_question_snapshot_hash": snapshot_hash,
            "assessment_attempt_id": str(attempt.id),
            "scoring_version": "PC4-1",
        })
        attempt.content_provenance = provenance
        self.db.add(EducationProgressEvent(
            user_id=user.id,
            track_id=attempt.release.track_id,
            track_release_id=attempt.track_release_id,
            lesson_id=attempt.lesson_id,
            lesson_assessment_id=attempt.lesson_assessment_id,
            quiz_id=attempt.quiz_id,
            quiz_attempt_id=legacy.id,
            assessment_attempt_id=attempt.id,
            quiz_revision=attempt.quiz_revision,
            locale=attempt.locale,
            event_type="ASSESSMENT_COMPLETED",
            idempotency_key=f"assessment:{attempt.id}:completed",
            provenance=provenance,
        ))
        self.db.flush()
        return attempt

    @staticmethod
    def public_question(row: AssessmentIssuedQuestion) -> dict:
        """Serialize only learner-visible issue-time data; correctness stays server-side."""
        return {
            "id": str(row.id),
            "sequence": row.sequence,
            "question_type": row.question_type,
            "points": row.points,
            "text": row.question_snapshot.get("text"),
            "options": row.option_snapshot,
        }
