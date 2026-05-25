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
CH-12  POST /challenges/send — duplicate active challenge → error=challenge_active
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
):
    c = MagicMock(spec=VirtualTrainingChallenge)
    c.id = cid
    c.challenger_id = challenger_id
    c.challenged_id = challenged_id
    c.game_id = game_id
    c.status = status
    c.expires_at = expires_at or (datetime.now(timezone.utc) + timedelta(days=7))
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
        assert values == {"pending", "accepted", "declined", "expired", "cancelled", "completed"}

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

    def test_ch12_duplicate_active_challenge(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user = _user(uid=1)
        target = _user(uid=2)
        game = _game(code="memory_sequence")
        existing = _challenge()
        db = _db()

        call_results = [target, game]
        db.query.return_value.filter.return_value.first.side_effect = call_results

        with patch(f"{_BASE}.is_friends", return_value=True), \
             patch(f"{_BASE}.get_active_challenge", return_value=existing):
            result = _run(send_challenge(
                challenged_user_id=2, game_id=1, message=None,
                db=db, user=user,
            ))
        assert "error=challenge_active" in result.headers["location"]

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
             patch(f"{_BASE}.get_active_challenge", return_value=None), \
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
             patch(f"{_BASE}.get_active_challenge", return_value=None), \
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
             patch(f"{_BASE}.get_active_challenge", return_value=None):
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
             patch(f"{_BASE}.get_active_challenge", return_value=None), \
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
             patch(f"{_BASE}.get_active_challenge", return_value=None), \
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
