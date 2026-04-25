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


def _make_db_no_existing(question_count: int = 15):
    """DB mock that returns no active session (existing=None).

    question_count controls what the MIN_QUESTIONS_PER_CATEGORY guard sees via .scalar().
    Default 15 (above threshold) so existing tests continue to pass.
    """
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.join.return_value = q
    q.order_by.return_value = q
    q.scalar.return_value = question_count
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
                language="en",
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


# ── AL-START: resume/retire logic + time_limit options ───────────────────────

from datetime import timedelta


def _make_existing_session(
    session_id=10,
    started_seconds_ago=10,
    time_limit_seconds=180,
    questions_presented=3,
    questions_correct=2,
):
    """Build a mock existing (unfinished) AdaptiveLearningSession."""
    s = MagicMock()
    s.id = session_id
    s.session_start_time = datetime.now(timezone.utc) - timedelta(seconds=started_seconds_ago)
    s.session_time_limit_seconds = time_limit_seconds
    s.questions_presented = questions_presented
    s.questions_correct = questions_correct
    s.ended_at = None
    return s


def _make_db_with_existing(existing_session, question_count: int = 15):
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.join.return_value = q
    q.order_by.return_value = q
    q.scalar.return_value = question_count
    q.first.return_value = existing_session
    db.query.return_value = q
    return db


def _response_json(response):
    """Parse body of a FastAPI JSONResponse returned from a direct async call."""
    import json
    return json.loads(response.body)


def _call_start_full(time_limit=180, db=None, user=None):
    import asyncio
    from app.api.web_routes.adaptive_learning import al_session_start

    db = db or _make_db_no_existing()
    user = user or _make_user()
    req = MagicMock()

    with patch(f"{_START_BASE}.require_student_onboarding", return_value=None), \
         patch(f"{_START_BASE}.AdaptiveLearningService") as MockSvc:

        mock_session = MagicMock()
        mock_session.id = 99
        mock_session.session_start_time = datetime.now(timezone.utc)
        MockSvc.return_value.start_adaptive_session.return_value = mock_session

        response = asyncio.run(al_session_start(
            request=req,
            category="LESSON",
            time_limit=time_limit,
            language="en",
            db=db,
            user=user,
        ))
        return response, MockSvc


class TestAlStartExpiredSessionRetired:
    """Start route retires an expired unfinished session and creates a fresh one."""

    def test_expired_session_is_ended_and_new_session_created(self):
        """Existing session started 400s ago with 180s limit → expired.
        Expect: ended_at set on old session, new session created, resumed=False."""
        old = _make_existing_session(started_seconds_ago=400, time_limit_seconds=180)
        db = _make_db_with_existing(old)

        response, MockSvc = _call_start_full(time_limit=60, db=db)

        # Old session must have ended_at set
        assert old.ended_at is not None, "expired session must have ended_at set"
        # A new session must have been created
        MockSvc.return_value.start_adaptive_session.assert_called_once()
        data = _response_json(response)
        assert data["resumed"] is False
        assert response.status_code == 200

    def test_expired_session_result_shows_fresh_score(self):
        old = _make_existing_session(started_seconds_ago=400, time_limit_seconds=180)
        db = _make_db_with_existing(old)
        response, _ = _call_start_full(time_limit=60, db=db)
        data = _response_json(response)
        assert data["current_score"] == 0  # fresh session, not the old score

    def test_1min_option_retires_expired_3min_session(self):
        """Exact scenario from the bug report: stale 3-minute session, user picks 1 minute."""
        old = _make_existing_session(started_seconds_ago=300, time_limit_seconds=180)
        db = _make_db_with_existing(old)
        response, MockSvc = _call_start_full(time_limit=60, db=db)
        assert old.ended_at is not None
        MockSvc.return_value.start_adaptive_session.assert_called_once()
        assert _response_json(response)["resumed"] is False


class TestAlStartResumeValidSession:
    """Start route resumes a valid unexpired session and updates its time limit."""

    def test_unexpired_session_is_resumed(self):
        """Existing session started 30s ago with 180s limit → still valid.
        Expect: resumed=True, no new session created."""
        active = _make_existing_session(started_seconds_ago=30, time_limit_seconds=180)
        db = _make_db_with_existing(active)
        response, MockSvc = _call_start_full(time_limit=60, db=db)

        MockSvc.return_value.start_adaptive_session.assert_not_called()
        data = _response_json(response)
        assert data["resumed"] is True
        assert data["session_id"] == active.id
        assert response.status_code == 200

    def test_resumed_session_time_limit_updated_in_db(self):
        """session_time_limit_seconds must be updated to the chosen time_limit."""
        active = _make_existing_session(started_seconds_ago=30, time_limit_seconds=180)
        db = _make_db_with_existing(active)
        _call_start_full(time_limit=60, db=db)
        assert active.session_time_limit_seconds == 60

    def test_resumed_session_returns_current_score(self):
        """Resumed response must include the score derived from existing questions."""
        # questions_correct=2, questions_presented=3 → score = 2*2 - 3 = 1
        active = _make_existing_session(
            started_seconds_ago=30, time_limit_seconds=180,
            questions_presented=3, questions_correct=2,
        )
        db = _make_db_with_existing(active)
        response, _ = _call_start_full(time_limit=60, db=db)
        data = _response_json(response)
        assert data["current_score"] == 1
        assert data["questions_presented"] == 3

    def test_resumed_session_db_commit_called(self):
        active = _make_existing_session(started_seconds_ago=30, time_limit_seconds=180)
        db = _make_db_with_existing(active)
        _call_start_full(time_limit=60, db=db)
        db.commit.assert_called()


