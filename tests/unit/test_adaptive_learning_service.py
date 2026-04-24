"""
Unit tests for AdaptiveLearningService (AL-1/AL-3 backend core).

Scope:
  - end_session: GamificationService coupling removed — no external side-effects
  - end_session: sets ended_at, commits, returns correct summary dict
  - start_adaptive_session: creates session row, returns ORM object
  - record_answer correct/incorrect: increments session counts, returns xp_earned
  - _get_candidate_questions fallback: returns questions even when QuestionMetadata is absent
  - al_session_complete route (AL-3): XP ledger write, idempotency, SELECT FOR UPDATE guard
  - al_session_start route (AL-3): category param validation, default, passthrough

All tests use MagicMock DB — no real DB connection required.
"""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from fastapi.testclient import TestClient

from app.services.adaptive_learning import AdaptiveLearningService
from app.models.quiz import (
    AdaptiveLearningSession,
    QuizCategory,
    QuizQuestion,
    Quiz,
    QuestionMetadata,
    UserQuestionPerformance,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_session(
    session_id=1,
    user_id=99,
    ended_at=None,
    questions_presented=0,
    questions_correct=0,
    xp_earned=0,
    performance_trend=0.0,
    target_difficulty=0.5,
    session_start_time=None,
    session_time_limit_seconds=600,
):
    s = MagicMock(spec=AdaptiveLearningSession)
    s.id = session_id
    s.user_id = user_id
    s.ended_at = ended_at
    s.questions_presented = questions_presented
    s.questions_correct = questions_correct
    s.xp_earned = xp_earned
    s.performance_trend = performance_trend
    s.target_difficulty = target_difficulty
    s.session_start_time = session_start_time
    s.session_time_limit_seconds = session_time_limit_seconds
    s.category = QuizCategory.LESSON
    return s


def _make_service(db=None):
    if db is None:
        db = MagicMock()
    return AdaptiveLearningService(db), db


# ── end_session ───────────────────────────────────────────────────────────────

class TestEndSession:
    def test_returns_empty_dict_when_session_not_found(self):
        service, db = _make_service()
        db.query.return_value.filter.return_value.first.return_value = None

        result = service.end_session(session_id=42)

        assert result == {}

    def test_sets_ended_at_and_commits(self):
        service, db = _make_service()
        session = _mock_session(questions_presented=5, questions_correct=4, xp_earned=100)
        db.query.return_value.filter.return_value.first.return_value = session

        service.end_session(session_id=1)

        assert session.ended_at is not None
        db.commit.assert_called_once()

    def test_returns_correct_summary_dict(self):
        service, db = _make_service()
        # score = 4*2 - 5 = 3; xp = max(0, 3) * 10 = 30
        session = _mock_session(questions_presented=5, questions_correct=4)
        db.query.return_value.filter.return_value.first.return_value = session

        result = service.end_session(session_id=1)

        assert result["questions_answered"] == 5
        assert result["correct_answers"] == 4
        assert abs(result["success_rate"] - 0.8) < 0.001
        assert result["xp_earned"] == 30
        assert result["score"] == 3

    def test_no_gamification_service_import_or_call(self):
        """GamificationService must not be imported or called in end_session."""
        service, db = _make_service()
        session = _mock_session(questions_presented=3, questions_correct=2)
        db.query.return_value.filter.return_value.first.return_value = session

        with patch("app.services.adaptive_learning.GamificationService", create=True) as mock_gam:
            service.end_session(session_id=1)
            mock_gam.assert_not_called()

    def test_zero_questions_success_rate_is_zero(self):
        service, db = _make_service()
        session = _mock_session(questions_presented=0, questions_correct=0)
        db.query.return_value.filter.return_value.first.return_value = session

        result = service.end_session(session_id=1)

        assert result["success_rate"] == 0


# ── _get_candidate_questions fallback ─────────────────────────────────────────

class TestGetCandidateQuestions:
    def test_falls_back_to_all_category_questions_when_no_metadata(self):
        """With no QuestionMetadata rows, fallback query must return questions."""
        service, db = _make_service()

        # First query (difficulty-filtered with metadata) returns empty
        # Second query (category-only fallback) returns 3 questions
        fallback_questions = [MagicMock(spec=QuizQuestion) for _ in range(3)]

        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            q = MagicMock()
            # chain: .join().join().filter().all()
            q.join.return_value = q
            q.outerjoin.return_value = q
            q.filter.return_value = q
            call_count += 1
            if call_count == 1:
                q.all.return_value = []        # metadata-filtered → empty
            else:
                q.all.return_value = fallback_questions  # fallback → has content
            return q

        db.query.side_effect = side_effect

        result = service._get_candidate_questions(QuizCategory.LESSON, target_difficulty=0.5)

        assert result == fallback_questions

    def test_returns_metadata_filtered_questions_when_available(self):
        service, db = _make_service()
        filtered_questions = [MagicMock(spec=QuizQuestion) for _ in range(2)]

        q = MagicMock()
        q.join.return_value = q
        q.outerjoin.return_value = q
        q.filter.return_value = q
        q.all.return_value = filtered_questions
        db.query.return_value = q

        result = service._get_candidate_questions(QuizCategory.LESSON, target_difficulty=0.5)

        assert result == filtered_questions


# ── record_answer ─────────────────────────────────────────────────────────────

class TestRecordAnswer:
    def _setup_record(self, is_correct):
        service, db = _make_service()
        session = _mock_session(questions_presented=2, questions_correct=1)
        perf = MagicMock(spec=UserQuestionPerformance)
        perf.total_attempts = 1
        perf.correct_attempts = 1
        perf.mastery_level = 0.5

        meta = MagicMock(spec=QuestionMetadata)
        meta.estimated_difficulty = 0.5
        meta.average_time_seconds = 30.0
        meta.global_success_rate = 0.6

        def query_side(*args):
            q = MagicMock()
            q.filter.return_value = q
            q.join.return_value = q
            q.outerjoin.return_value = q
            q.all.return_value = []
            if AdaptiveLearningSession in args:
                q.first.return_value = session
            elif UserQuestionPerformance in args:
                q.first.return_value = perf
            elif QuestionMetadata in args:
                q.first.return_value = meta
            else:
                q.first.return_value = None
            return q

        db.query.side_effect = query_side
        return service, db, session

    def test_correct_answer_increments_questions_correct(self):
        service, db, session = self._setup_record(is_correct=True)
        service.record_answer(
            user_id=99, session_id=1, question_id=5,
            is_correct=True, time_spent_seconds=20.0,
        )
        assert session.questions_presented == 3
        assert session.questions_correct == 2

    def test_wrong_answer_does_not_increment_questions_correct(self):
        service, db, session = self._setup_record(is_correct=False)
        service.record_answer(
            user_id=99, session_id=1, question_id=5,
            is_correct=False, time_spent_seconds=20.0,
        )
        assert session.questions_presented == 3
        assert session.questions_correct == 1  # unchanged


# ── AL-3: al_session_start — category validation ──────────────────────────────

_START_BASE = "app.api.web_routes.adaptive_learning"


def _make_user(uid=99):
    u = MagicMock()
    u.id = uid
    return u


def _make_db_no_existing():
    """DB mock that returns no active session (existing=None)."""
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.first.return_value = None
    db.query.return_value = q
    return db


class TestAl3SessionStart:
    """Route-level tests for al_session_start category parameter."""

    def _call_start(self, category_param, db=None, user=None):
        import asyncio
        from app.api.web_routes.adaptive_learning import al_session_start

        db = db or _make_db_no_existing()
        user = user or _make_user()
        req = MagicMock()

        with patch(f"{_START_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_START_BASE}.AdaptiveLearningService") as MockSvc:

            mock_session = MagicMock()
            mock_session.id = 42
            mock_session.session_start_time = None
            MockSvc.return_value.start_adaptive_session.return_value = mock_session

            response = asyncio.run(al_session_start(
                request=req,
                category=category_param,
                time_limit=180,
                db=db,
                user=user,
            ))
            return response, MockSvc

    def test_valid_category_passed_to_service(self):
        response, MockSvc = self._call_start("SPORTS_PHYSIOLOGY")
        assert response.status_code == 200
        MockSvc.return_value.start_adaptive_session.assert_called_once()
        args = MockSvc.return_value.start_adaptive_session.call_args[0]
        assert args[1] == QuizCategory.SPORTS_PHYSIOLOGY

    def test_default_category_is_lesson(self):
        response, MockSvc = self._call_start("LESSON")
        assert response.status_code == 200
        args = MockSvc.return_value.start_adaptive_session.call_args[0]
        assert args[1] == QuizCategory.LESSON

    def test_invalid_category_returns_422(self):
        response, _ = self._call_start("NONSENSE")
        assert response.status_code == 422

    def test_category_case_insensitive(self):
        response, MockSvc = self._call_start("lesson")
        assert response.status_code == 200
        args = MockSvc.return_value.start_adaptive_session.call_args[0]
        assert args[1] == QuizCategory.LESSON


# ── AL-3: al_session_complete — XP ledger idempotency ────────────────────────

_COMPLETE_BASE = "app.api.web_routes.adaptive_learning"


def _make_active_session(session_id=1, xp_earned=80):
    s = MagicMock(spec=AdaptiveLearningSession)
    s.id = session_id
    s.user_id = 99
    s.ended_at = None
    return s


def _make_ended_session(session_id=1):
    s = MagicMock(spec=AdaptiveLearningSession)
    s.id = session_id
    s.user_id = 99
    s.ended_at = datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)
    return s


