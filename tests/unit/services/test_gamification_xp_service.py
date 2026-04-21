"""
Sprint P4 — gamification/xp_service.py
========================================
Target: ≥85% stmt, ≥75% branch

Covers all three functions:
  award_attendance_xp  — no attendance / no session / already awarded / base XP /
                         instructor feedback (performance_rating + rating fallback) /
                         quiz XP tiers (≥90, 70-89, <70) / HYBRID & VIRTUAL /
                         auto-fetch best attempt / level calculation
  calculate_user_stats — empty bookings / multi-booking / cancelled status /
                         attendance_rate / avg_rating / attendance_xp branch /
                         level formula / commit
  award_xp             — XP addition / level calc / level_up print / no level_up /
                         user.xp_balance update / user not found / commit / None balance
"""
import pytest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.gamification.xp_service import (
    award_attendance_xp,
    award_xp,
    calculate_user_stats,
)
from app.models.attendance import Attendance as AttModel
from app.models.session import Session as SessModel
from app.models.feedback import Feedback as FbModel
from app.models.gamification import UserStats


# ── Constants ─────────────────────────────────────────────────────────────────

_PATCH_STATS = "app.services.gamification.xp_service.get_or_create_user_stats"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _att(xp_earned=0, session_id=10, user_id=42):
    return SimpleNamespace(id=1, session_id=session_id, user_id=user_id, xp_earned=xp_earned)


def _sess(sport_type="on_site", sess_id=10):
    return SimpleNamespace(id=sess_id, sport_type=sport_type, semester_id=None)


def _stats(total_xp=0, level=1):
    return SimpleNamespace(total_xp=total_xp, level=level, updated_at=None)


def _att_db(attendance=None, session=None, feedback=None,
            session_quiz=None, best_attempt=None) -> MagicMock:
    """
    Smart DB mock for award_attendance_xp.
    Discriminates db.query() calls by model type:
      AttModel   → .filter().first()   = attendance
      SessModel  → .filter().first()   = session
      FbModel    → .filter().first()   = feedback
      other      → .filter().first()   = session_quiz   (SessionQuiz path)
                   .filter().order_by().first() = best_attempt  (QuizAttempt path)
    """
    def _query(*args):
        q = MagicMock()
        first = args[0] if args else None
        if first is AttModel:
            q.filter.return_value.first.return_value = attendance
        elif first is SessModel:
            q.filter.return_value.first.return_value = session
        elif first is FbModel:
            q.filter.return_value.first.return_value = feedback
        else:
            # SessionQuiz  → .filter().first()
            # QuizAttempt  → .filter().order_by().first()
            q.filter.return_value.first.return_value = session_quiz
            q.filter.return_value.order_by.return_value.first.return_value = best_attempt
        return q

    db = MagicMock()
    db.query.side_effect = _query
    return db


# ── award_attendance_xp ───────────────────────────────────────────────────────