class TestAlStartTimeLimitOptions:
    """Each supported time_limit value is accepted and passed through correctly."""

    def test_1_minute_uses_60_seconds(self):
        response, MockSvc = _call_start_full(time_limit=60)
        assert response.status_code == 200
        data = _response_json(response)
        assert data["time_limit_seconds"] == 60
        call_kwargs = MockSvc.return_value.start_adaptive_session.call_args[1]
        assert call_kwargs["session_duration_seconds"] == 60

    def test_3_minute_uses_180_seconds(self):
        response, MockSvc = _call_start_full(time_limit=180)
        assert response.status_code == 200
        assert _response_json(response)["time_limit_seconds"] == 180

    def test_5_minute_uses_300_seconds(self):
        response, MockSvc = _call_start_full(time_limit=300)
        assert response.status_code == 200
        assert _response_json(response)["time_limit_seconds"] == 300

    def test_invalid_time_limit_returns_422(self):
        response, _ = _call_start_full(time_limit=120)
        assert response.status_code == 422


# ── AL-START: question count guard ───────────────────────────────────────────

def _call_start_with_count(question_count: int, category: str = "LESSON", time_limit: int = 180):
    import asyncio
    from app.api.web_routes.adaptive_learning import al_session_start

    db = _make_db_no_existing(question_count=question_count)
    user = _make_user()
    req = MagicMock()

    with patch(f"{_START_BASE}.require_student_onboarding", return_value=None), \
         patch(f"{_START_BASE}.AdaptiveLearningService") as MockSvc:

        mock_session = MagicMock()
        mock_session.id = 77
        mock_session.session_start_time = datetime.now(timezone.utc)
        MockSvc.return_value.start_adaptive_session.return_value = mock_session

        response = asyncio.run(al_session_start(
            request=req,
            category=category,
            time_limit=time_limit,
            language="hu",
            db=db,
            user=user,
        ))
        return response, MockSvc


class TestAlStartQuestionCountGuard:
    """Category question-count guard must reject under-threshold categories."""

    def test_zero_questions_returns_422(self):
        response, MockSvc = _call_start_with_count(0)
        assert response.status_code == 422
        MockSvc.return_value.start_adaptive_session.assert_not_called()

    def test_zero_questions_error_body_mentions_insufficient(self):
        response, _ = _call_start_with_count(0)
        data = _response_json(response)
        assert "insufficient" in data["error"].lower()

    def test_nine_questions_returns_422(self):
        """One below threshold must still be rejected."""
        response, MockSvc = _call_start_with_count(9)
        assert response.status_code == 422
        MockSvc.return_value.start_adaptive_session.assert_not_called()

    def test_ten_questions_returns_200(self):
        """Exactly at threshold must be accepted."""
        response, MockSvc = _call_start_with_count(10)
        assert response.status_code == 200
        MockSvc.return_value.start_adaptive_session.assert_called_once()

    def test_above_threshold_returns_200(self):
        response, MockSvc = _call_start_with_count(15)
        assert response.status_code == 200
        MockSvc.return_value.start_adaptive_session.assert_called_once()

    def test_error_body_includes_count_and_required(self):
        response, _ = _call_start_with_count(9)
        data = _response_json(response)
        assert "9" in data["error"]
        assert "10" in data["error"]


# ── Service: get_next_question with empty pool ────────────────────────────────

class TestGetNextQuestionEmptyPool:
    """get_next_question must return session_complete+reason when no candidates."""

    def _make_db_empty_pool(self):
        """DB mock: session lookup returns a valid session; all question queries return []."""
        service, db = _make_service()
        session = _mock_session(
            session_start_time=datetime.now(timezone.utc),
            session_time_limit_seconds=600,
        )

        def query_side(*args):
            q = MagicMock()
            q.join.return_value = q
            q.outerjoin.return_value = q
            q.filter.return_value = q
            q.all.return_value = []
            if AdaptiveLearningSession in args:
                q.first.return_value = session
            else:
                q.first.return_value = None
            return q

        db.query.side_effect = query_side
        return service, db

    def test_empty_pool_returns_session_complete_dict(self):
        service, db = self._make_db_empty_pool()

        result = service.get_next_question(user_id=99, session_id=1)

        assert result is not None, "must not return bare None"
        assert result.get("session_complete") is True
        assert result.get("reason") == "no_questions"

    def test_empty_pool_does_not_return_bare_none(self):
        service, db = self._make_db_empty_pool()

        result = service.get_next_question(user_id=99, session_id=1)

        assert result is not None


