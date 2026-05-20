"""
AL-LOG tests — adaptive_learning_answer_log audit trail
=======================================================

AL-LOG-01  successful answer → ALAnswerLog row written to DB
AL-LOG-02  presented_option_ids matches what frontend sent
AL-LOG-03  correct_option_position = index of correct option in presented list
AL-LOG-04  timed_out=True → selected_option_id=NULL, row still written
AL-LOG-05  db.add() raises → /answer still returns 200 (audit never blocks flow)
AL-LOG-06  presented_option_ids absent → row written with NULL, response 200

Strategy:
  - Use postgres_db with minimal seed: Quiz + QuizQuestion + 4 QuizAnswerOptions
  - Patch _session_guard (avoids AdaptiveLearningSession FK), require_student_onboarding,
    and AdaptiveLearningService.record_answer (avoids UserQuestionPerformance queries)
  - Call al_session_answer via asyncio.run() directly (no HTTP stack)
  - Verify ALAnswerLog rows via postgres_db after the call
"""
from __future__ import annotations

import asyncio
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from app.api.web_routes.adaptive_learning import al_session_answer
from app.models.quiz import (
    ALAnswerLog,
    AdaptiveLearningSession,
    Quiz, QuizCategory, QuizDifficulty,
    QuizQuestion, QuestionType,
    QuizAnswerOption,
)
from app.models.user import User, UserRole

_BASE = "app.api.web_routes.adaptive_learning"

# ── seed helpers ──────────────────────────────────────────────────────────────

_UID = 42
_SID = 99


def _seed_all(db) -> tuple:
    """Insert User + AdaptiveLearningSession + Quiz + QuizQuestion + 4 options.

    Returns (user, al_session, question, [opts], correct_opt).
    FK chain: user → al_session; quiz → question → opts.
    ALAnswerLog requires user_id and session_id FKs to exist.
    """
    import uuid
    user = User(
        name="AL Log Test User",
        email=f"allog_{uuid.uuid4().hex[:8]}@test.invalid",
        password_hash="x",
        role=UserRole.STUDENT,
    )
    db.add(user)
    db.flush()

    al_session = AdaptiveLearningSession(
        user_id=user.id,
        category=QuizCategory.GENERAL,
        language="en",
    )
    db.add(al_session)
    db.flush()

    quiz = Quiz(
        title="__al_log_test__",
        category=QuizCategory.GENERAL,
        difficulty=QuizDifficulty.EASY,
        time_limit_minutes=5,
        xp_reward=0,
        passing_score=50.0,
        language="en",
    )
    db.add(quiz)
    db.flush()

    question = QuizQuestion(
        quiz_id=quiz.id,
        question_text="AL-LOG integration test question?",
        question_type=QuestionType.MULTIPLE_CHOICE,
        order_index=0,
        explanation="Because it is.",
    )
    db.add(question)
    db.flush()

    opts = []
    for i in range(3):
        opt = QuizAnswerOption(
            question_id=question.id,
            option_text=f"Wrong {i}",
            is_correct=False,
            order_index=i,
        )
        db.add(opt)
        opts.append(opt)

    correct = QuizAnswerOption(
        question_id=question.id,
        option_text="Correct option",
        is_correct=True,
        order_index=3,
    )
    db.add(correct)
    opts.append(correct)
    db.commit()

    return user, al_session, question, opts, correct


def _mock_request(body: dict) -> MagicMock:
    req = MagicMock()
    req.json = AsyncMock(return_value=body)
    return req


def _mock_user(uid: int) -> MagicMock:
    u = MagicMock()
    u.id = uid
    return u


def _mock_al_session(sid: int) -> MagicMock:
    s = MagicMock()
    s.id = sid
    s.ended_at = None
    return s


# ── shared patches ────────────────────────────────────────────────────────────