class TestAwardAttendanceXP:
    """award_attendance_xp — full logic tree."""

    # ── early-return branches ─────────────────────────────────────────────────

    def test_no_attendance_returns_0(self):
        db = _att_db(attendance=None)
        assert award_attendance_xp(db, attendance_id=1) == 0

    def test_no_session_returns_0(self):
        db = _att_db(attendance=_att(), session=None)
        assert award_attendance_xp(db, attendance_id=1) == 0

    def test_already_awarded_returns_existing_xp(self):
        """xp_earned > 0 → skip recalculation."""
        db = _att_db(attendance=_att(xp_earned=120), session=_sess())
        assert award_attendance_xp(db, attendance_id=1) == 120

    # ── base XP, no feedback, non-quiz session type ───────────────────────────

    def test_base_xp_only_on_site_session(self):
        stats = _stats()
        db = _att_db(attendance=_att(), session=_sess("on_site"), feedback=None)
        with patch(_PATCH_STATS, return_value=stats):
            result = award_attendance_xp(db, attendance_id=1)
        assert result == 50

    def test_base_xp_only_when_no_feedback(self):
        stats = _stats()
        db = _att_db(attendance=_att(), session=_sess("on_site"), feedback=None)
        with patch(_PATCH_STATS, return_value=stats):
            result = award_attendance_xp(db, attendance_id=1)
        assert result == 50

    # ── instructor feedback XP ────────────────────────────────────────────────

    def test_instructor_xp_via_performance_rating(self):
        """hasattr(feedback, 'performance_rating') → instructor_xp = rating * 10."""
        stats = _stats()
        feedback = SimpleNamespace(performance_rating=8)  # 8 * 10 = 80
        db = _att_db(attendance=_att(), session=_sess("on_site"), feedback=feedback)
        with patch(_PATCH_STATS, return_value=stats):
            result = award_attendance_xp(db, attendance_id=1)
        assert result == 50 + 80  # base + instructor

    def test_instructor_xp_via_rating_fallback(self):
        """No performance_rating attr → falls back to feedback.rating."""
        stats = _stats()
        feedback = SimpleNamespace(rating=7)  # no performance_rating → 7 * 10 = 70
        db = _att_db(attendance=_att(), session=_sess("on_site"), feedback=feedback)
        with patch(_PATCH_STATS, return_value=stats):
            result = award_attendance_xp(db, attendance_id=1)
        assert result == 50 + 70

    # ── quiz XP tiers (HYBRID) ────────────────────────────────────────────────

    def test_quiz_xp_90_plus_for_hybrid(self):
        stats = _stats()
        session_quiz = MagicMock()
        db = _att_db(attendance=_att(), session=_sess("HYBRID"),
                     feedback=None, session_quiz=session_quiz)
        with patch(_PATCH_STATS, return_value=stats):
            result = award_attendance_xp(db, attendance_id=1, quiz_score_percent=95)
        assert result == 50 + 0 + 150  # base + no feedback + quiz_xp=150

    def test_quiz_xp_70_to_89_for_hybrid(self):
        stats = _stats()
        session_quiz = MagicMock()
        db = _att_db(attendance=_att(), session=_sess("HYBRID"),
                     feedback=None, session_quiz=session_quiz)
        with patch(_PATCH_STATS, return_value=stats):
            result = award_attendance_xp(db, attendance_id=1, quiz_score_percent=75)
        assert result == 50 + 0 + 75

    def test_quiz_xp_below_70_no_bonus(self):
        stats = _stats()
        session_quiz = MagicMock()
        db = _att_db(attendance=_att(), session=_sess("HYBRID"),
                     feedback=None, session_quiz=session_quiz)
        with patch(_PATCH_STATS, return_value=stats):
            result = award_attendance_xp(db, attendance_id=1, quiz_score_percent=65)
        assert result == 50  # quiz_xp = 0

    def test_quiz_xp_exact_90_boundary_returns_150(self):
        stats = _stats()
        session_quiz = MagicMock()
        db = _att_db(attendance=_att(), session=_sess("HYBRID"),
                     feedback=None, session_quiz=session_quiz)
        with patch(_PATCH_STATS, return_value=stats):
            result = award_attendance_xp(db, attendance_id=1, quiz_score_percent=90)
        assert result == 200  # 50 + 150

    def test_quiz_xp_exact_70_boundary_returns_75(self):
        stats = _stats()
        session_quiz = MagicMock()
        db = _att_db(attendance=_att(), session=_sess("HYBRID"),
                     feedback=None, session_quiz=session_quiz)
        with patch(_PATCH_STATS, return_value=stats):
            result = award_attendance_xp(db, attendance_id=1, quiz_score_percent=70)
        assert result == 125  # 50 + 75

    # ── VIRTUAL session type ──────────────────────────────────────────────────

    def test_quiz_xp_for_virtual_session(self):
        stats = _stats()
        session_quiz = MagicMock()
        db = _att_db(attendance=_att(), session=_sess("VIRTUAL"),
                     feedback=None, session_quiz=session_quiz)
        with patch(_PATCH_STATS, return_value=stats):
            result = award_attendance_xp(db, attendance_id=1, quiz_score_percent=92)
        assert result == 50 + 150

    def test_lowercase_hybrid_normalized_to_upper(self):
        """sport_type with upper() normalization — 'hybrid' → 'HYBRID'."""
        stats = _stats()
        session_quiz = MagicMock()
        db = _att_db(attendance=_att(), session=_sess("hybrid"),
                     feedback=None, session_quiz=session_quiz)
        with patch(_PATCH_STATS, return_value=stats):
            result = award_attendance_xp(db, attendance_id=1, quiz_score_percent=95)
        assert result == 200  # quiz XP awarded

    # ── session quiz present/absent ───────────────────────────────────────────

    def test_no_quiz_xp_when_no_session_quiz(self):
        stats = _stats()
        db = _att_db(attendance=_att(), session=_sess("HYBRID"),
                     feedback=None, session_quiz=None)  # no SessionQuiz
        with patch(_PATCH_STATS, return_value=stats):
            result = award_attendance_xp(db, attendance_id=1, quiz_score_percent=95)
        assert result == 50  # no quiz_xp

    # ── auto-fetch best attempt ───────────────────────────────────────────────

    def test_quiz_score_auto_fetched_from_best_attempt(self):
        """quiz_score_percent=None → fetches best_attempt → uses its score."""
        stats = _stats()
        session_quiz = MagicMock()
        best_attempt = SimpleNamespace(score=92.0)
        db = _att_db(attendance=_att(), session=_sess("HYBRID"),
                     feedback=None, session_quiz=session_quiz,
                     best_attempt=best_attempt)
        with patch(_PATCH_STATS, return_value=stats):
            result = award_attendance_xp(db, attendance_id=1, quiz_score_percent=None)
        assert result == 200  # 50 + 150 (score=92 ≥ 90)

    def test_quiz_score_none_no_attempt_found_zero_quiz_xp(self):
        stats = _stats()
        session_quiz = MagicMock()
        db = _att_db(attendance=_att(), session=_sess("HYBRID"),
                     feedback=None, session_quiz=session_quiz,
                     best_attempt=None)  # no attempt
        with patch(_PATCH_STATS, return_value=stats):
            result = award_attendance_xp(db, attendance_id=1, quiz_score_percent=None)
        assert result == 50  # no quiz_xp

    # ── stats update & level calculation ─────────────────────────────────────

    def test_attendance_xp_stored_on_attendance_object(self):
        stats = _stats()
        attendance = _att()
        db = _att_db(attendance=attendance, session=_sess("on_site"))
        with patch(_PATCH_STATS, return_value=stats):
            award_attendance_xp(db, attendance_id=1)
        assert attendance.xp_earned == 50

    def test_stats_total_xp_incremented(self):
        stats = _stats(total_xp=100)
        db = _att_db(attendance=_att(), session=_sess("on_site"))
        with patch(_PATCH_STATS, return_value=stats):
            award_attendance_xp(db, attendance_id=1)
        assert stats.total_xp == 150  # 100 + 50

    def test_level_increases_at_500_xp_boundary(self):
        """total_xp = 450 + 50 = 500 → level = (500//500)+1 = 2."""
        stats = _stats(total_xp=450, level=1)
        db = _att_db(attendance=_att(), session=_sess("on_site"))
        with patch(_PATCH_STATS, return_value=stats):
            award_attendance_xp(db, attendance_id=1)
        assert stats.level == 2

    def test_level_stays_1_below_boundary(self):
        """total_xp = 449 + 50 = 499 → level = (499//500)+1 = 1."""
        stats = _stats(total_xp=449, level=1)
        db = _att_db(attendance=_att(), session=_sess("on_site"))
        with patch(_PATCH_STATS, return_value=stats):
            award_attendance_xp(db, attendance_id=1)
        assert stats.level == 1

    def test_db_committed(self):
        stats = _stats()
        db = _att_db(attendance=_att(), session=_sess("on_site"))
        with patch(_PATCH_STATS, return_value=stats):
            award_attendance_xp(db, attendance_id=1)
        db.commit.assert_called_once()