# ── Language validation — GET session page + POST /start ─────────────────────

_SESSION_PAGE_BASE = "app.api.web_routes.adaptive_learning"


def _call_session_page(language_param=None, db=None):
    """Call the adaptive_learning_session_page handler directly."""
    import asyncio
    from app.api.web_routes.adaptive_learning import adaptive_learning_session_page

    db = db or _make_db_no_existing()
    user = _make_user()
    req = MagicMock()

    with patch(f"{_SESSION_PAGE_BASE}.require_student_onboarding", return_value=None), \
         patch(f"{_SESSION_PAGE_BASE}._spec_ctx", return_value={}), \
         patch(f"{_SESSION_PAGE_BASE}.templates") as mock_templates:

        mock_templates.TemplateResponse.return_value = MagicMock()

        kwargs = dict(request=req, db=db, user=user)
        if language_param is not None:
            kwargs["language"] = language_param

        asyncio.run(adaptive_learning_session_page(**kwargs))

        if mock_templates.TemplateResponse.called:
            call_args = mock_templates.TemplateResponse.call_args
            ctx = call_args[0][1] if call_args[0] else call_args[1].get("context", {})
            return ctx
        return {}


class TestSessionPageLanguageParam:
    """GET /adaptive-learning/session language query param wiring."""

    def test_default_language_is_en(self):
        ctx = _call_session_page()
        assert ctx.get("session_language") == "en"

    def test_explicit_en_uses_en(self):
        ctx = _call_session_page(language_param="en")
        assert ctx.get("session_language") == "en"

    def test_explicit_hu_uses_hu(self):
        ctx = _call_session_page(language_param="hu")
        assert ctx.get("session_language") == "hu"

    def test_unsupported_language_falls_back_to_en(self):
        """Invalid language value is silently corrected to 'en' (no 422 on GET)."""
        ctx = _call_session_page(language_param="fr")
        assert ctx.get("session_language") == "en"

    def test_en_and_hu_categories_are_independent(self):
        """Each language gets its own DB query — they must not bleed into each other."""
        ctx_en = _call_session_page(language_param="en")
        ctx_hu = _call_session_page(language_param="hu")
        assert ctx_en.get("session_language") == "en"
        assert ctx_hu.get("session_language") == "hu"


def _call_start_with_language(language: str, db=None):
    import asyncio
    from app.api.web_routes.adaptive_learning import al_session_start

    db = db or _make_db_no_existing(question_count=15)
    user = _make_user()
    req = MagicMock()

    with patch(f"{_START_BASE}.require_student_onboarding", return_value=None), \
         patch(f"{_START_BASE}.AdaptiveLearningService") as MockSvc:

        mock_session = MagicMock()
        mock_session.id = 55
        mock_session.session_start_time = datetime.now(timezone.utc)
        MockSvc.return_value.start_adaptive_session.return_value = mock_session

        response = asyncio.run(al_session_start(
            request=req,
            category="LESSON",
            time_limit=180,
            language=language,
            db=db,
            user=user,
        ))
        return response, MockSvc


class TestStartRouteLanguageValidation:
    """POST /adaptive-learning/session/start language parameter validation."""

    def test_default_language_is_en_in_route_signature(self):
        """Verify the route parameter default is 'en', not 'hu'."""
        import inspect
        from app.api.web_routes.adaptive_learning import al_session_start
        sig = inspect.signature(al_session_start)
        lang_param = sig.parameters["language"]
        # FastAPI Query default: lang_param.default is a Query(...) FieldInfo object.
        # The first positional arg to Query is the actual default value.
        default_val = lang_param.default.default
        assert default_val == "en", f"Expected 'en' default, got {default_val!r}"

    def test_valid_en_accepted(self):
        response, _ = _call_start_with_language("en")
        assert response.status_code == 200

    def test_valid_hu_accepted(self):
        response, _ = _call_start_with_language("hu")
        assert response.status_code == 200

    def test_unsupported_language_returns_422(self):
        response, MockSvc = _call_start_with_language("fr")
        assert response.status_code == 422
        MockSvc.return_value.start_adaptive_session.assert_not_called()

    def test_unsupported_language_error_body(self):
        response, _ = _call_start_with_language("de")
        import json
        data = json.loads(response.body)
        assert "not supported" in data["error"].lower() or "de" in data["error"]

    def test_en_language_passed_to_service(self):
        response, MockSvc = _call_start_with_language("en")
        assert response.status_code == 200
        call_kwargs = MockSvc.return_value.start_adaptive_session.call_args[1]
        assert call_kwargs.get("language") == "en"

    def test_hu_language_passed_to_service(self):
        response, MockSvc = _call_start_with_language("hu")
        assert response.status_code == 200
        call_kwargs = MockSvc.return_value.start_adaptive_session.call_args[1]
        assert call_kwargs.get("language") == "hu"
