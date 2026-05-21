"""Unit tests for al_analytics_service.

AAT-01..06   TestGetGlobalStats        — aggregate defaults, rates, session count
AAT-07..09   TestGetPositionHeatmap    — 4 buckets, zero-row safety, quiz_id filter
AAT-10..11   TestGetTopDistractors     — empty-row safety, limit forwarded
AAT-12       TestGetPerQuizStats       — empty list for unknown quiz
"""
import pytest
from unittest.mock import MagicMock, call, patch

from app.services.al_analytics_service import (
    GlobalStats,
    PositionBucket,
    DistractorStat,
    SessionCategoryStat,
    QuestionStat,
    _pct,
    get_global_stats,
    get_position_heatmap,
    get_top_distractors,
    get_session_category_stats,
    get_per_quiz_question_stats,
)

_SVC = "app.services.al_analytics_service"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_db():
    return MagicMock()


def _row(**kwargs):
    r = MagicMock()
    for k, v in kwargs.items():
        setattr(r, k, v)
    return r


# ── TestGetGlobalStats ─────────────────────────────────────────────────────────

class TestGetGlobalStats:
    def _build_db(self, total=0, correct=0, timeouts=0, avg_time=0.0, sessions=0):
        db = _mock_db()
        agg_row = _row(total=total, correct=correct, timeouts=timeouts, avg_time=avg_time)
        # query().first() for ALAnswerLog aggregate
        q1 = MagicMock()
        q1.first.return_value = agg_row
        # query(func.count(...)).scalar() for sessions
        q2 = MagicMock()
        q2.scalar.return_value = sessions
        db.query.side_effect = [q1, q2]
        return db

    def test_aat01_zero_rows_returns_defaults(self):
        """AAT-01: All-zero DB returns GlobalStats with sensible zero defaults."""
        db = self._build_db()
        result = get_global_stats(db)
        assert isinstance(result, GlobalStats)
        assert result.total_answers == 0
        assert result.success_rate == 0.0
        assert result.timeout_rate == 0.0
        assert result.avg_time_seconds == 0.0
        assert result.total_sessions == 0

    def test_aat02_total_answers_populated(self):
        """AAT-02: total_answers and correct_count populated from DB aggregate."""
        db = self._build_db(total=100, correct=75)
        result = get_global_stats(db)
        assert result.total_answers == 100
        assert result.correct_count == 75

    def test_aat03_success_rate_computed(self):
        """AAT-03: success_rate = correct / total (4 decimal places)."""
        db = self._build_db(total=200, correct=150)
        result = get_global_stats(db)
        assert result.success_rate == pytest.approx(0.75, abs=1e-4)

    def test_aat04_timeout_rate_computed(self):
        """AAT-04: timeout_rate = timeouts / total."""
        db = self._build_db(total=100, timeouts=10)
        result = get_global_stats(db)
        assert result.timeout_rate == pytest.approx(0.1, abs=1e-4)

    def test_aat05_avg_time_rounded_to_2dp(self):
        """AAT-05: avg_time_seconds rounded to 2 decimal places."""
        db = self._build_db(total=10, avg_time=12.3456789)
        result = get_global_stats(db)
        assert result.avg_time_seconds == 12.35

    def test_aat06_total_sessions_from_session_query(self):
        """AAT-06: total_sessions comes from AdaptiveLearningSession count."""
        db = self._build_db(sessions=42)
        result = get_global_stats(db)
        assert result.total_sessions == 42


# ── TestGetPositionHeatmap ─────────────────────────────────────────────────────

class TestGetPositionHeatmap:

    def test_aat07_always_returns_4_buckets(self):
        """AAT-07: get_position_heatmap always returns exactly 4 PositionBucket objects."""
        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.group_by.return_value = q
        q.all.return_value = []  # no data
        db.query.return_value = q

        result = get_position_heatmap(db)
        assert len(result) == 4
        assert all(isinstance(b, PositionBucket) for b in result)
        assert [b.label for b in result] == ["A", "B", "C", "D"]

    def test_aat08_zero_rows_all_zero_counts(self):
        """AAT-08: Empty DB → all buckets have total_count=0, pct=0.0."""
        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.group_by.return_value = q
        q.all.return_value = []
        db.query.return_value = q

        result = get_position_heatmap(db)
        assert all(b.total_count == 0 for b in result)
        assert all(b.pct == 0.0 for b in result)

    def test_aat09_quiz_id_filter_applied(self):
        """AAT-09: When quiz_id provided, join+filter is called on the query."""
        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.join.return_value = q
        q.group_by.return_value = q
        q.all.return_value = []
        db.query.return_value = q

        get_position_heatmap(db, quiz_id=5)
        # join() must have been called (for the quiz_id restriction)
        assert q.join.called


# ── TestGetTopDistractors ──────────────────────────────────────────────────────

class TestGetTopDistractors:

    def _make_chain(self, rows=None):
        db = _mock_db()
        q = MagicMock()
        q.join.return_value = q
        q.filter.return_value = q
        q.group_by.return_value = q
        q.order_by.return_value = q
        q.limit.return_value = q
        q.all.return_value = rows or []
        db.query.return_value = q
        return db, q

    def test_aat10_empty_db_returns_empty_list(self):
        """AAT-10: No wrong answers → empty list."""
        db, _ = self._make_chain()
        result = get_top_distractors(db)
        assert result == []

    def test_aat11_limit_forwarded_to_query(self):
        """AAT-11: limit parameter is forwarded to the query .limit() call."""
        db, q = self._make_chain()
        get_top_distractors(db, limit=7)
        q.limit.assert_called_once_with(7)


# ── TestGetPerQuizStats ────────────────────────────────────────────────────────

class TestGetPerQuizStats:

    def test_aat12_empty_quiz_returns_empty_list(self):
        """AAT-12: Quiz with no questions → empty list returned immediately."""
        db = _mock_db()
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.all.return_value = []  # no questions found
        db.query.return_value = q

        result = get_per_quiz_question_stats(db, quiz_id=999)
        assert result == []


# ── _pct helper ────────────────────────────────────────────────────────────────

def test_pct_helper_zero_denominator():
    assert _pct(5, 0) == 0.0

def test_pct_helper_normal():
    assert _pct(3, 4) == pytest.approx(0.75, abs=1e-4)
