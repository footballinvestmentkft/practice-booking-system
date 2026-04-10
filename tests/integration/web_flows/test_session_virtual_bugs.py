"""
Virtual Session Page — Bug Fix Tests (SBF-01..10)

SBF-01  passing_score=0.60 → template shows "60%" (not "6000%")
SBF-02  Tournament-enrolled student (SemesterEnrollment only, no Booking)
        → is_enrolled=True, quiz section visible (no "Enrollment Required")
SBF-03  Virtual session → "Meeting Link" section NOT shown (removed entirely)
SBF-04  Virtual session → "📍 Location:" NOT shown (only for on-site/hybrid)
SBF-05  SemesterEnrollment student → GET take_quiz → 200 (not 403)
SBF-06  SemesterEnrollment student → POST submit_quiz → 200 result page (not 403)
SBF-07  GET review?session_id=<id> → back link = /sessions/<id>
SBF-08  GET review (no session_id) → back link = /sessions (generic)
SBF-09  SemesterEnrollment students → enrolled_students count matches enrollment count (not 0)
SBF-10  Admin can view another user's quiz attempt review → 200 + admin banner shown
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import get_db
from app.dependencies import get_current_user_web
from app.models.session import Session as SessionModel, SessionType
from app.models.quiz import (
    Quiz, QuizCategory, QuizDifficulty,
    QuizQuestion, QuestionType, QuizAnswerOption, SessionQuiz,
    QuizAttempt, QuizUserAnswer,
)
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.license import UserLicense

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _override_get_db(test_db):
    def override():
        try:
            yield test_db
        finally:
            pass
    return override


@contextmanager
def _web_client(test_db, user, csrf_bypass: bool = False):
    app.dependency_overrides[get_db] = _override_get_db(test_db)
    app.dependency_overrides[get_current_user_web] = lambda: user
    headers = {"Authorization": "Bearer test-csrf-bypass"} if csrf_bypass else {}
    with TestClient(app, raise_server_exceptions=True, headers=headers) as c:
        yield c
    app.dependency_overrides.clear()


def _make_quiz(test_db, passing_score: float) -> Quiz:
    """Create an active quiz with one MC question."""
    quiz = Quiz(
        title=f"SBF Quiz {uuid.uuid4().hex[:6]}",
        category=QuizCategory.GENERAL,
        difficulty=QuizDifficulty.EASY,
        time_limit_minutes=10,
        xp_reward=50,
        passing_score=passing_score,
        is_active=True,
    )
    test_db.add(quiz)
    test_db.flush()

    q = QuizQuestion(
        quiz_id=quiz.id,
        question_text="Test question?",
        question_type=QuestionType.MULTIPLE_CHOICE,
        points=1,
        order_index=0,
    )
    test_db.add(q)
    test_db.flush()

    test_db.add(QuizAnswerOption(question_id=q.id, option_text="Yes", is_correct=True, order_index=0))
    test_db.add(QuizAnswerOption(question_id=q.id, option_text="No", is_correct=False, order_index=1))
    test_db.commit()
    test_db.refresh(quiz)
    return quiz


def _make_virtual_session(test_db, semester_id, instructor_id, meeting_link=None) -> SessionModel:
    """Create a future virtual session linked to a semester (well within booking window)."""
    now = datetime.now()
    s = SessionModel(
        title=f"Virtual SBF Session {uuid.uuid4().hex[:6]}",
        session_type=SessionType.virtual,
        semester_id=semester_id,
        instructor_id=instructor_id,
        date_start=now + timedelta(days=3),
        date_end=now + timedelta(days=3, hours=2),
        capacity=10,
        meeting_link=meeting_link,
    )
    test_db.add(s)
    test_db.commit()
    test_db.refresh(s)
    return s


def _make_started_virtual_session(test_db, semester_id, instructor_id) -> SessionModel:
    """Create a virtual session that has already started (quiz taking is allowed)."""
    now = datetime.now()
    s = SessionModel(
        title=f"Virtual SBF Started {uuid.uuid4().hex[:6]}",
        session_type=SessionType.virtual,
        semester_id=semester_id,
        instructor_id=instructor_id,
        date_start=now - timedelta(hours=1),
        date_end=now + timedelta(hours=1),
        capacity=10,
    )
    test_db.add(s)
    test_db.commit()
    test_db.refresh(s)
    return s


def _make_completed_attempt(test_db, user_id: int, quiz: Quiz) -> QuizAttempt:
    """Create a completed QuizAttempt with one QuizUserAnswer (correct)."""
    now = datetime.now(timezone.utc)
    attempt = QuizAttempt(
        user_id=user_id,
        quiz_id=quiz.id,
        started_at=now - timedelta(minutes=10),
        completed_at=now - timedelta(minutes=5),
        time_spent_minutes=5.0,
        score=100.0,
        total_questions=1,
        correct_answers=1,
        xp_awarded=0,
        passed=True,
    )
    test_db.add(attempt)
    test_db.flush()

    question = quiz.questions[0]
    correct_opt = next(o for o in question.answer_options if o.is_correct)
    test_db.add(QuizUserAnswer(
        attempt_id=attempt.id,
        question_id=question.id,
        selected_option_id=correct_opt.id,
        is_correct=True,
    ))
    test_db.commit()
    test_db.refresh(attempt)
    return attempt


def _enroll(test_db, user_id, semester_id) -> SemesterEnrollment:
    """Enroll a student via SemesterEnrollment (tournament-style, no Booking).

    SemesterEnrollment requires a UserLicense FK (NOT NULL). Create one if absent.
    """
    lic = test_db.query(UserLicense).filter(UserLicense.user_id == user_id).first()
    if not lic:
        lic = UserLicense(
            user_id=user_id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.now(timezone.utc),
            is_active=True,
        )
        test_db.add(lic)
        test_db.flush()

    enr = SemesterEnrollment(
        user_id=user_id,
        semester_id=semester_id,
        user_license_id=lic.id,
        is_active=True,
        request_status=EnrollmentStatus.APPROVED,
        enrolled_at=datetime.now(timezone.utc),
    )
    test_db.add(enr)
    test_db.commit()
    test_db.refresh(enr)
    return enr


# ─────────────────────────────────────────────────────────────────────────────
# Tests — use `semester` fixture from web_flows/conftest.py (fresh per test)
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionVirtualBugs:

    def test_SBF_01_passing_score_0_60_displays_60_percent(
        self, test_db, semester, student_user, instructor_user
    ):
        """SBF-01: passing_score=0.60 → template shows '60%', NOT '6000%'."""
        quiz = _make_quiz(test_db, passing_score=0.60)
        session = _make_virtual_session(test_db, semester.id, instructor_user.id)
        sq = SessionQuiz(session_id=session.id, quiz_id=quiz.id, max_attempts=2)
        test_db.add(sq)
        test_db.commit()

        _enroll(test_db, student_user.id, semester.id)

        with _web_client(test_db, student_user) as client:
            resp = client.get(f"/sessions/{session.id}")

        assert resp.status_code == 200
        html = resp.text
        assert "6000%" not in html, "passing_score=0.60 must NOT display as 6000%"
        assert "60%" in html, "passing_score=0.60 must display as '60%'"

    def test_SBF_02_semester_enrolled_student_sees_quiz_not_enrollment_required(
        self, test_db, semester, student_user, instructor_user
    ):
        """SBF-02: SemesterEnrollment (no Booking) → quiz visible, not 'Enrollment Required'."""
        quiz = _make_quiz(test_db, passing_score=0.60)
        session = _make_virtual_session(test_db, semester.id, instructor_user.id)
        sq = SessionQuiz(session_id=session.id, quiz_id=quiz.id, max_attempts=2)
        test_db.add(sq)
        test_db.commit()

        # Only SemesterEnrollment — NO Booking created
        _enroll(test_db, student_user.id, semester.id)

        with _web_client(test_db, student_user) as client:
            resp = client.get(f"/sessions/{session.id}")

        assert resp.status_code == 200
        html = resp.text
        assert "Enrollment Required" not in html, \
            "SemesterEnrollment must grant quiz access — no 'Enrollment Required'"
        assert "You must book this session before you can take the quiz" not in html
        assert quiz.title in html, "Quiz title must appear when student is enrolled"

    def test_SBF_03_virtual_session_does_not_show_meeting_link_section(
        self, test_db, semester, student_user, instructor_user
    ):
        """SBF-03: Virtual session → '💻 Meeting Link' section removed entirely."""
        meeting_url = "https://meet.example.com/lfa-virtual-abc"
        session = _make_virtual_session(
            test_db, semester.id, instructor_user.id, meeting_link=meeting_url
        )
        _enroll(test_db, student_user.id, semester.id)

        with _web_client(test_db, student_user) as client:
            resp = client.get(f"/sessions/{session.id}")

        assert resp.status_code == 200
        html = resp.text
        assert "Meeting Link" not in html, \
            "Virtual session must NOT show 'Meeting Link' section (LFA platform sessions)"
        assert "📍 Location:" not in html, \
            "Virtual session must NOT show '📍 Location:'"

    def test_SBF_04_virtual_session_does_not_show_location(
        self, test_db, semester, student_user, instructor_user
    ):
        """SBF-04: Virtual session → neither 'Meeting Link' nor '📍 Location:' shown."""
        session = _make_virtual_session(
            test_db, semester.id, instructor_user.id, meeting_link=None
        )
        _enroll(test_db, student_user.id, semester.id)

        with _web_client(test_db, student_user) as client:
            resp = client.get(f"/sessions/{session.id}")

        assert resp.status_code == 200
        html = resp.text
        assert "Meeting Link" not in html, \
            "Virtual session must NOT show 'Meeting Link'"
        assert "📍 Location:" not in html, \
            "Virtual session must NOT show '📍 Location:'"

    def test_SBF_05_semester_enrolled_student_can_take_quiz(
        self, test_db, semester, student_user, instructor_user
    ):
        """SBF-05: SemesterEnrollment student (no Booking) → GET take_quiz → 200."""
        quiz = _make_quiz(test_db, passing_score=0.60)
        session = _make_started_virtual_session(test_db, semester.id, instructor_user.id)
        test_db.add(SessionQuiz(session_id=session.id, quiz_id=quiz.id, max_attempts=3))
        test_db.commit()

        _enroll(test_db, student_user.id, semester.id)

        with _web_client(test_db, student_user) as client:
            resp = client.get(f"/quizzes/{quiz.id}/take?session_id={session.id}")

        assert resp.status_code == 200, (
            f"SemesterEnrollment student must be allowed to take quiz, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_SBF_06_semester_enrolled_student_can_submit_quiz(
        self, test_db, semester, student_user, instructor_user
    ):
        """SBF-06: SemesterEnrollment student (no Booking) → POST submit_quiz → 200."""
        quiz = _make_quiz(test_db, passing_score=0.60)
        session = _make_started_virtual_session(test_db, semester.id, instructor_user.id)
        test_db.add(SessionQuiz(session_id=session.id, quiz_id=quiz.id, max_attempts=3))
        test_db.commit()

        _enroll(test_db, student_user.id, semester.id)

        # Create an active (incomplete) attempt directly in DB
        now = datetime.now(timezone.utc)
        attempt = QuizAttempt(
            user_id=student_user.id,
            quiz_id=quiz.id,
            started_at=now - timedelta(minutes=5),
            completed_at=None,
            total_questions=len(quiz.questions),
            correct_answers=0,
            xp_awarded=0,
            passed=False,
        )
        test_db.add(attempt)
        test_db.commit()
        test_db.refresh(attempt)

        question = quiz.questions[0]
        correct_opt = next(o for o in question.answer_options if o.is_correct)
        form_data = {
            "session_id": str(session.id),
            "attempt_id": str(attempt.id),
            "time_spent": "5.0",
            f"question_{question.id}": str(correct_opt.id),
        }

        with _web_client(test_db, student_user, csrf_bypass=True) as client:
            resp = client.post(f"/quizzes/{quiz.id}/submit", data=form_data)

        assert resp.status_code == 200, (
            f"SemesterEnrollment student must be allowed to submit quiz, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_SBF_07_review_with_session_id_shows_session_back_link(
        self, test_db, semester, student_user, instructor_user
    ):
        """SBF-07: GET /quizzes/attempts/{id}/review?session_id=X → back link = /sessions/X."""
        quiz = _make_quiz(test_db, passing_score=0.60)
        session = _make_virtual_session(test_db, semester.id, instructor_user.id)
        test_db.add(SessionQuiz(session_id=session.id, quiz_id=quiz.id, max_attempts=3))
        test_db.commit()

        attempt = _make_completed_attempt(test_db, student_user.id, quiz)

        with _web_client(test_db, student_user) as client:
            resp = client.get(
                f"/quizzes/attempts/{attempt.id}/review?session_id={session.id}"
            )

        assert resp.status_code == 200
        html = resp.text
        assert f'href="/sessions/{session.id}"' in html, \
            f"Back link must point to /sessions/{session.id}"

    def test_SBF_08_review_without_session_id_shows_generic_back_link(
        self, test_db, semester, student_user, instructor_user
    ):
        """SBF-08: GET /quizzes/attempts/{id}/review (no session_id) → back link = /sessions."""
        quiz = _make_quiz(test_db, passing_score=0.60)
        attempt = _make_completed_attempt(test_db, student_user.id, quiz)

        with _web_client(test_db, student_user) as client:
            resp = client.get(f"/quizzes/attempts/{attempt.id}/review")

        assert resp.status_code == 200
        html = resp.text
        assert 'href="/sessions"' in html, \
            "Back link must point to generic /sessions when no session_id given"
        assert f'href="/sessions/{attempt.id}"' not in html, \
            "Back link must NOT use attempt id as session id"

    def test_SBF_09_virtual_session_enrollment_count_matches_semester_enrollment(
        self, test_db, semester, student_user, instructor_user
    ):
        """SBF-09: Virtual session with SemesterEnrollment → enrolled count > 0 (not 0/capacity)."""
        session = _make_virtual_session(test_db, semester.id, instructor_user.id)
        _enroll(test_db, student_user.id, semester.id)

        with _web_client(test_db, student_user) as client:
            resp = client.get(f"/sessions/{session.id}")

        assert resp.status_code == 200
        html = resp.text
        # Template renders "{{ enrolled_students|length }}/{{ session.capacity }} students enrolled"
        assert "0/10 students enrolled" not in html, \
            "Enrollment count must NOT be 0 for SemesterEnrollment students"
        assert "1/10 students enrolled" in html, \
            "Enrollment count must reflect SemesterEnrollment (1 enrolled student)"

    def test_SBF_10_admin_can_view_any_participant_quiz_attempt_review(
        self, test_db, semester, student_user, admin_user, instructor_user
    ):
        """SBF-10: Admin can GET /quizzes/attempts/{id}/review for another user's attempt → 200 + admin banner."""
        quiz = _make_quiz(test_db, passing_score=0.60)
        attempt = _make_completed_attempt(test_db, student_user.id, quiz)

        with _web_client(test_db, admin_user) as client:
            resp = client.get(f"/quizzes/attempts/{attempt.id}/review")

        assert resp.status_code == 200, (
            f"Admin must be able to view any user's quiz attempt, got {resp.status_code}: {resp.text[:300]}"
        )
        html = resp.text
        assert "Admin view" in html, \
            "Review page must show admin banner when viewed by admin"
        assert student_user.name in html, \
            "Admin banner must include the reviewed student's name"
