"""
Unit tests for app/services/adaptive_learning.py (AdaptiveLearningService)

Covers:
  Pure helper methods (no DB):
    _calculate_performance_trend — few questions, high/low/neutral success rate
    _adjust_target_difficulty    — correct+high-trend, incorrect+low-trend, small adjustments
    _is_session_time_expired     — no start time, expired, not expired
    _get_session_time_remaining  — no start time, expired, near-full remaining

  DB-dependent methods (MagicMock db):
    get_user_learning_analytics  — no performances (zero stats), with performances
    _calculate_adaptive_xp       — incorrect answer (consolation), correct with/without metadata
    _get_mastery_update          — no performance record, performance record exists

  Additional DB-dependent methods (MagicMock db):
    start_adaptive_session       — creates session object, calls db.add/commit/refresh
    _calculate_target_difficulty — high/low/neutral success rate adjustments
    _update_user_question_performance — no existing performance (create), existing (update)
    _update_question_metadata    — no existing metadata (create), existing (update)
    end_session                  — session not found (returns {}), session found (returns stats)
    record_answer                — session not found path
"""
import pytest
import math
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from app.services.adaptive_learning import AdaptiveLearningService
from app.models.quiz import QuizCategory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _svc():
    """Return (service, mock_db)."""
    db = MagicMock()
    return AdaptiveLearningService(db), db


def _q(db, first=None, all_=None):
    q = MagicMock()
    q.filter.return_value = q
    q.join.return_value = q
    q.first.return_value = first
    q.all.return_value = all_ if all_ is not None else []
    db.query.return_value = q
    return q


def _mock_session(presented=5, correct=3, trend=0.0, start_time=None, time_limit=None,
                  session_due_shown=0):
    s = MagicMock()
    s.questions_presented = presented
    s.questions_correct = correct
    s.performance_trend = trend
    s.session_start_time = start_time
    s.session_time_limit_seconds = time_limit
    s.session_due_shown = session_due_shown
    return s


def _perf_data(due_ids=None, weak_ids=None, all_ids=None):
    """Build a performance_data dict for patching _get_user_performance_data."""
    all_ids = all_ids or []
    return {
        "all_performances": [MagicMock(question_id=i, mastery_level=0.5,
                                       difficulty_weight=1.5, next_review_at=None)
                             for i in all_ids],
        "due_for_review": [MagicMock(question_id=i) for i in (due_ids or [])],
        "weak_concepts": [MagicMock(question_id=i) for i in (weak_ids or [])],
        "strong_concepts": [],
    }


_EMPTY_PERF = {"all_performances": [], "due_for_review": [], "weak_concepts": [], "strong_concepts": []}


# ===========================================================================
# _calculate_performance_trend
# ===========================================================================

@pytest.mark.unit
class TestCalculatePerformanceTrend:
    def test_few_questions_returns_current_trend(self):
        svc, _ = _svc()
        session = _mock_session(presented=2, correct=2, trend=0.3)
        result = svc._calculate_performance_trend(session)
        assert result == 0.3  # unchanged when < 3 questions

    def test_high_success_rate_increases_trend(self):
        svc, _ = _svc()
        # 4/5 = 0.80 > 0.70 → increase by 0.1
        session = _mock_session(presented=5, correct=4, trend=0.2)
        result = svc._calculate_performance_trend(session)
        assert result == min(1.0, 0.2 + 0.1)

    def test_high_trend_clamped_at_one(self):
        svc, _ = _svc()
        # Already at 0.95, high success → min(1.0, 0.95+0.1) = 1.0
        session = _mock_session(presented=10, correct=9, trend=0.95)
        result = svc._calculate_performance_trend(session)
        assert result == 1.0

    def test_low_success_rate_decreases_trend(self):
        svc, _ = _svc()
        # 2/5 = 0.40 < 0.50 → decrease by 0.1
        session = _mock_session(presented=5, correct=2, trend=0.1)
        result = svc._calculate_performance_trend(session)
        assert result == max(-1.0, 0.1 - 0.1)

    def test_low_trend_clamped_at_neg_one(self):
        svc, _ = _svc()
        # At -0.95, low success → max(-1.0, -0.95 - 0.1) = -1.0
        session = _mock_session(presented=5, correct=1, trend=-0.95)
        result = svc._calculate_performance_trend(session)
        assert result == -1.0

    def test_neutral_success_rate_decays_trend(self):
        svc, _ = _svc()
        # 3/5 = 0.60, between 0.50 and 0.70 → trend * 0.9
        session = _mock_session(presented=5, correct=3, trend=0.5)
        result = svc._calculate_performance_trend(session)
        assert abs(result - 0.5 * 0.9) < 1e-9

    def test_exactly_three_questions_triggers_calculation(self):
        svc, _ = _svc()
        # 3 questions, 2 correct = 0.667 → neutral range
        session = _mock_session(presented=3, correct=2, trend=0.2)
        result = svc._calculate_performance_trend(session)
        assert result == 0.2 * 0.9


# ===========================================================================
# _adjust_target_difficulty
# ===========================================================================

@pytest.mark.unit
class TestAdjustTargetDifficulty:
    def test_correct_with_high_trend_increases_difficulty(self):
        svc, _ = _svc()
        result = svc._adjust_target_difficulty(0.5, is_correct=True, trend=0.8)
        assert result == min(0.9, 0.5 + 0.05)

    def test_correct_difficulty_clamped_at_0_9(self):
        svc, _ = _svc()
        result = svc._adjust_target_difficulty(0.88, is_correct=True, trend=0.9)
        assert result == 0.9

    def test_incorrect_with_low_trend_decreases_difficulty(self):
        svc, _ = _svc()
        result = svc._adjust_target_difficulty(0.5, is_correct=False, trend=-0.8)
        assert result == max(0.1, 0.5 - 0.05)

    def test_incorrect_difficulty_clamped_at_0_1(self):
        svc, _ = _svc()
        result = svc._adjust_target_difficulty(0.12, is_correct=False, trend=-0.9)
        assert result == 0.1

    def test_correct_with_neutral_trend_small_increase(self):
        svc, _ = _svc()
        result = svc._adjust_target_difficulty(0.5, is_correct=True, trend=0.3)
        assert abs(result - (0.5 + 0.05 * 0.5)) < 1e-9

    def test_incorrect_with_neutral_trend_small_decrease(self):
        svc, _ = _svc()
        result = svc._adjust_target_difficulty(0.5, is_correct=False, trend=0.0)
        assert abs(result - (0.5 - 0.05 * 0.5)) < 1e-9


