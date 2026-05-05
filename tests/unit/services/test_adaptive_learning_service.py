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


def _mock_session(presented=5, correct=3, trend=0.0, start_time=None, time_limit=None):
    s = MagicMock()
    s.questions_presented = presented
    s.questions_correct = correct
    s.performance_trend = trend
    s.session_start_time = start_time
    s.session_time_limit_seconds = time_limit
    return s


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
        """All candidate questions are passed to adaptive selection — no 1-hour blackout applied."""
        svc, db = _svc()
        session = MagicMock()
        question = MagicMock()
        question.id = 7
        _q(db, first=session)
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value={}), \
             patch.object(svc, "_get_candidate_questions", return_value=[question]), \
             patch.object(svc, "_select_adaptive_question", return_value=question) as mock_select, \
             patch.object(svc, "_get_session_time_remaining", return_value=120):
            result = svc.get_next_question(user_id=42, session_id=1)
        # The full candidate list must reach _select_adaptive_question
        mock_select.assert_called_once()
        candidates_passed = mock_select.call_args[0][0]
        assert question in candidates_passed
        assert result["id"] == 7

    def test_no_selected_question_returns_pool_exhausted_dict(self):
        """_select_adaptive_question returns None → pool_exhausted dict."""
        svc, db = _svc()
        session = MagicMock()
        _q(db, first=session, all_=[])   # recent questions → empty
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value={}), \
             patch.object(svc, "_get_candidate_questions", return_value=[MagicMock()]), \
             patch.object(svc, "_select_adaptive_question", return_value=None):
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
# _select_adaptive_question — branch coverage
# ===========================================================================