# ── calculate_user_stats ──────────────────────────────────────────────────────

def _stats_full(**kwargs):
    defaults = dict(
        total_xp=0, level=1, semesters_participated=0, first_semester_date=None,
        total_bookings=0, total_attended=0, total_cancelled=0, attendance_rate=0.0,
        feedback_given=0, average_rating_given=0.0, updated_at=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _booking_row(status_val="confirmed", semester_id=1, start_date=None):
    from app.models.booking import Booking
    booking = SimpleNamespace(status=SimpleNamespace(value=status_val))
    session = MagicMock()
    semester = SimpleNamespace(id=semester_id, start_date=start_date or date(2026, 1, 1))
    return (booking, session, semester)


def _calc_db(booking_rows=None, att_count=0, fb_count=0,
             avg_rating_val=None, xp_sum_val=None) -> MagicMock:
    """
    DB mock for calculate_user_stats.
    Booking query → booking_rows list
    Attendance → .filter().count() = att_count
    Feedback   → .filter().count() = fb_count
    func.avg / func.sum (both go to else) tracked by call counter.
    """
    from app.models.booking import Booking

    _func_n = [0]

    def _query(*args):
        q = MagicMock()
        first = args[0] if args else None

        if first is Booking:
            q.join.return_value.join.return_value.filter.return_value.all.return_value = \
                booking_rows or []
        elif first is AttModel:
            q.filter.return_value.count.return_value = att_count
        elif first is FbModel:
            q.filter.return_value.count.return_value = fb_count
        else:
            # 1st call → func.avg (avg_rating); 2nd call → func.sum (xp_sum)
            _func_n[0] += 1
            q.filter.return_value.scalar.return_value = (
                avg_rating_val if _func_n[0] == 1 else xp_sum_val
            )
        return q

    db = MagicMock()
    db.query.side_effect = _query
    return db


class TestCalculateUserStats:
    """calculate_user_stats — covers booking loop, rate formulas, XP/level update."""

    def test_no_bookings_zeroes_out_fields(self):
        stats = _stats_full()
        db = _calc_db(booking_rows=[], att_count=0, fb_count=0)
        with patch(_PATCH_STATS, return_value=stats):
            result = calculate_user_stats(db, user_id=42)
        assert result.total_bookings == 0
        assert result.semesters_participated == 0
        assert result.attendance_rate == 0.0
        assert result.first_semester_date is None

    def test_two_bookings_same_semester_counts_one_unique(self):
        rows = [_booking_row(semester_id=1), _booking_row(semester_id=1)]
        stats = _stats_full()
        db = _calc_db(booking_rows=rows)
        with patch(_PATCH_STATS, return_value=stats):
            result = calculate_user_stats(db, user_id=42)
        assert result.semesters_participated == 1
        assert result.total_bookings == 2

    def test_two_different_semesters(self):
        rows = [_booking_row(semester_id=1), _booking_row(semester_id=2)]
        stats = _stats_full()
        db = _calc_db(booking_rows=rows)
        with patch(_PATCH_STATS, return_value=stats):
            result = calculate_user_stats(db, user_id=42)
        assert result.semesters_participated == 2

    def test_cancelled_booking_increments_total_cancelled(self):
        rows = [
            _booking_row(status_val="confirmed"),
            _booking_row(status_val="cancelled"),
        ]
        stats = _stats_full()
        db = _calc_db(booking_rows=rows)
        with patch(_PATCH_STATS, return_value=stats):
            result = calculate_user_stats(db, user_id=42)
        assert result.total_cancelled == 1

    def test_attendance_rate_computed_correctly(self):
        """3 attendances / 4 bookings → 75%."""
        rows = [_booking_row() for _ in range(4)]
        stats = _stats_full()
        db = _calc_db(booking_rows=rows, att_count=3)
        with patch(_PATCH_STATS, return_value=stats):
            result = calculate_user_stats(db, user_id=42)
        assert abs(result.attendance_rate - 75.0) < 0.01

    def test_attendance_rate_zero_when_no_bookings(self):
        stats = _stats_full()
        db = _calc_db(booking_rows=[], att_count=0)
        with patch(_PATCH_STATS, return_value=stats):
            result = calculate_user_stats(db, user_id=42)
        assert result.attendance_rate == 0.0

    def test_avg_rating_from_feedback_query(self):
        stats = _stats_full()
        db = _calc_db(fb_count=2, avg_rating_val=4.5)
        with patch(_PATCH_STATS, return_value=stats):
            result = calculate_user_stats(db, user_id=42)
        assert result.average_rating_given == 4.5
        assert result.feedback_given == 2

    def test_avg_rating_none_defaults_to_zero(self):
        stats = _stats_full()
        db = _calc_db(avg_rating_val=None)
        with patch(_PATCH_STATS, return_value=stats):
            result = calculate_user_stats(db, user_id=42)
        assert result.average_rating_given == 0.0

    def test_attendance_xp_updates_total_xp_when_higher(self):
        """attendance_xp > stats.total_xp → stats.total_xp = attendance_xp."""
        stats = _stats_full(total_xp=0)
        db = _calc_db(xp_sum_val=300)
        with patch(_PATCH_STATS, return_value=stats):
            result = calculate_user_stats(db, user_id=42)
        assert result.total_xp == 300

    def test_existing_total_xp_kept_when_higher_than_attendance_xp(self):
        """stats.total_xp=500 > attendance_xp=300 → stays at 500."""
        stats = _stats_full(total_xp=500)
        db = _calc_db(xp_sum_val=300)
        with patch(_PATCH_STATS, return_value=stats):
            result = calculate_user_stats(db, user_id=42)
        assert result.total_xp == 500

    def test_attendance_xp_zero_does_not_update(self):
        stats = _stats_full(total_xp=100)
        db = _calc_db(xp_sum_val=None)  # scalar returns None → or 0
        with patch(_PATCH_STATS, return_value=stats):
            result = calculate_user_stats(db, user_id=42)
        assert result.total_xp == 100  # unchanged

    def test_level_formula_500_xp_gives_level_2(self):
        """level = max(1, total_xp // 500 + 1); 500 XP → level 2."""
        stats = _stats_full(total_xp=0)
        db = _calc_db(xp_sum_val=500)
        with patch(_PATCH_STATS, return_value=stats):
            result = calculate_user_stats(db, user_id=42)
        assert result.level == 2

    def test_level_stays_1_with_zero_xp(self):
        stats = _stats_full(total_xp=0)
        db = _calc_db()
        with patch(_PATCH_STATS, return_value=stats):
            result = calculate_user_stats(db, user_id=42)
        assert result.level == 1

    def test_first_semester_date_set_to_min_date(self):
        early = date(2025, 1, 1)
        late = date(2026, 3, 1)
        rows = [_booking_row(start_date=late), _booking_row(start_date=early)]
        stats = _stats_full()
        db = _calc_db(booking_rows=rows)
        with patch(_PATCH_STATS, return_value=stats):
            result = calculate_user_stats(db, user_id=42)
        assert result.first_semester_date == early

    def test_db_committed(self):
        stats = _stats_full()
        db = _calc_db()
        with patch(_PATCH_STATS, return_value=stats):
            calculate_user_stats(db, user_id=42)
        db.commit.assert_called_once()


# ── award_xp ──────────────────────────────────────────────────────────────────

class TestAwardXP:
    """award_xp — XP accumulation, level, level-up signal, user.xp_balance."""

    def _db(self, xp_balance=0, user_found=True) -> MagicMock:
        db = MagicMock()
        if user_found:
            user = SimpleNamespace(xp_balance=xp_balance)
            db.query.return_value.filter.return_value.first.return_value = user
        else:
            db.query.return_value.filter.return_value.first.return_value = None
        return db

    def test_xp_added_to_stats_total_xp(self):
        stats = _stats(total_xp=100)
        with patch(_PATCH_STATS, return_value=stats):
            result = award_xp(self._db(), user_id=42, xp_amount=50)
        assert stats.total_xp == 150
        assert result is stats

    def test_none_total_xp_treated_as_zero(self):
        stats = _stats(total_xp=0)
        stats.total_xp = None  # override to None
        with patch(_PATCH_STATS, return_value=stats):
            award_xp(self._db(), user_id=42, xp_amount=50)
        assert stats.total_xp == 50

    def test_level_calculated_via_divide_1000(self):
        """new_level = max(1, total_xp // 1000); 2000 XP → level 2."""
        stats = _stats(total_xp=1950, level=1)
        with patch(_PATCH_STATS, return_value=stats):
            award_xp(self._db(), user_id=42, xp_amount=50)
        # total_xp = 2000 → 2000 // 1000 = 2 → max(1, 2) = 2
        assert stats.level == 2

    def test_level_stays_1_below_1000(self):
        stats = _stats(total_xp=0, level=1)
        with patch(_PATCH_STATS, return_value=stats):
            award_xp(self._db(), user_id=42, xp_amount=50)
        assert stats.level == 1  # 50 // 1000 = 0 → max(1, 0) = 1

    def test_level_up_message_printed(self, capsys):
        """Level increases → print statement fires."""
        stats = _stats(total_xp=1999, level=1)
        with patch(_PATCH_STATS, return_value=stats):
            award_xp(self._db(), user_id=42, xp_amount=1)
        out = capsys.readouterr().out
        assert "leveled up" in out.lower()

    def test_no_level_up_no_print(self, capsys):
        stats = _stats(total_xp=100, level=1)
        with patch(_PATCH_STATS, return_value=stats):
            award_xp(self._db(), user_id=42, xp_amount=50)
        assert capsys.readouterr().out == ""

    def test_user_xp_balance_updated(self):
        """Atomic SQL UPDATE is issued to increment users.xp_balance."""
        stats = _stats()
        db = self._db()
        with patch(_PATCH_STATS, return_value=stats):
            award_xp(db, user_id=42, xp_amount=50)
        db.execute.assert_called_once()
        sql_params = db.execute.call_args[0][1]
        assert sql_params == {"delta": 50, "uid": 42}

    def test_none_xp_balance_treated_as_zero(self):
        """When DB RETURNING yields NULL, balance falls back to 0 (no crash)."""
        stats = _stats()
        db = self._db()
        db.execute.return_value.scalar.return_value = None  # simulate NULL from DB
        with patch(_PATCH_STATS, return_value=stats):
            award_xp(db, user_id=42, xp_amount=50)
        assert stats.total_xp == 50  # stats still updated

    def test_no_user_xp_balance_update_skipped(self):
        """User query returns None → no xp_balance crash."""
        stats = _stats()
        with patch(_PATCH_STATS, return_value=stats):
            result = award_xp(self._db(user_found=False), user_id=42, xp_amount=50)
        assert result is stats  # function still returns

    def test_db_committed(self):
        stats = _stats()
        db = self._db()
        with patch(_PATCH_STATS, return_value=stats):
            award_xp(db, user_id=42, xp_amount=10)
        db.commit.assert_called_once()

    def test_returns_stats_object(self):
        stats = _stats()
        with patch(_PATCH_STATS, return_value=stats):
            result = award_xp(self._db(), user_id=42, xp_amount=100)
        assert result is stats