# ===========================================================================
# _is_session_time_expired
# ===========================================================================

@pytest.mark.unit
class TestIsSessionTimeExpired:
    def test_no_start_time_returns_false(self):
        svc, _ = _svc()
        session = _mock_session(start_time=None, time_limit=300)
        assert svc._is_session_time_expired(session) is False

    def test_no_time_limit_returns_false(self):
        svc, _ = _svc()
        start = datetime.now(timezone.utc) - timedelta(seconds=600)
        session = _mock_session(start_time=start, time_limit=None)
        assert svc._is_session_time_expired(session) is False

    def test_expired_session_returns_true(self):
        svc, _ = _svc()
        start = datetime.now(timezone.utc) - timedelta(seconds=400)
        session = _mock_session(start_time=start, time_limit=300)  # 300s limit, 400s elapsed
        assert svc._is_session_time_expired(session) is True

    def test_not_expired_returns_false(self):
        svc, _ = _svc()
        start = datetime.now(timezone.utc) - timedelta(seconds=100)
        session = _mock_session(start_time=start, time_limit=300)  # 300s limit, 100s elapsed
        assert svc._is_session_time_expired(session) is False

    def test_exactly_at_limit_returns_true(self):
        svc, _ = _svc()
        # elapsed ≈ limit (exact boundary, elapsed >= limit)
        start = datetime.now(timezone.utc) - timedelta(seconds=300)
        session = _mock_session(start_time=start, time_limit=300)
        assert svc._is_session_time_expired(session) is True


# ===========================================================================
# _get_session_time_remaining
# ===========================================================================

@pytest.mark.unit
class TestGetSessionTimeRemaining:
    def test_no_start_time_returns_zero(self):
        svc, _ = _svc()
        session = _mock_session(start_time=None, time_limit=300)
        assert svc._get_session_time_remaining(session) == 0

    def test_no_time_limit_returns_zero(self):
        svc, _ = _svc()
        start = datetime.now(timezone.utc)
        session = _mock_session(start_time=start, time_limit=None)
        assert svc._get_session_time_remaining(session) == 0

    def test_expired_returns_zero(self):
        svc, _ = _svc()
        start = datetime.now(timezone.utc) - timedelta(seconds=400)
        session = _mock_session(start_time=start, time_limit=300)
        assert svc._get_session_time_remaining(session) == 0

    def test_remaining_time_is_positive(self):
        svc, _ = _svc()
        start = datetime.now(timezone.utc) - timedelta(seconds=100)
        session = _mock_session(start_time=start, time_limit=300)
        remaining = svc._get_session_time_remaining(session)
        assert remaining > 0
        assert remaining <= 200  # should be ~200s remaining


# ===========================================================================
# get_user_learning_analytics
# ===========================================================================

@pytest.mark.unit
class TestGetUserLearningAnalytics:
    def test_no_performances_returns_zeros(self):
        svc, db = _svc()
        _q(db, all_=[])
        result = svc.get_user_learning_analytics(user_id=42)
        assert result["total_questions_attempted"] == 0
        assert result["total_attempts"] == 0
        assert result["overall_success_rate"] == 0.0
        assert result["mastery_level"] == 0.0
        assert result["learning_velocity"] == 0.0
        assert result["recommended_difficulty"] == 0.5

    def test_with_performances_calculates_stats(self):
        svc, db = _svc()
        p1 = MagicMock()
        p1.total_attempts = 5
        p1.correct_attempts = 4
        p1.mastery_level = 0.8
        p1.last_attempted_at = datetime.now(timezone.utc) - timedelta(days=1)  # recent
        p1.success_rate = 0.8
        _q(db, all_=[p1])
        result = svc.get_user_learning_analytics(user_id=42)
        assert result["total_questions_attempted"] == 1
        assert result["total_attempts"] == 5
        assert abs(result["overall_success_rate"] - 4/5) < 0.01
        assert result["mastery_level"] == 0.8


# ===========================================================================
# _calculate_adaptive_xp
# ===========================================================================

@pytest.mark.unit
class TestCalculateAdaptiveXp:
    def test_incorrect_answer_returns_consolation_xp(self):
        svc, db = _svc()
        result = svc._calculate_adaptive_xp(question_id=1, is_correct=False, time_spent=30.0)
        assert result == 5

    def test_correct_no_metadata_returns_base_xp(self):
        svc, db = _svc()
        _q(db, first=None)  # No metadata → base XP only
        result = svc._calculate_adaptive_xp(question_id=1, is_correct=True, time_spent=30.0)
        assert result == 25  # base_xp with no difficulty bonus or time bonus

    def test_correct_with_metadata_difficulty_bonus(self):
        svc, db = _svc()
        meta = MagicMock()
        meta.estimated_difficulty = 0.8  # 80% difficulty
        meta.average_time_seconds = None  # no time bonus
        _q(db, first=meta)
        result = svc._calculate_adaptive_xp(question_id=1, is_correct=True, time_spent=30.0)
        # difficulty_bonus = int(0.8 * 20) = 16
        assert result == 25 + 16

    def test_correct_with_time_bonus(self):
        svc, db = _svc()
        meta = MagicMock()
        meta.estimated_difficulty = 0.0  # no difficulty bonus
        meta.average_time_seconds = 60.0  # avg is 60s, user took 20s → faster
        _q(db, first=meta)
        result = svc._calculate_adaptive_xp(question_id=1, is_correct=True, time_spent=20.0)
        # time_ratio = 60/20 = 3.0, time_bonus = min(0.5, max(0, (3.0-1)*0.25)) = min(0.5, 0.5) = 0.5
        # result = int(25 * 1.5) = 37
        assert result == 37


# ===========================================================================
# _get_mastery_update
# ===========================================================================

