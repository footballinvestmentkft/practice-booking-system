"""
Quiz Attempt Detail & Admin Attempts List — QA-01..07

QA-01  GET /api/v1/quizzes/attempts/{id}  — own completed attempt → 200 + full answer detail
QA-02  GET /api/v1/quizzes/attempts/{id}  — question→answer→correctness mapping is accurate
QA-03  GET /api/v1/quizzes/attempts/{id}  — other user's attempt → 404 (ownership check)
QA-04  GET /api/v1/quizzes/attempts/{id}  — attempt not yet completed → 400
QA-05  GET /api/v1/quizzes/attempts/{id}  — non-existent attempt → 404
QA-06  GET /api/v1/quizzes/admin/{quiz_id}/attempts  — admin sees all attempts → 200 + metadata
QA-07  GET /api/v1/quizzes/admin/{quiz_id}/attempts  — student role → 403

All tests use SAVEPOINT-isolated DB.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.database import get_db
from app.dependencies import get_current_user
from app.models.quiz import (
    Quiz, QuizCategory, QuizDifficulty,
    QuizQuestion, QuestionType,
    QuizAnswerOption,
    QuizAttempt, QuizUserAnswer,
)

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
def _api_client(test_db, user):
    """TestClient that overrides get_db + get_current_user for API endpoints."""
    app.dependency_overrides[get_db] = _override_get_db(test_db)
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _rich_quiz(test_db) -> tuple:
    """
    Create a quiz with 1 MC question (2 options: 1 correct, 1 wrong).

    Returns (quiz, question, correct_option, wrong_option).
    """
    quiz = Quiz(
        title=f"Detail Test Quiz {uuid.uuid4().hex[:6]}",
        category=QuizCategory.GENERAL,
        difficulty=QuizDifficulty.EASY,
        time_limit_minutes=10,
        xp_reward=50,
        passing_score=0.6,
    )
    test_db.add(quiz)
    test_db.flush()

    question = QuizQuestion(
        quiz_id=quiz.id,
        question_text="Which team won the 2014 FIFA World Cup?",
        question_type=QuestionType.MULTIPLE_CHOICE,
        points=1,
        order_index=0,
        explanation="Germany beat Argentina 1–0 in the final.",
    )
    test_db.add(question)
    test_db.flush()

    correct_opt = QuizAnswerOption(
        question_id=question.id,
        option_text="Germany",
        is_correct=True,
        order_index=0,
    )
    wrong_opt = QuizAnswerOption(
        question_id=question.id,
        option_text="Brazil",
        is_correct=False,
        order_index=1,
    )
    test_db.add(correct_opt)
    test_db.add(wrong_opt)
    test_db.commit()
    test_db.refresh(quiz)
    test_db.refresh(question)
    test_db.refresh(correct_opt)
    test_db.refresh(wrong_opt)
    return quiz, question, correct_opt, wrong_opt


def _completed_attempt(test_db, user, quiz, question, selected_option) -> tuple:
    """
    Create a completed QuizAttempt + QuizUserAnswer for (user, quiz, question).

    Returns (attempt, user_answer).
    """
    attempt = QuizAttempt(
        user_id=user.id,
        quiz_id=quiz.id,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        score=0.0 if not selected_option.is_correct else 100.0,
        total_questions=1,
        correct_answers=1 if selected_option.is_correct else 0,
        xp_awarded=0,
        passed=False,
    )
    test_db.add(attempt)
    test_db.flush()

    answer = QuizUserAnswer(
        attempt_id=attempt.id,
        question_id=question.id,
        selected_option_id=selected_option.id,
        is_correct=selected_option.is_correct,
    )
    test_db.add(answer)
    test_db.commit()
    test_db.refresh(attempt)
    return attempt, answer


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestQuizAttemptDetail:

    def test_QA_01_own_completed_attempt_returns_200(
        self, test_db, student_user
    ):
        """QA-01: GET /api/v1/quizzes/attempts/{id} → 200 for own completed attempt."""
        quiz, question, correct_opt, wrong_opt = _rich_quiz(test_db)
        attempt, _ = _completed_attempt(test_db, student_user, quiz, question, wrong_opt)

        with _api_client(test_db, student_user) as client:
            resp = client.get(f"/api/v1/quizzes/attempts/{attempt.id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == attempt.id
        assert body["quiz_id"] == quiz.id
        assert body["user_id"] == student_user.id
        assert body["quiz_title"] == quiz.title
        assert body["passed"] is False
        assert body["correct_answers"] == 0
        assert body["total_questions"] == 1
        assert len(body["answers"]) == 1

    def test_QA_02_answer_detail_correctness_mapping(
        self, test_db, student_user
    ):
        """QA-02: answer list contains accurate question text, selected & correct option."""
        quiz, question, correct_opt, wrong_opt = _rich_quiz(test_db)
        attempt, _ = _completed_attempt(test_db, student_user, quiz, question, wrong_opt)

        with _api_client(test_db, student_user) as client:
            resp = client.get(f"/api/v1/quizzes/attempts/{attempt.id}")

        assert resp.status_code == 200
        ans = resp.json()["answers"][0]

        # Question metadata
        assert ans["question_id"] == question.id
        assert ans["question_text"] == "Which team won the 2014 FIFA World Cup?"
        assert ans["question_order"] == 0

        # Correctness
        assert ans["is_correct"] is False
        assert ans["selected_option_id"] == wrong_opt.id
        assert ans["selected_option_text"] == "Brazil"   # what student chose

        # Pedagogical reveal: correct option exposed
        assert ans["correct_option_text"] == "Germany"

        # Explanation propagated
        assert ans["explanation"] == "Germany beat Argentina 1–0 in the final."

    def test_QA_03_other_users_attempt_returns_404(
        self, test_db, student_user, admin_user
    ):
        """QA-03: Student cannot see another user's attempt (ownership → 404)."""
        quiz, question, correct_opt, wrong_opt = _rich_quiz(test_db)
        # Attempt belongs to admin_user
        attempt, _ = _completed_attempt(test_db, admin_user, quiz, question, wrong_opt)

        with _api_client(test_db, student_user) as client:
            resp = client.get(f"/api/v1/quizzes/attempts/{attempt.id}")

        assert resp.status_code == 404

    def test_QA_04_incomplete_attempt_returns_400(
        self, test_db, student_user
    ):
        """QA-04: Attempt with completed_at=None → 400."""
        quiz, _, _, _ = _rich_quiz(test_db)
        incomplete = QuizAttempt(
            user_id=student_user.id,
            quiz_id=quiz.id,
            started_at=datetime.now(timezone.utc),
            completed_at=None,
            total_questions=1,
            correct_answers=0,
            xp_awarded=0,
            passed=False,
        )
        test_db.add(incomplete)
        test_db.commit()
        test_db.refresh(incomplete)

        with _api_client(test_db, student_user) as client:
            resp = client.get(f"/api/v1/quizzes/attempts/{incomplete.id}")

        assert resp.status_code == 400

    def test_QA_05_nonexistent_attempt_returns_404(
        self, test_db, student_user
    ):
        """QA-05: Non-existent attempt_id → 404."""
        with _api_client(test_db, student_user) as client:
            resp = client.get("/api/v1/quizzes/attempts/999999")

        assert resp.status_code == 404


