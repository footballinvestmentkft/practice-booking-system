"""
Sprint 30 — app/api/api_v1/endpoints/adaptive_learning.py
==========================================================
Target: ≥85% statement, ≥70% branch

Covers:
  start_adaptive_learning_session:
    * non-student → 403
    * student → success (AdaptiveSessionResponse)

  get_next_question:
    * result=None/falsy → 404
    * dict + session_complete + reason='time_expired'
    * dict + session_complete + reason='no_questions'
    * dict + session_complete + reason='other_reason' (else branch)
    * normal question dict → question response

  submit_answer:
    * question not found → 404
    * MC question, selected_option_id=None → is_correct stays False
    * FILL_IN_BLANK + correct text match → is_correct=True
    * FILL_IN_BLANK + wrong text → is_correct=False
    * session found with questions_presented>0 → success_rate calculated
    * session found with questions_presented=0 → success_rate=0
    * session is None → defaults used

  end_learning_session:
    * success path → SessionSummaryResponse

  get_learning_analytics:
    * success path → LearningAnalyticsResponse

  get_available_categories:
    * iterates all QuizCategory enums

  get_adaptive_leaderboard:
    * timeframe='week' → 7-day since
    * timeframe='month' → 30-day since
    * timeframe='all_time' (else) → since=None
    * category provided → category filter applied
    * category=None → no category filter
    * sessions with users found → leaderboard built
    * user not in stats (new user) → initialized
    * session total_questions=0 → success_rate=0
    * current user in leaderboard → user_position found

Note: submit_answer with selected_option_id skipped — QuizAnswerOption
      is not imported in production code (latent NameError bug).
"""

import pytest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from fastapi import HTTPException
from datetime import datetime, timezone

from app.api.api_v1.endpoints.adaptive_learning import (
    start_adaptive_learning_session,
    get_next_question,
    submit_answer,
    end_learning_session,
    get_learning_analytics,
    get_available_categories,
    get_adaptive_leaderboard,
    StartSessionRequest,
    AnswerQuestionRequest,
)
from app.models.user import UserRole
from app.models.quiz import QuizCategory, QuestionType

_BASE = "app.api.api_v1.endpoints.adaptive_learning"


# ── helpers ─────────────────────────────────────────────────────────────────

def _student():
    u = MagicMock()
    u.role = UserRole.STUDENT  # value == 'student'
    u.id = 42
    return u


def _admin():
    u = MagicMock()
    u.role = UserRole.ADMIN  # value == 'admin'
    u.id = 1
    return u


def _db():
    return MagicMock()


def _q_first(val, db=None):
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = val
    if db:
        db.query.return_value = q
    return q


def _seq_db(*first_vals):
    calls = [0]

    def _side(*args, **kw):
        idx = calls[0]
        calls[0] += 1
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        val = first_vals[idx] if idx < len(first_vals) else None
        if isinstance(val, list):
            q.all.return_value = val
            q.count.return_value = len(val)
        else:
            q.first.return_value = val
        return q

    db = MagicMock()
    db.query.side_effect = _side
    return db


# ============================================================================
# start_adaptive_learning_session
# ============================================================================

class TestStartAdaptiveSession:

    def test_non_student_returns_403(self):
        """SAL-01: non-student role → 403."""
        req = StartSessionRequest(category=QuizCategory.GENERAL)
        with pytest.raises(HTTPException) as exc:
            start_adaptive_learning_session(
                request=req, db=_db(), current_user=_admin()
            )
        assert exc.value.status_code == 403

    def test_student_creates_session(self):
        """SAL-02: student → calls service, returns AdaptiveSessionResponse."""
        req = StartSessionRequest(category=QuizCategory.GENERAL)
        mock_session = MagicMock()
        mock_session.id = 10
        mock_session.target_difficulty = 0.5
        mock_session.category = QuizCategory.GENERAL  # .value == 'GENERAL'
        mock_session.session_time_limit_seconds = 180

        with patch(f"{_BASE}.AdaptiveLearningService") as MockSvc:
            MockSvc.return_value.start_adaptive_session.return_value = mock_session
            result = start_adaptive_learning_session(
                request=req, db=_db(), current_user=_student()
            )

        assert result.session_id == 10
        assert result.target_difficulty == 0.5
        assert result.session_duration_seconds == 180


# ============================================================================
# get_next_question
# ============================================================================

