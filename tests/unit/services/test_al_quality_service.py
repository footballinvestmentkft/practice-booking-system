"""Unit tests for al_quality_service.

CQT-01..04   TestQuizQualitySummaryBasic     — None for unknown, defaults, min-questions flag
CQT-05..07   TestExplanationAndScore         — explanation ratio, quality_score range
CQT-08..09   TestReadyToPublish              — ready_to_publish property logic
CQT-10..11   TestPerQuestionFlags            — fixed is_correct count, pool distractor floor
CQT-12       TestGlobalReport               — ARCHIVED quizzes excluded
"""
import pytest
from unittest.mock import MagicMock, patch

from app.models.quiz import ContentStatus, OptionType
from app.services.al_quality_service import (
    QuizQualitySummary,
    QuestionQualityFlag,
    get_quiz_quality_summary,
    get_global_quality_report,
    _difficulty_spread,
)

_SVC = "app.services.al_quality_service"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_quiz(quiz_id=1, title="Test Quiz", status=ContentStatus.DRAFT.value):
    q = MagicMock()
    q.id = quiz_id
    q.title = title
    q.content_status = status
    return q


def _make_question(question_id=10, quiz_id=1, text="What is pressing?", explanation="Pressing is..."):
    q = MagicMock()
    q.id = question_id
    q.quiz_id = quiz_id
    q.question_text = text
    q.explanation = explanation
    q.order_index = question_id
    return q


def _make_option(option_id=100, question_id=10, is_correct=False,
                 option_type=OptionType.FIXED, text="Option"):
    o = MagicMock()
    o.id = option_id
    o.question_id = question_id
    o.is_correct = is_correct
    o.option_type = option_type
    o.option_text = text
    return o


def _make_meta(question_id=10, difficulty=0.5, cognitive=0.5, avg_time=30.0):
    m = MagicMock()
    m.question_id = question_id
    m.estimated_difficulty = difficulty
    m.cognitive_load = cognitive
    m.average_time_seconds = avg_time
    return m


def _db_for_quiz(quiz, questions, options, meta_list):
    """Build a mock db that returns the right objects in query order."""
    db = MagicMock()

    # query(Quiz).filter().first() → quiz
    q_quiz = MagicMock()
    q_quiz.filter.return_value = q_quiz
    q_quiz.first.return_value = quiz

    # query(QuizQuestion).filter().order_by().all() → questions
    q_questions = MagicMock()
    q_questions.filter.return_value = q_questions
    q_questions.order_by.return_value = q_questions
    q_questions.all.return_value = questions

    # query(QuizAnswerOption).filter().all() → options
    q_options = MagicMock()
    q_options.filter.return_value = q_options
    q_options.all.return_value = options

    # query(QuestionMetadata).filter().all() → meta
    q_meta = MagicMock()
    q_meta.filter.return_value = q_meta
    q_meta.all.return_value = meta_list

    db.query.side_effect = [q_quiz, q_questions, q_options, q_meta]
    return db


# ── TestQuizQualitySummaryBasic ────────────────────────────────────────────────

class TestQuizQualitySummaryBasic:

    def test_cqt01_returns_none_for_unknown_quiz(self):
        """CQT-01: Quiz not found → None returned."""
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = None
        db.query.return_value = q

        result = get_quiz_quality_summary(db, quiz_id=9999)
        assert result is None

    def test_cqt02_returns_summary_for_known_quiz(self):
        """CQT-02: Known quiz → QuizQualitySummary returned with correct quiz_id."""
        quiz = _make_quiz(quiz_id=5)
        db = _db_for_quiz(quiz, questions=[], options=[], meta_list=[])
        result = get_quiz_quality_summary(db, quiz_id=5)
        assert isinstance(result, QuizQualitySummary)
        assert result.quiz_id == 5

    def test_cqt03_flags_too_few_questions(self):
        """CQT-03: < 3 questions → quiz-level flag present."""
        quiz = _make_quiz()
        questions = [_make_question(i) for i in range(1, 3)]  # only 2
        db = _db_for_quiz(quiz, questions, options=[], meta_list=[])
        result = get_quiz_quality_summary(db, quiz_id=1)
        assert any("Too few" in f for f in result.flags)
        assert result.has_min_questions is False

    def test_cqt04_has_min_questions_true_when_enough(self):
        """CQT-04: ≥ 3 questions → has_min_questions=True, no min-question flag."""
        quiz = _make_quiz()
        # 3 questions, each with 1 correct FIXED option + 3 wrong
        questions = [_make_question(i, explanation="ok") for i in range(1, 4)]
        options = []
        for q in questions:
            for j, correct in enumerate([True, False, False, False]):
                options.append(_make_option(q.id * 10 + j, q.id, is_correct=correct))
        db = _db_for_quiz(quiz, questions, options, meta_list=[])
        result = get_quiz_quality_summary(db, quiz_id=1)
        assert result.has_min_questions is True
        assert not any("Too few" in f for f in result.flags)


# ── TestExplanationAndScore ────────────────────────────────────────────────────