@pytest.mark.unit
class TestSelectAdaptiveQuestion:
    """
    Covers _select_adaptive_question:
      L279: due_questions found → random.choice (True path)
      L286: weak_concept_questions found AND random < 0.7 → random.choice
      L286: weak_concept OR random >= 0.7 → fallback random.choice
    """

    def _perf_data(self, due_ids=None, weak_ids=None, attempted_ids=None):
        # attempted_ids defaults to all candidate IDs so the never-seen path
        # is bypassed and the original due/weak paths can be exercised.
        return {
            "due_for_review": [MagicMock(question_id=i) for i in (due_ids or [])],
            "weak_concepts": [MagicMock(question_id=i) for i in (weak_ids or [])],
            "attempted_ids": set(attempted_ids) if attempted_ids is not None else {10, 20, 30},
        }

    def test_due_question_returned_preferentially(self):
        svc, _ = _svc()
        q_due = MagicMock(); q_due.id = 10
        q_other = MagicMock(); q_other.id = 20
        perf_data = self._perf_data(due_ids=[10])
        with patch("random.choice", return_value=q_due):
            result = svc._select_adaptive_question([q_due, q_other], perf_data, MagicMock())
        assert result is q_due

    def test_weak_concept_selected_when_random_below_threshold(self):
        svc, _ = _svc()
        q_weak = MagicMock(); q_weak.id = 20
        q_other = MagicMock(); q_other.id = 30
        perf_data = self._perf_data(due_ids=[], weak_ids=[20])
        with patch("random.random", return_value=0.5), \
             patch("random.choice", return_value=q_weak):
            result = svc._select_adaptive_question([q_weak, q_other], perf_data, MagicMock())
        assert result is q_weak

    def test_random_question_returned_when_random_above_threshold(self):
        """random.random() >= 0.7 → fallback to random.choice from all candidates."""
        svc, _ = _svc()
        q_weak = MagicMock(); q_weak.id = 20
        q_other = MagicMock(); q_other.id = 30
        perf_data = self._perf_data(due_ids=[], weak_ids=[20])
        with patch("random.random", return_value=0.9), \
             patch("random.choice", return_value=q_other):
            result = svc._select_adaptive_question([q_weak, q_other], perf_data, MagicMock())
        assert result is q_other


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
        _q(db, first=session)
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value={}), \
             patch.object(svc, "_get_candidate_questions", return_value=candidates), \
             patch.object(svc, "_select_adaptive_question", return_value=selected) as mock_sel, \
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
        """All candidates must reach _select_adaptive_question — no filtering applied."""
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
        q = MagicMock(); q.id = 1
        _q(db, first=session)
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value={}), \
             patch.object(svc, "_get_candidate_questions", return_value=[q]), \
             patch.object(svc, "_select_adaptive_question", return_value=q), \
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
            patch.object(svc, "_get_user_performance_data", return_value={
                "weak_concepts": [], "strong_concepts": [], "due_for_review": []
            }),
            patch.object(svc, "_get_candidate_questions", return_value=candidates),
            patch.object(svc, "_select_adaptive_question", return_value=selected),
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

    def test_exclude_ids_filters_before_selection(self):
        """exclude_ids={1,2} with only Q1/Q2 in pool → all_seen, never calls selection."""
        svc, db = _svc()
        q1, q2 = self._make_q(1), self._make_q(2)
        _q(db, first=MagicMock())
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value={
                 "weak_concepts": [], "strong_concepts": [], "due_for_review": []
             }), \
             patch.object(svc, "_get_candidate_questions", return_value=[q1, q2]), \
             patch.object(svc, "_select_adaptive_question") as mock_select:
            result = svc.get_next_question(user_id=42, session_id=1, exclude_ids={1, 2})
        assert result is not None
        assert result.get("session_complete") is True
        assert result.get("reason") == "all_seen"
        mock_select.assert_not_called()

    def test_exclude_ids_never_returns_seen_question(self):
        """With 3 candidates and exclude_ids={1}, returned question must not be Q1."""
        svc, db = _svc()
        q1, q2, q3 = self._make_q(1), self._make_q(2), self._make_q(3)
        _q(db, first=MagicMock())
        # Run 50 times to catch probabilistic failures
        for _ in range(50):
            with patch.object(svc, "_is_session_time_expired", return_value=False), \
                 patch.object(svc, "_get_user_performance_data", return_value={
                     "weak_concepts": [], "strong_concepts": [], "due_for_review": []
                 }), \
                 patch.object(svc, "_get_candidate_questions", return_value=[q1, q2, q3]), \
                 patch.object(svc, "_get_session_time_remaining", return_value=55), \
                 patch.object(svc, "_get_question_difficulty", return_value=0.5):
                result = svc.get_next_question(user_id=42, session_id=1, exclude_ids={1})
            assert result is not None
            assert not result.get("session_complete"), "Should have returned a question"
            assert result.get("id") != 1, f"Returned excluded Q1 in iteration"

    def test_pool_exhausted_returns_session_complete(self):
        """All candidates excluded → session_complete: True with all_seen reason."""
        svc, db = _svc()
        q1 = self._make_q(1)
        _q(db, first=MagicMock())
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value={
                 "weak_concepts": [], "strong_concepts": [], "due_for_review": []
             }), \
             patch.object(svc, "_get_candidate_questions", return_value=[q1]):
            result = svc.get_next_question(user_id=42, session_id=1, exclude_ids={1})
        assert result["session_complete"] is True
        assert result["reason"] == "all_seen"

    def test_weak_due_mismatch_falls_back_to_all_candidates(self):
        """No weak/due matches in candidates → random selection from full pool, no exception."""
        svc, db = _svc()
        q5 = self._make_q(5)
        _q(db, first=MagicMock())

        perf_q1 = MagicMock()
        perf_q1.question_id = 1
        perf_q1.mastery_level = 0.1  # weak
        perf_q1.next_review_at = None

        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value={
                 "weak_concepts": [perf_q1],   # Q1 is weak
                 "strong_concepts": [],
                 "due_for_review": [],
             }), \
             patch.object(svc, "_get_candidate_questions", return_value=[q5]), \
             patch.object(svc, "_get_session_time_remaining", return_value=55), \
             patch.object(svc, "_get_question_difficulty", return_value=0.5):
            # Q1 is weak but not in candidates (only Q5) → must fall back to random(candidates)
            result = svc.get_next_question(user_id=42, session_id=1)
        assert result is not None
        assert not result.get("session_complete"), "Should return Q5 as fallback"
        assert result.get("id") == 5


# ===========================================================================
# Answer-option shuffle — Fix #1
# ===========================================================================

