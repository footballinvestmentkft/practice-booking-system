"""Unit tests for challenge_completion_service — PR-P1 async deadline + forfeit logic.

ASYNC-07  apply_forfeit_if_deadline_passed — non-ACCEPTED status → no-op, returns False
ASYNC-08  apply_forfeit_if_deadline_passed — deadline in future → no-op, returns False
ASYNC-09  apply_forfeit_if_deadline_passed — challenger played, challenged didn't → forfeit win (challenger)
ASYNC-12  apply_forfeit_if_deadline_passed — challenged played, challenger didn't → forfeit win (challenged)
ASYNC-14  sweep_accepted_deadlines — applies to past-deadline challenges, returns count, flushes once
ASYNC-16  apply_forfeit_if_deadline_passed — NULL completion_deadline → no-op, returns False
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.models.vt_challenge import ChallengeStatus, VirtualTrainingChallenge

_BASE_SVC = "app.services.challenge_completion_service"


# ── helpers ────────────────────────────────────────────────────────────────────

def _ch(
    status=ChallengeStatus.ACCEPTED,
    completion_deadline=None,
    challenger_attempt_id=None,
    challenged_attempt_id=None,
    challenger_id=1,
    challenged_id=2,
):
    c = MagicMock(spec=VirtualTrainingChallenge)
    c.status = status
    c.completion_deadline = completion_deadline
    c.challenger_attempt_id = challenger_attempt_id
    c.challenged_attempt_id = challenged_attempt_id
    c.challenger_id = challenger_id
    c.challenged_id = challenged_id
    c.winner_id = None
    c.is_draw = False
    c.forfeit_user_id = None
    c.forfeit_reason = None
    return c


def _db():
    return MagicMock()


_PAST = datetime(2020, 1, 1, tzinfo=timezone.utc)
_NOW  = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
_FUTURE = datetime(2099, 1, 1, tzinfo=timezone.utc)


# ── ASYNC-07 ───────────────────────────────────────────────────────────────────

class TestApplyForfeitNoOps:

    def test_async07_non_accepted_status_returns_false(self):
        from app.services.challenge_completion_service import apply_forfeit_if_deadline_passed
        for st in (ChallengeStatus.PENDING, ChallengeStatus.COMPLETED,
                   ChallengeStatus.EXPIRED, ChallengeStatus.DECLINED,
                   ChallengeStatus.CANCELLED):
            c = _ch(status=st, completion_deadline=_PAST)
            result = apply_forfeit_if_deadline_passed(_db(), c, _NOW)
            assert result is False, f"Expected False for status={st}"
            assert c.status == st  # unchanged

    # ASYNC-08
    def test_async08_deadline_in_future_returns_false(self):
        from app.services.challenge_completion_service import apply_forfeit_if_deadline_passed
        c = _ch(completion_deadline=_FUTURE)
        result = apply_forfeit_if_deadline_passed(_db(), c, _NOW)
        assert result is False
        assert c.status == ChallengeStatus.ACCEPTED

    # ASYNC-16
    def test_async16_null_deadline_returns_false(self):
        from app.services.challenge_completion_service import apply_forfeit_if_deadline_passed
        c = _ch(completion_deadline=None)
        result = apply_forfeit_if_deadline_passed(_db(), c, _NOW)
        assert result is False
        assert c.status == ChallengeStatus.ACCEPTED


# ── ASYNC-09 ───────────────────────────────────────────────────────────────────

class TestApplyForfeitChallenger:

    def test_async09_challenger_played_challenged_forfeits(self):
        from app.services.challenge_completion_service import apply_forfeit_if_deadline_passed
        c = _ch(
            completion_deadline=_PAST,
            challenger_attempt_id=99,   # challenger played
            challenged_attempt_id=None, # challenged did not
        )
        db = _db()
        with patch(f"{_BASE_SVC}.notification_service"):
            result = apply_forfeit_if_deadline_passed(db, c, _NOW)

        assert result is True
        assert c.status == ChallengeStatus.COMPLETED
        assert c.winner_id == 1          # challenger_id
        assert c.forfeit_user_id == 2    # challenged_id
        assert c.forfeit_reason == "deadline_expired"
        assert c.is_draw is False
        assert c.completed_at == _NOW
        assert c.updated_at == _NOW


# ── ASYNC-12 ───────────────────────────────────────────────────────────────────

class TestApplyForfeitChallenged:

    def test_async12_challenged_played_challenger_forfeits(self):
        from app.services.challenge_completion_service import apply_forfeit_if_deadline_passed
        c = _ch(
            completion_deadline=_PAST,
            challenger_attempt_id=None, # challenger did not play
            challenged_attempt_id=88,   # challenged played
        )
        db = _db()
        with patch(f"{_BASE_SVC}.notification_service"):
            result = apply_forfeit_if_deadline_passed(db, c, _NOW)

        assert result is True
        assert c.status == ChallengeStatus.COMPLETED
        assert c.winner_id == 2          # challenged_id
        assert c.forfeit_user_id == 1    # challenger_id
        assert c.forfeit_reason == "deadline_expired"
        assert c.is_draw is False

    def test_async12b_neither_played_no_contest(self):
        from app.services.challenge_completion_service import apply_forfeit_if_deadline_passed
        c = _ch(
            completion_deadline=_PAST,
            challenger_attempt_id=None,
            challenged_attempt_id=None,
        )
        db = _db()
        with patch(f"{_BASE_SVC}.notification_service"):
            result = apply_forfeit_if_deadline_passed(db, c, _NOW)

        assert result is True
        assert c.status == ChallengeStatus.EXPIRED
        assert c.forfeit_reason == "no_contest"
        assert c.updated_at == _NOW


# ── ASYNC-14 ───────────────────────────────────────────────────────────────────

class TestSweepAcceptedDeadlines:

    def test_async14_sweep_returns_count_and_flushes_once(self):
        from app.services.challenge_completion_service import sweep_accepted_deadlines
        db = _db()

        # 2 applied + 1 no-op
        with patch(f"{_BASE_SVC}.apply_forfeit_if_deadline_passed",
                   side_effect=[True, True, False]) as mock_apply:
            count = sweep_accepted_deadlines(db, ["ch1", "ch2", "ch3"])

        assert count == 2
        assert mock_apply.call_count == 3
        db.flush.assert_called_once()

    def test_async14b_sweep_no_changes_does_not_flush(self):
        from app.services.challenge_completion_service import sweep_accepted_deadlines
        db = _db()

        with patch(f"{_BASE_SVC}.apply_forfeit_if_deadline_passed",
                   return_value=False):
            count = sweep_accepted_deadlines(db, ["ch1", "ch2"])

        assert count == 0
        db.flush.assert_not_called()

    def test_async14c_empty_list_returns_zero(self):
        from app.services.challenge_completion_service import sweep_accepted_deadlines
        db = _db()
        count = sweep_accepted_deadlines(db, [])
        assert count == 0
        db.flush.assert_not_called()
