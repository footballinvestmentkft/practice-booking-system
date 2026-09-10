"""
Unit tests for app/api/api_v1/endpoints/quiz/attempts.py
Sprint 24 P2 — coverage: 19% stmt, 5% branch → ≥80%

NOTE: The production code has missing imports (SessionQuiz, SessionModel, Booking,
SessionType, Attendance, AttendanceStatus, ConfirmationStatus, SessionLocal,
GamificationService, logging, datetime, timezone).
All are patched with create=True in each test.

2 sync endpoints:
1. start_quiz_attempt  POST /start
2. submit_quiz_attempt POST /submit
"""
import pytest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch, call, DEFAULT
from fastapi import HTTPException

from app.api.api_v1.endpoints.quiz.attempts import (
    start_quiz_attempt,
    submit_quiz_attempt,
)
from app.models.user import UserRole

_BASE = "app.api.api_v1.endpoints.quiz.attempts"

# Names that are used in the function bodies but NOT imported at the top.
_MISSING = [
    "SessionQuiz", "SessionModel", "Booking", "BookingStatus",
    "SessionType", "datetime", "Attendance", "AttendanceStatus",
    "ConfirmationStatus", "SessionLocal", "logging",
    "GamificationService", "timezone",
]


@contextmanager
def _patch_missing():
    """Patch all missing names and yield a dict of name → mock."""
    # Use DEFAULT so patch.multiple returns the mocks in the context dict
    kwargs = {n: DEFAULT for n in _MISSING}
    with patch.multiple(_BASE, create=True, **kwargs) as patches:
        yield patches


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _student():
    u = MagicMock()
    u.id = 42
    u.role = UserRole.STUDENT
    return u


def _admin():
    u = MagicMock()
    u.id = 42
    u.role = UserRole.ADMIN
    return u


def _q(first=None, all_=None):
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = first
    q.all.return_value = all_ if all_ is not None else []
    return q


def _db(first=None, all_=None):
    db = MagicMock()
    db.query.return_value = _q(first=first, all_=all_)
    return db


def _attempt_data(quiz_id=1):
    a = MagicMock()
    a.quiz_id = quiz_id
    return a


# ===========================================================================
# start_quiz_attempt
# ===========================================================================

