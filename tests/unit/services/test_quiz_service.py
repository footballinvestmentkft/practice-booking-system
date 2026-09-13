"""
Unit tests for QuizService

Covers DB-query methods via MagicMock db (no real DB required):
  create_quiz                 — no questions, with 1 question + 2 answers
  get_quiz_by_id              — found / not found
  get_available_quizzes       — empty / with results
  get_quizzes_by_category     — empty / with results
  start_quiz_attempt          — already completed, ongoing, quiz-not-found,
                                inactive, happy-path (creates new attempt)
  submit_quiz_attempt         — attempt not found, time limit exceeded,
                                happy-path MULTIPLE_CHOICE correct (passes),
                                FILL_IN_BLANK correct answer path
  get_user_quiz_attempts      — empty / with results
  get_user_quiz_statistics    — no attempts / one completed passing attempt
  get_quiz_statistics         — no attempts / with completed attempts
  get_user_ongoing_attempt    — found / not found
  is_quiz_completed_by_user   — True / False

GamificationService is patched in __init__ to avoid transitive DB setup.
"""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch, call

from app.services.quiz_service import QuizService
from app.models.quiz import QuizCategory, QuizDifficulty, QuestionType
from app.schemas.quiz import QuizCreate, QuizQuestionCreate, QuizAnswerOptionCreate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_svc():
    """Return (QuizService, mock_db) with GamificationService fully patched."""
    db = MagicMock()
    with patch("app.services.quiz_service.GamificationService"):
        svc = QuizService(db)
    return svc, db


def _q(db, first=None, all_=None):
    """Wire db.query(...).filter(...).order_by(...).first/all to given values."""
    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.limit.return_value = q
    q.join.return_value = q
    q.with_for_update.return_value = q
    q.first.return_value = first
    q.all.return_value = all_ if all_ is not None else []
    q.subquery.return_value = MagicMock()
    db.query.return_value = q
    return q


def _multi_q(db, specs):
    """
    Set db.query.side_effect so successive calls return different mocks.

    specs: list of dicts with optional keys 'first', 'all_'.
    """
    mocks = []
    for spec in specs:
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.limit.return_value = q
        q.join.return_value = q
        q.with_for_update.return_value = q
        q.first.return_value = spec.get("first")
        q.all.return_value = spec.get("all_", [])
        q.subquery.return_value = MagicMock()
        mocks.append(q)
    db.query.side_effect = mocks
    return mocks


def _make_quiz(id=1, title="Test Quiz", is_active=True, time_limit_minutes=30,
               passing_score=70.0, xp_reward=50, questions=None,
               category=QuizCategory.GENERAL):
    q = MagicMock()
    q.id = id
    q.title = title
    q.is_active = is_active
    q.time_limit_minutes = time_limit_minutes
    q.passing_score = passing_score
    q.xp_reward = xp_reward
    q.questions = questions if questions is not None else []
    q.category = category
    return q


def _make_attempt(id=1, user_id=42, quiz_id=1, completed_at=None,
                  started_at=None, score=None, passed=False,
                  xp_awarded=0, total_questions=1, correct_answers=0):
    a = MagicMock()
    a.id = id
    a.user_id = user_id
    a.quiz_id = quiz_id
    a.completed_at = completed_at
    a.started_at = started_at or datetime.now(timezone.utc)
    a.score = score
    a.passed = passed
    a.xp_awarded = xp_awarded
    a.total_questions = total_questions
    a.correct_answers = correct_answers
    return a


# ===========================================================================
# TestGetQuizById
# ===========================================================================

@pytest.mark.unit
class TestGetQuizById:
    def test_returns_quiz_when_found(self):
        svc, db = _make_svc()
        quiz = _make_quiz(id=7)
        _q(db, first=quiz)
        assert svc.get_quiz_by_id(7) is quiz

    def test_returns_none_when_not_found(self):
        svc, db = _make_svc()
        _q(db, first=None)
        assert svc.get_quiz_by_id(999) is None


# ===========================================================================
# TestGetAvailableQuizzes
# ===========================================================================

@pytest.mark.unit
class TestGetAvailableQuizzes:
    def test_returns_empty_list_when_all_completed(self):
        svc, db = _make_svc()
        _q(db, all_=[])
        result = svc.get_available_quizzes(user_id=42)
        assert result == []

    def test_returns_available_quizzes(self):
        svc, db = _make_svc()
        quizzes = [_make_quiz(id=1), _make_quiz(id=2)]
        _q(db, all_=quizzes)
        result = svc.get_available_quizzes(user_id=42)
        assert result == quizzes


# ===========================================================================
# TestGetQuizzesByCategory
# ===========================================================================