def _run_answer(db, body: dict, session_id: int, user_id: int):
    """Call al_session_answer with the real postgres_db and minimal patches."""
    al_session = _mock_al_session(session_id)
    with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
         patch(f"{_BASE}._session_guard", return_value=(al_session, None)), \
         patch(f"{_BASE}.AdaptiveLearningService") as MockSvc:
        MockSvc.return_value.record_answer.return_value = {
            "score_delta": 2,
            "score": 2,
            "new_target_difficulty": 0.5,
            "performance_trend": 0.0,
        }
        resp = asyncio.run(al_session_answer(
            session_id=session_id,
            request=_mock_request(body),
            db=db,
            user=_mock_user(user_id),
        ))
    return resp


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestALAnswerLog:

    def test_al_log_01_row_written_on_correct_answer(self, postgres_db):
        """AL-LOG-01: successful answer → ALAnswerLog row appears in DB."""
        user, al_session, question, opts, correct = _seed_all(postgres_db)
        presented_ids = [o.id for o in opts]

        body = {
            "question_id": question.id,
            "selected_option_id": correct.id,
            "time_spent_seconds": 12.0,
            "presented_option_ids": presented_ids,
        }
        resp = _run_answer(postgres_db, body, session_id=al_session.id, user_id=user.id)

        assert resp.status_code == 200

        row = (
            postgres_db.query(ALAnswerLog)
            .filter_by(question_id=question.id)
            .first()
        )
        assert row is not None, "ALAnswerLog row must exist after /answer call"
        assert row.is_correct is True
        assert row.timed_out is False
        assert row.correct_option_id == correct.id

    def test_al_log_02_presented_option_ids_stored(self, postgres_db):
        """AL-LOG-02: presented_option_ids from frontend stored verbatim."""
        user, al_session, question, opts, correct = _seed_all(postgres_db)
        # Simulate a shuffled presentation order (correct at position 2 = C)
        presented_ids = [opts[2].id, opts[0].id, correct.id, opts[1].id]

        body = {
            "question_id": question.id,
            "selected_option_id": correct.id,
            "time_spent_seconds": 8.0,
            "presented_option_ids": presented_ids,
        }
        _run_answer(postgres_db, body, session_id=al_session.id, user_id=user.id)

        row = postgres_db.query(ALAnswerLog).filter_by(question_id=question.id).first()
        assert row.presented_option_ids == presented_ids, (
            f"Expected {presented_ids}, got {row.presented_option_ids}"
        )

    def test_al_log_03_correct_option_position_derived(self, postgres_db):
        """AL-LOG-03: correct_option_position = index of correct_option_id in presented list."""
        user, al_session, question, opts, correct = _seed_all(postgres_db)
        # Present correct at index 1 (B)
        presented_ids = [opts[0].id, correct.id, opts[1].id, opts[2].id]

        body = {
            "question_id": question.id,
            "selected_option_id": correct.id,
            "time_spent_seconds": 10.0,
            "presented_option_ids": presented_ids,
        }
        _run_answer(postgres_db, body, session_id=al_session.id, user_id=user.id)

        row = postgres_db.query(ALAnswerLog).filter_by(question_id=question.id).first()
        assert row.correct_option_position == 1, (
            f"Correct option at presented index 1 (B), got position={row.correct_option_position}"
        )

    def test_al_log_04_timeout_null_selected_option(self, postgres_db):
        """AL-LOG-04: timed_out=True → selected_option_id is NULL, row written."""
        user, al_session, question, opts, correct = _seed_all(postgres_db)
        presented_ids = [o.id for o in opts]

        body = {
            "question_id": question.id,
            "timed_out": True,
            "time_spent_seconds": 180.0,
            "presented_option_ids": presented_ids,
        }
        resp = _run_answer(postgres_db, body, session_id=al_session.id, user_id=user.id)

        assert resp.status_code == 200

        row = postgres_db.query(ALAnswerLog).filter_by(question_id=question.id).first()
        assert row is not None
        assert row.timed_out is True
        assert row.selected_option_id is None
        assert row.is_correct is False

    def test_al_log_05_log_db_failure_does_not_break_answer(self, postgres_db):
        """AL-LOG-05: if audit log INSERT raises, /answer still returns 200."""
        user, al_session, question, opts, correct = _seed_all(postgres_db)

        body = {
            "question_id": question.id,
            "selected_option_id": correct.id,
            "time_spent_seconds": 5.0,
            "presented_option_ids": [o.id for o in opts],
        }

        mock_session_obj = _mock_al_session(al_session.id)
        original_add = postgres_db.add

        def _add_with_failure(obj):
            if isinstance(obj, ALAnswerLog):
                raise RuntimeError("simulated audit log DB failure")
            return original_add(obj)

        postgres_db.add = _add_with_failure
        try:
            with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
                 patch(f"{_BASE}._session_guard", return_value=(mock_session_obj, None)), \
                 patch(f"{_BASE}.AdaptiveLearningService") as MockSvc:
                MockSvc.return_value.record_answer.return_value = {
                    "score_delta": 2, "score": 2,
                    "new_target_difficulty": 0.5, "performance_trend": 0.0,
                }
                resp = asyncio.run(al_session_answer(
                    session_id=al_session.id,
                    request=_mock_request(body),
                    db=postgres_db,
                    user=_mock_user(user.id),
                ))
        finally:
            postgres_db.add = original_add

        assert resp.status_code == 200, (
            "Audit log failure must never break the /answer response"
        )
        data = json.loads(resp.body)
        assert "correct" in data

    def test_al_log_06_missing_presented_option_ids_writes_null(self, postgres_db):
        """AL-LOG-06: body without presented_option_ids → row written with NULL, 200."""
        user, al_session, question, opts, correct = _seed_all(postgres_db)

        body = {
            "question_id": question.id,
            "selected_option_id": correct.id,
            "time_spent_seconds": 9.0,
            # presented_option_ids intentionally absent
        }
        resp = _run_answer(postgres_db, body, session_id=al_session.id, user_id=user.id)

        assert resp.status_code == 200

        row = postgres_db.query(ALAnswerLog).filter_by(question_id=question.id).first()
        assert row is not None
        assert row.presented_option_ids is None
        assert row.correct_option_position is None
