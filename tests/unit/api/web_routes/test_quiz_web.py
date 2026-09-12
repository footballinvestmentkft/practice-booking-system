"""
Unit tests for app/api/web_routes/quiz.py

quiz.py contains three routes (unlock-quiz, take_quiz, submit_quiz) that are
structurally identical to the same routes in instructor.py (code duplication
in the production codebase). These tests mirror the instructor_web tests but
target the quiz.py module namespace.

Covers:
  unlock_quiz — not_instructor, not_found, wrong_type, wrong_instructor,
                not_started, success
  take_quiz — not_found, no_session_creates_attempt, time_expired,
              within_time_renders_take, session_quiz_not_found
  submit_quiz — not_found, attempt_not_found, already_completed,
                success_no_pass, success_with_pass_user_stats_created,
                session_id_none_string_skips_validation
"""
import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock, patch

from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from app.api.web_routes.quiz import (
    unlock_quiz,
    take_quiz,
    submit_quiz,
)
from app.models.user import UserRole
from app.models.session import SessionType


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_BASE = "app.api.web_routes.quiz"


def _instructor(uid=42):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.INSTRUCTOR
    return u


def _student(uid=99):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.STUDENT
    return u


def _session(
    session_id=1,
    instructor_id=42,
    session_type=SessionType.hybrid,
    actual_start=None,
):
    s = MagicMock()
    s.id = session_id
    s.instructor_id = instructor_id
    s.session_type = session_type
    s.actual_start_time = actual_start
    return s


def _mock_db(first_return=None):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = first_return
    db.query.return_value.options.return_value.filter.return_value.first.return_value = first_return
    return db


def _req():
    return MagicMock()


def _run(coro):
    return asyncio.run(coro)


# ──────────────────────────────────────────────────────────────────────────────
# unlock_quiz
# ──────────────────────────────────────────────────────────────────────────────

class TestUnlockQuiz:

    def test_not_instructor_redirects(self):
        result = _run(unlock_quiz(request=_req(), session_id=1, db=_mock_db(), user=_student()))
        assert isinstance(result, RedirectResponse)
        assert "unauthorized" in result.headers["location"]

    def test_session_not_found_redirects(self):
        result = _run(unlock_quiz(request=_req(), session_id=1, db=_mock_db(None), user=_instructor()))
        assert "session_not_found" in result.headers["location"]

    def test_wrong_type_not_hybrid_redirects(self):
        s = _session(session_type=SessionType.on_site)
        result = _run(unlock_quiz(request=_req(), session_id=1, db=_mock_db(s), user=_instructor()))
        assert "unlock_only_hybrid" in result.headers["location"]

    def test_wrong_instructor_redirects(self):
        s = _session(instructor_id=999, session_type=SessionType.hybrid)
        result = _run(unlock_quiz(request=_req(), session_id=1, db=_mock_db(s), user=_instructor(uid=42)))
        assert "not_your_session" in result.headers["location"]

    def test_session_not_started_redirects(self):
        s = _session(session_type=SessionType.hybrid, actual_start=None)
        result = _run(unlock_quiz(request=_req(), session_id=1, db=_mock_db(s), user=_instructor()))
        assert "session_not_started_unlock" in result.headers["location"]

    def test_success_unlocks_and_redirects(self):
        s = _session(session_type=SessionType.hybrid, actual_start=datetime.now(timezone.utc))
        db = _mock_db(s)
        result = _run(unlock_quiz(request=_req(), session_id=1, db=db, user=_instructor()))
        assert "quiz_unlocked" in result.headers["location"]
        assert s.quiz_unlocked is True
        db.commit.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# take_quiz
# ──────────────────────────────────────────────────────────────────────────────