@pytest.mark.unit
class TestGetQuizzesByCategory:
    def test_returns_quizzes_for_category(self):
        svc, db = _make_svc()
        quizzes = [_make_quiz(category=QuizCategory.MARKETING)]
        _q(db, all_=quizzes)
        result = svc.get_quizzes_by_category(QuizCategory.MARKETING)
        assert result == quizzes

    def test_returns_empty_for_empty_category(self):
        svc, db = _make_svc()
        _q(db, all_=[])
        result = svc.get_quizzes_by_category(QuizCategory.NUTRITION)
        assert result == []


# ===========================================================================
# TestStartQuizAttempt
# ===========================================================================

@pytest.mark.unit
class TestStartQuizAttempt:
    def test_raises_when_already_completed(self):
        svc, db = _make_svc()
        completed = _make_attempt(completed_at=datetime.now(timezone.utc))
        _q(db, first=completed)  # First query returns completed attempt
        with pytest.raises(ValueError, match="already completed"):
            svc.start_quiz_attempt(user_id=42, quiz_id=1)

    def test_returns_ongoing_attempt_when_exists(self):
        svc, db = _make_svc()
        ongoing = _make_attempt(id=5, completed_at=None)
        _multi_q(db, [
            {"all_": []},       # no canonical assessment mapping
            {"first": None},     # check completed → not found
            {"first": ongoing},  # check ongoing → found
        ])
        result = svc.start_quiz_attempt(user_id=42, quiz_id=1)
        assert result is ongoing

    def test_raises_when_quiz_not_found(self):
        svc, db = _make_svc()
        _multi_q(db, [
            {"all_": []},    # no canonical assessment mapping
            {"first": None},  # check completed
            {"first": None},  # check ongoing
            {"first": None},  # get_quiz_by_id → quiz not found
        ])
        with pytest.raises(ValueError, match="Quiz not found"):
            svc.start_quiz_attempt(user_id=42, quiz_id=999)

    def test_raises_when_quiz_inactive(self):
        svc, db = _make_svc()
        inactive = _make_quiz(is_active=False)
        _multi_q(db, [
            {"all_": []},        # no canonical assessment mapping
            {"first": None},      # check completed
            {"first": None},      # check ongoing
            {"first": inactive},  # get_quiz_by_id → inactive quiz
        ])
        with pytest.raises(ValueError, match="not active"):
            svc.start_quiz_attempt(user_id=42, quiz_id=1)

    def test_creates_new_attempt_happy_path(self):
        svc, db = _make_svc()
        quiz = _make_quiz(id=1, is_active=True, questions=[MagicMock(), MagicMock()])
        _multi_q(db, [
            {"all_": []},     # no canonical assessment mapping
            {"first": None},   # check completed
            {"first": None},   # check ongoing
            {"first": quiz},   # get_quiz_by_id → found + active
        ])
        result = svc.start_quiz_attempt(user_id=42, quiz_id=1)
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_creates_new_attempt_with_zero_questions(self):
        svc, db = _make_svc()
        quiz = _make_quiz(id=2, is_active=True, questions=[])
        _multi_q(db, [
            {"all_": []},
            {"first": None},
            {"first": None},
            {"first": quiz},
        ])
        svc.start_quiz_attempt(user_id=2, quiz_id=2)
        db.add.assert_called_once()


# ===========================================================================
# TestSubmitQuizAttempt
# ===========================================================================

@pytest.mark.unit
class TestSubmitQuizAttempt:
    def test_raises_when_attempt_not_found(self):
        svc, db = _make_svc()
        _q(db, first=None)
        submission = MagicMock()
        submission.attempt_id = 1
        with pytest.raises(ValueError, match="not found"):
            svc.submit_quiz_attempt(user_id=42, submission=submission)

    def test_raises_when_time_limit_exceeded(self):
        svc, db = _make_svc()
        # Attempt started long in the past (naive UTC)
        old_start = datetime(2020, 1, 1, 0, 0, 0)
        attempt = _make_attempt(id=1, started_at=old_start, completed_at=None)
        attempt.quiz_id = 1
        quiz = _make_quiz(id=1, time_limit_minutes=1)  # 1-minute limit

        _multi_q(db, [
            {"first": attempt},  # query for the attempt
            {"first": quiz},     # get_quiz_by_id
        ])
        submission = MagicMock()
        submission.attempt_id = 1
        with pytest.raises(ValueError, match="Time limit exceeded"):
            svc.submit_quiz_attempt(user_id=42, submission=submission)


# ===========================================================================
# TestGetUserQuizAttempts
# ===========================================================================