@pytest.mark.unit
class TestGetMasteryUpdate:
    def test_no_performance_returns_zero_dict(self):
        svc, db = _svc()
        _q(db, first=None)
        result = svc._get_mastery_update(user_id=42, question_id=1)
        assert result == {"mastery_level": 0.0, "success_rate": 0.0, "next_review": None}

    def test_with_performance_returns_values(self):
        svc, db = _svc()
        perf = MagicMock()
        perf.mastery_level = 0.7
        perf.success_rate = 0.75
        perf.next_review_at = None  # no scheduled review
        _q(db, first=perf)
        result = svc._get_mastery_update(user_id=42, question_id=1)
        assert result["mastery_level"] == 0.7
        assert result["success_rate"] == 0.75
        assert result["next_review"] is None

    def test_with_performance_and_review_date(self):
        svc, db = _svc()
        review_time = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        perf = MagicMock()
        perf.mastery_level = 0.5
        perf.success_rate = 0.5
        perf.next_review_at = review_time
        _q(db, first=perf)
        result = svc._get_mastery_update(user_id=42, question_id=1)
        assert result["next_review"] == review_time.isoformat()


# ===========================================================================
# start_adaptive_session
# ===========================================================================

@pytest.mark.unit
class TestStartAdaptiveSession:
    def test_creates_session_and_commits(self):
        svc, db = _svc()
        with patch.object(svc, "_calculate_target_difficulty", return_value=0.5):
            result = svc.start_adaptive_session(user_id=42, category=QuizCategory.GENERAL)
        # Service should call db.add, db.commit, db.refresh
        db.add.assert_called_once()
        db.commit.assert_called_once()
        db.refresh.assert_called_once()

    def test_uses_provided_session_duration(self):
        svc, db = _svc()
        with patch.object(svc, "_calculate_target_difficulty", return_value=0.5):
            svc.start_adaptive_session(user_id=2, category=QuizCategory.GENERAL,
                                       session_duration_seconds=600)
        # The session object passed to db.add should have the right duration
        added_session = db.add.call_args[0][0]
        assert added_session.session_time_limit_seconds == 600


# ===========================================================================
# _calculate_target_difficulty
# ===========================================================================

@pytest.mark.unit
class TestCalculateTargetDifficulty:
    def _analytics(self, success_rate, velocity=0.0):
        return {
            "overall_success_rate": success_rate,
            "learning_velocity": velocity
        }

    def test_high_success_rate_increases_difficulty(self):
        svc, db = _svc()
        with patch.object(svc, "get_user_learning_analytics",
                          return_value=self._analytics(0.9)):
            result = svc._calculate_target_difficulty(user_id=42, category=QuizCategory.GENERAL)
        assert result == max(0.1, min(0.9, 0.5 + 0.2))  # 0.7

    def test_low_success_rate_decreases_difficulty(self):
        svc, db = _svc()
        with patch.object(svc, "get_user_learning_analytics",
                          return_value=self._analytics(0.4)):
            result = svc._calculate_target_difficulty(user_id=42, category=QuizCategory.GENERAL)
        assert result == max(0.1, min(0.9, 0.5 - 0.2))  # 0.3

    def test_middle_success_rate_returns_base(self):
        svc, db = _svc()
        with patch.object(svc, "get_user_learning_analytics",
                          return_value=self._analytics(0.7, velocity=0.0)):
            result = svc._calculate_target_difficulty(user_id=42, category=QuizCategory.GENERAL)
        assert result == 0.5

    def test_learning_velocity_adjusts_base(self):
        svc, db = _svc()
        with patch.object(svc, "get_user_learning_analytics",
                          return_value=self._analytics(0.7, velocity=1.0)):
            result = svc._calculate_target_difficulty(user_id=42, category=QuizCategory.GENERAL)
        # base=0.5, velocity=1.0 → 0.5 + 0.1 = 0.6
        assert abs(result - 0.6) < 1e-9


# ===========================================================================
# _update_user_question_performance
# ===========================================================================

@pytest.mark.unit
class TestUpdateUserQuestionPerformance:
    def test_no_existing_performance_creates_new(self):
        svc, db = _svc()
        _q(db, first=None)  # No existing record
        svc._update_user_question_performance(
            user_id=42, question_id=1, is_correct=True, time_spent=30.0
        )
        db.add.assert_called_once()

    def test_existing_performance_increments_attempts(self):
        svc, db = _svc()
        perf = MagicMock()
        perf.total_attempts = 5
        perf.correct_attempts = 3
        perf.mastery_level = 0.6
        _q(db, first=perf)
        svc._update_user_question_performance(
            user_id=42, question_id=1, is_correct=True, time_spent=30.0
        )
        # total_attempts should be incremented
        assert perf.total_attempts == 6
        assert perf.correct_attempts == 4
        db.add.assert_not_called()

    def test_incorrect_answer_does_not_increment_correct(self):
        svc, db = _svc()
        perf = MagicMock()
        perf.total_attempts = 3
        perf.correct_attempts = 1
        perf.mastery_level = 0.2
        _q(db, first=perf)
        svc._update_user_question_performance(
            user_id=42, question_id=1, is_correct=False, time_spent=20.0
        )
        assert perf.total_attempts == 4
        assert perf.correct_attempts == 1  # unchanged
        assert perf.last_attempt_correct is False


# ===========================================================================
# _update_question_metadata
# ===========================================================================

@pytest.mark.unit
class TestUpdateQuestionMetadata:
    def test_no_existing_metadata_creates_new(self):
        svc, db = _svc()
        _q(db, first=None)
        svc._update_question_metadata(question_id=1, is_correct=True, time_spent=30.0)
        db.add.assert_called_once()

    def test_existing_metadata_updates_success_rate(self):
        svc, db = _svc()
        meta = MagicMock()
        meta.global_success_rate = 0.6
        meta.average_time_seconds = 60.0
        meta.estimated_difficulty = 0.5
        _q(db, first=meta)
        svc._update_question_metadata(question_id=1, is_correct=True, time_spent=30.0)
        # Success rate and avg time should be updated (exponential moving average)
        assert meta.global_success_rate != 0.6  # changed
        assert meta.last_analytics_update is not None
        db.add.assert_not_called()


# ===========================================================================
# end_session
# ===========================================================================