@pytest.mark.unit
class TestAnswerOptionShuffle:
    """
    Correct answer position must be random.
    Grading is ID-based, so shuffle must not affect correctness evaluation.

    AL-SHUFFLE-01: options returned by get_next_question are shuffled
    AL-SHUFFLE-02: shuffled options contain every original option id (no drop/dup)
    AL-SHUFFLE-03: with a reversed-shuffle, correct option id still survives (grading safe)
    AL-SHUFFLE-04: uniform position distribution over many calls (chi-squared sanity)
    """

    def _make_option(self, oid, text, is_correct=False):
        opt = MagicMock()
        opt.id = oid
        opt.option_text = text
        opt.is_correct = is_correct
        return opt

    def _make_question(self, qid, options):
        q = MagicMock()
        q.id = qid
        q.question_text = "Q?"
        q.question_type = MagicMock(value="multiple_choice")
        q.answer_options = options
        return q

    def _get_result(self, svc, db, question):
        """Wire up DB mock and call get_next_question, return the result dict."""
        _q(db, first=MagicMock())
        with patch.object(svc, "_is_session_time_expired", return_value=False), \
             patch.object(svc, "_get_user_performance_data", return_value={
                 "weak_concepts": [], "strong_concepts": [], "due_for_review": [],
                 "attempted_ids": set(),
             }), \
             patch.object(svc, "_get_candidate_questions", return_value=[question]), \
             patch.object(svc, "_get_session_time_remaining", return_value=120), \
             patch.object(svc, "_get_question_difficulty", return_value=0.4):
            return svc.get_next_question(user_id=42, session_id=99)

    def test_shuffle_applied_to_options(self):
        """AL-SHUFFLE-01: options in response differ from original insertion order."""
        svc, db = _svc()
        opts = [self._make_option(i, f"opt{i}") for i in range(1, 5)]
        question = self._make_question(99, opts)
        orders_seen = set()
        for _ in range(40):
            result = self._get_result(svc, db, question)
            assert result is not None and "options" in result
            orders_seen.add(tuple(o["id"] for o in result["options"]))
        # At least 2 distinct orderings must appear across 40 draws
        assert len(orders_seen) >= 2, "options always in same order — shuffle not working"

    def test_shuffle_preserves_all_option_ids(self):
        """AL-SHUFFLE-02: shuffled list has same IDs as original (no drop/duplication)."""
        svc, db = _svc()
        opts = [self._make_option(i, f"opt{i}") for i in [10, 20, 30, 40]]
        question = self._make_question(99, opts)
        result = self._get_result(svc, db, question)
        returned_ids = {o["id"] for o in result["options"]}
        assert returned_ids == {10, 20, 30, 40}

    def test_grading_not_broken_by_shuffle(self):
        """AL-SHUFFLE-03: correct option id is present regardless of shuffle order."""
        svc, db = _svc()
        correct_id = 3
        opts = [
            self._make_option(1, "wrong A"),
            self._make_option(2, "wrong B"),
            self._make_option(correct_id, "CORRECT", is_correct=True),
            self._make_option(4, "wrong D"),
        ]
        question = self._make_question(99, opts)
        for _ in range(20):
            result = self._get_result(svc, db, question)
            ids = [o["id"] for o in result["options"]]
            assert correct_id in ids, "Correct option id dropped from shuffled list"

    def test_correct_position_not_always_first(self):
        """AL-SHUFFLE-04: correct option id not always at index 0."""
        svc, db = _svc()
        correct_id = 99
        opts = [self._make_option(correct_id, "CORRECT", is_correct=True)] + \
               [self._make_option(i, f"wrong{i}") for i in range(1, 4)]
        question = self._make_question(7, opts)
        positions = []
        for _ in range(60):
            result = self._get_result(svc, db, question)
            ids = [o["id"] for o in result["options"]]
            positions.append(ids.index(correct_id))
        # Must appear at more than one position
        assert len(set(positions)) >= 2, "Correct answer always at same position after shuffle"
        # Must not be exclusively at index 0
        assert any(p != 0 for p in positions), "Correct answer never left index 0"


# ===========================================================================
# Never-seen question priority — Fix #2
# ===========================================================================