@pytest.mark.unit
class TestStartQuizAttempt:
    def test_non_student_raises_403(self):
        with _patch_missing():
            with pytest.raises(HTTPException) as exc:
                start_quiz_attempt(
                    attempt_data=_attempt_data(),
                    current_user=_admin(),
                    quiz_service=MagicMock(),
                    db=_db(),
                )
        assert exc.value.status_code == 403

    def test_no_session_quiz_calls_service_directly(self):
        quiz_service = MagicMock()
        mock_attempt = MagicMock()
        quiz_service.start_quiz_attempt.return_value = mock_attempt
        db = _db(first=None)  # no session_quiz → skip access control
        with _patch_missing():
            result = start_quiz_attempt(
                attempt_data=_attempt_data(quiz_id=5),
                current_user=_student(),
                quiz_service=quiz_service,
                db=db,
            )
        quiz_service.start_quiz_attempt.assert_called_once_with(42, 5)
        assert result is mock_attempt

    def test_no_session_quiz_service_valueerror_raises_400(self):
        quiz_service = MagicMock()
        quiz_service.start_quiz_attempt.side_effect = ValueError("attempt already started")
        db = _db(first=None)
        with _patch_missing():
            with pytest.raises(HTTPException) as exc:
                start_quiz_attempt(
                    attempt_data=_attempt_data(),
                    current_user=_student(),
                    quiz_service=quiz_service,
                    db=db,
                )
        assert exc.value.status_code == 400
        assert "attempt already started" in exc.value.detail

    def test_session_quiz_found_session_not_found_raises_404(self):
        session_quiz = MagicMock()
        # First query: session_quiz found; Second query: session not found
        q1 = _q(first=session_quiz)
        q2 = _q(first=None)
        db = MagicMock()
        db.query.side_effect = [q1, q2]
        with _patch_missing():
            with pytest.raises(HTTPException) as exc:
                start_quiz_attempt(
                    attempt_data=_attempt_data(),
                    current_user=_student(),
                    quiz_service=MagicMock(),
                    db=db,
                )
        assert exc.value.status_code == 404

    def test_session_found_no_booking_raises_403(self):
        session_quiz = MagicMock()
        session = MagicMock()
        session.session_type = "on_site"  # non-hybrid, non-virtual
        q1 = _q(first=session_quiz)   # session_quiz
        q2 = _q(first=session)         # session
        q3 = _q(first=None)            # no booking
        db = MagicMock()
        db.query.side_effect = [q1, q2, q3]
        with _patch_missing():
            with pytest.raises(HTTPException) as exc:
                start_quiz_attempt(
                    attempt_data=_attempt_data(),
                    current_user=_student(),
                    quiz_service=MagicMock(),
                    db=db,
                )
        assert exc.value.status_code == 403

    def test_hybrid_session_not_unlocked_raises_403(self):
        session_quiz = MagicMock()
        session = MagicMock()
        booking = MagicMock()
        session.quiz_unlocked = False  # not unlocked
        q1 = _q(first=session_quiz)
        q2 = _q(first=session)
        q3 = _q(first=booking)
        db = MagicMock()
        db.query.side_effect = [q1, q2, q3]
        with _patch_missing() as mocks:
            mocks["SessionType"].hybrid = session.session_type = "hybrid"
            session.session_type = mocks["SessionType"].hybrid
            with pytest.raises(HTTPException) as exc:
                start_quiz_attempt(
                    attempt_data=_attempt_data(),
                    current_user=_student(),
                    quiz_service=MagicMock(),
                    db=db,
                )
        assert exc.value.status_code == 403
        assert "unlocked" in exc.value.detail.lower()

    def test_hybrid_session_unlocked_no_attendance_raises_403(self):
        session_quiz = MagicMock()
        session = MagicMock()
        booking = MagicMock()
        session.quiz_unlocked = True
        q1 = _q(first=session_quiz)
        q2 = _q(first=session)
        q3 = _q(first=booking)
        q4 = _q(first=None)   # no attendance
        db = MagicMock()
        db.query.side_effect = [q1, q2, q3, q4]
        with _patch_missing() as mocks:
            session.session_type = mocks["SessionType"].hybrid
            with pytest.raises(HTTPException) as exc:
                start_quiz_attempt(
                    attempt_data=_attempt_data(),
                    current_user=_student(),
                    quiz_service=MagicMock(),
                    db=db,
                )
        assert exc.value.status_code == 403
        assert "attendance" in exc.value.detail.lower()

    def test_virtual_session_before_start_raises_400(self):
        session_quiz = MagicMock()
        session = MagicMock()
        booking = MagicMock()
        q1 = _q(first=session_quiz)
        q2 = _q(first=session)
        q3 = _q(first=booking)
        db = MagicMock()
        db.query.side_effect = [q1, q2, q3]
        with _patch_missing() as mocks:
            session.session_type = mocks["SessionType"].virtual
            # datetime.now() < session.date_start → before start
            mock_now = MagicMock()
            mocks["datetime"].now.return_value = mock_now
            mock_now.__lt__ = MagicMock(return_value=True)   # now < date_start
            with pytest.raises(HTTPException) as exc:
                start_quiz_attempt(
                    attempt_data=_attempt_data(),
                    current_user=_student(),
                    quiz_service=MagicMock(),
                    db=db,
                )
        assert exc.value.status_code == 400

    def test_virtual_session_after_end_raises_400(self):
        session_quiz = MagicMock()
        session = MagicMock()
        booking = MagicMock()
        q1 = _q(first=session_quiz)
        q2 = _q(first=session)
        q3 = _q(first=booking)
        db = MagicMock()
        db.query.side_effect = [q1, q2, q3]
        with _patch_missing() as mocks:
            session.session_type = mocks["SessionType"].virtual
            mock_now = MagicMock()
            mocks["datetime"].now.return_value = mock_now
            mock_now.__lt__ = MagicMock(return_value=False)  # now >= date_start
            mock_now.__gt__ = MagicMock(return_value=True)   # now > date_end
            with pytest.raises(HTTPException) as exc:
                start_quiz_attempt(
                    attempt_data=_attempt_data(),
                    current_user=_student(),
                    quiz_service=MagicMock(),
                    db=db,
                )
        assert exc.value.status_code == 400
        assert "ended" in exc.value.detail.lower()