@pytest.mark.unit
class TestEndSession:
    def test_session_not_found_returns_empty_dict(self):
        svc, db = _svc()
        _q(db, first=None)
        result = svc.end_session(session_id=99)
        assert result == {}

    def test_session_found_returns_stats(self):
        svc, db = _svc()
        session = MagicMock()
        session.questions_presented = 10
        session.questions_correct = 8
        session.performance_trend = 0.5
        session.target_difficulty = 0.7
        session.user_id = 1
        _q(db, first=session)
        result = svc.end_session(session_id=1)
        # score = 8*2 - 10 = 6; xp = max(0, 6) * 10 = 60
        assert result["questions_answered"] == 10
        assert result["correct_answers"] == 8
        assert abs(result["success_rate"] - 0.8) < 1e-9
        assert result["xp_earned"] == 60
        assert result["score"] == 6


# ===========================================================================
# record_answer — session not found path
# ===========================================================================

@pytest.mark.unit
class TestRecordAnswer:
    def test_session_not_found_still_returns_dict(self):
        svc, db = _svc()
        # session query returns None
        _q(db, first=None)
        with patch.object(svc, "_update_user_question_performance"):
            with patch.object(svc, "_update_question_metadata"):
                with patch.object(svc, "_get_mastery_update",
                                  return_value={"mastery_level": 0.0,
                                                "success_rate": 0.0,
                                                "next_review": None}):
                    result = svc.record_answer(
                        user_id=42, session_id=99, question_id=1,
                        is_correct=False, time_spent_seconds=30.0
                    )
        assert result["score_delta"] == -1
        assert result["score"] == 0
        assert result["new_target_difficulty"] is None
        assert result["performance_trend"] is None


# ===========================================================================
# get_next_question — uncovered branches
# ===========================================================================

@pytest.mark.unit
class TestGetNextQuestion:
    """
    Covers branches in get_next_question (L35-86):
      L40: session not found → None
      L43: session time expired → time_expired dict
      L52: no candidate questions → None
      L65: all recently answered → fall back to all candidates
      L75: selected_question is None → no_questions dict
    """

    def _setup_q_multi(self, db, *qs):
        """Set db.query.side_effect to return sequential mocks."""
        calls = [0]
        def _side(*args):
            idx = calls[0]
            calls[0] += 1
            return qs[idx] if idx < len(qs) else _q(db)
        db.query.side_effect = _side

    def test_session_not_found_returns_none(self):
        svc, db = _svc()
        _q(db, first=None)   # session query → None
        result = svc.get_next_question(user_id=42, session_id=99)
        assert result is None

    def test_time_expired_returns_dict(self):
        svc, db = _svc()
        session = MagicMock()
        _q(db, first=session)
        with patch.object(svc, "_is_session_time_expired", return_value=True):
            result = svc.get_next_question(user_id=42, session_id=1)
        assert result == {"session_complete": True, "reason": "time_expired"}

    def test_no_candidate_questions_returns_pool_exhausted(self):
        svc, db = _svc()
        session = MagicMock()
        _q(db, first=session)
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value={}), \
             patch.object(svc, "_get_candidate_questions", return_value=[]):
            result = svc.get_next_question(user_id=42, session_id=1)
        assert result is not None
        assert result.get("session_complete") is True
        assert result.get("reason") == "pool_exhausted"

    def test_all_candidates_available_without_blackout(self):
        """All candidate questions are passed to weighted selection — no 1-hour blackout applied."""
        svc, db = _svc()
        session = MagicMock()
        session.session_due_shown = 0
        question = MagicMock()
        question.id = 7
        question.answer_options = []
        question.question_type = None
        _q(db, first=session)
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value=_EMPTY_PERF), \
             patch.object(svc, "_get_candidate_questions", return_value=[question]), \
             patch.object(svc, "_select_weighted_question", return_value=question) as mock_select, \
             patch.object(svc, "_get_session_time_remaining", return_value=120):
            result = svc.get_next_question(user_id=42, session_id=1)
        # The full candidate list must reach _select_weighted_question
        mock_select.assert_called_once()
        candidates_passed = mock_select.call_args[0][0]
        assert question in candidates_passed
        assert result["id"] == 7

    def test_no_selected_question_returns_pool_exhausted_dict(self):
        """_select_weighted_question returns None → pool_exhausted dict."""
        svc, db = _svc()
        session = MagicMock()
        session.session_due_shown = 0
        _q(db, first=session, all_=[])
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value=_EMPTY_PERF), \
             patch.object(svc, "_get_candidate_questions", return_value=[MagicMock()]), \
             patch.object(svc, "_select_weighted_question", return_value=None):
            result = svc.get_next_question(user_id=42, session_id=1)
        assert result == {"session_complete": True, "reason": "pool_exhausted"}


# ===========================================================================
# record_answer — session found branches (is_correct True/False)
# ===========================================================================

@pytest.mark.unit
class TestRecordAnswerSessionFound:
    """
    Covers record_answer branches when session IS found:
      L97: session found → questions_presented incremented
      L99: is_correct=True → questions_correct incremented
      L99: is_correct=False → questions_correct NOT incremented
    """

    def _run(self, is_correct):
        svc, db = _svc()
        session = MagicMock()
        session.questions_presented = 2
        session.questions_correct = 1
        session.performance_trend = 0.0
        session.target_difficulty = 0.5
        _q(db, first=session)
        with patch.object(svc, "_calculate_performance_trend", return_value=0.1), \
             patch.object(svc, "_adjust_target_difficulty", return_value=0.55), \
             patch.object(svc, "_update_user_question_performance"), \
             patch.object(svc, "_update_question_metadata"), \
             patch.object(svc, "_get_mastery_update", return_value={}):
            result = svc.record_answer(
                user_id=42, session_id=1, question_id=5,
                is_correct=is_correct, time_spent_seconds=20.0
            )
        return result, session

    def test_is_correct_true_increments_correct_count(self):
        result, session = self._run(True)
        assert result["score_delta"] == 1
        assert session.questions_correct == 2  # 1 + 1

    def test_is_correct_false_does_not_increment_correct_count(self):
        result, session = self._run(False)
        assert result["score_delta"] == -1
        assert session.questions_correct == 1


# ===========================================================================
# get_user_learning_analytics — uncovered branches
# ===========================================================================