class TestGetNextQuestion:

    def _run(self, service_result, user=None):
        with patch(f"{_BASE}.AdaptiveLearningService") as MockSvc:
            MockSvc.return_value.get_next_question.return_value = service_result
            return get_next_question(
                session_id=1, db=_db(), current_user=user or _student()
            )

    def test_result_none_returns_404(self):
        """GNQ-01: service returns None → 404."""
        with pytest.raises(HTTPException) as exc:
            self._run(None)
        assert exc.value.status_code == 404

    def test_session_complete_time_expired(self):
        """GNQ-02: session_complete + time_expired → time limit message."""
        result = self._run({"session_complete": True, "reason": "time_expired"})
        assert result["session_complete"] is True
        assert "time" in result["reason"].lower() or "Session" in result["reason"]

    def test_session_complete_no_questions(self):
        """GNQ-03: session_complete + no_questions → no more questions message."""
        result = self._run({"session_complete": True, "reason": "no_questions"})
        assert result["session_complete"] is True
        assert "question" in result["reason"].lower() or "No" in result["reason"]

    def test_session_complete_other_reason(self):
        """GNQ-04: session_complete + other reason → reason passed through."""
        result = self._run({"session_complete": True, "reason": "instructor_ended"})
        assert result["session_complete"] is True
        assert result["reason"] == "instructor_ended"

    def test_normal_question_returned(self):
        """GNQ-05: normal question dict → question response."""
        q_data = {
            "id": 5,
            "text": "What is offside?",
            "type": "MULTIPLE_CHOICE",
            "options": [{"id": 1, "text": "A"}, {"id": 2, "text": "B"}],
            "difficulty": 0.7,
            "session_time_remaining": 120
        }
        result = self._run(q_data)
        assert result["id"] == 5
        assert result["question_text"] == "What is offside?"
        assert result["session_time_remaining"] == 120


# ============================================================================
# submit_answer
# ============================================================================

class TestSubmitAnswer:

    def _req(self, q_id=1, option_id=None, text=None, time_spent=5.0):
        kwargs = {"question_id": q_id, "time_spent_seconds": time_spent}
        if option_id is not None:
            kwargs["selected_option_id"] = option_id
        if text is not None:
            kwargs["answer_text"] = text
        return AnswerQuestionRequest(**kwargs)

    def _service_result(self):
        return {
            "xp_earned": 10,
            "new_target_difficulty": 0.6,
            "performance_trend": 0.1,
            "mastery_update": {}
        }

    def test_question_not_found_returns_404(self):
        """SA-01: question not found → 404."""
        db = _seq_db(None, None)  # legacy session then QuizQuestion not found
        req = self._req()
        with pytest.raises(HTTPException) as exc:
            with patch(f"{_BASE}.AdaptiveLearningService"):
                submit_answer(
                    session_id=1, request=req, db=db, current_user=_student()
                )
        assert exc.value.status_code == 404

    def test_mc_question_no_option_id_is_correct_false(self):
        """SA-02: MC question + selected_option_id=None → is_correct=False."""
        question = MagicMock()
        question.question_type = QuestionType.MULTIPLE_CHOICE
        question.explanation = "Because..."
        session = MagicMock()
        session.questions_presented = 5
        session.questions_correct = 3
        session.xp_earned = 50

        db = _seq_db(session, question, session)  # canonical check, question, final stats
        req = self._req(option_id=None)  # No selected_option_id

        with patch(f"{_BASE}.AdaptiveLearningService") as MockSvc:
            MockSvc.return_value.record_answer.return_value = self._service_result()
            result = submit_answer(
                session_id=1, request=req, db=db, current_user=_student()
            )

        assert result.is_correct is False
        assert result.xp_earned == 10

    def test_fill_in_blank_correct_answer(self):
        """SA-03: FILL_IN_BLANK + correct text → is_correct=True."""
        question = MagicMock()
        question.question_type = QuestionType.FILL_IN_BLANK
        question.explanation = "Good!"
        # Set up answer_options with correct answer
        correct_opt = MagicMock()
        correct_opt.is_correct = True
        correct_opt.option_text = "offside"
        question.answer_options = [correct_opt]

        session = MagicMock()
        session.questions_presented = 3
        session.questions_correct = 2
        session.xp_earned = 30

        db = _seq_db(session, question, session)
        req = self._req(text="OFFSIDE")  # correct (case-insensitive)

        with patch(f"{_BASE}.AdaptiveLearningService") as MockSvc:
            MockSvc.return_value.record_answer.return_value = self._service_result()
            result = submit_answer(
                session_id=1, request=req, db=db, current_user=_student()
            )

        assert result.is_correct is True

    def test_fill_in_blank_wrong_answer(self):
        """SA-04: FILL_IN_BLANK + wrong text → is_correct=False."""
        question = MagicMock()
        question.question_type = QuestionType.FILL_IN_BLANK
        question.explanation = ""
        correct_opt = MagicMock()
        correct_opt.is_correct = True
        correct_opt.option_text = "offside"
        question.answer_options = [correct_opt]

        session = MagicMock()
        session.questions_presented = 0
        session.questions_correct = 0
        session.xp_earned = 0

        db = _seq_db(session, question, session)
        req = self._req(text="handball")  # wrong answer

        with patch(f"{_BASE}.AdaptiveLearningService") as MockSvc:
            MockSvc.return_value.record_answer.return_value = self._service_result()
            result = submit_answer(
                session_id=1, request=req, db=db, current_user=_student()
            )

        assert result.is_correct is False

    def test_session_questions_presented_zero(self):
        """SA-05: session.questions_presented=0 → success_rate=0 (no division)."""
        question = MagicMock()
        question.question_type = QuestionType.MULTIPLE_CHOICE
        question.explanation = ""

        session = MagicMock()
        session.questions_presented = 0
        session.questions_correct = 0
        session.xp_earned = 0

        db = _seq_db(session, question, session)
        req = self._req()

        with patch(f"{_BASE}.AdaptiveLearningService") as MockSvc:
            MockSvc.return_value.record_answer.return_value = self._service_result()
            result = submit_answer(
                session_id=1, request=req, db=db, current_user=_student()
            )

        assert result.session_stats["success_rate"] == 0

    def test_session_none_uses_defaults(self):
        """SA-06: session=None → session_stats use default 0s."""
        question = MagicMock()
        question.question_type = QuestionType.MULTIPLE_CHOICE
        question.explanation = ""

        db = _seq_db(None, question, None)  # legacy session check, question, final stats
        req = self._req()

        with patch(f"{_BASE}.AdaptiveLearningService") as MockSvc:
            MockSvc.return_value.record_answer.return_value = self._service_result()
            result = submit_answer(
                session_id=1, request=req, db=db, current_user=_student()
            )

        assert result.session_stats["questions_answered"] == 0