class TestExplanationAndScore:

    def _make_complete_quiz(self, n_questions=3, with_explanation=True):
        quiz = _make_quiz()
        explanation = "Explanation text" if with_explanation else None
        questions = [_make_question(i, explanation=explanation) for i in range(1, n_questions + 1)]
        options = []
        for q in questions:
            for j, correct in enumerate([True, False, False, False]):
                options.append(_make_option(q.id * 10 + j, q.id, is_correct=correct))
        meta = [_make_meta(q.id, difficulty=0.2 + i * 0.2) for i, q in enumerate(questions)]
        return quiz, questions, options, meta

    def test_cqt05_flags_low_explanation_ratio(self):
        """CQT-05: 0% explanation coverage → explanation flag raised."""
        quiz, questions, options, meta = self._make_complete_quiz(with_explanation=False)
        db = _db_for_quiz(quiz, questions, options, meta)
        result = get_quiz_quality_summary(db, quiz_id=1)
        assert any("explanation" in f.lower() for f in result.flags)

    def test_cqt06_explanation_ratio_correct(self):
        """CQT-06: All questions with explanations → ratio=1.0."""
        quiz, questions, options, meta = self._make_complete_quiz(with_explanation=True)
        db = _db_for_quiz(quiz, questions, options, meta)
        result = get_quiz_quality_summary(db, quiz_id=1)
        assert result.explanation_ratio == pytest.approx(1.0, abs=1e-4)

    def test_cqt07_quality_score_in_range(self):
        """CQT-07: quality_score is always in [0.0, 1.0]."""
        quiz, questions, options, meta = self._make_complete_quiz()
        db = _db_for_quiz(quiz, questions, options, meta)
        result = get_quiz_quality_summary(db, quiz_id=1)
        assert 0.0 <= result.quality_score <= 1.0


# ── TestReadyToPublish ─────────────────────────────────────────────────────────

class TestReadyToPublish:

    def test_cqt08_not_ready_when_flags_present(self):
        """CQT-08: Quiz with quiz-level flags → ready_to_publish=False."""
        quiz = _make_quiz()
        db = _db_for_quiz(quiz, questions=[], options=[], meta_list=[])
        result = get_quiz_quality_summary(db, quiz_id=1)
        # 0 questions → has the "too few" flag
        assert result.ready_to_publish is False

    def test_cqt09_ready_when_no_flags(self):
        """CQT-09: QuizQualitySummary with no flags and no question_flags → ready."""
        s = QuizQualitySummary(
            quiz_id=1, quiz_title="T", content_status="DRAFT",
            total_questions=5, flagged_questions=0,
            has_min_questions=True, explanation_ratio=1.0,
            quality_score=1.0, flags=[], question_flags=[],
        )
        assert s.ready_to_publish is True


# ── TestPerQuestionFlags ───────────────────────────────────────────────────────

class TestPerQuestionFlags:

    def test_cqt10_flags_fixed_missing_correct_option(self):
        """CQT-10: FIXED question with 0 is_correct options → per-question flag."""
        quiz = _make_quiz()
        q = _make_question(1, explanation="ok")
        # 4 options but none is correct
        options = [_make_option(10 + j, 1, is_correct=False) for j in range(4)]
        db = _db_for_quiz(quiz, [q], options, meta_list=[])
        result = get_quiz_quality_summary(db, quiz_id=1)
        assert result.flagged_questions >= 1
        flags_text = " ".join(result.question_flags[0].flags)
        assert "correct" in flags_text.lower()

    def test_cqt11_flags_pool_insufficient_distractors(self):
        """CQT-11: Pool question with < 6 distractors → per-question distractor flag."""
        quiz = _make_quiz()
        q = _make_question(1, explanation="ok")
        # 2 variants + 3 distractors (below floor of 6)
        options = (
            [_make_option(10 + j, 1, is_correct=True,
                          option_type=OptionType.CORRECT_VARIANT) for j in range(2)]
            + [_make_option(20 + j, 1, is_correct=False,
                            option_type=OptionType.DISTRACTOR) for j in range(3)]
        )
        db = _db_for_quiz(quiz, [q], options, meta_list=[])
        result = get_quiz_quality_summary(db, quiz_id=1)
        assert result.flagged_questions >= 1
        flags_text = " ".join(result.question_flags[0].flags)
        assert "distractor" in flags_text.lower()


# ── TestGlobalReport ──────────────────────────────────────────────────────────

class TestGlobalReport:

    def test_cqt12_excludes_archived_quizzes(self):
        """CQT-12: get_global_quality_report does not include ARCHIVED quizzes."""
        db = MagicMock()
        # query(Quiz).filter().order_by().all() → empty list (archived filtered out)
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.all.return_value = []  # no non-archived quizzes
        db.query.return_value = q

        result = get_global_quality_report(db)
        assert result == []
        # Verify .filter() was called (the ARCHIVED exclusion)
        assert q.filter.called


# ── _difficulty_spread helper ─────────────────────────────────────────────────

def test_difficulty_spread_two_values():
    assert _difficulty_spread([0.2, 0.8]) == pytest.approx(0.6, abs=1e-4)

def test_difficulty_spread_one_value_is_zero():
    assert _difficulty_spread([0.5]) == 0.0

def test_difficulty_spread_empty_is_zero():
    assert _difficulty_spread([]) == 0.0