@pytest.mark.unit
class TestNeverSeenPriority:
    """
    Questions never attempted by the user are served before review/weak queues.

    AL-COVERAGE-01: never-seen candidate returned over due-for-review candidate
    AL-COVERAGE-02: never-seen candidate returned over weak-concept candidate
    AL-COVERAGE-03: once all candidates seen, falls through to due/weak/random
    AL-COVERAGE-04: full pool covered before repetition in a single session
    """

    def _perf(self, attempted_ids=None, due_ids=None, weak_ids=None):
        return {
            "attempted_ids": set(attempted_ids or []),
            "due_for_review": [MagicMock(question_id=i) for i in (due_ids or [])],
            "weak_concepts": [MagicMock(question_id=i) for i in (weak_ids or [])],
        }

    def _q(self, qid):
        q = MagicMock()
        q.id = qid
        return q

    def test_never_seen_beats_due_for_review(self):
        """AL-COVERAGE-01: unseen candidate wins over due-review candidate."""
        svc, _ = _svc()
        q_unseen = self._q(1)
        q_due = self._q(2)
        perf = self._perf(attempted_ids=[2], due_ids=[2])  # q2 due, q1 never seen
        with patch("random.choice", side_effect=lambda lst: lst[0]):
            result = svc._select_adaptive_question([q_unseen, q_due], perf, MagicMock())
        assert result is q_unseen

    def test_never_seen_beats_weak_concept(self):
        """AL-COVERAGE-02: unseen candidate wins over weak-concept candidate."""
        svc, _ = _svc()
        q_unseen = self._q(5)
        q_weak = self._q(6)
        perf = self._perf(attempted_ids=[6], weak_ids=[6])  # q6 weak, q5 never seen
        with patch("random.choice", side_effect=lambda lst: lst[0]):
            result = svc._select_adaptive_question([q_unseen, q_weak], perf, MagicMock())
        assert result is q_unseen

    def test_all_seen_falls_through_to_due(self):
        """AL-COVERAGE-03: when all candidates attempted, due-for-review path fires."""
        svc, _ = _svc()
        q_due = self._q(10)
        q_other = self._q(11)
        perf = self._perf(attempted_ids=[10, 11], due_ids=[10])
        with patch("random.choice", return_value=q_due):
            result = svc._select_adaptive_question([q_due, q_other], perf, MagicMock())
        assert result is q_due

    def test_full_pool_covered_before_repetition(self):
        """AL-COVERAGE-04: 5-question pool fully covered before any repeat."""
        svc, _ = _svc()
        pool = [self._q(i) for i in range(5)]
        seen_ids = set()
        attempted_ids: set = set()

        for _ in range(5):
            available = [q for q in pool if q.id not in seen_ids]
            perf = self._perf(attempted_ids=attempted_ids)
            chosen = svc._select_adaptive_question(available, perf, MagicMock())
            assert chosen.id not in seen_ids, f"Question {chosen.id} repeated before pool exhausted"
            seen_ids.add(chosen.id)
            attempted_ids.add(chosen.id)

        assert seen_ids == {0, 1, 2, 3, 4}


# ===========================================================================
# Difficulty fallback logging — Fix #3
# ===========================================================================

@pytest.mark.unit
class TestDifficultyFallbackLogging:
    """
    When the difficulty-window query returns empty, the fallback must log a warning.

    AL-FALLBACK-01: warning emitted when difficulty query returns empty
    AL-FALLBACK-02: no warning when difficulty query succeeds
    """

    def _make_db_with_two_calls(self, first_returns, second_returns):
        """DB mock where first .all() returns first_returns, second returns second_returns."""
        db = MagicMock()
        calls = {"n": 0}
        base_q = MagicMock()
        base_q.filter.return_value = base_q
        base_q.join.return_value = base_q
        base_q.outerjoin.return_value = base_q

        def _all():
            n = calls["n"]
            calls["n"] += 1
            return first_returns if n == 0 else second_returns

        base_q.all.side_effect = _all
        db.query.return_value = base_q
        return db

    def test_warning_logged_on_fallback(self):
        """AL-FALLBACK-01: logger.warning fired when difficulty window is empty."""
        db = self._make_db_with_two_calls(first_returns=[], second_returns=[MagicMock()])
        svc = AdaptiveLearningService(db)
        with patch("app.services.adaptive_learning.logger") as mock_log:
            result = svc._get_candidate_questions(QuizCategory.GENERAL, target_difficulty=0.9)
        mock_log.warning.assert_called_once()
        call_args = mock_log.warning.call_args[0]
        assert "al_difficulty_fallback" in call_args[0]

    def test_no_warning_when_questions_found(self):
        """AL-FALLBACK-02: logger.warning not called when difficulty query succeeds."""
        db = self._make_db_with_two_calls(
            first_returns=[MagicMock()], second_returns=[]
        )
        svc = AdaptiveLearningService(db)
        with patch("app.services.adaptive_learning.logger") as mock_log:
            svc._get_candidate_questions(QuizCategory.GENERAL, target_difficulty=0.5)
        mock_log.warning.assert_not_called()