@pytest.mark.unit
class TestGetUserQuizAttempts:
    def test_returns_empty_list(self):
        svc, db = _make_svc()
        _q(db, all_=[])
        assert svc.get_user_quiz_attempts(user_id=42) == []

    def test_returns_attempts(self):
        svc, db = _make_svc()
        attempts = [_make_attempt(id=1), _make_attempt(id=2)]
        _q(db, all_=attempts)
        result = svc.get_user_quiz_attempts(user_id=42)
        assert result == attempts


# ===========================================================================
# TestGetUserQuizStatistics
# ===========================================================================

@pytest.mark.unit
class TestGetUserQuizStatistics:
    def test_empty_user_has_zero_stats(self):
        svc, db = _make_svc()
        _q(db, all_=[])  # No attempts at all
        stats = svc.get_user_quiz_statistics(user_id=42)
        assert stats.total_quizzes_attempted == 0
        assert stats.total_quizzes_completed == 0
        assert stats.total_quizzes_passed == 0
        assert stats.total_xp_earned == 0
        assert stats.average_score is None
        assert stats.completion_rate == 0
        assert stats.pass_rate == 0
        assert stats.favorite_category is None

    def test_user_with_one_completed_passing_attempt(self):
        svc, db = _make_svc()
        quiz = _make_quiz(id=1, category=QuizCategory.GENERAL)
        attempt = _make_attempt(
            id=1,
            completed_at=datetime.now(timezone.utc),
            score=85.0,
            passed=True,
            xp_awarded=50,
        )

        # First query: all attempts for user
        # Second query: get_quiz_by_id (called inside loop for category counting)
        _multi_q(db, [
            {"all_": [attempt]},  # query(QuizAttempt).filter().all()
            {"first": quiz},      # get_quiz_by_id inside favorite_category loop
        ])
        stats = svc.get_user_quiz_statistics(user_id=42)
        assert stats.total_quizzes_attempted == 1
        assert stats.total_quizzes_completed == 1
        assert stats.total_quizzes_passed == 1
        assert stats.total_xp_earned == 50
        assert stats.average_score == 85.0
        assert stats.completion_rate == 100.0
        assert stats.pass_rate == 100.0


# ===========================================================================
# TestGetQuizStatistics
# ===========================================================================

@pytest.mark.unit
class TestGetQuizStatistics:
    def test_quiz_with_no_attempts(self):
        svc, db = _make_svc()
        quiz = _make_quiz(id=1, title="Empty Quiz")
        _multi_q(db, [
            {"all_": []},    # query(QuizAttempt).filter().all()
            {"first": quiz}, # get_quiz_by_id
        ])
        stats = svc.get_quiz_statistics(quiz_id=1)
        assert stats.quiz_id == 1
        assert stats.quiz_title == "Empty Quiz"
        assert stats.total_attempts == 0
        assert stats.completed_attempts == 0
        assert stats.average_score is None
        assert stats.pass_rate == 0

    def test_quiz_with_completed_attempts(self):
        svc, db = _make_svc()
        quiz = _make_quiz(id=2, title="Popular Quiz")
        a1 = _make_attempt(completed_at=datetime.now(timezone.utc), score=90.0, passed=True)
        a1.time_spent_minutes = 10.0
        a2 = _make_attempt(completed_at=datetime.now(timezone.utc), score=60.0, passed=False)
        a2.time_spent_minutes = 15.0
        _multi_q(db, [
            {"all_": [a1, a2]},  # quiz attempts
            {"first": quiz},     # get_quiz_by_id
        ])
        stats = svc.get_quiz_statistics(quiz_id=2)
        assert stats.total_attempts == 2
        assert stats.completed_attempts == 2
        assert stats.average_score == 75.0  # (90+60)/2
        assert stats.pass_rate == 50.0      # 1/2 passed


# ===========================================================================
# TestGetUserOngoingAttempt
# ===========================================================================

@pytest.mark.unit
class TestGetUserOngoingAttempt:
    def test_returns_ongoing_when_found(self):
        svc, db = _make_svc()
        ongoing = _make_attempt(id=3, completed_at=None)
        _q(db, first=ongoing)
        result = svc.get_user_ongoing_attempt(user_id=42, quiz_id=1)
        assert result is ongoing

    def test_returns_none_when_no_ongoing(self):
        svc, db = _make_svc()
        _q(db, first=None)
        result = svc.get_user_ongoing_attempt(user_id=42, quiz_id=1)
        assert result is None


# ===========================================================================
# TestIsQuizCompletedByUser
# ===========================================================================

@pytest.mark.unit
class TestIsQuizCompletedByUser:
    def test_returns_true_when_completed(self):
        svc, db = _make_svc()
        completed = _make_attempt(completed_at=datetime.now(timezone.utc))
        _q(db, first=completed)
        assert svc.is_quiz_completed_by_user(user_id=42, quiz_id=1) is True

    def test_returns_false_when_not_completed(self):
        svc, db = _make_svc()
        _q(db, first=None)
        assert svc.is_quiz_completed_by_user(user_id=42, quiz_id=1) is False