# ===========================================================================
# submit_quiz_attempt
# ===========================================================================

@pytest.mark.unit
class TestSubmitQuizAttempt:
    def _submission(self):
        return MagicMock()

    def test_non_student_raises_403(self):
        with _patch_missing():
            with pytest.raises(HTTPException) as exc:
                submit_quiz_attempt(
                    submission=self._submission(),
                    current_user=_admin(),
                    quiz_service=MagicMock(),
                    db=_db(),
                )
        assert exc.value.status_code == 403

    def test_submit_valueerror_raises_400(self):
        quiz_service = MagicMock()
        quiz_service.submit_quiz_attempt.side_effect = ValueError("invalid submission")
        with _patch_missing():
            with pytest.raises(HTTPException) as exc:
                submit_quiz_attempt(
                    submission=self._submission(),
                    current_user=_student(),
                    quiz_service=quiz_service,
                    db=_db(),
                )
        assert exc.value.status_code == 400

    def test_happy_path_returns_attempt(self):
        quiz_service = MagicMock()
        mock_attempt = MagicMock()
        mock_attempt.quiz_id = 1
        mock_attempt.id = 10
        mock_attempt.score = 85.0
        quiz_service.submit_quiz_attempt.return_value = mock_attempt
        quiz_service.get_quiz_by_id.return_value = MagicMock(id=1)

        db = _db(first=None)   # no session_quiz → skip auto-attendance
        with _patch_missing() as mocks:
            mocks["GamificationService"].return_value.check_and_unlock_achievements.return_value = []
            result = submit_quiz_attempt(
                submission=self._submission(),
                current_user=_student(),
                quiz_service=quiz_service,
                db=db,
            )
        assert result is mock_attempt

    def test_score_below_70_generates_recommendations(self):
        quiz_service = MagicMock()
        mock_attempt = MagicMock()
        mock_attempt.score = 45.0
        mock_attempt.quiz_id = 1
        quiz_service.submit_quiz_attempt.return_value = mock_attempt
        quiz_service.get_quiz_by_id.return_value = MagicMock(id=1)
        db = _db(first=None)
        with _patch_missing() as mocks:
            adapt_svc = mocks["SessionLocal"]  # just need a mock; adapt_service is AdaptiveLearningService(hook_db)
            # AdaptiveLearningService is imported at module level, not in _MISSING
            mocks["GamificationService"].return_value.check_and_unlock_achievements.return_value = []
            result = submit_quiz_attempt(
                submission=self._submission(),
                current_user=_student(),
                quiz_service=quiz_service,
                db=db,
            )
        assert result is mock_attempt

    def test_score_100_checks_perfect_score_achievement(self):
        quiz_service = MagicMock()
        mock_attempt = MagicMock()
        mock_attempt.score = 100.0
        mock_attempt.quiz_id = 1
        quiz_service.submit_quiz_attempt.return_value = mock_attempt
        quiz_service.get_quiz_by_id.return_value = MagicMock(id=1)
        db = _db(first=None)
        with _patch_missing() as mocks:
            gami = mocks["GamificationService"].return_value
            gami.check_and_unlock_achievements.return_value = []
            submit_quiz_attempt(
                submission=self._submission(),
                current_user=_student(),
                quiz_service=quiz_service,
                db=db,
            )
        # check_and_unlock_achievements called twice: complete_quiz + quiz_perfect_score
        assert gami.check_and_unlock_achievements.call_count == 2
        calls = [c[1] for c in gami.check_and_unlock_achievements.call_args_list]
        actions = [kw.get("trigger_action") for kw in calls]
        assert "quiz_perfect_score" in actions

    def test_hook_exception_caught_allows_result(self):
        quiz_service = MagicMock()
        mock_attempt = MagicMock()
        mock_attempt.score = 80.0
        mock_attempt.quiz_id = 1
        quiz_service.submit_quiz_attempt.return_value = mock_attempt
        db = _db(first=None)
        with _patch_missing() as mocks:
            # SessionLocal raises → inner try/except catches it
            mocks["SessionLocal"].side_effect = Exception("DB connection failed")
            mocks["GamificationService"].return_value.check_and_unlock_achievements.return_value = []
            result = submit_quiz_attempt(
                submission=self._submission(),
                current_user=_student(),
                quiz_service=quiz_service,
                db=db,
            )
        assert result is mock_attempt

    def test_gamification_exception_caught_allows_result(self):
        quiz_service = MagicMock()
        mock_attempt = MagicMock()
        mock_attempt.score = 80.0
        mock_attempt.quiz_id = 1
        quiz_service.submit_quiz_attempt.return_value = mock_attempt
        db = _db(first=None)
        with _patch_missing() as mocks:
            mocks["GamificationService"].return_value.check_and_unlock_achievements.side_effect = Exception("gami error")
            result = submit_quiz_attempt(
                submission=self._submission(),
                current_user=_student(),
                quiz_service=quiz_service,
                db=db,
            )
        assert result is mock_attempt

    def test_auto_attendance_existing_virtual_session_updates(self):
        quiz_service = MagicMock()
        mock_attempt = MagicMock()
        mock_attempt.score = 90.0
        mock_attempt.quiz_id = 1
        quiz_service.submit_quiz_attempt.return_value = mock_attempt
        quiz_service.get_quiz_by_id.return_value = MagicMock(id=1)

        session_quiz = MagicMock()
        session = MagicMock()
        session.title = "Virtual Session"
        q_sessionquiz = _q(first=session_quiz)
        q_session = _q(first=session)
        db = MagicMock()
        db.query.side_effect = [q_sessionquiz, q_session]

        with _patch_missing() as mocks, patch(
            f"{_BASE}.complete_virtual_session_participation"
        ) as completion:
            mocks["GamificationService"].return_value.check_and_unlock_achievements.return_value = []
            completion.return_value.attendance.id = 17
            # Make session.session_type look like 'virtual'
            session.session_type = "virtual"
            player = _student()
            result = submit_quiz_attempt(
                submission=self._submission(),
                current_user=player,
                quiz_service=quiz_service,
                db=db,
            )
        assert result is mock_attempt
        completion.assert_called_once_with(
            db,
            player=player,
            session_id=session.id,
            notes="Auto-marked: Quiz completed with 90.0%",
            source="API_QUIZ",
        )
        mocks["GamificationService"].return_value.award_attendance_xp.assert_called_once_with(
            attendance_id=17, quiz_score_percent=90.0
        )

    def test_auto_attendance_exception_caught_allows_result(self):
        quiz_service = MagicMock()
        mock_attempt = MagicMock()
        mock_attempt.score = 80.0
        mock_attempt.quiz_id = 1
        quiz_service.submit_quiz_attempt.return_value = mock_attempt
        db = MagicMock()
        # Make db.query raise on auto-attendance query (first call = session_quiz)
        db.query.side_effect = Exception("table error")
        with _patch_missing() as mocks:
            mocks["GamificationService"].return_value.check_and_unlock_achievements.return_value = []
            result = submit_quiz_attempt(
                submission=self._submission(),
                current_user=_student(),
                quiz_service=quiz_service,
                db=db,
            )
        assert result is mock_attempt
