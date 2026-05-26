"""Unit tests for PR-C1 — VirtualTrainingChallenge lifecycle + PR-1 Snapshot.

CH-01  VirtualTrainingChallenge has expected columns
CH-02  ChallengeStatus has all 6 expected values
CH-03  CheckConstraint 'ck_challenge_no_self' in __table_args__
CH-04  CHALLENGE_COMPATIBLE_GAMES contains expected game codes
CH-05  make_expires_at returns created_at + 7 days
CH-06  get_active_challenge returns challenge for PENDING (both FK directions)
CH-07  POST /challenges/send — self-challenge → error=self_challenge
CH-08  POST /challenges/send — target not found → error=user_not_found
CH-09  POST /challenges/send — not friends → error=not_friends
CH-10  POST /challenges/send — game not found → error=game_not_found
CH-11  POST /challenges/send — incompatible game → error=game_not_compatible
CH-12  POST /challenges/send — category limit reached (count==3) → error=challenge_limit_reached
CH-13  POST /challenges/send — success: PENDING row created + VT_CHALLENGE_RECEIVED notification
CH-14  POST /challenges/{id}/accept — wrong challenged_id → error=not_found
CH-15  POST /challenges/{id}/accept — expired → status=EXPIRED + error=challenge_expired
CH-16  POST /challenges/{id}/accept — success: ACCEPTED + VT_CHALLENGE_ACCEPTED notification
CH-17  POST /challenges/{id}/decline — wrong challenged_id → error=not_found
CH-18  POST /challenges/{id}/decline — success: DECLINED + VT_CHALLENGE_DECLINED notification
CH-19  POST /challenges/{id}/cancel — wrong challenger_id → error=not_found
CH-20  POST /challenges/{id}/cancel — non-cancellable status → error=cannot_cancel
CH-21  POST /challenges/{id}/cancel — success from PENDING: CANCELLED + VT_CHALLENGE_CANCELLED
CH-22  POST /challenges/{id}/cancel — success from ACCEPTED: CANCELLED + VT_CHALLENGE_CANCELLED
CH-23  _trim_message: strips whitespace, empty→None, truncates at 500 chars
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.responses import RedirectResponse
from sqlalchemy import CheckConstraint

from app.models.vt_challenge import (
    CHALLENGE_COMPATIBLE_GAMES,
    ChallengeStatus,
    VirtualTrainingChallenge,
    get_active_challenge,
    make_expires_at,
)
from app.models.notification import NotificationType

_BASE = "app.api.web_routes.vt_challenges"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _user(uid=1, active=True):
    u = MagicMock()
    u.id = uid
    u.email = f"user{uid}@lfa.com"
    u.nickname = None
    u.is_active = active
    return u


def _db():
    return MagicMock()


def _game(gid=1, code="memory_sequence"):
    g = MagicMock()
    g.id = gid
    g.code = code
    return g


def _challenge(
    cid=10,
    challenger_id=1,
    challenged_id=2,
    game_id=1,
    status=ChallengeStatus.PENDING,
    expires_at=None,
    completion_window_seconds=None,
    completion_deadline=None,
    accepted_at=None,
    challenger_attempt_id=None,
    challenged_attempt_id=None,
    challenge_mode="async",
    challenger_ready_at=None,
    challenged_ready_at=None,
    live_start_at=None,
    lobby_expires_at=None,
):
    c = MagicMock(spec=VirtualTrainingChallenge)
    c.id = cid
    c.challenger_id = challenger_id
    c.challenged_id = challenged_id
    c.game_id = game_id
    c.status = status
    c.expires_at = expires_at or (datetime.now(timezone.utc) + timedelta(days=7))
    c.completion_window_seconds = completion_window_seconds
    c.completion_deadline = completion_deadline
    c.accepted_at = accepted_at
    c.challenger_attempt_id = challenger_attempt_id
    c.challenged_attempt_id = challenged_attempt_id
    c.challenge_mode = challenge_mode
    c.challenger_ready_at = challenger_ready_at
    c.challenged_ready_at = challenged_ready_at
    c.live_start_at = live_start_at
    c.lobby_expires_at = lobby_expires_at
    return c


def _run(coro):
    return asyncio.run(coro)


# ── Model & enum tests ────────────────────────────────────────────────────────

class TestVTChallengeModel:

    def test_ch01_model_columns_present(self):
        cols = {c.key for c in VirtualTrainingChallenge.__table__.columns}
        assert {
            "id", "challenger_id", "challenged_id", "game_id", "status",
            "message", "challenger_attempt_id", "challenged_attempt_id",
            "winner_id", "is_draw", "completed_at", "expires_at",
            "created_at", "updated_at",
        }.issubset(cols)

    def test_ch02_challenge_status_values(self):
        values = {e.value for e in ChallengeStatus}
        assert {"pending", "accepted", "declined", "expired", "cancelled", "completed",
                "live_lobby", "live_in_progress"}.issubset(values)

    def test_ch03_check_constraint_no_self(self):
        args = VirtualTrainingChallenge.__table_args__
        names = {c.name for c in args if isinstance(c, CheckConstraint)}
        assert "ck_challenge_no_self" in names

    def test_ch04_compatible_games_allowlist(self):
        assert "memory_sequence" in CHALLENGE_COMPATIBLE_GAMES
        assert "target_tracking" in CHALLENGE_COMPATIBLE_GAMES
        assert "color_reaction" not in CHALLENGE_COMPATIBLE_GAMES

    def test_ch05_make_expires_at_seven_days(self):
        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = make_expires_at(now)
        assert result == now + timedelta(days=7)

    def test_ch05b_make_expires_at_defaults_to_now(self):
        before = datetime.now(timezone.utc)
        result = make_expires_at()
        after = datetime.now(timezone.utc)
        assert before + timedelta(days=7) <= result <= after + timedelta(days=7)


class TestGetActiveChallenge:

    def _make_db(self, row):
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row
        return db

    def test_ch06_returns_pending_challenge(self):
        row = _challenge(challenger_id=1, challenged_id=2, status=ChallengeStatus.PENDING)
        db = self._make_db(row)
        result = get_active_challenge(db, user_a_id=1, user_b_id=2, game_id=1)
        assert result is row

    def test_ch06b_returns_none_when_no_active(self):
        db = self._make_db(None)
        result = get_active_challenge(db, user_a_id=1, user_b_id=2, game_id=1)
        assert result is None


# ── Route: send_challenge ─────────────────────────────────────────────────────

class TestSendChallenge:

    def test_ch07_self_challenge_blocked(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user = _user(uid=5)
        result = _run(send_challenge(
            challenged_user_id=5, game_id=1, message=None,
            db=_db(), user=user,
        ))
        assert isinstance(result, RedirectResponse)
        assert "error=self_challenge" in result.headers["location"]

    def test_ch08_target_not_found(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user = _user(uid=1)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = None
        result = _run(send_challenge(
            challenged_user_id=99, game_id=1, message=None,
            db=db, user=user,
        ))
        assert isinstance(result, RedirectResponse)
        assert "error=user_not_found" in result.headers["location"]

    def test_ch09_not_friends(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user = _user(uid=1)
        target = _user(uid=2)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = target

        with patch(f"{_BASE}.is_friends", return_value=False):
            result = _run(send_challenge(
                challenged_user_id=2, game_id=1, message=None,
                db=db, user=user,
            ))
        assert "error=not_friends" in result.headers["location"]

    def test_ch10_game_not_found(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user = _user(uid=1)
        target = _user(uid=2)
        db = _db()

        call_results = [target, None]
        db.query.return_value.filter.return_value.first.side_effect = call_results

        with patch(f"{_BASE}.is_friends", return_value=True):
            result = _run(send_challenge(
                challenged_user_id=2, game_id=99, message=None,
                db=db, user=user,
            ))
        assert "error=game_not_found" in result.headers["location"]

    def test_ch11_incompatible_game(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user = _user(uid=1)
        target = _user(uid=2)
        game = _game(code="color_reaction")
        db = _db()

        call_results = [target, game]
        db.query.return_value.filter.return_value.first.side_effect = call_results

        with patch(f"{_BASE}.is_friends", return_value=True):
            result = _run(send_challenge(
                challenged_user_id=2, game_id=1, message=None,
                db=db, user=user,
            ))
        assert "error=game_not_compatible" in result.headers["location"]

    def test_ch12_category_limit_reached_blocks_send(self):
        """count_active_challenges_in_category == MAX → error=challenge_limit_reached."""
        from app.api.web_routes.vt_challenges import send_challenge
        user   = _user(uid=1)
        target = _user(uid=2)
        game   = _game(code="memory_sequence")
        db     = _db()
        db.query.return_value.filter.return_value.first.side_effect = [target, game]

        with patch(f"{_BASE}.is_friends", return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category", return_value=3):
            result = _run(send_challenge(
                challenged_user_id=2, game_id=1, message=None,
                db=db, user=user,
            ))
        assert "error=challenge_limit_reached" in result.headers["location"]

    def test_ch13_success_creates_pending_and_notifies(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user = _user(uid=1)
        target = _user(uid=2)
        game = _game(code="memory_sequence")
        db = _db()

        call_results = [target, game]
        db.query.return_value.filter.return_value.first.side_effect = call_results

        _mock_snap = {"game_code": "memory_sequence", "grid_tiles": 12, "phases": []}
        with patch(f"{_BASE}.is_friends", return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category", return_value=0), \
             patch(f"{_BASE}.generate_snapshot", return_value=_mock_snap), \
             patch(f"{_BASE}.notification_service") as mock_svc:
            result = _run(send_challenge(
                challenged_user_id=2, game_id=1, message="Good luck",
                challenge_mode=None, db=db, user=user,
            ))

        assert isinstance(result, RedirectResponse)
        assert "success=challenge_sent" in result.headers["location"]
        db.add.assert_called_once()
        db.commit.assert_called_once()
        mock_svc.create_notification.assert_called_once()
        kwargs = mock_svc.create_notification.call_args.kwargs
        assert kwargs["user_id"] == 2
        assert kwargs["notification_type"] == NotificationType.VT_CHALLENGE_RECEIVED


# ── Route: accept_challenge ───────────────────────────────────────────────────

class TestAcceptChallenge:

    def test_ch14_wrong_challenged_id(self):
        from app.api.web_routes.vt_challenges import accept_challenge
        user = _user(uid=3)
        row = _challenge(challenged_id=2)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row
        result = _run(accept_challenge(challenge_id=10, db=db, user=user))
        assert "error=not_found" in result.headers["location"]

    def test_ch15_expired_marks_expired_status(self):
        from app.api.web_routes.vt_challenges import accept_challenge
        user = _user(uid=2)
        row = _challenge(
            challenged_id=2,
            status=ChallengeStatus.PENDING,
            expires_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row
        result = _run(accept_challenge(challenge_id=10, db=db, user=user))
        assert row.status == ChallengeStatus.EXPIRED
        assert "error=challenge_expired" in result.headers["location"]
        db.commit.assert_called_once()

    def test_ch16_accept_success(self):
        from app.api.web_routes.vt_challenges import accept_challenge
        user = _user(uid=2)
        row = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.PENDING,
        )
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        with patch(f"{_BASE}.notification_service") as mock_svc:
            result = _run(accept_challenge(challenge_id=10, db=db, user=user))

        assert row.status == ChallengeStatus.ACCEPTED
        assert "success=challenge_accepted" in result.headers["location"]
        db.commit.assert_called_once()
        mock_svc.create_notification.assert_called_once()
        kwargs = mock_svc.create_notification.call_args.kwargs
        assert kwargs["user_id"] == 1  # sent to challenger
        assert kwargs["notification_type"] == NotificationType.VT_CHALLENGE_ACCEPTED


# ── Route: decline_challenge ──────────────────────────────────────────────────

class TestDeclineChallenge:

    def test_ch17_wrong_challenged_id(self):
        from app.api.web_routes.vt_challenges import decline_challenge
        user = _user(uid=3)
        row = _challenge(challenged_id=2)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row
        result = _run(decline_challenge(challenge_id=10, db=db, user=user))
        assert "error=not_found" in result.headers["location"]

    def test_ch18_decline_success(self):
        from app.api.web_routes.vt_challenges import decline_challenge
        user = _user(uid=2)
        row = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.PENDING,
        )
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        with patch(f"{_BASE}.notification_service") as mock_svc:
            result = _run(decline_challenge(challenge_id=10, db=db, user=user))

        assert row.status == ChallengeStatus.DECLINED
        assert "success=challenge_declined" in result.headers["location"]
        db.commit.assert_called_once()
        mock_svc.create_notification.assert_called_once()
        kwargs = mock_svc.create_notification.call_args.kwargs
        assert kwargs["user_id"] == 1  # sent to challenger
        assert kwargs["notification_type"] == NotificationType.VT_CHALLENGE_DECLINED


# ── Route: cancel_challenge ───────────────────────────────────────────────────

class TestCancelChallenge:

    def test_ch19_wrong_challenger_id(self):
        from app.api.web_routes.vt_challenges import cancel_challenge
        user = _user(uid=3)
        row = _challenge(challenger_id=1)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row
        result = _run(cancel_challenge(challenge_id=10, db=db, user=user))
        assert "error=not_found" in result.headers["location"]

    def test_ch20_non_cancellable_status(self):
        from app.api.web_routes.vt_challenges import cancel_challenge
        user = _user(uid=1)
        row = _challenge(challenger_id=1, status=ChallengeStatus.COMPLETED)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row
        result = _run(cancel_challenge(challenge_id=10, db=db, user=user))
        assert "error=cannot_cancel" in result.headers["location"]

    def test_ch21_cancel_from_pending(self):
        from app.api.web_routes.vt_challenges import cancel_challenge
        user = _user(uid=1)
        row = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.PENDING,
        )
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        with patch(f"{_BASE}.notification_service") as mock_svc:
            result = _run(cancel_challenge(challenge_id=10, db=db, user=user))

        assert row.status == ChallengeStatus.CANCELLED
        assert "success=challenge_cancelled" in result.headers["location"]
        db.commit.assert_called_once()
        mock_svc.create_notification.assert_called_once()
        kwargs = mock_svc.create_notification.call_args.kwargs
        assert kwargs["user_id"] == 2  # sent to challenged
        assert kwargs["notification_type"] == NotificationType.VT_CHALLENGE_CANCELLED

    def test_ch22_cancel_from_accepted(self):
        from app.api.web_routes.vt_challenges import cancel_challenge
        user = _user(uid=1)
        row = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.ACCEPTED,
        )
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        with patch(f"{_BASE}.notification_service") as mock_svc:
            result = _run(cancel_challenge(challenge_id=10, db=db, user=user))

        assert row.status == ChallengeStatus.CANCELLED
        assert "success=challenge_cancelled" in result.headers["location"]


# ── _trim_message ─────────────────────────────────────────────────────────────

class TestTrimMessage:

    def test_ch23_strips_whitespace(self):
        from app.api.web_routes.vt_challenges import _trim_message
        assert _trim_message("  hello  ") == "hello"

    def test_ch23b_empty_returns_none(self):
        from app.api.web_routes.vt_challenges import _trim_message
        assert _trim_message("") is None
        assert _trim_message("   ") is None
        assert _trim_message(None) is None

    def test_ch23c_truncates_at_500(self):
        from app.api.web_routes.vt_challenges import _trim_message
        long_msg = "x" * 600
        result = _trim_message(long_msg)
        assert result == "x" * 500


# ── PR-1 Snapshot + Mode tests ────────────────────────────────────────────────

_MS_GAME_CONFIG = {
    "grid_rows": 3,
    "grid_cols": 4,
    "phases": [
        {"phase": 0, "sequence_length": 3, "rounds": 3,
         "show_ms_per_item": 800, "isi_ms": 500, "recall_window_ms": 8000},
        {"phase": 1, "sequence_length": 5, "rounds": 3,
         "show_ms_per_item": 650, "isi_ms": 400, "recall_window_ms": 13000},
        {"phase": 2, "sequence_length": 7, "rounds": 3,
         "show_ms_per_item": 500, "isi_ms": 300, "recall_window_ms": 18000},
    ],
}

_TT_GAME_CONFIG = {
    "difficulties": {
        "easy": {
            "phases": [
                {"phase": 0, "rounds": 3, "object_count": 3, "object_speed": 1.00,
                 "highlight_ms": 1500, "tracking_ms": 4000, "window_ms": 3000,
                 "distractor_flash": 0},
            ],
            "difficulty_multiplier": 1.00,
        },
    },
}

_MOCK_MS_SNAPSHOT = {
    "game_code": "memory_sequence",
    "grid_tiles": 12,
    "phases": [
        {"phase": 1, "sequence_length": 3,
         "rounds": [{"round": 1, "sequence": [0, 5, 11]},
                    {"round": 2, "sequence": [2, 7, 3]},
                    {"round": 3, "sequence": [9, 1, 6]}]},
    ],
}

_MOCK_TT_SNAPSHOT = {
    "game_code": "target_tracking",
    "difficulty": "easy",
    "arena": {"width": 480, "height": 360},
    "phases": [
        {"phase": 1, "object_count": 3,
         "rounds": [{"round": 1, "target_index": 1,
                     "initial_positions": [{"x": 100, "y": 80},
                                           {"x": 250, "y": 200},
                                           {"x": 400, "y": 300}],
                     "initial_angles": [0.785, 2.094, 4.712]}]},
    ],
}


def _game_with_config(code="memory_sequence", config=None):
    g = MagicMock()
    g.id = 1
    g.code = code
    g.config = config or _MS_GAME_CONFIG
    return g


class TestSendChallengeSnapshot:
    """MODE-01..04 and FAIR-SEND-01..03"""

    def _send(self, game_code="memory_sequence", game_config=None,
              challenge_mode=None, snapshot=None, difficulty_level=None):
        from app.api.web_routes.vt_challenges import send_challenge
        user   = _user(uid=1)
        target = _user(uid=2)
        game   = _game_with_config(code=game_code, config=game_config or _MS_GAME_CONFIG)
        db     = _db()
        db.query.return_value.filter.return_value.first.side_effect = [target, game]

        snap = snapshot or _MOCK_MS_SNAPSHOT
        with patch(f"{_BASE}.is_friends", return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category", return_value=0), \
             patch(f"{_BASE}.generate_snapshot", return_value=snap), \
             patch(f"{_BASE}.notification_service"):
            result = _run(send_challenge(
                challenged_user_id=2,
                game_id=1,
                message=None,
                difficulty_level=difficulty_level,
                challenge_mode=challenge_mode,
                db=db,
                user=user,
            ))
        return result, db

    # MODE-01 ──────────────────────────────────────────────────────────────────

    def test_mode01_default_challenge_mode_is_async(self):
        result, db = self._send(challenge_mode=None)
        assert "success=challenge_sent" in result.headers["location"]
        added = db.add.call_args[0][0]
        assert added.challenge_mode == "async"

    # MODE-02 ──────────────────────────────────────────────────────────────────

    def test_mode02_explicit_async_stored(self):
        result, db = self._send(challenge_mode="async")
        assert "success=challenge_sent" in result.headers["location"]
        added = db.add.call_args[0][0]
        assert added.challenge_mode == "async"

    # MODE-03 ──────────────────────────────────────────────────────────────────

    def test_mode03_live_mode_stored(self):
        result, db = self._send(challenge_mode="live")
        assert "success=challenge_sent" in result.headers["location"]
        added = db.add.call_args[0][0]
        assert added.challenge_mode == "live"

    # MODE-04 ──────────────────────────────────────────────────────────────────

    def test_mode04_invalid_challenge_mode_redirects(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user   = _user(uid=1)
        target = _user(uid=2)
        game   = _game_with_config(code="memory_sequence")
        db     = _db()
        db.query.return_value.filter.return_value.first.side_effect = [target, game]

        with patch(f"{_BASE}.is_friends", return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category", return_value=0):
            result = _run(send_challenge(
                challenged_user_id=2, game_id=1, message=None,
                challenge_mode="invalid_mode",
                db=db, user=user,
            ))
        assert isinstance(result, RedirectResponse)
        assert "error=invalid_challenge_mode" in result.headers["location"]
        db.add.assert_not_called()

    # FAIR-SEND-01 ─────────────────────────────────────────────────────────────

    def test_fair_send01_ms_challenge_has_snapshot(self):
        result, db = self._send(
            game_code="memory_sequence",
            snapshot=_MOCK_MS_SNAPSHOT,
        )
        assert "success=challenge_sent" in result.headers["location"]
        added = db.add.call_args[0][0]
        assert added.challenge_config_snapshot is not None
        assert added.challenge_config_snapshot["game_code"] == "memory_sequence"

    # FAIR-SEND-02 ─────────────────────────────────────────────────────────────

    def test_fair_send02_tt_challenge_has_snapshot(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user   = _user(uid=1)
        target = _user(uid=2)
        game   = _game_with_config(code="target_tracking", config=_TT_GAME_CONFIG)
        db     = _db()
        db.query.return_value.filter.return_value.first.side_effect = [target, game]

        with patch(f"{_BASE}.is_friends", return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category", return_value=0), \
             patch(f"{_BASE}.VirtualTrainingService") as mock_vts, \
             patch(f"{_BASE}.generate_snapshot", return_value=_MOCK_TT_SNAPSHOT), \
             patch(f"{_BASE}.notification_service"):
            mock_vts.is_expert_unlocked.return_value = False
            result = _run(send_challenge(
                challenged_user_id=2, game_id=1, message=None,
                difficulty_level="easy",
                challenge_mode=None,
                db=db, user=user,
            ))
        assert "success=challenge_sent" in result.headers["location"]
        added = db.add.call_args[0][0]
        assert added.challenge_config_snapshot is not None
        assert added.challenge_config_snapshot["game_code"] == "target_tracking"

    # FAIR-SEND-03 ─────────────────────────────────────────────────────────────

    def test_fair_send03_snapshot_failure_no_db_row(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user   = _user(uid=1)
        target = _user(uid=2)
        game   = _game_with_config(code="memory_sequence")
        db     = _db()
        db.query.return_value.filter.return_value.first.side_effect = [target, game]

        with patch(f"{_BASE}.is_friends", return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category", return_value=0), \
             patch(f"{_BASE}.generate_snapshot",
                   side_effect=ValueError("boom")):
            result = _run(send_challenge(
                challenged_user_id=2, game_id=1, message=None,
                challenge_mode=None,
                db=db, user=user,
            ))
        assert isinstance(result, RedirectResponse)
        assert "error=snapshot_generation_failed" in result.headers["location"]
        db.add.assert_not_called()
        db.commit.assert_not_called()


class TestModelSnapshotColumns:
    """PR-1: new columns exist on the model and check constraint is present."""

    def test_challenge_mode_column_exists(self):
        cols = {c.key for c in VirtualTrainingChallenge.__table__.columns}
        assert "challenge_mode" in cols

    def test_challenge_config_snapshot_column_exists(self):
        cols = {c.key for c in VirtualTrainingChallenge.__table__.columns}
        assert "challenge_config_snapshot" in cols

    def test_check_constraint_mode_valid_present(self):
        args = VirtualTrainingChallenge.__table_args__
        names = {c.name for c in args if isinstance(c, CheckConstraint)}
        assert "ck_vt_challenge_mode_valid" in names


# ── PR-P1: Completion window / deadline / forfeit ─────────────────────────────

class TestCompletionWindowModel:
    """ASYNC-01..03: model helpers."""

    def test_async01_new_columns_present(self):
        cols = {c.key for c in VirtualTrainingChallenge.__table__.columns}
        assert {"accepted_at", "completion_window_seconds",
                "completion_deadline", "forfeit_user_id",
                "forfeit_reason"}.issubset(cols)

    def test_async01b_forfeit_reason_check_constraint_present(self):
        args = VirtualTrainingChallenge.__table_args__
        names = {c.name for c in args if isinstance(c, CheckConstraint)}
        assert "ck_vt_forfeit_reason_valid" in names

    def test_async02_valid_completion_windows_has_five_values(self):
        from app.models.vt_challenge import VALID_COMPLETION_WINDOWS
        assert len(VALID_COMPLETION_WINDOWS) == 5
        assert {1800, 3600, 86400, 259200, 604800} == VALID_COMPLETION_WINDOWS

    def test_async03a_validate_completion_window_accepts_all_valid(self):
        from app.models.vt_challenge import validate_completion_window, VALID_COMPLETION_WINDOWS
        for v in VALID_COMPLETION_WINDOWS:
            assert validate_completion_window(v) == v

    def test_async03b_validate_completion_window_rejects_invalid(self):
        from app.models.vt_challenge import validate_completion_window
        import pytest
        with pytest.raises(ValueError):
            validate_completion_window(9999)

    def test_async03c_make_completion_deadline_correct(self):
        from app.models.vt_challenge import make_completion_deadline
        t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        result = make_completion_deadline(t0, 86400)
        assert result == t0 + timedelta(seconds=86400)


class TestSendChallengeCompletionWindow:
    """ASYNC-04, ASYNC-05, ASYNC-15: send_challenge completion window handling."""

    def _send(self, completion_window_seconds=None):
        from app.api.web_routes.vt_challenges import send_challenge
        user   = _user(uid=1)
        target = _user(uid=2)
        game   = _game(code="memory_sequence")
        db     = _db()
        db.query.return_value.filter.return_value.first.side_effect = [target, game]

        _snap = {"game_code": "memory_sequence", "grid_tiles": 12, "phases": []}
        with patch(f"{_BASE}.is_friends", return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category", return_value=0), \
             patch(f"{_BASE}.generate_snapshot", return_value=_snap), \
             patch(f"{_BASE}.notification_service"):
            result = _run(send_challenge(
                challenged_user_id=2,
                game_id=1,
                message=None,
                completion_window_seconds=completion_window_seconds,
                db=db,
                user=user,
            ))
        return result, db

    def test_async04_default_window_stored_when_none(self):
        result, db = self._send(completion_window_seconds=None)
        assert "success=challenge_sent" in result.headers["location"]
        added = db.add.call_args[0][0]
        assert added.completion_window_seconds == 86400

    def test_async05_explicit_valid_window_stored(self):
        result, db = self._send(completion_window_seconds=3600)
        assert "success=challenge_sent" in result.headers["location"]
        added = db.add.call_args[0][0]
        assert added.completion_window_seconds == 3600

    def test_async15_invalid_window_redirects_with_error(self):
        result, db = self._send(completion_window_seconds=9999)
        assert isinstance(result, RedirectResponse)
        assert "error=invalid_completion_window" in result.headers["location"]
        db.add.assert_not_called()


class TestAcceptChallengeDeadline:
    """ASYNC-10: accept_challenge sets accepted_at + completion_deadline."""

    def test_async10_accept_sets_accepted_at_and_deadline(self):
        from app.api.web_routes.vt_challenges import accept_challenge
        user = _user(uid=2)
        row = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.PENDING,
            completion_window_seconds=86400,
        )
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        with patch(f"{_BASE}.notification_service"):
            _run(accept_challenge(challenge_id=10, db=db, user=user))

        assert row.accepted_at is not None
        assert row.completion_deadline is not None
        delta = row.completion_deadline - row.accepted_at
        assert abs(delta.total_seconds() - 86400) < 2

    def test_async10b_null_window_no_deadline_set(self):
        from app.api.web_routes.vt_challenges import accept_challenge
        user = _user(uid=2)
        row = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.PENDING,
            completion_window_seconds=None,
        )
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        with patch(f"{_BASE}.notification_service"):
            _run(accept_challenge(challenge_id=10, db=db, user=user))

        # completion_deadline should remain None since window is None
        assert row.completion_deadline is None


# ── LIVE-* — Live Lobby lifecycle ─────────────────────────────────────────────

class TestLiveLobbyModel:
    """LIVE-01..03: model columns + constants."""

    def test_live01_new_status_values(self):
        values = {e.value for e in ChallengeStatus}
        assert "live_lobby"       in values
        assert "live_in_progress" in values

    def test_live02_new_lobby_columns_present(self):
        from app.models.vt_challenge import LOBBY_TIMEOUT_SECONDS, POST_START_SUBMIT_WINDOW_SECONDS
        cols = {c.key for c in VirtualTrainingChallenge.__table__.columns}
        assert {"challenger_ready_at", "challenged_ready_at",
                "live_start_at", "lobby_expires_at"}.issubset(cols)
        assert LOBBY_TIMEOUT_SECONDS == 900
        assert POST_START_SUBMIT_WINDOW_SECONDS == 300

    def test_live03_forfeit_check_constraint_covers_live_reasons(self):
        from sqlalchemy import CheckConstraint
        args = VirtualTrainingChallenge.__table_args__
        ck = next((c for c in args if isinstance(c, CheckConstraint)
                   and c.name == "ck_vt_forfeit_reason_valid"), None)
        assert ck is not None
        expr = str(ck.sqltext)
        assert "no_show"            in expr
        assert "post_start_timeout" in expr


class TestAcceptLiveChallenge:
    """LIVE-04..05: accept live challenge → LIVE_LOBBY redirect."""

    def test_live04_accept_live_challenge_sets_live_lobby(self):
        from app.api.web_routes.vt_challenges import accept_challenge
        user = _user(uid=2)
        row = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.PENDING,
            challenge_mode="live",
        )
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        with patch(f"{_BASE}.notification_service"):
            resp = _run(accept_challenge(challenge_id=10, db=db, user=user))

        assert row.status == ChallengeStatus.LIVE_LOBBY
        assert row.lobby_expires_at is not None
        assert "/challenges/10/lobby" in resp.headers.get("location", "")

    def test_live05_accept_live_sets_no_deadline(self):
        from app.api.web_routes.vt_challenges import accept_challenge
        user = _user(uid=2)
        row = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.PENDING,
            challenge_mode="live",
            completion_window_seconds=3600,
        )
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        with patch(f"{_BASE}.notification_service"):
            _run(accept_challenge(challenge_id=10, db=db, user=user))

        # Live challenges must NOT set completion_deadline
        assert row.completion_deadline is None


class TestLobbyRoute:
    """LIVE-06..08: GET /challenges/{id}/lobby."""

    def test_live06_lobby_wrong_participant_redirects(self):
        from app.api.web_routes.vt_challenges import challenge_lobby
        request = MagicMock()
        request.query_params.get.return_value = None
        user = _user(uid=99)  # not a participant
        row = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.LIVE_LOBBY,
        )
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        with patch(f"{_BASE}.require_student_onboarding", return_value=None):
            resp = _run(challenge_lobby(challenge_id=10, request=request, db=db, user=user))

        assert resp.status_code == 303
        assert "error=not_found" in resp.headers["location"]

    def test_live07_lobby_wrong_status_redirects(self):
        from app.api.web_routes.vt_challenges import challenge_lobby
        request = MagicMock()
        request.query_params.get.return_value = None
        user = _user(uid=1)
        row = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.ACCEPTED,  # async, not live
        )
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        with patch(f"{_BASE}.require_student_onboarding", return_value=None):
            resp = _run(challenge_lobby(challenge_id=10, request=request, db=db, user=user))

        assert resp.status_code == 303
        assert "error=not_live" in resp.headers["location"]

    def test_live08_lobby_expired_lobby_redirects(self):
        from app.api.web_routes.vt_challenges import challenge_lobby
        from datetime import timedelta
        request = MagicMock()
        request.query_params.get.return_value = None
        user = _user(uid=1)
        row = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.LIVE_LOBBY,
            lobby_expires_at=datetime.now(timezone.utc) - timedelta(seconds=10),
        )
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_BASE}.apply_lobby_timeout_if_expired", return_value=True), \
             patch(f"{_BASE}.apply_post_start_timeout_if_expired", return_value=False):
            resp = _run(challenge_lobby(challenge_id=10, request=request, db=db, user=user))

        assert resp.status_code == 303
        assert "lobby_expired" in resp.headers["location"]


class TestReadyRoute:
    """LIVE-09..11: POST /challenges/{id}/ready."""

    def test_live09_ready_wrong_participant(self):
        from app.api.web_routes.vt_challenges import challenge_ready
        user = _user(uid=99)
        row = _challenge(challenger_id=1, challenged_id=2, status=ChallengeStatus.LIVE_LOBBY)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        resp = _run(challenge_ready(challenge_id=10, db=db, user=user))
        assert resp.status_code == 404

    def test_live10_ready_not_in_lobby(self):
        from app.api.web_routes.vt_challenges import challenge_ready
        user = _user(uid=1)
        row = _challenge(challenger_id=1, challenged_id=2, status=ChallengeStatus.ACCEPTED)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        resp = _run(challenge_ready(challenge_id=10, db=db, user=user))
        assert resp.status_code == 409

    def test_live11_ready_success_calls_set_ready(self):
        from app.api.web_routes.vt_challenges import challenge_ready
        user = _user(uid=1)
        row = _challenge(challenger_id=1, challenged_id=2, status=ChallengeStatus.LIVE_LOBBY)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = row

        fake_state = {"status": "live_lobby", "challenger_ready": True,
                      "challenged_ready": False, "live_start_at": None,
                      "lobby_expires_at": None, "post_start_deadline": None,
                      "server_now": "2026-05-25T12:00:00+00:00"}

        with patch(f"{_BASE}.set_ready", return_value=fake_state) as mock_ready:
            resp = _run(challenge_ready(challenge_id=10, db=db, user=user))

        mock_ready.assert_called_once()
        import json
        body = json.loads(resp.body)
        assert body["challenger_ready"] is True


class TestLobbyStatePollRoute:
    """LIVE-12: GET /challenges/{id}/lobby-state."""

    def test_live12_lobby_state_returns_json(self):
        from app.api.web_routes.vt_challenges import challenge_lobby_state
        from app.models.virtual_training import VirtualTrainingGame as VTGame
        user = _user(uid=1)
        row = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.LIVE_LOBBY,
            lobby_expires_at=datetime.now(timezone.utc) + timedelta(seconds=300),
        )
        game = _game(gid=1, code="memory_sequence")
        db = _db()

        def _qry(model):
            m = MagicMock()
            if model is VTGame:
                m.filter.return_value.first.return_value = game
            else:
                m.filter.return_value.first.return_value = row
            return m
        db.query.side_effect = _qry

        fake_state = {"status": "live_lobby", "challenger_ready": False,
                      "challenged_ready": False, "live_start_at": None,
                      "lobby_expires_at": None, "post_start_deadline": None,
                      "server_now": "2026-05-25T12:00:00+00:00"}

        with patch(f"{_BASE}.apply_lobby_timeout_if_expired", return_value=False), \
             patch(f"{_BASE}.apply_post_start_timeout_if_expired", return_value=False), \
             patch(f"{_BASE}.get_lobby_state", return_value=fake_state):
            resp = _run(challenge_lobby_state(challenge_id=10, db=db, user=user))

        import json
        body = json.loads(resp.body)
        assert body["status"] == "live_lobby"
        assert "game_url" in body


class TestInboxLiveOutcomes:
    """LIVE-13..15: _build_inbox_row live outcomes."""

    def test_live13_live_lobby_outcome(self):
        from app.api.web_routes.vt_challenges import _build_inbox_row
        ch = _challenge(status=ChallengeStatus.LIVE_LOBBY, challenge_mode="live")
        row = _build_inbox_row(ch, 1, {}, {}, {})
        assert row["outcome"] == "live_lobby"
        assert row["status"]  == "live_lobby"

    def test_live14_live_in_progress_play_now(self):
        from app.api.web_routes.vt_challenges import _build_inbox_row
        ch = _challenge(
            status=ChallengeStatus.LIVE_IN_PROGRESS,
            challenge_mode="live",
            challenger_attempt_id=None,  # challenger has not played yet
        )
        row = _build_inbox_row(ch, 1, {}, {}, {})  # user_id=1 is challenger
        assert row["outcome"] == "live_play_now"

    def test_live15_live_in_progress_waiting(self):
        from app.api.web_routes.vt_challenges import _build_inbox_row
        ch = _challenge(
            status=ChallengeStatus.LIVE_IN_PROGRESS,
            challenge_mode="live",
            challenger_attempt_id=99,  # challenger has played
            challenged_attempt_id=None,
        )
        row = _build_inbox_row(ch, 1, {99: MagicMock(score_normalized=0.8)}, {}, {})
        assert row["outcome"] == "live_waiting_for_opponent"


# ── LIVE-BUG-01..07 ───────────────────────────────────────────────────────────

class TestLiveBugFixes:
    """Regression tests for the ready→countdown→game-start bug fix.

    Root cause: lobby template fetch POST omitted X-CSRF-Token header →
    CSRF middleware blocked all POST /ready requests with 403 →
    challenger_ready_at / challenged_ready_at never written →
    status never transitioned to LIVE_IN_PROGRESS.

    LIVE-BUG-01  both ready → status LIVE_IN_PROGRESS (service level)
    LIVE-BUG-02  both ready → live_start_at not None (service level)
    LIVE-BUG-03  lobby-state after both ready → status=live_in_progress + game_url present
    LIVE-BUG-04  lobby template JS contains live_in_progress branch (static analysis)
    LIVE-BUG-05  lobby-state status string is lowercase 'live_in_progress' (frontend match)
    LIVE-BUG-06  game_url for memory_sequence is correct
    LIVE-BUG-07  game_url for target_tracking includes difficulty
    """

    # ── LIVE-BUG-01 / 02 ──────────────────────────────────────────────────────

    def test_live_bug01_both_ready_status_live_in_progress(self):
        from app.services.live_lobby_service import set_ready
        from app.models.vt_challenge import VirtualTrainingChallenge
        from unittest.mock import MagicMock, patch

        _NOW = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
        _PAST = _NOW - timedelta(minutes=5)

        c = MagicMock(spec=VirtualTrainingChallenge)
        c.id = 10
        c.status = ChallengeStatus.LIVE_LOBBY
        c.challenger_id = 1
        c.challenged_id = 2
        c.challenger_ready_at = _PAST   # challenger already ready
        c.challenged_ready_at = None
        c.live_start_at = None
        c.lobby_expires_at = _NOW + timedelta(minutes=10)
        c.challenger_attempt_id = None
        c.challenged_attempt_id = None
        c.winner_id = None
        c.is_draw = False
        c.forfeit_user_id = None
        c.forfeit_reason = None
        c.completed_at = None
        c.updated_at = None

        with patch("app.services.live_lobby_service.notification_service"):
            set_ready(MagicMock(), c, 2, _NOW)   # challenged marks ready

        assert c.status == ChallengeStatus.LIVE_IN_PROGRESS

    def test_live_bug02_both_ready_live_start_at_set(self):
        from app.services.live_lobby_service import set_ready
        from app.models.vt_challenge import VirtualTrainingChallenge
        from unittest.mock import MagicMock, patch

        _NOW = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
        _PAST = _NOW - timedelta(minutes=5)

        c = MagicMock(spec=VirtualTrainingChallenge)
        c.id = 10
        c.status = ChallengeStatus.LIVE_LOBBY
        c.challenger_id = 1
        c.challenged_id = 2
        c.challenger_ready_at = _PAST
        c.challenged_ready_at = None
        c.live_start_at = None
        c.lobby_expires_at = _NOW + timedelta(minutes=10)
        c.challenger_attempt_id = None
        c.challenged_attempt_id = None
        c.winner_id = None
        c.is_draw = False
        c.forfeit_user_id = None
        c.forfeit_reason = None
        c.completed_at = None
        c.updated_at = None

        with patch("app.services.live_lobby_service.notification_service"):
            set_ready(MagicMock(), c, 2, _NOW)

        assert c.live_start_at == _NOW

    # ── LIVE-BUG-03 ───────────────────────────────────────────────────────────

    def test_live_bug03_lobby_state_both_ready_game_url_present(self):
        from app.api.web_routes.vt_challenges import challenge_lobby_state
        from app.models.virtual_training import VirtualTrainingGame as VTGame

        _NOW = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)

        user = _user(uid=1)
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.LIVE_IN_PROGRESS,
            live_start_at=_NOW,
        )
        game = _game(gid=1, code="memory_sequence")
        db = _db()

        def _qry(model):
            m = MagicMock()
            if model is VTGame:
                m.filter.return_value.first.return_value = game
            else:
                m.filter.return_value.first.return_value = ch
            return m
        db.query.side_effect = _qry

        in_progress_state = {
            "status": "live_in_progress",
            "challenger_ready": True, "challenged_ready": True,
            "live_start_at": _NOW.isoformat(),
            "lobby_expires_at": None, "post_start_deadline": None,
            "server_now": _NOW.isoformat(),
        }

        with patch(f"{_BASE}.apply_lobby_timeout_if_expired", return_value=False), \
             patch(f"{_BASE}.apply_post_start_timeout_if_expired", return_value=False), \
             patch(f"{_BASE}.get_lobby_state", return_value=in_progress_state):
            resp = _run(challenge_lobby_state(challenge_id=10, db=db, user=user))

        import json
        body = json.loads(resp.body)
        assert body["status"] == "live_in_progress"
        assert "game_url" in body
        assert body["game_url"]  # not empty / None

    # ── LIVE-BUG-04 ───────────────────────────────────────────────────────────

    def test_live_bug04_lobby_template_js_has_live_in_progress_branch(self):
        template_path = "app/templates/vt_challenge_lobby.html"
        with open(template_path) as f:
            src = f.read()
        assert "status === 'live_in_progress'" in src, (
            "lobby JS must have live_in_progress branch in applyState()"
        )
        assert "getCsrf" in src, (
            "lobby JS must have getCsrf() helper for X-CSRF-Token on POST /ready"
        )
        assert "X-CSRF-Token" in src, (
            "lobby JS fetch POST must include X-CSRF-Token header"
        )

    # ── LIVE-BUG-05 ───────────────────────────────────────────────────────────

    def test_live_bug05_lobby_state_status_string_is_lowercase(self):
        from app.services.live_lobby_service import get_lobby_state
        from app.models.vt_challenge import VirtualTrainingChallenge

        _NOW = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)

        c = MagicMock(spec=VirtualTrainingChallenge)
        c.id = 10
        c.status = ChallengeStatus.LIVE_IN_PROGRESS
        c.challenger_id = 1
        c.challenged_id = 2
        c.challenger_ready_at = _NOW - timedelta(seconds=10)
        c.challenged_ready_at = _NOW - timedelta(seconds=8)
        c.live_start_at = _NOW
        c.lobby_expires_at = None
        c.challenger_attempt_id = None
        c.challenged_attempt_id = None
        c.winner_id = None
        c.is_draw = False
        c.forfeit_user_id = None
        c.forfeit_reason = None
        c.completed_at = None
        c.updated_at = None

        state = get_lobby_state(c, _NOW)
        # Frontend checks: status === 'live_in_progress' (lowercase, no underscores)
        assert state["status"] == "live_in_progress"
        assert state["challenger_ready"] is True
        assert state["challenged_ready"] is True

    # ── LIVE-BUG-06 / 07 ──────────────────────────────────────────────────────

    def test_live_bug06_game_url_memory_sequence(self):
        from app.api.web_routes.vt_challenges import challenge_lobby_state
        from app.models.virtual_training import VirtualTrainingGame as VTGame

        user = _user(uid=1)
        ch = _challenge(challenger_id=1, challenged_id=2,
                        status=ChallengeStatus.LIVE_LOBBY)
        game = _game(gid=1, code="memory_sequence")
        db = _db()

        def _qry(model):
            m = MagicMock()
            if model is VTGame:
                m.filter.return_value.first.return_value = game
            else:
                m.filter.return_value.first.return_value = ch
            return m
        db.query.side_effect = _qry

        with patch(f"{_BASE}.apply_lobby_timeout_if_expired", return_value=False), \
             patch(f"{_BASE}.apply_post_start_timeout_if_expired", return_value=False), \
             patch(f"{_BASE}.get_lobby_state", return_value={"status": "live_lobby",
                   "challenger_ready": False, "challenged_ready": False,
                   "live_start_at": None, "lobby_expires_at": None,
                   "post_start_deadline": None, "server_now": ""}):
            resp = _run(challenge_lobby_state(challenge_id=10, db=db, user=user))

        import json
        body = json.loads(resp.body)
        assert body["game_url"] == f"/virtual-training/memory-sequence?challenge_id={ch.id}"

    def test_live_bug07_game_url_target_tracking_includes_difficulty(self):
        from app.api.web_routes.vt_challenges import challenge_lobby_state
        from app.models.virtual_training import VirtualTrainingGame as VTGame

        user = _user(uid=1)
        ch = _challenge(challenger_id=1, challenged_id=2,
                        status=ChallengeStatus.LIVE_LOBBY)
        ch.difficulty_level = "medium"
        game = _game(gid=2, code="target_tracking")
        db = _db()

        def _qry(model):
            m = MagicMock()
            if model is VTGame:
                m.filter.return_value.first.return_value = game
            else:
                m.filter.return_value.first.return_value = ch
            return m
        db.query.side_effect = _qry

        with patch(f"{_BASE}.apply_lobby_timeout_if_expired", return_value=False), \
             patch(f"{_BASE}.apply_post_start_timeout_if_expired", return_value=False), \
             patch(f"{_BASE}.get_lobby_state", return_value={"status": "live_lobby",
                   "challenger_ready": False, "challenged_ready": False,
                   "live_start_at": None, "lobby_expires_at": None,
                   "post_start_deadline": None, "server_now": ""}):
            resp = _run(challenge_lobby_state(challenge_id=10, db=db, user=user))

        import json
        body = json.loads(resp.body)
        expected = f"/virtual-training/target-tracking?challenge_id={ch.id}&difficulty=medium"
        assert body["game_url"] == expected


# ── CH-24/CH-25: category-level limit boundary tests ─────────────────────────

class TestCategoryLimitBoundary:
    """
    CH-24  count < MAX_ACTIVE_PER_CATEGORY → send succeeds (not blocked)
    CH-25  count in other category does NOT affect this category (isolated limit)
    """

    def _send(self, db, user, target, game, count_return):
        from app.api.web_routes.vt_challenges import send_challenge
        _snap = {"game_code": "memory_sequence", "grid_tiles": 12, "phases": []}
        with patch(f"{_BASE}.is_friends", return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category", return_value=count_return), \
             patch(f"{_BASE}.generate_snapshot", return_value=_snap), \
             patch(f"{_BASE}.notification_service"):
            return _run(send_challenge(
                challenged_user_id=target.id,
                game_id=1,
                message=None,
                db=db,
                user=user,
            ))

    def test_ch24_below_limit_allows_send(self):
        """2 active in category < 3 → challenge is created."""
        user   = _user(uid=1)
        target = _user(uid=2)
        game   = _game(code="memory_sequence")
        db     = _db()
        db.query.return_value.filter.return_value.first.side_effect = [target, game]

        result = self._send(db, user, target, game, count_return=2)
        assert "success=challenge_sent" in result.headers["location"]
        db.add.assert_called_once()

    def test_ch25_category_limit_is_isolated_per_game_type(self):
        """count_active_challenges_in_category is called with game.game_type,
        not a cross-category count.  If tracking category has 3 active challenges
        but memory_span has 0, a new memory_sequence challenge must still succeed.
        """
        from app.api.web_routes.vt_challenges import send_challenge
        from app.models.vt_challenge import MAX_ACTIVE_PER_CATEGORY

        user   = _user(uid=1)
        target = _user(uid=2)
        game   = _game(code="memory_sequence")
        db     = _db()
        db.query.return_value.filter.return_value.first.side_effect = [target, game]

        _snap = {"game_code": "memory_sequence", "grid_tiles": 12, "phases": []}

        captured_game_type = {}

        def _count_mock(db, uid_a, uid_b, game_type):
            captured_game_type["value"] = game_type
            # Simulate: tracking has 3 active challenges, memory_span has 0
            return MAX_ACTIVE_PER_CATEGORY if game_type == "tracking" else 0

        with patch(f"{_BASE}.is_friends", return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category", side_effect=_count_mock), \
             patch(f"{_BASE}.generate_snapshot", return_value=_snap), \
             patch(f"{_BASE}.notification_service"):
            result = _run(send_challenge(
                challenged_user_id=2, game_id=1, message=None,
                db=db, user=user,
            ))

        # memory_sequence → game_type should be passed, not "tracking"
        assert captured_game_type.get("value") == game.game_type
        # Since memory_span count == 0 < 3, challenge succeeds
        assert "success=challenge_sent" in result.headers["location"]