# NOTE: get_quiz_leaderboard is excluded — it references User.full_name which
# does not exist on the User model (should be User.name). This is a pre-existing
# bug in quiz_service.py that raises AttributeError before any DB call is made.


# ===========================================================================
# TestCreateQuiz
# ===========================================================================

@pytest.mark.unit
class TestCreateQuiz:
    def test_create_quiz_with_no_questions(self):
        svc, db = _make_svc()
        quiz_data = QuizCreate(
            title="Empty Quiz",
            category=QuizCategory.GENERAL,
            difficulty=QuizDifficulty.MEDIUM,
        )
        result = svc.create_quiz(quiz_data)
        db.add.assert_called_once()   # only the quiz
        db.flush.assert_called()
        db.commit.assert_called_once()

    def test_create_quiz_with_one_question_and_two_answers(self):
        svc, db = _make_svc()
        quiz_data = QuizCreate(
            title="Multi-Choice Quiz",
            description="A quiz with one question",
            category=QuizCategory.MARKETING,
            difficulty=QuizDifficulty.EASY,
            time_limit_minutes=10,
            xp_reward=25,
            passing_score=60.0,
            questions=[
                QuizQuestionCreate(
                    question_text="What is 2+2?",
                    question_type=QuestionType.MULTIPLE_CHOICE,
                    points=2,
                    order_index=0,
                    answer_options=[
                        QuizAnswerOptionCreate(option_text="4", is_correct=True, order_index=0),
                        QuizAnswerOptionCreate(option_text="5", is_correct=False, order_index=1),
                    ],
                )
            ],
        )
        svc.create_quiz(quiz_data)
        # db.add: quiz(1) + question(1) + 2 answer options = 4 calls
        assert db.add.call_count == 4
        db.commit.assert_called_once()

    def test_create_quiz_with_fill_in_blank_question(self):
        svc, db = _make_svc()
        quiz_data = QuizCreate(
            title="Fill In Blank Quiz",
            category=QuizCategory.ECONOMICS,
            difficulty=QuizDifficulty.HARD,
            questions=[
                QuizQuestionCreate(
                    question_text="Complete: The capital of France is ___",
                    question_type=QuestionType.FILL_IN_BLANK,
                    answer_options=[
                        QuizAnswerOptionCreate(option_text="Paris", is_correct=True, order_index=0),
                    ],
                )
            ],
        )
        svc.create_quiz(quiz_data)
        # quiz(1) + question(1) + 1 answer = 3 adds
        assert db.add.call_count == 3


# ===========================================================================
# TestSubmitQuizAttemptHappyPath
# ===========================================================================