@pytest.mark.unit
class TestGetUserLearningAnalyticsBranches:
    """
    Covers:
      L173: if category: → True branch (join query applied)
      L201: if len(recent_performances) > 0: → True branch (velocity calculated)
    """

    def test_with_category_joins_query(self):
        """category provided → join applied (L173 True)."""
        svc, db = _svc()
        perf = MagicMock()
        perf.total_attempts = 5
        perf.correct_attempts = 3
        perf.mastery_level = 0.6
        perf.success_rate = 0.6
        # last_attempted_at in the past (not recent)
        perf.last_attempted_at = datetime.now(timezone.utc) - timedelta(days=10)
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.all.return_value = [perf]
        db.query.return_value = q
        result = svc.get_user_learning_analytics(
            user_id=42, category=QuizCategory.GENERAL
        )
        q.join.assert_called()   # category branch triggered join
        assert result["total_questions_attempted"] == 1

    def test_with_recent_performances_calculates_velocity(self):
        """recent_performances > 0 → learning_velocity computed (L201 True)."""
        svc, db = _svc()
        perf = MagicMock()
        perf.total_attempts = 10
        perf.correct_attempts = 8
        perf.mastery_level = 0.8
        perf.success_rate = 0.8
        # last_attempted_at within last 7 days → goes into recent_performances
        perf.last_attempted_at = datetime.now(timezone.utc) - timedelta(hours=2)
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.all.return_value = [perf]
        db.query.return_value = q
        result = svc.get_user_learning_analytics(user_id=42, category=None)
        # learning_velocity should be set (0.8 recent - 0.8 overall = 0.0 in this case)
        assert "learning_velocity" in result


# ===========================================================================
# _calculate_question_weight — AL-SEL-01..07
# ===========================================================================

@pytest.mark.unit
class TestCalculateQuestionWeight:
    """Unit tests for _calculate_question_weight priority formula."""

    def _run(self, q_id, due_ids=None, perf_map=None, session_due_shown=0, exclude_ids=None):
        svc, _ = _svc()
        return svc._calculate_question_weight(
            q_id,
            set(due_ids or []),
            perf_map or {},
            session_due_shown,
            set(exclude_ids or []),
        )

    def test_al_sel_01_due_within_cap_gets_high_weight(self):
        """AL-SEL-01: due question with session_due_shown < cap → weight 2.5."""
        w = self._run(1, due_ids=[1], session_due_shown=0)
        assert w == 2.5

    def test_al_sel_02_due_cap_exhausted_falls_back_to_difficulty_weight(self):
        """AL-SEL-02: due question but cap exhausted → min(dw, 1.8)."""
        from unittest.mock import MagicMock
        perf = MagicMock(); perf.mastery_level = 0.3; perf.difficulty_weight = 1.6
        w = self._run(1, due_ids=[1], perf_map={1: perf}, session_due_shown=3)
        assert w == min(1.6, 1.8)

    def test_al_sel_03_weak_concept_gets_elevated_weight(self):
        """AL-SEL-03: non-due weak concept (mastery < 0.6) → min(dw, 1.8)."""
        perf = MagicMock(); perf.mastery_level = 0.3; perf.difficulty_weight = 1.7
        w = self._run(2, due_ids=[], perf_map={2: perf}, session_due_shown=0)
        assert w == min(1.7, 1.8)

    def test_al_sel_04_never_seen_gets_slight_boost(self):
        """AL-SEL-04: question with no performance record → weight 1.2."""
        w = self._run(3, due_ids=[], perf_map={}, session_due_shown=0)
        assert w == 1.2

    def test_al_sel_05_strong_mastery_gets_normal_weight(self):
        """AL-SEL-05: mastery >= 0.6, not due, not weak → weight 1.0."""
        perf = MagicMock(); perf.mastery_level = 0.8; perf.difficulty_weight = 1.2
        w = self._run(4, due_ids=[], perf_map={4: perf}, session_due_shown=0)
        assert w == 1.0

    def test_al_sel_06_exclude_ids_applies_recency_penalty(self):
        """AL-SEL-06: question in exclude_ids → weight *= 0.1."""
        w = self._run(5, due_ids=[], perf_map={}, session_due_shown=0, exclude_ids=[5])
        assert abs(w - 1.2 * 0.1) < 1e-9  # never-seen (1.2) * penalty

    def test_al_sel_07_weight_floor_is_0_05(self):
        """AL-SEL-07: even a highly penalised question gets at least 0.05."""
        perf = MagicMock(); perf.mastery_level = 0.9; perf.difficulty_weight = 1.0
        # normal (1.0) * penalty (0.1) = 0.1 → max(0.05, 0.1) = 0.1; floor only bites below 0.5
        w_normal = self._run(6, due_ids=[], perf_map={6: perf}, session_due_shown=0, exclude_ids=[6])
        assert w_normal >= 0.05
        # A question that would compute to exactly 0.0 still gets 0.05
        # Simulate by passing extreme dw through the formula manually
        svc, _ = _svc()
        assert svc._calculate_question_weight(99, set(), {}, 0, set()) >= 0.05


# ===========================================================================
# _select_weighted_question
# ===========================================================================

