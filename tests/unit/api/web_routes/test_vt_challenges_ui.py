"""Unit tests for PR-C3 — VT Challenge UI + inbox + end-to-end flow.

UI-01  VirtualTrainingChallenge model has difficulty_level column
UI-02  _build_inbox_row: pending received → outcome=received, play_url correct (MS)
UI-03  _build_inbox_row: pending sent → outcome=sent
UI-04  _build_inbox_row: accepted + no my attempt → outcome=play_now, MS play_url
UI-05  _build_inbox_row: accepted + my attempt + no opp attempt → outcome=waiting_for_opponent
UI-06  _build_inbox_row: completed won → outcome=won, scores populated
UI-07  _build_inbox_row: completed lost → outcome=lost
UI-08  _build_inbox_row: completed draw → outcome=draw, is_draw=True
UI-09  _build_inbox_row: TT challenge → play_url includes difficulty
UI-10  _build_inbox_row: unknown game code → play_url=/virtual-training
UI-11  _build_challenge_result_ctx: challenge not found → None
UI-12  _build_challenge_result_ctx: user not participant → None
UI-13  _build_challenge_result_ctx: attempt_id mismatch → None
UI-14  _build_challenge_result_ctx: accepted + no opp attempt → waiting_for_opponent
UI-15  _build_challenge_result_ctx: accepted + opp attempt set → waiting_for_resolution
UI-16  _build_challenge_result_ctx: completed won (challenger side)
UI-17  _build_challenge_result_ctx: completed lost (challenged side)
UI-18  _build_challenge_result_ctx: completed draw
UI-19  POST /challenges/send: TT + valid difficulty → difficulty_level stored
UI-20  POST /challenges/send: TT + invalid difficulty → error=invalid_difficulty
UI-21  POST /challenges/send: TT + expert + not unlocked → error=expert_locked
UI-22  POST /challenges/send: MS game → difficulty_level=NULL stored
UI-23  GET /challenges: renders inbox template (200)
UI-24  GET /challenges/send: renders send form (200, friends + games in context)
UI-25  Notification links: all POST actions redirect to /challenges
UI-26  _accepted_friends: returns User objects for both FK directions
UI-27  WS publish: accept_challenge → challenge_accepted to both challenger + challenged
UI-28  WS publish: send_challenge → challenge_sent to both sender + recipient
UI-29  WS publish: decline_challenge → challenge_declined published
UI-30  WS publish: cancel_challenge → challenge_cancelled published
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, call, patch

import pytest

from app.models.vt_challenge import (
    CHALLENGE_COMPATIBLE_GAMES,
    ChallengeStatus,
    VirtualTrainingChallenge,
)

_BASE  = "app.api.web_routes.vt_challenges"
_VT    = "app.api.web_routes.virtual_training"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _user(uid=1, email=None, nick=None, active=True):
    u = MagicMock()
    u.id       = uid
    u.email    = email or f"user{uid}@lfa.com"
    u.nickname = nick
    u.is_active = active
    return u


def _game(gid=1, code="memory_sequence", name="Memory Sequence"):
    g = MagicMock()
    g.id   = gid
    g.code = code
    g.name = name
    return g


def _attempt(aid=100, score=75.0):
    a = MagicMock()
    a.id               = aid
    a.score_normalized = score
    return a


def _challenge(
    cid=10,
    challenger_id=1,
    challenged_id=2,
    game_id=1,
    status=ChallengeStatus.PENDING,
    expires_at=None,
    challenger_attempt_id=None,
    challenged_attempt_id=None,
    winner_id=None,
    is_draw=False,
    difficulty_level=None,
    completed_at=None,
    message=None,
    created_at=None,
    forfeit_user_id=None,
    forfeit_reason=None,
    completion_deadline=None,
):
    c = MagicMock(spec=VirtualTrainingChallenge)
    c.id                    = cid
    c.challenger_id         = challenger_id
    c.challenged_id         = challenged_id
    c.game_id               = game_id
    c.status                = status
    c.expires_at            = expires_at or (datetime.now(timezone.utc) + timedelta(days=7))
    c.challenger_attempt_id = challenger_attempt_id
    c.challenged_attempt_id = challenged_attempt_id
    c.winner_id             = winner_id
    c.is_draw               = is_draw
    c.difficulty_level      = difficulty_level
    c.completed_at          = completed_at
    c.message               = message
    c.created_at            = created_at or datetime.now(timezone.utc)
    c.forfeit_user_id       = forfeit_user_id
    c.forfeit_reason        = forfeit_reason
    c.completion_deadline   = completion_deadline
    return c


def _db():
    return MagicMock()


def _run(coro):
    return asyncio.run(coro)


# ── UI-01: Model column ────────────────────────────────────────────────────────

class TestModelColumn:

    def test_ui01_difficulty_level_column_exists(self):
        cols = {c.key for c in VirtualTrainingChallenge.__table__.columns}
        assert "difficulty_level" in cols


# ── UI-02..UI-10: _build_inbox_row ────────────────────────────────────────────

class TestBuildInboxRow:

    def _row(self, ch, user_id, my_score=None, opp_score=None, game=None, opp=None):
        from app.api.web_routes.vt_challenges import _build_inbox_row

        my_a  = _attempt(score=my_score)  if my_score  is not None else None
        opp_a = _attempt(score=opp_score) if opp_score is not None else None

        a_map = {}
        if ch.challenger_attempt_id and my_score is not None:
            a_map[ch.challenger_attempt_id] = my_a
        if ch.challenged_attempt_id and opp_score is not None:
            a_map[ch.challenged_attempt_id] = opp_a

        g = game or _game(gid=ch.game_id)
        opp_user = opp or _user(uid=2)

        u_map = {user_id: _user(uid=user_id), opp_user.id: opp_user}
        g_map = {g.id: g}

        return _build_inbox_row(ch, user_id, a_map, u_map, g_map)

    def test_ui02_pending_received_ms(self):
        ch = _challenge(challenger_id=2, challenged_id=1, status=ChallengeStatus.PENDING)
        row = self._row(ch, user_id=1)
        assert row["outcome"]       == "received"
        assert row["is_challenger"] is False
        # play_url pre-computed (used after accepting, not shown for pending)
        assert "memory-sequence" in row["play_url"]
        assert "challenge_id=10"  in row["play_url"]

    def test_ui03_pending_sent(self):
        ch = _challenge(challenger_id=1, challenged_id=2, status=ChallengeStatus.PENDING)
        row = self._row(ch, user_id=1)
        assert row["outcome"] == "sent"
        assert row["is_challenger"] is True

    def test_ui04_accepted_no_my_attempt_ms(self):
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.ACCEPTED,
            challenger_attempt_id=None,
            challenged_attempt_id=None,
        )
        row = self._row(ch, user_id=1)
        assert row["outcome"] == "play_now"
        assert "memory-sequence" in row["play_url"]
        assert "challenge_id=10" in row["play_url"]

    def test_ui05_accepted_my_attempt_no_opp(self):
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.ACCEPTED,
            challenger_attempt_id=100,
            challenged_attempt_id=None,
        )
        row = self._row(ch, user_id=1, my_score=80.0)
        assert row["outcome"] == "waiting_for_opponent"

    def test_ui06_completed_won(self):
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.COMPLETED,
            challenger_attempt_id=100,
            challenged_attempt_id=200,
            winner_id=1,
            is_draw=False,
        )
        from app.api.web_routes.vt_challenges import _build_inbox_row

        my_a  = _attempt(aid=100, score=90.0)
        opp_a = _attempt(aid=200, score=70.0)
        a_map = {100: my_a, 200: opp_a}
        u_map = {1: _user(uid=1), 2: _user(uid=2)}
        g_map = {1: _game()}
        row = _build_inbox_row(ch, 1, a_map, u_map, g_map)
        assert row["outcome"]  == "won"
        assert row["my_score"] == 90.0
        assert row["opp_score"] == 70.0

    def test_ui07_completed_lost(self):
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.COMPLETED,
            challenger_attempt_id=100,
            challenged_attempt_id=200,
            winner_id=2,
            is_draw=False,
        )
        from app.api.web_routes.vt_challenges import _build_inbox_row
        my_a  = _attempt(aid=100, score=60.0)
        opp_a = _attempt(aid=200, score=85.0)
        a_map = {100: my_a, 200: opp_a}
        u_map = {1: _user(uid=1), 2: _user(uid=2)}
        g_map = {1: _game()}
        row = _build_inbox_row(ch, 1, a_map, u_map, g_map)
        assert row["outcome"] == "lost"

    def test_ui08_completed_draw(self):
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.COMPLETED,
            challenger_attempt_id=100,
            challenged_attempt_id=200,
            winner_id=None,
            is_draw=True,
        )
        from app.api.web_routes.vt_challenges import _build_inbox_row
        a_map = {100: _attempt(aid=100, score=75.0), 200: _attempt(aid=200, score=75.0)}
        u_map = {1: _user(uid=1), 2: _user(uid=2)}
        g_map = {1: _game()}
        row = _build_inbox_row(ch, 1, a_map, u_map, g_map)
        assert row["outcome"] == "draw"

    def test_ui09_tt_play_url_includes_difficulty(self):
        ch = _challenge(
            game_id=2,
            status=ChallengeStatus.ACCEPTED,
            challenger_attempt_id=None,
            difficulty_level="hard",
        )
        tt_game = _game(gid=2, code="target_tracking", name="Target Tracking")
        row = self._row(ch, user_id=1, game=tt_game)
        assert row["outcome"] == "play_now"
        assert "target-tracking" in row["play_url"]
        assert "challenge_id=10" in row["play_url"]
        assert "difficulty=hard"  in row["play_url"]

    def test_ui10_unknown_game_code_fallback_url(self):
        ch = _challenge(game_id=9, status=ChallengeStatus.ACCEPTED)
        unknown_game = _game(gid=9, code="unknown_game", name="Unknown")
        row = self._row(ch, user_id=1, game=unknown_game)
        assert row["play_url"] == "/virtual-training"


# ── UI-11..UI-18: _build_challenge_result_ctx ─────────────────────────────────

class TestBuildChallengeResultCtx:

    def _ctx(self, ch, user_id, attempt_id, opp_user=None, my_attempt=None, opp_attempt=None):
        from app.api.web_routes.virtual_training import _build_challenge_result_ctx

        db = _db()

        def _query_side_effect(model):
            q = MagicMock()
            store = {"ch": ch, "opp": opp_user, "my_a": my_attempt, "opp_a": opp_attempt}

            def _filter(*_a, **_kw):
                inner = MagicMock()
                # Determine which entity by inspecting call order — use a queue
                if not hasattr(_filter, "_calls"):
                    _filter._calls = []
                return inner

            q.filter.return_value.first.side_effect = [
                opp_user,
                my_attempt,
                opp_attempt,
            ]
            return q

        # Patch internal db queries via a single mock chain
        db.query.return_value.filter.return_value.first.side_effect = [
            ch,            # VirtualTrainingChallenge lookup
            opp_user,      # opponent User lookup
            my_attempt,    # my attempt lookup (only when COMPLETED)
            opp_attempt,   # opp attempt lookup (only when COMPLETED)
        ]

        return _build_challenge_result_ctx(db, ch.id if ch else 99, user_id, attempt_id)

    def test_ui11_challenge_not_found_returns_none(self):
        from app.api.web_routes.virtual_training import _build_challenge_result_ctx
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = None
        result = _build_challenge_result_ctx(db, challenge_id=99, user_id=1, attempt_id=100)
        assert result is None

    def test_ui12_user_not_participant_returns_none(self):
        from app.api.web_routes.virtual_training import _build_challenge_result_ctx
        ch = _challenge(challenger_id=3, challenged_id=4)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = ch
        result = _build_challenge_result_ctx(db, challenge_id=10, user_id=1, attempt_id=100)
        assert result is None

    def test_ui13_attempt_id_mismatch_returns_none(self):
        from app.api.web_routes.virtual_training import _build_challenge_result_ctx
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.ACCEPTED,
            challenger_attempt_id=999,   # different from attempt_id
        )
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = ch
        result = _build_challenge_result_ctx(db, challenge_id=10, user_id=1, attempt_id=100)
        assert result is None

    def test_ui14_accepted_no_opp_attempt_waiting(self):
        from app.api.web_routes.virtual_training import _build_challenge_result_ctx
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.ACCEPTED,
            challenger_attempt_id=100,
            challenged_attempt_id=None,
        )
        opp = _user(uid=2, nick="Bob")
        db = _db()
        db.query.return_value.filter.return_value.first.side_effect = [ch, opp]
        result = _build_challenge_result_ctx(db, challenge_id=10, user_id=1, attempt_id=100)
        assert result is not None
        assert result["outcome"] == "waiting_for_opponent"
        assert result["opponent_name"] == "Bob"

    def test_ui15_accepted_both_attempts_waiting_resolution(self):
        from app.api.web_routes.virtual_training import _build_challenge_result_ctx
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.ACCEPTED,
            challenger_attempt_id=100,
            challenged_attempt_id=200,
        )
        opp = _user(uid=2)
        db = _db()
        db.query.return_value.filter.return_value.first.side_effect = [ch, opp]
        result = _build_challenge_result_ctx(db, challenge_id=10, user_id=1, attempt_id=100)
        assert result is not None
        assert result["outcome"] == "waiting_for_resolution"

    def test_ui16_completed_won_challenger_side(self):
        from app.api.web_routes.virtual_training import _build_challenge_result_ctx
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.COMPLETED,
            challenger_attempt_id=100,
            challenged_attempt_id=200,
            winner_id=1,
            is_draw=False,
        )
        opp      = _user(uid=2, nick="Opp")
        my_att   = _attempt(aid=100, score=90.0)
        opp_att  = _attempt(aid=200, score=60.0)
        db = _db()
        db.query.return_value.filter.return_value.first.side_effect = [
            ch, opp, my_att, opp_att
        ]
        result = _build_challenge_result_ctx(db, challenge_id=10, user_id=1, attempt_id=100)
        assert result["outcome"]    == "won"
        assert result["my_score"]   == 90.0
        assert result["opp_score"]  == 60.0

    def test_ui17_completed_lost_challenged_side(self):
        from app.api.web_routes.virtual_training import _build_challenge_result_ctx
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.COMPLETED,
            challenger_attempt_id=100,
            challenged_attempt_id=200,
            winner_id=1,
            is_draw=False,
        )
        opp     = _user(uid=1)
        my_att  = _attempt(aid=200, score=60.0)
        opp_att = _attempt(aid=100, score=90.0)
        db = _db()
        db.query.return_value.filter.return_value.first.side_effect = [
            ch, opp, my_att, opp_att
        ]
        result = _build_challenge_result_ctx(db, challenge_id=10, user_id=2, attempt_id=200)
        assert result["outcome"] == "lost"

    def test_ui18_completed_draw(self):
        from app.api.web_routes.virtual_training import _build_challenge_result_ctx
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.COMPLETED,
            challenger_attempt_id=100,
            challenged_attempt_id=200,
            winner_id=None,
            is_draw=True,
        )
        opp     = _user(uid=2)
        my_att  = _attempt(aid=100, score=75.0)
        opp_att = _attempt(aid=200, score=75.0)
        db = _db()
        db.query.return_value.filter.return_value.first.side_effect = [
            ch, opp, my_att, opp_att
        ]
        result = _build_challenge_result_ctx(db, challenge_id=10, user_id=1, attempt_id=100)
        assert result["outcome"] == "draw"


# ── UI-19..UI-22: POST /challenges/send difficulty guards ─────────────────────

class TestSendChallengeDifficulty:

    def _target_and_game(self, db, target_uid=2, game_code="target_tracking"):
        target = _user(uid=target_uid)
        game   = _game(gid=2, code=game_code)
        db.query.return_value.filter.return_value.first.side_effect = [target, game, None]
        return target, game

    def test_ui19_tt_valid_difficulty_stored(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user = _user(uid=1)
        db   = _db()
        target = _user(uid=2)
        game   = _game(gid=2, code="target_tracking")
        db.query.return_value.filter.return_value.first.side_effect = [target, game, None]

        created_challenge = None

        def _capture_add(obj):
            nonlocal created_challenge
            created_challenge = obj
        db.add.side_effect = _capture_add

        _mock_snap = {"game_code": "target_tracking", "difficulty": "hard",
                      "arena": {"width": 480, "height": 360}, "phases": []}
        with patch(f"{_BASE}.is_friends",        return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category", return_value=0), \
             patch(f"{_BASE}.VirtualTrainingService.is_expert_unlocked", return_value=False), \
             patch(f"{_BASE}.generate_snapshot", return_value=_mock_snap), \
             patch(f"{_BASE}.notification_service.create_notification"), \
             patch(f"{_BASE}.VirtualTrainingChallenge") as MockCh:
            MockCh.return_value = MagicMock()
            result = _run(send_challenge(
                challenged_user_id=2, game_id=2, message=None,
                difficulty_level="hard",
                db=db, user=user,
            ))

        assert "error" not in result.headers["location"]
        # Verify VirtualTrainingChallenge was called with difficulty_level="hard"
        call_kwargs = MockCh.call_args[1] if MockCh.call_args else {}
        assert call_kwargs.get("difficulty_level") == "hard"

    def test_ui20_tt_invalid_difficulty_rejected(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user = _user(uid=1)
        db   = _db()
        target = _user(uid=2)
        game   = _game(gid=2, code="target_tracking")
        db.query.return_value.filter.return_value.first.side_effect = [target, game, None]

        with patch(f"{_BASE}.is_friends",        return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category", return_value=0):
            result = _run(send_challenge(
                challenged_user_id=2, game_id=2, message=None,
                difficulty_level="legendary",
                db=db, user=user,
            ))
        assert "error=invalid_difficulty" in result.headers["location"]

    def test_ui21_tt_expert_not_unlocked_rejected(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user = _user(uid=1)
        db   = _db()
        target = _user(uid=2)
        game   = _game(gid=2, code="target_tracking")
        db.query.return_value.filter.return_value.first.side_effect = [target, game, None]

        with patch(f"{_BASE}.is_friends",                                    return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category",           return_value=0), \
             patch(f"{_BASE}.VirtualTrainingService.is_expert_unlocked",     return_value=False):
            result = _run(send_challenge(
                challenged_user_id=2, game_id=2, message=None,
                difficulty_level="expert",
                db=db, user=user,
            ))
        assert "error=expert_locked" in result.headers["location"]

    def test_ui22_ms_game_no_difficulty_stored(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user = _user(uid=1)
        db   = _db()
        target = _user(uid=2)
        game   = _game(gid=1, code="memory_sequence")
        db.query.return_value.filter.return_value.first.side_effect = [target, game, None]

        _mock_snap = {"game_code": "memory_sequence", "grid_tiles": 12, "phases": []}
        with patch(f"{_BASE}.is_friends",                              return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category",   return_value=0), \
             patch(f"{_BASE}.generate_snapshot", return_value=_mock_snap), \
             patch(f"{_BASE}.notification_service.create_notification"), \
             patch(f"{_BASE}.VirtualTrainingChallenge") as MockCh:
            MockCh.return_value = MagicMock()
            result = _run(send_challenge(
                challenged_user_id=2, game_id=1, message=None,
                difficulty_level=None,
                db=db, user=user,
            ))

        call_kwargs = MockCh.call_args[1] if MockCh.call_args else {}
        assert call_kwargs.get("difficulty_level") is None


# ── UI-23: GET /challenges inbox ──────────────────────────────────────────────

class TestChallengeInbox:

    def test_ui23_inbox_renders_200(self):
        from app.api.web_routes.vt_challenges import challenge_inbox
        user = _user(uid=1)
        db   = _db()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        request = MagicMock()
        request.query_params.get.return_value = None

        with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_BASE}._spec_ctx",                  return_value={}), \
             patch(f"{_BASE}.templates.TemplateResponse") as mock_tmpl:
            mock_tmpl.return_value = MagicMock()
            result = _run(challenge_inbox(request=request, db=db, user=user))

        mock_tmpl.assert_called_once()
        args = mock_tmpl.call_args
        assert args[0][0] == "vt_challenges.html"
        ctx = args[0][1]
        assert "active_rows"   in ctx
        assert "terminal_rows" in ctx


# ── UI-24: GET /challenges/send form ─────────────────────────────────────────

class TestSendForm:

    def test_ui24_send_form_renders_200(self):
        from app.api.web_routes.vt_challenges import challenge_send_form
        user = _user(uid=1)
        db   = _db()
        ms_game = _game(gid=1, code="memory_sequence", name="Memory Sequence")
        tt_game = _game(gid=2, code="target_tracking", name="Target Tracking")
        tt_game.is_active = True
        ms_game.is_active = True

        # _accepted_friends returns empty list; compatible_games returns 2 games
        db.query.return_value.filter.return_value.all.side_effect = [
            [],            # Friendship rows
            [ms_game, tt_game],  # compatible games
        ]

        request = MagicMock()
        request.query_params.get.return_value = None

        with patch(f"{_BASE}.require_student_onboarding",               return_value=None), \
             patch(f"{_BASE}._spec_ctx",                                 return_value={}), \
             patch(f"{_BASE}.VirtualTrainingService.is_expert_unlocked", return_value=False), \
             patch(f"{_BASE}.templates.TemplateResponse") as mock_tmpl:
            mock_tmpl.return_value = MagicMock()
            result = _run(challenge_send_form(
                request=request, friend_id=None, game_code=None,
                db=db, user=user,
            ))

        mock_tmpl.assert_called_once()
        assert mock_tmpl.call_args[0][0] == "vt_challenge_send.html"


# ── UI-25: Notification links → /challenges ───────────────────────────────────

class TestNotificationLinks:

    def _mock_db_for_challenge(self, ch):
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = ch
        return db

    def test_ui25a_accept_notif_link(self):
        from app.api.web_routes.vt_challenges import accept_challenge
        ch = MagicMock()
        ch.id                       = 10
        ch.challenged_id            = 2
        ch.challenger_id            = 1
        ch.status                   = ChallengeStatus.PENDING
        ch.expires_at               = datetime.now(timezone.utc) + timedelta(days=7)
        ch.completion_window_seconds = None
        db = self._mock_db_for_challenge(ch)
        user = _user(uid=2)

        captured_link = {}

        def _capture(**kwargs):
            captured_link["link"] = kwargs.get("link")
        with patch(f"{_BASE}.notification_service.create_notification", side_effect=_capture):
            _run(accept_challenge(challenge_id=10, db=db, user=user))

        assert captured_link.get("link") == "/challenges"

    def test_ui25b_decline_notif_link(self):
        from app.api.web_routes.vt_challenges import decline_challenge
        ch = MagicMock()
        ch.id             = 10
        ch.challenged_id  = 2
        ch.challenger_id  = 1
        ch.status         = ChallengeStatus.PENDING
        db = self._mock_db_for_challenge(ch)
        user = _user(uid=2)

        captured_link = {}

        def _capture(**kwargs):
            captured_link["link"] = kwargs.get("link")
        with patch(f"{_BASE}.notification_service.create_notification", side_effect=_capture):
            _run(decline_challenge(challenge_id=10, db=db, user=user))

        assert captured_link.get("link") == "/challenges"

    def test_ui25c_cancel_notif_link(self):
        from app.api.web_routes.vt_challenges import cancel_challenge
        ch = MagicMock()
        ch.id             = 10
        ch.challenger_id  = 1
        ch.challenged_id  = 2
        ch.status         = ChallengeStatus.PENDING
        db = self._mock_db_for_challenge(ch)
        user = _user(uid=1)

        captured_link = {}

        def _capture(**kwargs):
            captured_link["link"] = kwargs.get("link")
        with patch(f"{_BASE}.notification_service.create_notification", side_effect=_capture):
            _run(cancel_challenge(challenge_id=10, db=db, user=user))

        assert captured_link.get("link") == "/challenges"

    def test_ui25d_send_notif_link(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user   = _user(uid=1)
        target = _user(uid=2)
        game   = _game(code="memory_sequence")
        db     = _db()
        db.query.return_value.filter.return_value.first.side_effect = [target, game, None]

        captured_link = {}

        def _capture(**kwargs):
            captured_link["link"] = kwargs.get("link")

        with patch(f"{_BASE}.is_friends",                             return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category",  return_value=0), \
             patch(f"{_BASE}.VirtualTrainingChallenge", MagicMock), \
             patch(f"{_BASE}.notification_service.create_notification", side_effect=_capture):
            _run(send_challenge(
                challenged_user_id=2, game_id=1, message=None,
                difficulty_level=None, db=db, user=user,
            ))

        assert captured_link.get("link") == "/challenges"


# ── UI-27..30: WS publish contract ───────────────────────────────────────────

class TestChallengeWsPublish:
    """Verify that lifecycle actions publish the right event to the right user_ids.

    These tests guard the real-time UX chain: if the publish call is missing or
    sends to wrong IDs, the other party's browser never updates.
    """

    def _pending_challenge(self, challenger_id=1, challenged_id=2):
        ch = MagicMock()
        ch.id             = 10
        ch.challenger_id  = challenger_id
        ch.challenged_id  = challenged_id
        ch.status         = ChallengeStatus.PENDING
        ch.expires_at     = datetime.now(timezone.utc) + timedelta(days=7)
        ch.completion_window_seconds = None
        return ch

    def test_ui27_accept_publishes_to_both_users(self):
        """accept_challenge fires challenge_accepted to BOTH challenger and challenged."""
        from app.api.web_routes.vt_challenges import accept_challenge

        ch = self._pending_challenge(challenger_id=1, challenged_id=2)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = ch
        user = _user(uid=2)

        calls = []
        with patch(f"{_BASE}.publish_challenge_event", side_effect=lambda *a, **kw: calls.append(a)) as mock_pub, \
             patch(f"{_BASE}.notification_service.create_notification"):
            _run(accept_challenge(challenge_id=10, db=db, user=user))

        assert mock_pub.called, "publish_challenge_event must be called on accept"
        published_ids, event_type = calls[0][0], calls[0][1]
        assert set(published_ids) == {1, 2}, "both challenger and challenged must receive the event"
        assert event_type == "challenge_accepted"

    def test_ui28_send_publishes_to_both_users(self):
        """send_challenge fires challenge_sent to sender AND recipient."""
        from app.api.web_routes.vt_challenges import send_challenge

        user   = _user(uid=1)
        target = _user(uid=2)
        game   = _game(code="memory_sequence")
        db     = _db()
        db.query.return_value.filter.return_value.first.side_effect = [target, game, None]

        calls = []
        with patch(f"{_BASE}.is_friends", return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category", return_value=0), \
             patch(f"{_BASE}.VirtualTrainingChallenge", MagicMock), \
             patch(f"{_BASE}.publish_challenge_event", side_effect=lambda *a, **kw: calls.append(a)) as mock_pub, \
             patch(f"{_BASE}.notification_service.create_notification"):
            _run(send_challenge(
                challenged_user_id=2, game_id=1, message=None,
                difficulty_level=None, db=db, user=user,
            ))

        assert mock_pub.called, "publish_challenge_event must be called on send"
        published_ids, event_type = calls[0][0], calls[0][1]
        assert 1 in published_ids and 2 in published_ids
        assert event_type == "challenge_sent"

    def test_ui29_decline_publishes_event(self):
        """decline_challenge fires challenge_declined."""
        from app.api.web_routes.vt_challenges import decline_challenge

        ch = self._pending_challenge(challenger_id=1, challenged_id=2)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = ch
        user = _user(uid=2)

        with patch(f"{_BASE}.publish_challenge_event") as mock_pub, \
             patch(f"{_BASE}.notification_service.create_notification"):
            _run(decline_challenge(challenge_id=10, db=db, user=user))

        mock_pub.assert_called_once()
        assert mock_pub.call_args[0][1] == "challenge_declined"

    def test_ui30_cancel_publishes_event(self):
        """cancel_challenge fires challenge_cancelled."""
        from app.api.web_routes.vt_challenges import cancel_challenge

        ch = self._pending_challenge(challenger_id=1, challenged_id=2)
        db = _db()
        db.query.return_value.filter.return_value.first.return_value = ch
        user = _user(uid=1)

        with patch(f"{_BASE}.publish_challenge_event") as mock_pub, \
             patch(f"{_BASE}.notification_service.create_notification"):
            _run(cancel_challenge(challenge_id=10, db=db, user=user))

        mock_pub.assert_called_once()
        assert mock_pub.call_args[0][1] == "challenge_cancelled"


# ── UI-26: _accepted_friends ──────────────────────────────────────────────────

class TestAcceptedFriends:

    def test_ui26_returns_friends_both_directions(self):
        from app.api.web_routes.vt_challenges import _accepted_friends
        from app.models.friendship import Friendship, FriendshipStatus

        row1 = MagicMock(spec=Friendship)
        row1.requester_id = 1
        row1.addressee_id = 2
        row1.status       = FriendshipStatus.ACCEPTED

        row2 = MagicMock(spec=Friendship)
        row2.requester_id = 3
        row2.addressee_id = 1
        row2.status       = FriendshipStatus.ACCEPTED

        friend_a = _user(uid=2)
        friend_b = _user(uid=3)

        db = _db()
        db.query.return_value.filter.return_value.all.return_value = [row1, row2]
        db.query.return_value.filter.return_value.first.side_effect = [friend_a, friend_b]

        result = _accepted_friends(db, user_id=1)
        assert len(result) == 2
        ids = {u.id for u in result}
        assert ids == {2, 3}