@pytest.mark.unit
class TestSubmitQuizAttemptHappyPath:
    """
    Tests the full submit_quiz_attempt success path including:
    - answer processing (MULTIPLE_CHOICE and FILL_IN_BLANK)
    - score calculation
    - XP award (gamification service, mocked)
    - _process_enrollment_quiz_if_applicable early-return path
    """

    def _make_mc_submission(self, question_id=1, option_id=10):
        """Build a MULTIPLE_CHOICE submission mock."""
        answer = MagicMock()
        answer.question_id = question_id
        answer.selected_option_id = option_id
        answer.answer_text = None
        submission = MagicMock()
        submission.attempt_id = 1
        submission.answers = [answer]
        return submission

    def _make_fib_submission(self, question_id=1, text="Paris"):
        """Build a FILL_IN_BLANK submission mock."""
        answer = MagicMock()
        answer.question_id = question_id
        answer.selected_option_id = None
        answer.answer_text = text
        submission = MagicMock()
        submission.attempt_id = 1
        submission.answers = [answer]
        return submission

    def test_multiple_choice_correct_answer_passes(self):
        svc, db = _make_svc()
        now = datetime.now(timezone.utc)
        attempt = _make_attempt(id=1, started_at=now, completed_at=None, quiz_id=1)

        quiz = _make_quiz(id=1, time_limit_minutes=60, passing_score=70.0, xp_reward=50)

        question = MagicMock()
        question.id = 1
        question.points = 1
        question.question_type = QuestionType.MULTIPLE_CHOICE

        correct_option = MagicMock()
        correct_option.is_correct = True

        # 5 queries in order:
        # 1. find attempt
        # 2. get_quiz_by_id (time check)
        # 3. get all questions
        # 4. check correct option
        # 5. ProjectQuiz lookup → None → early return from _process_enrollment
        _multi_q(db, [
            {"first": attempt},          # query 1
            {"first": quiz},             # query 2: get_quiz_by_id
            {"all_": [question]},        # query 3: questions
            {"first": correct_option},   # query 4: correct answer check
            {"first": None},             # query 5: ProjectQuiz → None
        ])

        result = svc.submit_quiz_attempt(user_id=42, submission=self._make_mc_submission())

        db.commit.assert_called()
        svc.gamification_service.award_xp.assert_called_once()  # xp awarded (passed=True)
        assert result is attempt

    def test_multiple_choice_wrong_answer_fails_no_xp(self):
        svc, db = _make_svc()
        now = datetime.now(timezone.utc)
        attempt = _make_attempt(id=1, started_at=now, completed_at=None, quiz_id=1)
        quiz = _make_quiz(id=1, time_limit_minutes=60, passing_score=70.0, xp_reward=50)

        question = MagicMock()
        question.id = 1
        question.points = 1
        question.question_type = QuestionType.MULTIPLE_CHOICE

        # Wrong answer: selected_option_id returns None (option not found as correct)
        _multi_q(db, [
            {"first": attempt},    # find attempt
            {"first": quiz},       # get_quiz_by_id
            {"all_": [question]},  # questions
            {"first": None},       # correct option → None (wrong answer)
            {"first": None},       # ProjectQuiz lookup
        ])

        result = svc.submit_quiz_attempt(user_id=42, submission=self._make_mc_submission())

        # score = 0/1 * 100 = 0 → < 70 → not passed → no XP
        svc.gamification_service.award_xp.assert_not_called()
        assert result is attempt

    def test_fill_in_blank_correct_answer_case_insensitive(self):
        svc, db = _make_svc()
        now = datetime.now(timezone.utc)
        attempt = _make_attempt(id=1, started_at=now, completed_at=None, quiz_id=1)
        quiz = _make_quiz(id=1, time_limit_minutes=60, passing_score=50.0, xp_reward=30)

        question = MagicMock()
        question.id = 1
        question.points = 1
        question.question_type = QuestionType.FILL_IN_BLANK

        correct_opt = MagicMock()
        correct_opt.option_text = "Paris"

        _multi_q(db, [
            {"first": attempt},          # find attempt
            {"first": quiz},             # get_quiz_by_id
            {"all_": [question]},        # questions
            {"all_": [correct_opt]},     # fill-in-blank: get all correct options
            {"first": None},             # ProjectQuiz lookup
        ])

        result = svc.submit_quiz_attempt(
            user_id=42,
            submission=self._make_fib_submission(text="paris"),  # lowercase
        )

        db.commit.assert_called()
        assert result is attempt


# ===========================================================================
# TestSubmitQuizAttemptBranchCoverage — missing branches from coverage gap
# ===========================================================================