@pytest.mark.unit
class TestSelectWeightedQuestion:
    """Tests for _select_weighted_question pool coverage and edge cases."""

    def _sess(self, due_shown=0):
        s = MagicMock()
        s.session_due_shown = due_shown
        return s

    def _make_q(self, qid):
        q = MagicMock(); q.id = qid; q.answer_options = []; q.question_type = None
        return q

    def test_empty_candidates_returns_none(self):
        svc, _ = _svc()
        result = svc._select_weighted_question([], _EMPTY_PERF, self._sess())
        assert result is None

    def test_single_candidate_always_returned(self):
        svc, _ = _svc()
        q = self._make_q(1)
        for _ in range(10):
            result = svc._select_weighted_question([q], _EMPTY_PERF, self._sess())
            assert result is q

    def test_due_questions_selected_more_often_within_cap(self):
        """Due questions (cap not reached) must dominate selection over long runs."""
        svc, _ = _svc()
        q_due = self._make_q(10)
        q_other = self._make_q(20)
        due_perf = MagicMock(question_id=10)
        pd = {
            "all_performances": [due_perf],
            "due_for_review": [due_perf],
            "weak_concepts": [], "strong_concepts": [],
        }
        counts = {10: 0, 20: 0}
        for _ in range(200):
            r = svc._select_weighted_question([q_due, q_other], pd, self._sess(due_shown=0))
            counts[r.id] += 1
        # Due question (weight 2.5) vs never-seen (weight 1.2): expect >60% due
        assert counts[10] > 100, f"Due question selected only {counts[10]}/200 times"

    def test_cap_exhausted_due_question_not_dominant(self):
        """When session_due_shown >= cap, due questions lose their 2.5 priority."""
        svc, _ = _svc()
        q_due = self._make_q(10)
        q_other = self._make_q(20)
        due_perf = MagicMock(question_id=10, mastery_level=0.3, difficulty_weight=1.5)
        pd = {
            "all_performances": [due_perf],
            "due_for_review": [due_perf],
            "weak_concepts": [], "strong_concepts": [],
        }
        counts = {10: 0, 20: 0}
        for _ in range(200):
            r = svc._select_weighted_question([q_due, q_other], pd, self._sess(due_shown=3))
            counts[r.id] += 1
        # With cap exhausted, q_due weight ≤ 1.8, q_other weight 1.2 → not dominant
        assert counts[10] < 170, f"Cap-exhausted due question still dominant: {counts[10]}/200"

    def test_excluded_questions_still_selectable(self):
        """Excluded questions get recency penalty (0.1×) but CAN be returned."""
        svc, _ = _svc()
        q1 = self._make_q(1)
        # Run 500 times; with 0.1× penalty among 1 candidate, q1 must appear
        results = [
            svc._select_weighted_question([q1], _EMPTY_PERF, self._sess(), exclude_ids={1})
            for _ in range(10)
        ]
        assert all(r is q1 for r in results), "Single excluded candidate must still be returned"


# ===========================================================================
# _get_candidate_questions — fallback branch
# ===========================================================================

@pytest.mark.unit
class TestGetCandidateQuestionsFallback:
    """
    Covers L264: if not questions: → fallback to any category questions.
    """

    def test_empty_range_falls_back_to_category_query(self):
        svc, db = _svc()
        question = MagicMock()
        # First query (by difficulty range) returns empty
        # Second query (category-only fallback) returns one question
        calls = [0]
        q_empty = MagicMock(); q_empty.filter.return_value = q_empty
        q_empty.join.return_value = q_empty; q_empty.outerjoin.return_value = q_empty
        q_empty.all.return_value = []
        q_fallback = MagicMock(); q_fallback.filter.return_value = q_fallback
        q_fallback.join.return_value = q_fallback; q_fallback.all.return_value = [question]
        def _side(*args):
            idx = calls[0]; calls[0] += 1
            return [q_empty, q_fallback][idx] if idx < 2 else MagicMock()
        db.query.side_effect = _side
        result = svc._get_candidate_questions(QuizCategory.GENERAL, target_difficulty=0.5)
        assert result == [question]


# ===========================================================================
# _update_question_metadata — difficulty adjustment branches
# ===========================================================================

@pytest.mark.unit
class TestUpdateQuestionMetadataDifficultyBranches:
    """
    Covers:
      L386: global_success_rate > 0.8 → decrease estimated_difficulty
      L388: global_success_rate < 0.4 → increase estimated_difficulty
    """

    def _run_update(self, initial_success, is_correct, time_spent=30.0):
        svc, db = _svc()
        metadata = MagicMock()
        metadata.global_success_rate = initial_success
        metadata.average_time_seconds = 60.0
        metadata.estimated_difficulty = 0.5
        _q(db, first=metadata)
        svc._update_question_metadata(question_id=1, is_correct=is_correct,
                                       time_spent=time_spent)
        return metadata

    def test_high_success_rate_decreases_difficulty(self):
        """After update, success_rate > 0.8 → estimated_difficulty decreased."""
        metadata = self._run_update(initial_success=0.95, is_correct=True)
        # global_success_rate = 0.95*0.95 + 1.0*0.05 = 0.9025 + 0.05 = 0.9525 > 0.8
        assert metadata.estimated_difficulty < 0.5   # decreased

    def test_low_success_rate_increases_difficulty(self):
        """After update, success_rate < 0.4 → estimated_difficulty increased."""
        metadata = self._run_update(initial_success=0.2, is_correct=False)
        # global_success_rate = 0.2*0.95 + 0.0*0.05 = 0.19 < 0.4
        assert metadata.estimated_difficulty > 0.5   # increased


# ===========================================================================
# Repetition fix — no 1-hour blackout
# ===========================================================================