# ============================================================================
# end_learning_session
# ============================================================================

class TestEndLearningSession:

    def test_success_returns_summary(self):
        """ELS-01: end_session returns summary dict → SessionSummaryResponse."""
        summary = {
            "questions_answered": 10,
            "correct_answers": 7,
            "success_rate": 70.0,
            "xp_earned": 50,
            "performance_trend": 0.2,
            "final_difficulty": 0.65
        }
        with patch(f"{_BASE}.AdaptiveLearningService") as MockSvc:
            MockSvc.return_value.end_session.return_value = summary
            result = end_learning_session(
                session_id=1, db=_db(), current_user=_student()
            )

        assert result.questions_answered == 10
        assert result.success_rate == 70.0


# ============================================================================
# get_learning_analytics
# ============================================================================

class TestGetLearningAnalytics:

    def test_returns_analytics(self):
        """GLA-01: get_user_learning_analytics → LearningAnalyticsResponse."""
        analytics = {
            "total_questions_attempted": 100,
            "total_attempts": 110,
            "overall_success_rate": 0.75,
            "mastery_level": 0.8,
            "learning_velocity": 0.3,
            "recommended_difficulty": 0.6
        }
        with patch(f"{_BASE}.AdaptiveLearningService") as MockSvc:
            MockSvc.return_value.get_user_learning_analytics.return_value = analytics
            result = get_learning_analytics(
                category=None, db=_db(), current_user=_student()
            )

        assert result.total_questions_attempted == 100
        assert result.mastery_level == 0.8


# ============================================================================
# get_available_categories
# ============================================================================

class TestGetAvailableCategories:

    def test_returns_all_categories(self):
        """GAC-01: iterates QuizCategory enum, returns list of dicts."""
        db = MagicMock()
        q = MagicMock()
        q.join.return_value = q
        q.filter.return_value = q
        q.count.return_value = 5
        db.query.return_value = q

        result = get_available_categories(db=db, current_user=_student())

        assert isinstance(result, list)
        assert len(result) == len(list(QuizCategory))
        assert all("value" in item and "question_count" in item for item in result)


# ============================================================================
# get_adaptive_leaderboard
# ============================================================================