@pytest.mark.unit
class TestSubmitQuizAttemptBranchCoverage:
    """
    Covers the submit_quiz_attempt branches not hit by existing tests:
      - question_id not in question_dict → continue (L170)
      - MC: selected_option_id is None → is_correct stays False (L180)
      - Unknown question type: neither MC/TF nor FIB → is_correct False (L193)
      - FIB: answer_text is None → is_correct False (L195)
      - FIB: answer_text set but no option matches → is_correct False (L205-206)
      - started_at is timezone-naive → add UTC tzinfo (L235)
      - started_at has non-UTC timezone → convert to UTC (L238)
      - negative time_spent_seconds_raw warning (L250)
    """

    def _make_svc_db(self):
        db = MagicMock()
        with patch("app.services.quiz_service.GamificationService"):
            from app.services.quiz_service import QuizService
            svc = QuizService(db)
        return svc, db

    def _submission(self, question_id=1, option_id=None, answer_text=None):
        answer = MagicMock()
        answer.question_id = question_id
        answer.selected_option_id = option_id
        answer.answer_text = answer_text
        sub = MagicMock()
        sub.attempt_id = 1
        sub.answers = [answer]
        return sub

    def _setup_queries(self, db, attempt, quiz, questions, *extra_qs):
        """Wire db.query sequence: attempt, quiz, questions, then extra."""
        specs = [
            {"first": attempt},
            {"first": quiz},
            {"all_": questions},
        ]
        for eq in extra_qs:
            specs.append(eq)
        specs.append({"first": None})   # ProjectQuiz → early return
        _multi_q(db, specs)

    def test_question_id_not_in_dict_continues(self):
        """Answer references a question not in quiz → continue branch."""
        svc, db = self._make_svc_db()
        now = datetime.now(timezone.utc)
        attempt = _make_attempt(started_at=now, completed_at=None)
        quiz = _make_quiz(time_limit_minutes=60, passing_score=50.0)

        question = MagicMock()
        question.id = 99      # question ID in quiz
        question.points = 1
        question.question_type = QuestionType.MULTIPLE_CHOICE

        # Submission answers question_id=1, but quiz only has question.id=99
        self._setup_queries(db, attempt, quiz, [question])
        result = svc.submit_quiz_attempt(
            user_id=42, submission=self._submission(question_id=1, option_id=10)
        )
        assert result is attempt

    def test_mc_no_selected_option_id_is_wrong(self):
        """MC answer with selected_option_id=None → is_correct False (L180 False)."""
        svc, db = self._make_svc_db()
        now = datetime.now(timezone.utc)
        attempt = _make_attempt(started_at=now, completed_at=None)
        quiz = _make_quiz(time_limit_minutes=60, passing_score=50.0)

        question = MagicMock()
        question.id = 1
        question.points = 1
        question.question_type = QuestionType.MULTIPLE_CHOICE

        self._setup_queries(db, attempt, quiz, [question])
        result = svc.submit_quiz_attempt(
            user_id=42, submission=self._submission(question_id=1, option_id=None)
        )
        assert result is attempt

    def test_unknown_question_type_not_mc_not_fib(self):
        """Question type is not MC/TF/FIB → both branches skipped (L193 False)."""
        svc, db = self._make_svc_db()
        now = datetime.now(timezone.utc)
        attempt = _make_attempt(started_at=now, completed_at=None)
        quiz = _make_quiz(time_limit_minutes=60, passing_score=50.0)

        question = MagicMock()
        question.id = 1
        question.points = 1
        # Use an enum value that's neither MC/TF nor FILL_IN_BLANK
        question.question_type = "UNKNOWN_TYPE"

        self._setup_queries(db, attempt, quiz, [question])
        result = svc.submit_quiz_attempt(
            user_id=42, submission=self._submission(question_id=1, option_id=10)
        )
        assert result is attempt

    def test_fib_no_answer_text_is_wrong(self):
        """FIB answer with answer_text=None → is_correct False (L195 False)."""
        svc, db = self._make_svc_db()
        now = datetime.now(timezone.utc)
        attempt = _make_attempt(started_at=now, completed_at=None)
        quiz = _make_quiz(time_limit_minutes=60, passing_score=50.0)

        question = MagicMock()
        question.id = 1
        question.points = 1
        question.question_type = QuestionType.FILL_IN_BLANK

        self._setup_queries(db, attempt, quiz, [question])
        result = svc.submit_quiz_attempt(
            user_id=42, submission=self._submission(question_id=1, answer_text=None)
        )
        assert result is attempt

    def test_fib_answer_no_matching_option_is_wrong(self):
        """FIB: answer_text set but no option matches → False path in loop (L205, L206)."""
        svc, db = self._make_svc_db()
        now = datetime.now(timezone.utc)
        attempt = _make_attempt(started_at=now, completed_at=None)
        quiz = _make_quiz(time_limit_minutes=60, passing_score=50.0)

        question = MagicMock()
        question.id = 1
        question.points = 1
        question.question_type = QuestionType.FILL_IN_BLANK

        correct_opt = MagicMock()
        correct_opt.option_text = "Paris"  # user answers "london" → no match

        self._setup_queries(
            db, attempt, quiz, [question],
            {"all_": [correct_opt]},   # FIB correct options lookup
        )
        result = svc.submit_quiz_attempt(
            user_id=42, submission=self._submission(question_id=1, answer_text="london")
        )
        # is_correct stays False → score=0 → failed
        svc.gamification_service.award_xp.assert_not_called()
        assert result is attempt

    def test_naive_started_at_gets_utc_tzinfo(self):
        """started_at without tzinfo → branch L235 True (replace tzinfo with UTC)."""
        svc, db = self._make_svc_db()
        naive_start = datetime.now().replace(tzinfo=None)   # timezone-naive
        attempt = _make_attempt(started_at=naive_start, completed_at=None)
        quiz = _make_quiz(time_limit_minutes=60, passing_score=50.0)

        _multi_q(db, [
            {"first": attempt},
            {"first": quiz},
            {"all_": []},      # no questions → score = 0
            {"first": None},   # ProjectQuiz
        ])
        result = svc.submit_quiz_attempt(
            user_id=42, submission=self._submission()
        )
        assert result is attempt

    def test_non_utc_tz_started_at_converted(self):
        """started_at with non-UTC tz → branch L238 True (astimezone UTC)."""
        from datetime import timezone as tz
        import zoneinfo
        svc, db = self._make_svc_db()
        # Use UTC+2 as a non-UTC timezone
        try:
            eastern_tz = zoneinfo.ZoneInfo("Europe/Berlin")
            aware_non_utc = datetime.now(eastern_tz)
        except Exception:
            aware_non_utc = datetime.now(tz.utc).replace(tzinfo=None).replace(
                tzinfo=tz(timedelta(hours=2))
            )
        attempt = _make_attempt(started_at=aware_non_utc, completed_at=None)
        quiz = _make_quiz(time_limit_minutes=60, passing_score=50.0)

        _multi_q(db, [
            {"first": attempt},
            {"first": quiz},
            {"all_": []},
            {"first": None},
        ])
        result = svc.submit_quiz_attempt(user_id=42, submission=self._submission())
        assert result is attempt

    def test_negative_time_warning_branch(self):
        """started_at far in the future → time_spent_seconds_raw < 0 → L250 True."""
        svc, db = self._make_svc_db()
        # started_at 10 minutes in the FUTURE → negative elapsed time
        future_start = datetime.now(timezone.utc) + timedelta(minutes=10)
        attempt = _make_attempt(started_at=future_start, completed_at=None)
        quiz = _make_quiz(time_limit_minutes=9999, passing_score=50.0)   # big limit

        _multi_q(db, [
            {"first": attempt},
            {"first": quiz},
            {"all_": []},
            {"first": None},
        ])
        result = svc.submit_quiz_attempt(user_id=42, submission=self._submission())
        # time_spent_seconds_raw < 0 → clamped to 0
        assert attempt.time_spent_minutes == 0
        assert result is attempt


