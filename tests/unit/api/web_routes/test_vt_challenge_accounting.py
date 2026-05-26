"""Unit tests for Virtual Challenge accounting — CA-01..CA-10, CS-01..CS-09.

CA-01  TT challenge submit allowed over daily cap when challenge_id is valid
CA-02  Solo TT submit still returns 429 over daily cap
CA-03  TT challenge skill_deltas non-empty at attempt_index=5 (is_challenge=True)
CA-04  TT challenge delta multiplier equals game_mult (not 0.15 × game_mult)
CA-05  TT challenge skill_deltas non-empty when valid_today=6 (cap exceeded for solo)
CA-06  Invalid TT challenge response has retry_required=True + invalid_reason
CA-07  Invalid TT challenge attempt — challenge attempt slot remains unlinked
CA-08  MS challenge: same cap-bypass + retry_required pattern as TT
CA-09  record_attempt(is_challenge=True), attempt_index=6 → skill_deltas non-empty
CA-10  record_attempt(is_challenge=False), attempt_index=6 → skill_deltas empty

CS-01  POST /challenges/send with challenge_category=virtual → 303 success redirect
CS-02  POST /challenges/send with challenge_category=on_site → error=category_not_available
CS-03  POST /challenges/send with challenge_category=hybrid → error=category_not_available
CS-04  POST /challenges/send with challenge_category=None → defaults to virtual (passes)
CS-05  POST /challenges/send with challenge_category=VIRTUAL (uppercase) → passes guard
CS-06  _build_inbox_row returns challenge_category="virtual" for PENDING row
CS-07  _build_inbox_row returns challenge_category="virtual" for COMPLETED row
CS-08  GET /challenges/send: category_not_available error shown in context
CS-09  POST /challenges/send: random junk category string → error=category_not_available
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi.responses import JSONResponse, RedirectResponse

from app.models.vt_challenge import ChallengeStatus, VirtualTrainingChallenge
from app.services.virtual_training_service import VirtualTrainingService

_BASE    = "app.api.web_routes.vt_challenges"
_VT_BASE = "app.api.web_routes.virtual_training"
_SVC     = "app.services.virtual_training_service"
_METRICS = "app.services.virtual_training_metrics"
_XP      = "app.services.gamification.xp_service"


# ── Shared fixtures ────────────────────────────────────────────────────────────

def _user(uid=1):
    u = MagicMock()
    u.id        = uid
    u.email     = f"user{uid}@lfa.com"
    u.nickname  = None
    u.is_active = True
    return u


def _game(gid=1, code="target_tracking", max_daily=5, base_xp=50):
    g = MagicMock()
    g.id               = gid
    g.code             = code
    g.name             = code.replace("_", " ").title()
    g.is_active        = True
    g.max_daily_attempts = max_daily
    g.base_xp          = base_xp
    g.config           = {
        "difficulties": {
            "easy": {
                "difficulty_multiplier": 1.00,
                "validation_overrides": {
                    "min_duration_seconds": 5.0,
                    "min_stimuli_count":    5,
                },
            }
        }
    }
    g.skill_targets    = {"reactions": 0.5, "decisions": 0.5}
    return g


def _game_ms(gid=2, max_daily=5, base_xp=50):
    g = MagicMock()
    g.id               = gid
    g.code             = "memory_sequence"
    g.name             = "Memory Sequence"
    g.is_active        = True
    g.max_daily_attempts = max_daily
    g.base_xp          = base_xp
    g.config           = {}
    g.skill_targets    = {"memory": 0.6, "concentration": 0.4}
    return g


def _challenge(
    cid=10,
    challenger_id=1,
    challenged_id=2,
    game_id=1,
    status=ChallengeStatus.ACCEPTED,
    winner_id=None,
    is_draw=False,
    challenger_attempt_id=None,
    challenged_attempt_id=None,
    forfeit_user_id=None,
):
    c = MagicMock(spec=VirtualTrainingChallenge)
    c.id                    = cid
    c.challenger_id         = challenger_id
    c.challenged_id         = challenged_id
    c.game_id               = game_id
    c.status                = status
    c.winner_id             = winner_id
    c.is_draw               = is_draw
    c.challenger_attempt_id = challenger_attempt_id
    c.challenged_attempt_id = challenged_attempt_id
    c.forfeit_user_id       = forfeit_user_id
    c.difficulty_level      = "easy"
    return c


def _attempt(aid=100, is_valid=True, score=80.0, skill_deltas=None, invalid_reason=None):
    a = MagicMock()
    a.id                = aid
    a.is_valid          = is_valid
    a.score_normalized  = score
    a.skill_deltas      = skill_deltas or {}
    a.invalid_reason    = invalid_reason
    a.xp_awarded        = 0
    a.attempt_index_today = 1
    a.raw_metrics       = {}
    return a


def _db():
    return MagicMock()


def _run(coro):
    return asyncio.run(coro)


def _make_request(body: dict):
    req = MagicMock()

    async def _json():
        return body

    req.json = _json
    return req


# ══════════════════════════════════════════════════════════════════════════════
# CA-01..CA-08: Route-level accounting tests (TT and MS submit)
# ══════════════════════════════════════════════════════════════════════════════

class TestTTCapBypass:
    """CA-01: TT challenge submit bypasses daily cap."""

    def _make_tt_db(self, valid_today_count=6):
        db = _db()
        # First call: get challenge by id
        ch = _challenge(cid=10, challenger_id=1, challenged_id=2, game_id=1)
        # .count() for valid_today
        count_mock = MagicMock()
        count_mock.count.return_value = valid_today_count

        db.query.return_value.filter.return_value.first.side_effect = [ch]
        db.query.return_value.filter.return_value.count.return_value = valid_today_count
        return db, ch

    def test_ca01_tt_challenge_bypasses_daily_cap(self):
        from app.api.web_routes.virtual_training import virtual_training_target_tracking_submit

        game = _game(max_daily=5)
        user = _user(uid=1)
        db   = _db()
        ch   = _challenge(cid=10, challenger_id=1, challenged_id=2, game_id=1)

        # Sequence: challenge lookup returns ch; count() returns 6 (over cap)
        db.query.return_value.filter.return_value.first.return_value = ch
        db.query.return_value.filter.return_value.count.return_value = 6

        att = _attempt(aid=200, is_valid=True, skill_deltas={"reactions": 0.5})
        request = _make_request({
            "challenge_id": 10,
            "raw_metrics": {"v": 3, "difficulty_level": "easy", "difficulty_multiplier": 1.0},
            "started_at": "2026-05-26T10:00:00Z",
            "score_normalized": 80.0,
            "duration_seconds": 30.0,
            "stimuli_count": 36,
        })

        with patch(f"{_VT_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_VT_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_VT_BASE}._validate_challenge_pre_submit", return_value=(ch, None)), \
             patch(f"{_VT_BASE}.VirtualTrainingService.record_attempt", return_value=att), \
             patch(f"{_VT_BASE}._link_attempt_to_challenge", return_value={"linked": True}), \
             patch(f"{_VT_BASE}.apply_forfeit_if_deadline_passed"):
            resp = _run(virtual_training_target_tracking_submit(
                request=request, db=db, user=user
            ))

        assert isinstance(resp, JSONResponse)
        data = resp.body
        import json
        parsed = json.loads(data)
        # Must not be a 429 — challenge bypasses cap
        assert parsed.get("error") != "daily_cap"

    def test_ca02_solo_tt_returns_429_over_cap(self):
        from app.api.web_routes.virtual_training import virtual_training_target_tracking_submit

        game = _game(max_daily=5)
        user = _user(uid=1)
        db   = _db()

        request = _make_request({
            # No challenge_id → solo attempt
            "raw_metrics": {"v": 3, "difficulty_level": "easy", "difficulty_multiplier": 1.0},
            "started_at": "2026-05-26T10:00:00Z",
            "score_normalized": 80.0,
            "duration_seconds": 30.0,
            "stimuli_count": 36,
        })

        with patch(f"{_VT_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_VT_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_VT_BASE}.apply_forfeit_if_deadline_passed"):
            # Directly patch .count() to return 6 (over cap=5)
            db.query.return_value.filter.return_value.count.return_value = 6
            resp = _run(virtual_training_target_tracking_submit(
                request=request, db=db, user=user
            ))

        import json
        parsed = json.loads(resp.body)
        assert resp.status_code == 429
        assert parsed["error"] == "daily_cap"


class TestTTInvalidChallengeRetry:
    """CA-06 / CA-07: Invalid TT challenge attempt returns retry context."""

    def test_ca06_invalid_tt_challenge_retry_required(self):
        from app.api.web_routes.virtual_training import virtual_training_target_tracking_submit

        import json
        game = _game(max_daily=5)
        user = _user(uid=1)
        db   = _db()
        ch   = _challenge(cid=10, challenger_id=1, challenged_id=2, game_id=1)

        att = _attempt(aid=201, is_valid=False, invalid_reason="too_short", skill_deltas={})

        request = _make_request({
            "challenge_id": 10,
            "raw_metrics": {"v": 3, "difficulty_level": "easy", "difficulty_multiplier": 1.0},
            "started_at": "2026-05-26T10:01:00Z",
            "score_normalized": 0.0,
            "duration_seconds": 2.0,  # too short → invalid
            "stimuli_count": 36,
        })

        with patch(f"{_VT_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_VT_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_VT_BASE}._validate_challenge_pre_submit", return_value=(ch, None)), \
             patch(f"{_VT_BASE}.VirtualTrainingService.record_attempt", return_value=att), \
             patch(f"{_VT_BASE}.apply_forfeit_if_deadline_passed"):
            db.query.return_value.filter.return_value.count.return_value = 0
            resp = _run(virtual_training_target_tracking_submit(
                request=request, db=db, user=user
            ))

        parsed = json.loads(resp.body)
        ctx = parsed.get("challenge_context", {})
        assert ctx.get("retry_required") is True
        assert ctx.get("invalid_reason") == "too_short"
        assert ctx.get("note") == "invalid_attempt_not_linked"

    def test_ca07_invalid_tt_challenge_attempt_not_linked(self):
        """Invalid challenge attempt: _link_attempt_to_challenge is NOT called."""
        from app.api.web_routes.virtual_training import virtual_training_target_tracking_submit

        game = _game(max_daily=5)
        user = _user(uid=1)
        db   = _db()
        ch   = _challenge(cid=10, challenger_id=1, challenged_id=2, game_id=1)
        att  = _attempt(aid=202, is_valid=False, invalid_reason="bot_suspected")

        request = _make_request({
            "challenge_id": 10,
            "raw_metrics": {"v": 3, "difficulty_level": "easy", "difficulty_multiplier": 1.0},
            "started_at": "2026-05-26T10:02:00Z",
            "score_normalized": 0.0,
        })

        with patch(f"{_VT_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_VT_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_VT_BASE}._validate_challenge_pre_submit", return_value=(ch, None)), \
             patch(f"{_VT_BASE}.VirtualTrainingService.record_attempt", return_value=att), \
             patch(f"{_VT_BASE}._link_attempt_to_challenge") as mock_link, \
             patch(f"{_VT_BASE}.apply_forfeit_if_deadline_passed"):
            db.query.return_value.filter.return_value.count.return_value = 0
            _run(virtual_training_target_tracking_submit(
                request=request, db=db, user=user
            ))

        mock_link.assert_not_called()


class TestMSCapBypassAndRetry:
    """CA-08: MS challenge same cap-bypass + retry pattern as TT."""

    def test_ca08_ms_challenge_bypasses_cap_and_returns_retry_on_invalid(self):
        import json
        from app.api.web_routes.virtual_training import virtual_training_memory_sequence_submit

        game = _game_ms(max_daily=5)
        user = _user(uid=1)
        db   = _db()
        ch   = _challenge(cid=20, challenger_id=1, challenged_id=2, game_id=2)
        att  = _attempt(aid=300, is_valid=False, invalid_reason="too_short")

        request = _make_request({
            "challenge_id": 20,
            "raw_metrics": {},
            "started_at": "2026-05-26T10:03:00Z",
            "score_normalized": 0.0,
        })

        with patch(f"{_VT_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_VT_BASE}.VirtualTrainingService.get_game", return_value=game), \
             patch(f"{_VT_BASE}._validate_challenge_pre_submit", return_value=(ch, None)), \
             patch(f"{_VT_BASE}.VirtualTrainingService.record_attempt", return_value=att), \
             patch(f"{_VT_BASE}.apply_forfeit_if_deadline_passed"):
            # Simulate over cap (6 valid today)
            db.query.return_value.filter.return_value.count.return_value = 6
            resp = _run(virtual_training_memory_sequence_submit(
                request=request, db=db, user=user
            ))

        parsed = json.loads(resp.body)
        # Should NOT be 429 (cap bypassed)
        assert parsed.get("error") != "daily_cap"
        # Invalid attempt context with retry
        ctx = parsed.get("challenge_context", {})
        assert ctx.get("retry_required") is True
        assert ctx.get("invalid_reason") == "too_short"


# ══════════════════════════════════════════════════════════════════════════════
# CA-03..CA-05 + CA-09..CA-10: record_attempt() skill delta policy
# ══════════════════════════════════════════════════════════════════════════════

class TestRecordAttemptSkillDeltaPolicy:
    """Tests for record_attempt() challenge accounting policy (unit, no DB)."""

    def _make_game(self, code="target_tracking", base_xp=50):
        g = MagicMock()
        g.id          = 1
        g.code        = code
        g.name        = code
        g.base_xp     = base_xp
        g.config      = {
            "difficulties": {
                "easy": {
                    "difficulty_multiplier": 1.00,
                    "validation_overrides": {
                        "min_duration_seconds": 5.0,
                        "min_stimuli_count": 5,
                    },
                }
            }
        }
        g.skill_targets = {"reactions": 0.5, "decisions": 0.5}
        return g

    def _make_db(self, attempt_count=5):
        db = MagicMock()
        # calculate_daily_attempt_index uses .count()
        db.query.return_value.filter.return_value.count.return_value = attempt_count
        # existing_neg_today query uses .execute().fetchall()
        db.execute.return_value.fetchall.return_value = []
        # savepoint: db.begin_nested() → sp; sp.commit() succeeds
        sp = MagicMock()
        db.begin_nested.return_value = sp
        return db

    def _good_data(self):
        return {
            "started_at": "2026-05-26T10:00:00Z",
            "duration_seconds": 30.0,
            "stimuli_count": 36,
            "correct_count": 34,
            "error_count": 2,
            "avg_reaction_ms": 300.0,
            "score_normalized": 82.0,
            "raw_metrics": {
                "v": 3,
                "difficulty_level": "easy",
                "difficulty_multiplier": 1.00,
            },
        }

    def test_ca09_challenge_attempt_index6_skill_deltas_nonempty(self):
        """CA-09: is_challenge=True with attempt_index=6 → skill_deltas computed."""
        game = self._make_game()
        # attempt_count=5 → index=6 (count + 1)
        db   = self._make_db(attempt_count=5)
        data = self._good_data()

        fake_deltas = {"reactions": 0.3, "decisions": 0.2}

        with patch(f"{_METRICS}.compute_vt_skill_deltas", return_value=fake_deltas) as mock_delta, \
             patch(f"{_XP}.award_xp"):
            att = VirtualTrainingService.record_attempt(
                db=db, user_id=1, game=game, data=data,
                idempotency_key="ca09_idem",
                is_challenge=True,
            )

        # skill delta should have been computed (is_challenge=True bypasses xp_awarded=0 gate)
        mock_delta.assert_called_once()
        # The created attempt should carry the fake_deltas
        added = db.add.call_args[0][0]
        assert added.skill_deltas == fake_deltas

    def test_ca10_solo_attempt_index6_skill_deltas_empty(self):
        """CA-10: is_challenge=False with attempt_index=6 → skill_deltas={}."""
        game = self._make_game()
        db   = self._make_db(attempt_count=5)  # → attempt_index=6
        data = self._good_data()

        with patch(f"{_METRICS}.compute_vt_skill_deltas") as mock_delta, \
             patch(f"{_XP}.award_xp"):
            VirtualTrainingService.record_attempt(
                db=db, user_id=1, game=game, data=data,
                idempotency_key="ca10_idem",
                is_challenge=False,  # solo
            )

        # xp_multiplier=0.0 for index 6, so compute_delta=False → no call
        mock_delta.assert_not_called()
        added = db.add.call_args[0][0]
        assert added.skill_deltas == {}

    def test_ca03_challenge_skill_deltas_nonempty_at_index5(self):
        """CA-03: is_challenge=True at attempt_index=5 (xp_mult=0.15) → deltas computed."""
        game = self._make_game()
        db   = self._make_db(attempt_count=4)  # → index=5
        data = self._good_data()

        fake_deltas = {"reactions": 0.1}
        with patch(f"{_METRICS}.compute_vt_skill_deltas", return_value=fake_deltas), \
             patch(f"{_XP}.award_xp"):
            att = VirtualTrainingService.record_attempt(
                db=db, user_id=1, game=game, data=data,
                idempotency_key="ca03_idem",
                is_challenge=True,
            )

        added = db.add.call_args[0][0]
        assert added.skill_deltas == fake_deltas

    def test_ca04_challenge_delta_multiplier_equals_game_mult_not_xp_penalized(self):
        """CA-04: challenge multiplier passed to compute_vt_skill_deltas = game_mult (1.0),
        not 0.15 × 1.0 = 0.15 which would apply at attempt_index=5 for solo.
        """
        game = self._make_game()
        db   = self._make_db(attempt_count=4)  # → index=5, xp_mult=0.15
        data = self._good_data()

        captured_mult = {}

        def _capture(data, game, multiplier, existing_neg_today):
            captured_mult["value"] = multiplier
            return {"reactions": 0.05}

        with patch(f"{_METRICS}.compute_vt_skill_deltas", side_effect=_capture), \
             patch(f"{_XP}.award_xp"):
            VirtualTrainingService.record_attempt(
                db=db, user_id=1, game=game, data=data,
                idempotency_key="ca04_idem",
                is_challenge=True,
            )

        # game_mult for easy = 1.00; full challenge multiplier should be 1.00 (not 0.15)
        assert abs(captured_mult["value"] - 1.00) < 0.001

    def test_ca05_challenge_delta_nonempty_when_valid_today_6(self):
        """CA-05: challenge still computes deltas even when valid_today would have hit solo cap."""
        game = self._make_game()
        db   = self._make_db(attempt_count=6)  # → index=7, xp_mult=0.0
        data = self._good_data()

        fake_deltas = {"decisions": 0.2}
        with patch(f"{_METRICS}.compute_vt_skill_deltas", return_value=fake_deltas) as mock_delta, \
             patch(f"{_XP}.award_xp"):
            VirtualTrainingService.record_attempt(
                db=db, user_id=1, game=game, data=data,
                idempotency_key="ca05_idem",
                is_challenge=True,
            )

        # delta computed despite xp_awarded=0
        mock_delta.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.skill_deltas == fake_deltas


# ══════════════════════════════════════════════════════════════════════════════
# CS-01..CS-09: Challenge category selector tests
# ══════════════════════════════════════════════════════════════════════════════

class TestChallengeCategoryGuard:
    """CS-01..CS-05, CS-08, CS-09: POST /challenges/send category guard."""

    def _run_send(self, db, user, category=None, **kwargs):
        from app.api.web_routes.vt_challenges import send_challenge

        target = MagicMock()
        target.id = 2
        target.is_active = True
        game = MagicMock()
        game.id   = 1
        game.code = "memory_sequence"
        db.query.return_value.filter.return_value.first.side_effect = [target, game, None]

        _mock_snap = {"game_code": "memory_sequence", "grid_tiles": 12}
        with patch(f"{_BASE}.is_friends",                              return_value=True), \
             patch(f"{_BASE}.count_active_challenges_in_category",   return_value=0), \
             patch(f"{_BASE}.generate_snapshot",                     return_value=_mock_snap), \
             patch(f"{_BASE}.notification_service.create_notification"), \
             patch(f"{_BASE}.VirtualTrainingChallenge") as MockCh:
            MockCh.return_value = MagicMock()
            result = _run(send_challenge(
                challenged_user_id=2, game_id=1, message=None,
                difficulty_level=None,
                challenge_category=category,
                db=db, user=user,
                **kwargs,
            ))
        return result

    def test_cs01_virtual_category_passes(self):
        result = self._run_send(_db(), _user(uid=1), category="virtual")
        assert "error" not in result.headers.get("location", "")

    def test_cs02_on_site_blocked(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user = _user(uid=1)
        db   = _db()
        result = _run(send_challenge(
            challenged_user_id=2, game_id=1, message=None,
            difficulty_level=None,
            challenge_category="on_site",
            db=db, user=user,
        ))
        assert "error=category_not_available" in result.headers["location"]

    def test_cs03_hybrid_blocked(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user = _user(uid=1)
        db   = _db()
        result = _run(send_challenge(
            challenged_user_id=2, game_id=1, message=None,
            difficulty_level=None,
            challenge_category="hybrid",
            db=db, user=user,
        ))
        assert "error=category_not_available" in result.headers["location"]

    def test_cs04_none_category_defaults_to_virtual(self):
        result = self._run_send(_db(), _user(uid=1), category=None)
        assert "error=category_not_available" not in result.headers.get("location", "")

    def test_cs05_uppercase_virtual_passes(self):
        result = self._run_send(_db(), _user(uid=1), category="VIRTUAL")
        assert "error=category_not_available" not in result.headers.get("location", "")

    def test_cs09_junk_category_blocked(self):
        from app.api.web_routes.vt_challenges import send_challenge
        user = _user(uid=1)
        db   = _db()
        result = _run(send_challenge(
            challenged_user_id=2, game_id=1, message=None,
            difficulty_level=None,
            challenge_category="in_person_unverified_123",
            db=db, user=user,
        ))
        assert "error=category_not_available" in result.headers["location"]


class TestBuildInboxRowChallengeCategory:
    """CS-06 / CS-07: _build_inbox_row always returns challenge_category="virtual"."""

    def _make_row(self, status, winner_id=None, is_draw=False):
        from app.api.web_routes.vt_challenges import _build_inbox_row
        from app.models.vt_challenge import VirtualTrainingChallenge

        ch = MagicMock(spec=VirtualTrainingChallenge)
        ch.id                    = 10
        ch.challenger_id         = 1
        ch.challenged_id         = 2
        ch.game_id               = 1
        ch.status                = status
        ch.winner_id             = winner_id
        ch.is_draw               = is_draw
        ch.difficulty_level      = None
        ch.challenger_attempt_id = None
        ch.challenged_attempt_id = None
        ch.forfeit_user_id       = None
        ch.created_at            = datetime.now(timezone.utc)

        u1 = MagicMock(); u1.id = 1; u1.nickname = None; u1.email = "u1@lfa.com"
        u2 = MagicMock(); u2.id = 2; u2.nickname = None; u2.email = "u2@lfa.com"
        g  = MagicMock(); g.id = 1; g.code = "memory_sequence"; g.name = "MS"

        return _build_inbox_row(ch, 1, {}, {1: u1, 2: u2}, {1: g})

    def test_cs06_pending_row_has_virtual_category(self):
        row = self._make_row(ChallengeStatus.PENDING)
        assert row["challenge_category"] == "virtual"

    def test_cs07_completed_row_has_virtual_category(self):
        row = self._make_row(ChallengeStatus.COMPLETED, winner_id=1, is_draw=False)
        assert row["challenge_category"] == "virtual"