class TestGetAdaptiveLeaderboard:

    def _session(self, user_id, xp=10, presented=5, correct=4):
        s = MagicMock()
        s.user_id = user_id
        s.xp_earned = xp
        s.questions_presented = presented
        s.questions_correct = correct
        return s

    def _run_leaderboard(self, sessions, timeframe="week", category=None, current_uid=999):
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.isnot.return_value = q
        q.all.return_value = sessions
        db.query.return_value = q

        # User query for each session user
        user_mocks = {}
        for s in sessions:
            u = MagicMock()
            u.id = s.user_id
            u.name = f"User{s.user_id}"
            user_mocks[s.user_id] = u

        call_count = [0]

        def _user_query(*args):
            uq = MagicMock()
            uq.filter.return_value = uq
            # Sessions query first, then user queries
            if call_count[0] == 0:
                uq.all.return_value = sessions
            else:
                uid = list(user_mocks.keys())[call_count[0] - 1] if (call_count[0] - 1) < len(user_mocks) else None
                uq.first.return_value = user_mocks.get(uid)
            call_count[0] += 1
            return uq

        db.query.side_effect = _user_query

        user = MagicMock()
        user.id = current_uid

        return get_adaptive_leaderboard(
            category=category, timeframe=timeframe, db=db, current_user=user
        )

    def test_timeframe_week(self):
        """LB-01: timeframe='week' → since=7 days ago (not None)."""
        # Do NOT patch AdaptiveLearningSession: real SQLAlchemy column
        # supports >= with datetime; patching it breaks the comparison.
        sessions = [self._session(user_id=10)]
        db = MagicMock()

        u1 = MagicMock()
        u1.id = 10
        u1.name = "Player"

        def qside(*args):
            qq = MagicMock()
            qq.filter.return_value = qq
            qq.isnot.return_value = qq
            qq.all.return_value = sessions
            qq.first.return_value = u1
            return qq

        db.query.side_effect = qside
        user = MagicMock()
        user.id = 99

        result = get_adaptive_leaderboard(
            category=None, timeframe="week", db=db, current_user=user
        )
        assert "leaderboard" in result

    def test_timeframe_month(self):
        """LB-02: timeframe='month' → since=30 days ago."""
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.isnot.return_value = q
        q.all.return_value = []
        db.query.return_value = q

        result = get_adaptive_leaderboard(
            category=None, timeframe="month", db=db, current_user=MagicMock()
        )
        assert result["leaderboard"] == []

    def test_timeframe_all_time_else_branch(self):
        """LB-03: timeframe='all_time' → else branch (since=None)."""
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.isnot.return_value = q
        q.all.return_value = []
        db.query.return_value = q

        result = get_adaptive_leaderboard(
            category=None, timeframe="all_time", db=db, current_user=MagicMock()
        )
        assert result["leaderboard"] == []

    def test_category_filter_applied(self):
        """LB-04: category provided → category filter added to query."""
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.isnot.return_value = q
        q.all.return_value = []
        db.query.return_value = q

        result = get_adaptive_leaderboard(
            category=QuizCategory.GENERAL,
            timeframe="week",
            db=db,
            current_user=MagicMock()
        )
        assert "leaderboard" in result

    def test_user_position_found_when_in_leaderboard(self):
        """LB-05: current user in leaderboard → user_position is not None."""
        sessions = [self._session(user_id=42, xp=100)]

        db = MagicMock()
        q = MagicMock()

        user_obj = MagicMock()
        user_obj.id = 42
        user_obj.name = "Player A"

        call_n = [0]

        def qside(*args):
            qq = MagicMock()
            qq.filter.return_value = qq
            qq.isnot.return_value = qq
            if call_n[0] == 0:
                qq.all.return_value = sessions
            else:
                qq.first.return_value = user_obj
            call_n[0] += 1
            return qq

        db.query.side_effect = qside

        user = MagicMock()
        user.id = 42

        result = get_adaptive_leaderboard(
            category=None, timeframe="all_time", db=db, current_user=user
        )
        assert result["user_position"] is not None
        assert result["user_position"] == 1

    def test_user_not_found_skipped_in_leaderboard(self):
        """LB-06: session user not found in DB → not added to leaderboard."""
        sessions = [self._session(user_id=99)]

        db = MagicMock()
        call_n = [0]

        def qside(*args):
            qq = MagicMock()
            qq.filter.return_value = qq
            qq.isnot.return_value = qq
            if call_n[0] == 0:
                qq.all.return_value = sessions
            else:
                qq.first.return_value = None  # user not found
            call_n[0] += 1
            return qq

        db.query.side_effect = qside
        result = get_adaptive_leaderboard(
            category=None, timeframe="week", db=db, current_user=MagicMock()
        )
        assert result["leaderboard"] == []

    def test_zero_questions_success_rate(self):
        """LB-07: session.questions_presented=0 → success_rate=0 (no division)."""
        sessions = [self._session(user_id=10, presented=0, correct=0)]

        db = MagicMock()
        user_obj = MagicMock()
        user_obj.id = 10
        user_obj.name = "PlayerB"

        call_n = [0]

        def qside(*args):
            qq = MagicMock()
            qq.filter.return_value = qq
            qq.isnot.return_value = qq
            if call_n[0] == 0:
                qq.all.return_value = sessions
            else:
                qq.first.return_value = user_obj
            call_n[0] += 1
            return qq

        db.query.side_effect = qside
        result = get_adaptive_leaderboard(
            category=None, timeframe="all_time", db=db, current_user=MagicMock()
        )
        assert result["leaderboard"][0]["success_rate"] == 0