class TestTakeQuiz:

    def test_quiz_not_found_raises_404(self):
        db = _mock_db(None)
        with pytest.raises(HTTPException) as exc_info:
            _run(take_quiz(request=_req(), quiz_id=1, session_id=None, db=db, user=_student()))
        assert exc_info.value.status_code == 404

    def test_no_session_no_active_attempt_creates_attempt(self):
        user = _student(uid=99)
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = [MagicMock()]
        quiz.time_limit_minutes = 30

        db = MagicMock()
        db.query.return_value.options.return_value.filter.return_value.first.return_value = quiz
        db.query.return_value.filter.return_value.first.return_value = None  # no active attempt
        db.refresh.side_effect = lambda obj: setattr(obj, 'id', 10)

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(take_quiz(request=_req(), quiz_id=1, session_id=None, db=db, user=user))

        db.add.assert_called()
        db.commit.assert_called()

    def test_time_expired_returns_quiz_result(self):
        user = _student(uid=99)
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.time_limit_minutes = 0  # Immediately expired

        active_attempt = MagicMock()
        active_attempt.started_at = datetime.now(timezone.utc) - timedelta(hours=2)

        db = MagicMock()
        db.query.return_value.options.return_value.filter.return_value.first.return_value = quiz
        db.query.return_value.filter.return_value.first.return_value = active_attempt

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(take_quiz(request=_req(), quiz_id=1, session_id=None, db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "quiz_result.html"
        assert active_attempt.score == 0.0
        assert active_attempt.passed is False

    def test_within_time_renders_take_template(self):
        user = _student(uid=99)
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = [MagicMock()]
        quiz.time_limit_minutes = 60

        active_attempt = MagicMock()
        active_attempt.started_at = datetime.now(timezone.utc)
        active_attempt.id = 5

        db = MagicMock()
        db.query.return_value.options.return_value.filter.return_value.first.return_value = quiz
        db.query.return_value.filter.return_value.first.return_value = active_attempt

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(take_quiz(request=_req(), quiz_id=1, session_id=None, db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "quiz_take.html"

    def test_session_quiz_not_linked_raises_404(self):
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.time_limit_minutes = 30

        db = MagicMock()
        db.query.return_value.options.return_value.filter.return_value.first.return_value = quiz
        db.query.return_value.filter.return_value.first.return_value = None  # No SessionQuiz

        with pytest.raises(HTTPException) as exc_info:
            _run(take_quiz(request=_req(), quiz_id=1, session_id=5, db=db, user=_student()))
        assert exc_info.value.status_code == 404

    def test_session_not_started_raises_403(self):
        """take_quiz with session_id: session found but not yet started (future)."""
        from zoneinfo import ZoneInfo
        from datetime import timedelta
        bud = ZoneInfo("Europe/Budapest")
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.time_limit_minutes = 30

        session_quiz = MagicMock()
        s = MagicMock()
        # Future naive Budapest datetime → session not started
        s.date_start = (datetime.now(bud) + timedelta(hours=3)).replace(tzinfo=None)

        db = MagicMock()
        db.query.return_value.options.return_value.filter.return_value.first.return_value = quiz
        # side_effect: SessionQuiz, Session
        db.query.return_value.filter.return_value.first.side_effect = [session_quiz, s]

        with pytest.raises(HTTPException) as exc_info:
            _run(take_quiz(request=_req(), quiz_id=1, session_id=5, db=db, user=_student()))
        assert exc_info.value.status_code == 403

    def test_session_started_not_booked_raises_403(self):
        """take_quiz with session_id: session started but student not booked."""
        from zoneinfo import ZoneInfo
        from datetime import timedelta
        bud = ZoneInfo("Europe/Budapest")
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.time_limit_minutes = 30

        session_quiz = MagicMock()
        s = MagicMock()
        # Past naive Budapest datetime → session already started
        s.date_start = (datetime.now(bud) - timedelta(hours=2)).replace(tzinfo=None)

        db = MagicMock()
        db.query.return_value.options.return_value.filter.return_value.first.return_value = quiz
        # side_effect: SessionQuiz, Session, Booking=None (not booked)
        db.query.return_value.filter.return_value.first.side_effect = [session_quiz, s, None]

        with pytest.raises(HTTPException) as exc_info:
            _run(take_quiz(request=_req(), quiz_id=1, session_id=5, db=db, user=_student()))
        assert exc_info.value.status_code == 403

    def test_session_not_found_after_session_quiz_raises_404(self):
        """take_quiz: session_quiz found but session=None → 404."""
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.time_limit_minutes = 30

        session_quiz = MagicMock()

        db = MagicMock()
        db.query.return_value.options.return_value.filter.return_value.first.return_value = quiz
        # side_effect: SessionQuiz found, Session=None
        db.query.return_value.filter.return_value.first.side_effect = [session_quiz, None]

        with pytest.raises(HTTPException) as exc_info:
            _run(take_quiz(request=_req(), quiz_id=1, session_id=5, db=db, user=_student()))
        assert exc_info.value.status_code == 404

    def test_session_start_aware_datetime_skips_tz_replace(self):
        """take_quiz: session_start already has tzinfo → False branch of tzinfo check."""
        from zoneinfo import ZoneInfo
        bud = ZoneInfo("Europe/Budapest")
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.time_limit_minutes = 30

        session_quiz = MagicMock()
        s = MagicMock()
        # Aware datetime (already past) → tzinfo is NOT None → skip replace → False branch
        s.date_start = datetime.now(bud) - timedelta(hours=1)  # past, tz-aware

        booking = MagicMock()
        active_attempt = MagicMock()
        active_attempt.started_at = datetime.now(timezone.utc)

        db = MagicMock()
        db.query.return_value.options.return_value.filter.return_value.first.return_value = quiz
        # Queries: SessionQuiz, SessionModel (validation), Booking, QuizAttempt,
        #          + SessionModel again at line 202 (template context)
        db.query.return_value.filter.return_value.first.side_effect = [session_quiz, s, booking, active_attempt, s]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(take_quiz(request=_req(), quiz_id=1, session_id=5, db=db, user=_student()))

        mock_tmpl.TemplateResponse.assert_called_once()

    def test_session_started_booked_renders_take_template(self):
        """take_quiz with session_id: all checks pass → renders quiz_take.html."""
        from zoneinfo import ZoneInfo
        from datetime import timedelta
        bud = ZoneInfo("Europe/Budapest")
        user = _student(uid=99)
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = [MagicMock()]
        quiz.time_limit_minutes = 60

        session_quiz = MagicMock()
        s = MagicMock()
        s.id = 5
        s.date_start = (datetime.now(bud) - timedelta(hours=1)).replace(tzinfo=None)

        booking = MagicMock()
        active_attempt = MagicMock()
        active_attempt.started_at = datetime.now(timezone.utc)
        active_attempt.id = 3

        db = MagicMock()
        db.query.return_value.options.return_value.filter.return_value.first.return_value = quiz
        # SessionQuiz, Session, Booking (booked), active_attempt, Session (line 202)
        db.query.return_value.filter.return_value.first.side_effect = [
            session_quiz, s, booking, active_attempt, s,
        ]

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(take_quiz(request=_req(), quiz_id=1, session_id=5, db=db, user=user))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "quiz_take.html"


# ──────────────────────────────────────────────────────────────────────────────
# submit_quiz
# ──────────────────────────────────────────────────────────────────────────────

class TestSubmitQuiz:

    def _form_req(self, form_data=None):
        req = MagicMock()
        req.form = AsyncMock(return_value=form_data or {})
        return req

    @staticmethod
    def _make_db(*side_effects):
        """Mock DB where .with_for_update() loops to the same filter mock.

        submit_quiz fetches the attempt with SELECT FOR UPDATE:
            db.query(QuizAttempt).filter(...).with_for_update().first()

        Without the loop, MagicMock auto-creates a new child mock for
        with_for_update().first() that ignores the side_effect list.
        With the loop, with_for_update() returns the same filter mock,
        so .first() continues popping from the shared side_effect sequence.
        """
        db = MagicMock()
        fm = db.query.return_value.filter.return_value
        fm.with_for_update.return_value = fm
        fm.first.side_effect = list(side_effects)
        return db

    def test_quiz_not_found_raises_404(self):
        db = _mock_db(None)
        with pytest.raises(HTTPException) as exc_info:
            _run(submit_quiz(
                request=self._form_req(),
                quiz_id=1,
                session_id=None,
                attempt_id=1,
                time_spent=5.0,
                db=db,
                user=_student(),
            ))
        assert exc_info.value.status_code == 404

    def test_attempt_not_found_raises_404(self):
        quiz = MagicMock()
        quiz.id = 1

        db = self._make_db(quiz, None)

        with pytest.raises(HTTPException) as exc_info:
            _run(submit_quiz(
                request=self._form_req(),
                quiz_id=1,
                session_id=None,
                attempt_id=1,
                time_spent=5.0,
                db=db,
                user=_student(),
            ))
        assert exc_info.value.status_code == 404

    def test_already_completed_raises_400(self):
        quiz = MagicMock()
        attempt = MagicMock()
        attempt.completed_at = datetime.now(timezone.utc)

        db = self._make_db(quiz, attempt)

        with pytest.raises(HTTPException) as exc_info:
            _run(submit_quiz(
                request=self._form_req(),
                quiz_id=1,
                session_id=None,
                attempt_id=1,
                time_spent=5.0,
                db=db,
                user=_student(),
            ))
        assert exc_info.value.status_code == 400

    def test_submit_success_no_questions_no_pass(self):
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.passing_score = 0.70
        quiz.xp_reward = 50

        attempt = MagicMock()
        attempt.completed_at = None
        attempt.id = 1

        db = self._make_db(quiz, attempt)

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(submit_quiz(
                request=self._form_req(),
                quiz_id=1,
                session_id=None,
                attempt_id=1,
                time_spent=5.0,
                db=db,
                user=_student(),
            ))

        template_name = mock_tmpl.TemplateResponse.call_args.args[0]
        assert template_name == "quiz_result.html"
        assert attempt.passed is False
        assert attempt.xp_awarded == 0

    def test_submit_pass_zero_threshold_creates_user_stats(self):
        """passing_score=0 means any score passes → XP awarded → UserStats created."""
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.passing_score = 0.0
        quiz.xp_reward = 50

        attempt = MagicMock()
        attempt.completed_at = None
        attempt.id = 1

        db = self._make_db(quiz, attempt, None)  # quiz, attempt, UserStats(None→create)

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(submit_quiz(
                request=self._form_req(),
                quiz_id=1,
                session_id=None,
                attempt_id=1,
                time_spent=3.0,
                db=db,
                user=_student(),
            ))

        db.add.assert_called()
        assert attempt.xp_awarded == 50

    def test_session_id_none_string_skips_session_validation(self):
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.passing_score = 1.0
        quiz.xp_reward = 0

        attempt = MagicMock()
        attempt.completed_at = None
        attempt.id = 1

        db = self._make_db(quiz, attempt)

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(submit_quiz(
                request=self._form_req(),
                quiz_id=1,
                session_id="None",
                attempt_id=1,
                time_spent=2.0,
                db=db,
                user=_student(),
            ))

        mock_tmpl.TemplateResponse.assert_called_once()

    def test_submit_with_session_id_validation_passes(self):
        """submit_quiz with valid session_id string: session started, user booked → success."""
        from zoneinfo import ZoneInfo
        from datetime import timedelta
        bud = ZoneInfo("Europe/Budapest")

        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.passing_score = 1.0
        quiz.xp_reward = 0

        attempt = MagicMock()
        attempt.completed_at = None
        attempt.id = 1

        session_quiz_rec = MagicMock()
        session_obj = MagicMock()
        # Past naive Budapest datetime → already started
        session_obj.date_start = (datetime.now(bud) - timedelta(hours=1)).replace(tzinfo=None)
        session_obj.session_type.value = "on_site"
        booking = MagicMock()

        # 1. quiz, 2. SessionQuiz, 3. Session, 4. Booking, 5. attempt(FOR UPDATE), 6. session_obj
        db = self._make_db(quiz, session_quiz_rec, session_obj, booking, attempt, session_obj)

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(submit_quiz(
                request=self._form_req(),
                quiz_id=1,
                session_id="5",
                attempt_id=1,
                time_spent=2.0,
                db=db,
                user=_student(),
            ))

        mock_tmpl.TemplateResponse.assert_called_once()

    def test_submit_with_questions_scores_answers(self):
        """submit_quiz with questions in quiz → exercises answer scoring loop."""
        question = MagicMock()
        question.id = 1
        question.points = 10

        correct_option = MagicMock()
        correct_option.is_correct = True

        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = [question]
        quiz.passing_score = 0.5
        quiz.xp_reward = 20

        attempt = MagicMock()
        attempt.completed_at = None
        attempt.id = 1

        db = self._make_db(quiz, attempt, correct_option, None)  # quiz, attempt, answer, UserStats

        form_data = {f"question_{question.id}": "1"}

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(submit_quiz(
                request=self._form_req(form_data),
                quiz_id=1,
                session_id=None,
                attempt_id=1,
                time_spent=1.0,
                db=db,
                user=_student(),
            ))

        assert attempt.passed is True  # 10/10 = 100% ≥ 50%
        assert attempt.xp_awarded == 20

    # ── Sprint 52: BrPart fixes ───────────────────────────────────────────────

    def test_submit_session_quiz_not_found_raises_404(self):
        """submit_quiz: session_id set, SessionQuiz=None → 404 (covers line 242)."""
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.passing_score = 1.0
        quiz.xp_reward = 0

        db = MagicMock()
        # quiz, then SessionQuiz=None → 404
        db.query.return_value.filter.return_value.first.side_effect = [quiz, None]

        with pytest.raises(HTTPException) as exc_info:
            _run(submit_quiz(
                request=self._form_req(),
                quiz_id=1,
                session_id="5",
                attempt_id=1,
                time_spent=1.0,
                db=db,
                user=_student(),
            ))
        assert exc_info.value.status_code == 404

    def test_submit_session_not_found_after_session_quiz_raises_404(self):
        """submit_quiz: session_quiz found, session=None → 404 (covers line 247)."""
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.passing_score = 1.0
        quiz.xp_reward = 0

        session_quiz_rec = MagicMock()

        db = MagicMock()
        # quiz, SessionQuiz, Session=None → 404
        db.query.return_value.filter.return_value.first.side_effect = [quiz, session_quiz_rec, None]

        with pytest.raises(HTTPException) as exc_info:
            _run(submit_quiz(
                request=self._form_req(),
                quiz_id=1,
                session_id="5",
                attempt_id=1,
                time_spent=1.0,
                db=db,
                user=_student(),
            ))
        assert exc_info.value.status_code == 404

    def test_submit_future_session_raises_403(self):
        """submit_quiz: session not yet started → 403 (covers line 257)."""
        from zoneinfo import ZoneInfo
        bud = ZoneInfo("Europe/Budapest")

        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.passing_score = 1.0
        quiz.xp_reward = 0

        session_quiz_rec = MagicMock()
        session_obj = MagicMock()
        # Future naive Budapest datetime → session not started yet
        session_obj.date_start = (datetime.now(bud) + timedelta(hours=3)).replace(tzinfo=None)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [quiz, session_quiz_rec, session_obj]

        with pytest.raises(HTTPException) as exc_info:
            _run(submit_quiz(
                request=self._form_req(),
                quiz_id=1,
                session_id="5",
                attempt_id=1,
                time_spent=1.0,
                db=db,
                user=_student(),
            ))
        assert exc_info.value.status_code == 403

    def test_submit_user_not_booked_raises_403(self):
        """submit_quiz: session started, no booking → 403 (covers line 270)."""
        from zoneinfo import ZoneInfo
        bud = ZoneInfo("Europe/Budapest")

        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.passing_score = 1.0
        quiz.xp_reward = 0

        session_quiz_rec = MagicMock()
        session_obj = MagicMock()
        # Past naive datetime → session already started
        session_obj.date_start = (datetime.now(bud) - timedelta(hours=1)).replace(tzinfo=None)

        db = MagicMock()
        # quiz, session_quiz, session, booking=None → 403
        db.query.return_value.filter.return_value.first.side_effect = [quiz, session_quiz_rec, session_obj, None]

        with pytest.raises(HTTPException) as exc_info:
            _run(submit_quiz(
                request=self._form_req(),
                quiz_id=1,
                session_id="5",
                attempt_id=1,
                time_spent=1.0,
                db=db,
                user=_student(),
            ))
        assert exc_info.value.status_code == 403

    def test_submit_session_start_aware_datetime_skips_tz_replace(self):
        """submit_quiz: session_start already has tzinfo → False branch 253→256."""
        from zoneinfo import ZoneInfo
        bud = ZoneInfo("Europe/Budapest")

        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.passing_score = 1.0
        quiz.xp_reward = 0

        session_quiz_rec = MagicMock()
        session_obj = MagicMock()
        # Aware datetime (already past) → tzinfo is not None → skip replace
        session_obj.date_start = datetime.now(bud) - timedelta(hours=1)  # tz-aware
        session_obj.session_type.value = "on_site"  # not virtual → no auto-attendance

        booking = MagicMock()
        attempt = MagicMock()
        attempt.completed_at = None
        attempt.id = 1

        # 1.quiz, 2.session_quiz, 3.session_obj, 4.booking, 5.attempt(FOR UPDATE), 6.session_obj
        db = self._make_db(quiz, session_quiz_rec, session_obj, booking, attempt, session_obj)

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(submit_quiz(
                request=self._form_req(),
                quiz_id=1,
                session_id="5",
                attempt_id=1,
                time_spent=1.0,
                db=db,
                user=_student(),
            ))

        mock_tmpl.TemplateResponse.assert_called_once()

    def test_submit_no_form_answer_loop_fallthrough(self):
        """submit_quiz: question exists but no form answer → loop False branch (302→297)."""
        question = MagicMock()
        question.id = 7
        question.points = 10

        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = [question]
        quiz.passing_score = 0.5
        quiz.xp_reward = 0

        attempt = MagicMock()
        attempt.completed_at = None
        attempt.id = 1

        db = self._make_db(quiz, attempt)

        # No form answer for question_7
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(submit_quiz(
                request=self._form_req({}),  # empty form
                quiz_id=1,
                session_id=None,
                attempt_id=1,
                time_spent=1.0,
                db=db,
                user=_student(),
            ))

        assert attempt.passed is False  # 0/10 < 50%

    def test_submit_wrong_answer_scores_zero(self):
        """submit_quiz: answer submitted but is_correct=False → 306→311 False branch."""
        question = MagicMock()
        question.id = 3
        question.points = 10

        wrong_option = MagicMock()
        wrong_option.is_correct = False

        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = [question]
        quiz.passing_score = 0.5
        quiz.xp_reward = 0

        attempt = MagicMock()
        attempt.completed_at = None
        attempt.id = 1

        db = self._make_db(quiz, attempt, wrong_option)

        form_data = {f"question_{question.id}": "3"}

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(submit_quiz(
                request=self._form_req(form_data),
                quiz_id=1,
                session_id=None,
                attempt_id=1,
                time_spent=1.0,
                db=db,
                user=_student(),
            ))

        assert attempt.passed is False  # 0 correct points < 50% threshold

    def test_submit_existing_user_stats_updates_xp(self):
        """submit_quiz: user_stats exists → else branch updates XP (covers 346-348)."""
        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.passing_score = 0.0  # always passes
        quiz.xp_reward = 100

        attempt = MagicMock()
        attempt.completed_at = None
        attempt.id = 1

        existing_stats = MagicMock()
        existing_stats.total_xp = 500
        existing_stats.level = 0

        db = self._make_db(quiz, attempt, existing_stats)  # quiz, attempt, user_stats(exists)

        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(submit_quiz(
                request=self._form_req(),
                quiz_id=1,
                session_id=None,
                attempt_id=1,
                time_spent=1.0,
                db=db,
                user=_student(),
            ))

        # existing_stats.total_xp should be updated
        assert existing_stats.total_xp == 600  # 500 + 100

    def test_submit_virtual_session_auto_marks_attendance(self):
        """submit_quiz: virtual session + passed + booking found + no attendance → auto-creates (360-383)."""
        from zoneinfo import ZoneInfo
        bud = ZoneInfo("Europe/Budapest")

        quiz = MagicMock()
        quiz.id = 1
        quiz.questions = []
        quiz.passing_score = 0.0  # always passes (0% threshold)
        quiz.xp_reward = 0         # no user_stats query needed

        # Session used in BOTH validation (line 245) and back-link (line 356) sections
        session_obj = MagicMock()
        session_obj.id = 5
        session_obj.date_start = (datetime.now(bud) - timedelta(hours=1)).replace(tzinfo=None)
        session_obj.session_type.value = "virtual"

        session_quiz_rec = MagicMock()
        booking_validation = MagicMock()   # user is booked (validation passes)

        attempt = MagicMock()
        attempt.completed_at = None
        attempt.id = 1

        booking_auto = MagicMock()   # booking for auto-attendance check
        booking_auto.id = 7

        # 1.quiz, 2.session_quiz, 3.session_obj(validation), 4.booking_validation,
        # 5.attempt(FOR UPDATE), 6.session_obj(back-link), 7.booking_auto, 8.None(no attendance)
        db = self._make_db(
            quiz, session_quiz_rec, session_obj, booking_validation, attempt,
            session_obj, booking_auto, None,
        )

        with (
            patch(f"{_BASE}.templates") as mock_tmpl,
            patch(f"{_BASE}.complete_virtual_session_participation") as complete,
        ):
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(submit_quiz(
                request=self._form_req(),
                quiz_id=1,
                session_id="5",
                attempt_id=1,
                time_spent=1.0,
                db=db,
                user=_student(),
            ))

        complete.assert_called_once_with(
            db,
            player=complete.call_args.kwargs["player"],
            session_id=5,
            notes="Auto-marked: Quiz completed with 0%",
            source="WEB_QUIZ",
        )
        assert complete.call_args.kwargs["player"].id == 99