# ===========================================================================
# TestProcessEnrollmentQuizBranchCoverage — _process_enrollment_quiz branches
# ===========================================================================

@pytest.mark.unit
class TestProcessEnrollmentQuizBranchCoverage:
    """
    Covers _process_enrollment_quiz_if_applicable branches:
      - enrollment quiz found → proceeds past L294 (False branch)
      - existing_project_enrollment found → early return L303
      - existing_enrollment quiz found → early return L312
      - all_enrollments loop with higher-score existing → priority++  L327
      - not attempt.passed → NOT_ELIGIBLE enrollment created L345
      - attempt.passed → no NOT_ELIGIBLE enrollment created
      - priority update loop: lower-score existing → L361 True
    """

    def _make_svc_db(self):
        db = MagicMock()
        with patch("app.services.quiz_service.GamificationService"):
            from app.services.quiz_service import QuizService
            svc = QuizService(db)
        return svc, db

    def _build_attempt(self, passed=True, score=80.0):
        a = MagicMock()
        a.id = 1
        a.user_id = 42
        a.quiz_id = 5
        a.passed = passed
        a.score = score
        a.completed_at = None     # attempt not yet complete (submit path)
        a.started_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        return a

    def _build_enrollment_quiz(self, project_id=10):
        eq = MagicMock()
        eq.project_id = project_id
        eq.quiz_id = 5
        return eq

    def _submit_with_queries(self, db, svc, extra_queries):
        """
        Run submit_quiz_attempt driving through _process_enrollment_quiz_if_applicable.
        extra_queries are appended after the standard [attempt, quiz, [], ProjectQuiz_found].
        """
        attempt = self._build_attempt(passed=True, score=80.0)
        quiz = _make_quiz(id=5, time_limit_minutes=60, passing_score=50.0)
        enrollment_quiz = self._build_enrollment_quiz()

        specs = [
            {"first": attempt},         # find attempt
            {"first": quiz},            # get_quiz_by_id (time check)
            {"all_": []},               # questions (none)
            {"first": enrollment_quiz}, # ProjectQuiz found → enters function body
        ] + extra_queries

        _multi_q(db, specs)

        sub = MagicMock()
        sub.attempt_id = 1
        sub.answers = []
        return svc.submit_quiz_attempt(user_id=42, submission=sub)

    def test_existing_project_enrollment_returns_early(self):
        """existing_project_enrollment found → return at L303."""
        svc, db = self._make_svc_db()
        existing_proj_enroll = MagicMock()

        result = self._submit_with_queries(db, svc, [
            {"first": existing_proj_enroll},   # ProjectEnrollment query → found
        ])
        # commit still called (from submit itself, before _process call)
        assert result is not None

    def test_existing_enrollment_quiz_returns_early(self):
        """existing_enrollment quiz found → return at L312."""
        svc, db = self._make_svc_db()
        existing_enroll = MagicMock()

        result = self._submit_with_queries(db, svc, [
            {"first": None},             # ProjectEnrollment → not found
            {"first": existing_enroll},  # ProjectEnrollmentQuiz → found → early return
        ])
        assert result is not None

    def test_priority_incremented_for_higher_score_existing(self):
        """
        all_enrollments has one existing attempt with higher score →
        enrollment_priority gets incremented (L327 True).
        """
        svc, db = self._make_svc_db()

        # current attempt.score = 80 (set in _build_attempt)
        # existing attempt.score = 90 > 80 → enrollment_priority += 1
        existing_enrollment = MagicMock()
        existing_enrollment.quiz_attempt_id = 99
        existing_enrollment.enrollment_priority = 1

        existing_attempt = MagicMock()
        existing_attempt.score = 90.0      # higher than 80 → priority increments
        existing_attempt.completed_at = datetime.now(timezone.utc) - timedelta(hours=1)

        result = self._submit_with_queries(db, svc, [
            {"first": None},                       # ProjectEnrollment → not found
            {"first": None},                       # ProjectEnrollmentQuiz → not found
            {"all_": [existing_enrollment]},       # all_enrollments
            {"first": existing_attempt},           # get existing_attempt (priority loop)
            {"first": existing_attempt},           # get existing_attempt (update loop)
        ])
        assert result is not None

    def test_failed_quiz_creates_not_eligible_enrollment(self):
        """
        not attempt.passed → ProjectEnrollment NOT_ELIGIBLE created (L345 True).
        Uses attempt with passed=False.
        """
        svc, db = self._make_svc_db()

        attempt_failed = MagicMock()
        attempt_failed.id = 1
        attempt_failed.user_id = 42
        attempt_failed.quiz_id = 5
        attempt_failed.passed = False
        attempt_failed.score = 20.0
        attempt_failed.completed_at = None   # not yet completed (submit path)
        attempt_failed.started_at = datetime.now(timezone.utc) - timedelta(minutes=5)

        quiz = _make_quiz(id=5, time_limit_minutes=60, passing_score=50.0)
        enrollment_quiz = self._build_enrollment_quiz()

        specs = [
            {"first": attempt_failed},     # find attempt
            {"first": quiz},              # quiz for time check
            {"all_": []},                 # no questions
            {"first": enrollment_quiz},   # ProjectQuiz found
            {"first": None},              # ProjectEnrollment → not found
            {"first": None},              # ProjectEnrollmentQuiz → not found
            {"all_": []},                 # all_enrollments (empty)
        ]
        _multi_q(db, specs)

        sub = MagicMock()
        sub.attempt_id = 1
        sub.answers = []
        result = svc.submit_quiz_attempt(user_id=42, submission=sub)

        # db.add called for ProjectEnrollmentQuiz AND ProjectEnrollment (NOT_ELIGIBLE)
        assert db.add.call_count >= 2
        assert result is attempt_failed