class TestAdminAttemptsList:

    def test_QA_06_admin_sees_all_attempts(
        self, test_db, admin_user, student_user
    ):
        """QA-06: GET /api/v1/quizzes/admin/{quiz_id}/attempts → 200, all attempts listed."""
        quiz, question, correct_opt, wrong_opt = _rich_quiz(test_db)
        # Two attempts: student (wrong), admin (correct)
        attempt_s, _ = _completed_attempt(test_db, student_user, quiz, question, wrong_opt)
        attempt_a, _ = _completed_attempt(test_db, admin_user, quiz, question, correct_opt)

        with _api_client(test_db, admin_user) as client:
            resp = client.get(f"/api/v1/quizzes/admin/{quiz.id}/attempts")

        assert resp.status_code == 200
        body = resp.json()
        assert body["quiz_id"] == quiz.id
        assert body["quiz_title"] == quiz.title
        assert body["total_attempts"] == 2

        ids = {a["id"] for a in body["attempts"]}
        assert attempt_s.id in ids
        assert attempt_a.id in ids

        # Verify user metadata present
        for item in body["attempts"]:
            assert "user_email" in item
            assert "user_name" in item
            assert "score" in item
            assert "passed" in item

    def test_QA_07_student_on_admin_endpoint_returns_403(
        self, test_db, student_user
    ):
        """QA-07: Student role on admin attempts list → 403."""
        quiz, _, _, _ = _rich_quiz(test_db)

        with _api_client(test_db, student_user) as client:
            resp = client.get(f"/api/v1/quizzes/admin/{quiz.id}/attempts")

        assert resp.status_code == 403