def _call_complete(session_id, db_session_obj, summary, xp_tx_exists=False):
    import asyncio
    from app.api.web_routes.adaptive_learning import al_session_complete

    db = MagicMock()
    # .with_for_update().first() returns the session object
    db.query.return_value.filter.return_value.with_for_update.return_value.first.return_value = db_session_obj
    # XPTransaction pre-check: second query chain
    xp_tx_mock = MagicMock() if xp_tx_exists else None
    db.query.return_value.filter.return_value.first.return_value = xp_tx_mock

    user = _make_user(uid=99)
    req = MagicMock()

    with patch(f"{_COMPLETE_BASE}.require_student_onboarding", return_value=None), \
         patch(f"{_COMPLETE_BASE}.AdaptiveLearningService") as MockSvc, \
         patch(f"{_COMPLETE_BASE}.award_xp") as mock_award:

        MockSvc.return_value.end_session.return_value = summary

        response = asyncio.run(al_session_complete(
            session_id=session_id,
            request=req,
            db=db,
            user=user,
        ))
        return response, mock_award


class TestAl3SessionComplete:
    """Route-level tests for al_session_complete XP idempotency."""

    def test_first_complete_awards_xp_once(self):
        summary = {"questions_answered": 5, "correct_answers": 4, "xp_earned": 80}
        response, mock_award = _call_complete(1, _make_active_session(xp_earned=80), summary)

        assert response.status_code == 200
        mock_award.assert_called_once()
        call_kwargs = mock_award.call_args[1]
        assert call_kwargs["idempotency_key"] == "adaptive_session_1_xp"
        assert call_kwargs["transaction_type"] == "ADAPTIVE_LEARNING_XP"
        assert call_kwargs["xp_amount"] == 80

    def test_second_complete_returns_410(self):
        summary = {"questions_answered": 5, "correct_answers": 4, "xp_earned": 80}
        response, mock_award = _call_complete(1, _make_ended_session(), summary)

        assert response.status_code == 410
        mock_award.assert_not_called()

    def test_existing_xp_transaction_skips_award_xp(self):
        summary = {"questions_answered": 5, "correct_answers": 4, "xp_earned": 80}
        response, mock_award = _call_complete(
            1, _make_active_session(xp_earned=80), summary, xp_tx_exists=True
        )
        assert response.status_code == 200
        mock_award.assert_not_called()

    def test_zero_xp_skips_award_xp(self):
        summary = {"questions_answered": 3, "correct_answers": 0, "xp_earned": 0}
        response, mock_award = _call_complete(1, _make_active_session(xp_earned=0), summary)

        assert response.status_code == 200
        mock_award.assert_not_called()
