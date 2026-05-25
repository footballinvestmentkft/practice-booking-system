"""Unit tests for live_lobby_service — PR-L1 live lobby + ready-state logic.

LIVE-S01  set_ready — non-lobby status → no-op, state unchanged
LIVE-S02  set_ready — challenger marks ready, not both → LIVE_LOBBY remains
LIVE-S03  set_ready — both ready → LIVE_IN_PROGRESS + live_start_at set
LIVE-S04  set_ready — already ready, idempotent re-POST → no duplicate timestamp
LIVE-S05  apply_lobby_timeout_if_expired — non-LIVE_LOBBY status → False
LIVE-S06  apply_lobby_timeout_if_expired — expires_at in future → False
LIVE-S07  apply_lobby_timeout_if_expired — expires_at passed → EXPIRED + no_show
LIVE-S08  apply_lobby_timeout_if_expired — NULL lobby_expires_at → False
LIVE-S09  apply_post_start_timeout_if_expired — non-LIVE_IN_PROGRESS status → False
LIVE-S10  apply_post_start_timeout_if_expired — within window → False
LIVE-S11  apply_post_start_timeout_if_expired — challenger submitted, other didn't → forfeit win
LIVE-S12  apply_post_start_timeout_if_expired — challenged submitted, other didn't → forfeit win
LIVE-S13  apply_post_start_timeout_if_expired — neither submitted → EXPIRED no_contest
LIVE-S14  sweep_live_challenges — applies lobby + post-start, counts, flushes once
LIVE-S15  get_lobby_state — returns expected keys
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.models.vt_challenge import ChallengeStatus, VirtualTrainingChallenge, POST_START_SUBMIT_WINDOW_SECONDS

_BASE_SVC = "app.services.live_lobby_service"

_NOW  = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
_PAST = _NOW - timedelta(minutes=30)
_FUT  = _NOW + timedelta(minutes=30)


def _ch(
    status=ChallengeStatus.LIVE_LOBBY,
    challenger_id=1,
    challenged_id=2,
    challenger_ready_at=None,
    challenged_ready_at=None,
    live_start_at=None,
    lobby_expires_at=None,
    challenger_attempt_id=None,
    challenged_attempt_id=None,
):
    c = MagicMock(spec=VirtualTrainingChallenge)
    c.id = 10
    c.status = status
    c.challenger_id = challenger_id
    c.challenged_id = challenged_id
    c.challenger_ready_at = challenger_ready_at
    c.challenged_ready_at = challenged_ready_at
    c.live_start_at = live_start_at
    c.lobby_expires_at = lobby_expires_at
    c.challenger_attempt_id = challenger_attempt_id
    c.challenged_attempt_id = challenged_attempt_id
    c.winner_id = None
    c.is_draw = False
    c.forfeit_user_id = None
    c.forfeit_reason = None
    c.completed_at = None
    c.updated_at = None
    return c


def _db():
    return MagicMock()


# ── LIVE-S01 ───────────────────────────────────────────────────────────────────

class TestSetReadyNoOp:

    def test_live_s01_non_lobby_status_noop(self):
        from app.services.live_lobby_service import set_ready
        for st in (ChallengeStatus.ACCEPTED, ChallengeStatus.LIVE_IN_PROGRESS,
                   ChallengeStatus.COMPLETED, ChallengeStatus.EXPIRED):
            c = _ch(status=st)
            original_cr = c.challenger_ready_at
            set_ready(_db(), c, 1, _NOW)
            assert c.challenger_ready_at == original_cr, f"Should not set ready for {st}"


# ── LIVE-S02 ───────────────────────────────────────────────────────────────────

class TestSetReadyPartial:

    def test_live_s02_challenger_ready_not_both(self):
        from app.services.live_lobby_service import set_ready
        c = _ch()
        with patch(f"{_BASE_SVC}.notification_service"):
            state = set_ready(_db(), c, 1, _NOW)

        assert c.challenger_ready_at == _NOW
        assert c.challenged_ready_at is None
        assert c.status == ChallengeStatus.LIVE_LOBBY
        assert state["challenger_ready"] is True
        assert state["challenged_ready"] is False
        assert state["status"] == "live_lobby"


# ── LIVE-S03 ───────────────────────────────────────────────────────────────────

class TestSetReadyBoth:

    def test_live_s03_both_ready_transitions_to_in_progress(self):
        from app.services.live_lobby_service import set_ready
        c = _ch(challenger_ready_at=_PAST)  # challenger already ready
        with patch(f"{_BASE_SVC}.notification_service"):
            state = set_ready(_db(), c, 2, _NOW)  # challenged marks ready

        assert c.status == ChallengeStatus.LIVE_IN_PROGRESS
        assert c.live_start_at == _NOW
        assert state["status"] == "live_in_progress"
        assert state["live_start_at"] is not None


# ── LIVE-S04 ───────────────────────────────────────────────────────────────────

class TestSetReadyIdempotent:

    def test_live_s04_already_ready_no_timestamp_overwrite(self):
        from app.services.live_lobby_service import set_ready
        c = _ch(challenger_ready_at=_PAST)  # challenger was already ready
        with patch(f"{_BASE_SVC}.notification_service"):
            set_ready(_db(), c, 1, _NOW)  # re-post ready

        assert c.challenger_ready_at == _PAST  # unchanged


# ── LIVE-S05..S08 ─────────────────────────────────────────────────────────────

class TestLobbyTimeout:

    def test_live_s05_non_lobby_status_returns_false(self):
        from app.services.live_lobby_service import apply_lobby_timeout_if_expired
        for st in (ChallengeStatus.ACCEPTED, ChallengeStatus.LIVE_IN_PROGRESS,
                   ChallengeStatus.COMPLETED):
            c = _ch(status=st, lobby_expires_at=_PAST)
            result = apply_lobby_timeout_if_expired(_db(), c, _NOW)
            assert result is False

    def test_live_s06_expires_in_future_returns_false(self):
        from app.services.live_lobby_service import apply_lobby_timeout_if_expired
        c = _ch(lobby_expires_at=_FUT)
        result = apply_lobby_timeout_if_expired(_db(), c, _NOW)
        assert result is False

    def test_live_s07_expired_lobby_sets_expired_no_show(self):
        from app.services.live_lobby_service import apply_lobby_timeout_if_expired
        c = _ch(lobby_expires_at=_PAST)
        with patch(f"{_BASE_SVC}.notification_service"):
            result = apply_lobby_timeout_if_expired(_db(), c, _NOW)

        assert result is True
        assert c.status == ChallengeStatus.EXPIRED
        assert c.forfeit_reason == "no_show"
        assert c.updated_at == _NOW

    def test_live_s08_null_lobby_expires_at_returns_false(self):
        from app.services.live_lobby_service import apply_lobby_timeout_if_expired
        c = _ch(lobby_expires_at=None)
        result = apply_lobby_timeout_if_expired(_db(), c, _NOW)
        assert result is False


# ── LIVE-S09..S13 ─────────────────────────────────────────────────────────────

class TestPostStartTimeout:

    def test_live_s09_non_in_progress_status_returns_false(self):
        from app.services.live_lobby_service import apply_post_start_timeout_if_expired
        c = _ch(status=ChallengeStatus.LIVE_LOBBY,
                live_start_at=_PAST - timedelta(seconds=POST_START_SUBMIT_WINDOW_SECONDS + 10))
        result = apply_post_start_timeout_if_expired(_db(), c, _NOW)
        assert result is False

    def test_live_s10_within_window_returns_false(self):
        from app.services.live_lobby_service import apply_post_start_timeout_if_expired
        recent_start = _NOW - timedelta(seconds=10)
        c = _ch(status=ChallengeStatus.LIVE_IN_PROGRESS, live_start_at=recent_start)
        result = apply_post_start_timeout_if_expired(_db(), c, _NOW)
        assert result is False

    def test_live_s11_challenger_submitted_challenged_didnt_forfeit(self):
        from app.services.live_lobby_service import apply_post_start_timeout_if_expired
        old_start = _NOW - timedelta(seconds=POST_START_SUBMIT_WINDOW_SECONDS + 60)
        c = _ch(status=ChallengeStatus.LIVE_IN_PROGRESS, live_start_at=old_start,
                challenger_attempt_id=99, challenged_attempt_id=None)
        with patch(f"{_BASE_SVC}.notification_service"):
            result = apply_post_start_timeout_if_expired(_db(), c, _NOW)

        assert result is True
        assert c.status == ChallengeStatus.COMPLETED
        assert c.winner_id == 1          # challenger
        assert c.forfeit_user_id == 2    # challenged
        assert c.forfeit_reason == "post_start_timeout"

    def test_live_s12_challenged_submitted_challenger_didnt_forfeit(self):
        from app.services.live_lobby_service import apply_post_start_timeout_if_expired
        old_start = _NOW - timedelta(seconds=POST_START_SUBMIT_WINDOW_SECONDS + 60)
        c = _ch(status=ChallengeStatus.LIVE_IN_PROGRESS, live_start_at=old_start,
                challenger_attempt_id=None, challenged_attempt_id=88)
        with patch(f"{_BASE_SVC}.notification_service"):
            result = apply_post_start_timeout_if_expired(_db(), c, _NOW)

        assert result is True
        assert c.winner_id == 2          # challenged
        assert c.forfeit_user_id == 1    # challenger

    def test_live_s13_neither_submitted_no_contest(self):
        from app.services.live_lobby_service import apply_post_start_timeout_if_expired
        old_start = _NOW - timedelta(seconds=POST_START_SUBMIT_WINDOW_SECONDS + 60)
        c = _ch(status=ChallengeStatus.LIVE_IN_PROGRESS, live_start_at=old_start,
                challenger_attempt_id=None, challenged_attempt_id=None)
        with patch(f"{_BASE_SVC}.notification_service"):
            result = apply_post_start_timeout_if_expired(_db(), c, _NOW)

        assert result is True
        assert c.status == ChallengeStatus.EXPIRED
        assert c.forfeit_reason == "no_contest"


# ── LIVE-S14 ───────────────────────────────────────────────────────────────────

class TestSweepLiveChallenges:

    def test_live_s14_sweep_counts_and_flushes(self):
        from app.services.live_lobby_service import sweep_live_challenges
        db = _db()

        with patch(f"{_BASE_SVC}.apply_lobby_timeout_if_expired",
                   side_effect=[True, False]) as mock_lt, \
             patch(f"{_BASE_SVC}.apply_post_start_timeout_if_expired",
                   side_effect=[True]) as mock_ps:
            count = sweep_live_challenges(db, ["ch1", "ch2"])

        assert count == 2
        db.flush.assert_called_once()

    def test_live_s14b_sweep_no_changes_no_flush(self):
        from app.services.live_lobby_service import sweep_live_challenges
        db = _db()

        with patch(f"{_BASE_SVC}.apply_lobby_timeout_if_expired", return_value=False), \
             patch(f"{_BASE_SVC}.apply_post_start_timeout_if_expired", return_value=False):
            count = sweep_live_challenges(db, ["ch1"])

        assert count == 0
        db.flush.assert_not_called()


# ── LIVE-S15 ───────────────────────────────────────────────────────────────────

class TestGetLobbyState:

    def test_live_s15_state_dict_keys(self):
        from app.services.live_lobby_service import get_lobby_state
        c = _ch(challenger_ready_at=_PAST, challenged_ready_at=None)
        state = get_lobby_state(c, _NOW)

        assert "status"             in state
        assert "challenger_ready"   in state
        assert "challenged_ready"   in state
        assert "live_start_at"      in state
        assert "lobby_expires_at"   in state
        assert "post_start_deadline" in state
        assert "server_now"         in state

        assert state["challenger_ready"] is True
        assert state["challenged_ready"] is False
        assert state["status"]           == "live_lobby"