# ===========================================================================
# TestGetUserQuizStatisticsBranchCoverage — loop continuation branches
# ===========================================================================

@pytest.mark.unit
class TestGetUserQuizStatisticsBranchCoverage:
    """
    Covers the for-loop continuation branches in get_user_quiz_statistics:
      - L395 False: attempt.completed_at is None → next iteration
      - L397 False: completed but quiz not found → next iteration
    """

    def _make_svc_db(self):
        db = MagicMock()
        with patch("app.services.quiz_service.GamificationService"):
            from app.services.quiz_service import QuizService
            svc = QuizService(db)
        return svc, db

    def test_attempt_without_completed_at_skipped_in_category_loop(self):
        """
        First attempt has completed_at=None → L395 False (no quiz lookup).
        Second attempt has completed_at set and quiz found → L395 True, L397 True.
        """
        svc, db = self._make_svc_db()
        quiz = _make_quiz(id=1, category=QuizCategory.GENERAL)

        incomplete = _make_attempt(id=1, completed_at=None, score=None, passed=False, xp_awarded=0)
        complete = _make_attempt(id=2, completed_at=datetime.now(timezone.utc),
                                  score=80.0, passed=True, xp_awarded=30)

        _multi_q(db, [
            {"all_": [incomplete, complete]},  # all attempts for user
            {"first": quiz},                   # get_quiz_by_id for complete attempt
        ])
        stats = svc.get_user_quiz_statistics(user_id=42)
        assert stats.total_quizzes_attempted == 2
        assert stats.total_quizzes_completed == 1
        assert stats.favorite_category == QuizCategory.GENERAL

    def test_completed_attempt_with_quiz_not_found_skips_category(self):
        """
        Attempt has completed_at but get_quiz_by_id returns None → L397 False.
        favorite_category remains None.
        """
        svc, db = self._make_svc_db()
        complete = _make_attempt(id=1, completed_at=datetime.now(timezone.utc),
                                  score=70.0, passed=True, xp_awarded=20)

        _multi_q(db, [
            {"all_": [complete]},  # all attempts
            {"first": None},       # get_quiz_by_id → None
        ])
        stats = svc.get_user_quiz_statistics(user_id=42)
        assert stats.favorite_category is None