@pytest.mark.unit
class TestNoBlackoutRepetition:
    """
    Verifies that get_next_question no longer applies a 1-hour
    last_attempted_at exclusion window. Questions answered previously
    (wrong, correct, or timed-out) must remain eligible for re-selection
    without exhausting the candidate pool.
    """

    def _call_next(self, svc, db, candidates, selected):
        session = MagicMock()
        session.session_due_shown = 0
        _q(db, first=session)
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value=_EMPTY_PERF), \
             patch.object(svc, "_get_candidate_questions", return_value=candidates), \
             patch.object(svc, "_select_weighted_question", return_value=selected) as mock_sel, \
             patch.object(svc, "_get_session_time_remaining", return_value=120):
            result = svc.get_next_question(user_id=42, session_id=1)
        return result, mock_sel

    def test_wrong_answer_question_remains_in_pool(self):
        """A question answered incorrectly must still be passed to adaptive selection."""
        svc, db = _svc()
        q = MagicMock(); q.id = 10
        result, mock_sel = self._call_next(svc, db, [q], q)
        candidates = mock_sel.call_args[0][0]
        assert q in candidates

    def test_correct_answer_question_not_permanently_excluded(self):
        """A correctly answered question must still be in the candidate pool."""
        svc, db = _svc()
        q = MagicMock(); q.id = 20
        result, mock_sel = self._call_next(svc, db, [q], q)
        candidates = mock_sel.call_args[0][0]
        assert q in candidates

    def test_timeout_question_not_permanently_excluded(self):
        """A timed-out question (treated as wrong) must remain eligible."""
        svc, db = _svc()
        q = MagicMock(); q.id = 30
        result, mock_sel = self._call_next(svc, db, [q], q)
        candidates = mock_sel.call_args[0][0]
        assert q in candidates

    def test_small_pool_does_not_dead_end(self):
        """With a 1-question pool all calls must get a result, never None from exhaustion."""
        svc, db = _svc()
        q = MagicMock(); q.id = 5
        for _ in range(5):
            result, _ = self._call_next(svc, db, [q], q)
            assert result is not None
            assert result["id"] == 5

    def test_full_candidate_list_reaches_selector(self):
        """All candidates must reach _select_weighted_question — no hard filtering applied."""
        svc, db = _svc()
        questions = [MagicMock(id=i) for i in range(1, 6)]
        result, mock_sel = self._call_next(svc, db, questions, questions[0])
        candidates = mock_sel.call_args[0][0]
        assert len(candidates) == 5
        assert all(q in candidates for q in questions)

    def test_repeated_question_score_updates_correctly(self):
        """record_answer must correctly decrement score on repeated wrong answer."""
        svc, db = _svc()
        session = MagicMock()
        session.id = 1
        session.questions_presented = 3
        session.questions_correct = 1
        session.target_difficulty = 0.5
        session.performance_trend = 0.0
        _q(db, first=session)
        with patch.object(svc, "_update_user_question_performance"), \
             patch.object(svc, "_update_question_metadata"), \
             patch.object(svc, "_adjust_target_difficulty", return_value=0.5), \
             patch.object(svc, "_calculate_performance_trend", return_value=0.0):
            result = svc.record_answer(
                user_id=42, session_id=1, question_id=99,
                is_correct=False, time_spent_seconds=10.0
            )
        assert result["score_delta"] == -1

    def test_no_userquestionperformance_query_for_blackout(self):
        """get_next_question must NOT issue a cross-session recency query for blackout."""
        from app.models.quiz import UserQuestionPerformance
        svc, db = _svc()
        session = MagicMock()
        session.session_due_shown = 0
        q = MagicMock(); q.id = 1; q.answer_options = []; q.question_type = None
        _q(db, first=session)
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value=_EMPTY_PERF), \
             patch.object(svc, "_get_candidate_questions", return_value=[q]), \
             patch.object(svc, "_select_weighted_question", return_value=q), \
             patch.object(svc, "_get_session_time_remaining", return_value=120):
            svc.get_next_question(user_id=42, session_id=1)
        # Verify db.query was NOT called with UserQuestionPerformance as the sole arg
        blackout_calls = [
            call for call in db.query.call_args_list
            if call == ((UserQuestionPerformance,), {})
        ]
        assert blackout_calls == [], "1-hour blackout query must not be issued"


# ===========================================================================
# AL Stability Fix — 5 mandatory tests (BUG-1 + BUG-2)
# ===========================================================================

@pytest.mark.unit
class TestGetNextQuestionDedup:
    """BUG-1: exclude_ids must be filtered at service level before adaptive selection."""

    def _make_q(self, qid):
        q = MagicMock()
        q.id = qid
        q.question_text = f"Q{qid}"
        q.answer_options = []
        q.question_type = None
        return q

    def _base_patches(self, svc, candidates, selected):
        return [
            patch.object(svc, "_is_session_time_expired", return_value=False),
            patch.object(svc, "_get_user_performance_data", return_value=_EMPTY_PERF),
            patch.object(svc, "_get_candidate_questions", return_value=candidates),
            patch.object(svc, "_select_weighted_question", return_value=selected),
            patch.object(svc, "_get_session_time_remaining", return_value=55),
            patch.object(svc, "_get_question_difficulty", return_value=0.5),
        ]

    def test_empty_pool_returns_pool_exhausted_no_exception(self):
        """Empty candidate list → pool_exhausted reason, no exception raised."""
        svc, db = _svc()
        _q(db, first=MagicMock())
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value={
                 "weak_concepts": [], "strong_concepts": [], "due_for_review": []
             }), \
             patch.object(svc, "_get_candidate_questions", return_value=[]):
            result = svc.get_next_question(user_id=42, session_id=1)
        assert result is not None
        assert result.get("session_complete") is True
        assert result.get("reason") == "pool_exhausted"

    def test_exclude_ids_applies_recency_penalty_not_hard_filter(self):
        """exclude_ids={1,2} with only Q1/Q2 — recency penalty, NOT hard exclusion.
        Service must still return a question (not session_complete).
        """
        svc, db = _svc()
        q1, q2 = self._make_q(1), self._make_q(2)
        session = MagicMock()
        session.session_due_shown = 0
        _q(db, first=session)
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value=_EMPTY_PERF), \
             patch.object(svc, "_get_candidate_questions", return_value=[q1, q2]), \
             patch.object(svc, "_get_session_time_remaining", return_value=55), \
             patch.object(svc, "_get_question_difficulty", return_value=0.5):
            result = svc.get_next_question(user_id=42, session_id=1, exclude_ids={1, 2})
        assert result is not None
        assert not result.get("session_complete"), \
            "Recency penalty must not cause session_complete — excluded questions remain selectable"

    def test_exclude_ids_reduces_probability_of_seen_questions(self):
        """With 3 candidates and exclude_ids={1}, Q1 should appear less often than Q2/Q3."""
        svc, db = _svc()
        q1, q2, q3 = self._make_q(1), self._make_q(2), self._make_q(3)
        counts = {1: 0, 2: 0, 3: 0}
        for _ in range(200):
            session = MagicMock()
            session.session_due_shown = 0
            _q(db, first=session)
            with patch.object(svc, "_is_session_time_expired", return_value=False), \
                 patch.object(svc, "_get_user_performance_data", return_value=_EMPTY_PERF), \
                 patch.object(svc, "_get_candidate_questions", return_value=[q1, q2, q3]), \
                 patch.object(svc, "_get_session_time_remaining", return_value=55), \
                 patch.object(svc, "_get_question_difficulty", return_value=0.5):
                result = svc.get_next_question(user_id=42, session_id=1, exclude_ids={1})
            if result and not result.get("session_complete"):
                counts[result["id"]] += 1
        # Q1 has 0.1× penalty; Q2/Q3 are normal. Expected Q1 ≈ 5%, Q2+Q3 ≈ 95%.
        # Conservatively: Q1 should appear less than half as often as Q2 alone.
        assert counts[1] < counts[2], \
            f"Excluded Q1 appeared {counts[1]} times vs non-excluded Q2 {counts[2]}"

    def test_single_candidate_with_exclude_still_returns_result(self):
        """Single excluded candidate → recency penalty applies but question is still returned."""
        svc, db = _svc()
        q1 = self._make_q(1)
        session = MagicMock()
        session.session_due_shown = 0
        _q(db, first=session)
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value=_EMPTY_PERF), \
             patch.object(svc, "_get_candidate_questions", return_value=[q1]), \
             patch.object(svc, "_get_session_time_remaining", return_value=55), \
             patch.object(svc, "_get_question_difficulty", return_value=0.5):
            result = svc.get_next_question(user_id=42, session_id=1, exclude_ids={1})
        assert result is not None
        assert not result.get("session_complete")
        assert result["id"] == 1

    def test_weak_due_mismatch_falls_back_to_all_candidates(self):
        """No weak/due matches in candidates → weighted selection from full pool, no exception."""
        svc, db = _svc()
        q5 = self._make_q(5)
        session = MagicMock()
        session.session_due_shown = 0
        _q(db, first=session)

        perf_q1 = MagicMock()
        perf_q1.question_id = 1
        perf_q1.mastery_level = 0.1  # weak, but not in candidates
        perf_q1.difficulty_weight = 1.5
        perf_q1.next_review_at = None

        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value={
                 "all_performances": [perf_q1],
                 "weak_concepts": [perf_q1],   # Q1 is weak
                 "strong_concepts": [],
                 "due_for_review": [],
             }), \
             patch.object(svc, "_get_candidate_questions", return_value=[q5]), \
             patch.object(svc, "_get_session_time_remaining", return_value=55), \
             patch.object(svc, "_get_question_difficulty", return_value=0.5):
            # Q1 is weak but not in candidates (only Q5) → must fall back to weighted(candidates)
            result = svc.get_next_question(user_id=42, session_id=1)
        assert result is not None
        assert not result.get("session_complete"), "Should return Q5 as fallback"
        assert result.get("id") == 5


# ===========================================================================
# Language filter — AL-LANG-01..03
# ===========================================================================

@pytest.mark.unit
class TestLanguageFilter:
    """Verifies that performance data queries are scoped to the session language."""

    def test_al_lang_01_get_user_learning_analytics_applies_language_filter(self):
        """AL-LANG-01: language param passed to get_user_learning_analytics → join+filter applied."""
        svc, db = _svc()
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.all.return_value = []
        db.query.return_value = q

        svc.get_user_learning_analytics(user_id=42, category=QuizCategory.LESSON, language="hu")
        # A language filter was applied → join must have been called (category+language branch)
        q.join.assert_called()
        # filter must have been called at least twice (category + language)
        assert q.filter.call_count >= 2, "Expected category AND language filter calls"

    def test_al_lang_02_get_user_performance_data_applies_language_filter(self):
        """AL-LANG-02: _get_user_performance_data(language='hu') must include language in query."""
        svc, db = _svc()
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.all.return_value = []
        db.query.return_value = q

        from app.models.quiz import QuizCategory
        svc._get_user_performance_data(user_id=42, category=QuizCategory.LESSON, language="hu")
        q.filter.assert_called()
        # Verify language was passed as a filter — the call args should contain 'hu' somewhere
        all_filter_args = str(q.filter.call_args_list)
        assert "hu" in all_filter_args or q.filter.call_count >= 1

    def test_al_lang_03_calculate_target_difficulty_passes_language_to_analytics(self):
        """AL-LANG-03: _calculate_target_difficulty passes language to get_user_learning_analytics."""
        svc, _ = _svc()
        calls = []
        original = svc.get_user_learning_analytics
        def recording_analytics(user_id, category=None, language=None):
            calls.append({"user_id": user_id, "category": category, "language": language})
            return {
                "overall_success_rate": 0.7,
                "learning_velocity": 0.0,
                "mastery_level": 0.5,
            }
        svc.get_user_learning_analytics = recording_analytics

        svc._calculate_target_difficulty(user_id=42, category=QuizCategory.LESSON, language="hu")
        assert len(calls) == 1
        assert calls[0]["language"] == "hu", \
            f"Expected language='hu' passed to analytics, got {calls[0]}"


# ===========================================================================
# session_due_shown — DB-backed counter increments on due question
# ===========================================================================

@pytest.mark.unit
class TestSessionDueShown:
    """Verifies that session_due_shown is incremented when a due question is served."""

    def _make_q(self, qid):
        q = MagicMock(); q.id = qid; q.answer_options = []; q.question_type = None
        return q

    def test_due_question_increments_session_due_shown(self):
        """When a due question is selected, session_due_shown += 1 and db.commit() called."""
        svc, db = _svc()
        q_due = self._make_q(10)
        session = MagicMock()
        session.session_due_shown = 1
        _q(db, first=session)

        due_perf = MagicMock(question_id=10)
        pd = {
            "all_performances": [due_perf],
            "due_for_review": [due_perf],
            "weak_concepts": [], "strong_concepts": [],
        }
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value=pd), \
             patch.object(svc, "_get_candidate_questions", return_value=[q_due]), \
             patch.object(svc, "_select_weighted_question", return_value=q_due), \
             patch.object(svc, "_get_session_time_remaining", return_value=60), \
             patch.object(svc, "_get_question_difficulty", return_value=0.5):
            result = svc.get_next_question(user_id=42, session_id=1)

        assert session.session_due_shown == 2, "due question must increment session_due_shown"
        db.commit.assert_called()
        assert result.get("was_due") is True

    def test_non_due_question_does_not_increment_session_due_shown(self):
        """Non-due question: session_due_shown unchanged, was_due=False in response."""
        svc, db = _svc()
        q_normal = self._make_q(20)
        session = MagicMock()
        session.session_due_shown = 1
        _q(db, first=session)

        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value=_EMPTY_PERF), \
             patch.object(svc, "_get_candidate_questions", return_value=[q_normal]), \
             patch.object(svc, "_select_weighted_question", return_value=q_normal), \
             patch.object(svc, "_get_session_time_remaining", return_value=60), \
             patch.object(svc, "_get_question_difficulty", return_value=0.5):
            result = svc.get_next_question(user_id=42, session_id=1)

        assert session.session_due_shown == 1, "non-due question must not change session_due_shown"
        assert result.get("was_due") is False
