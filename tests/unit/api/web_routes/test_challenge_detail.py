"""Unit tests for GET /challenges/{id} — Virtual Challenge detail page.

CD-01  Not found → 404 (vt_challenges.html fallback, error=challenge_not_found)
CD-02  Non-participant → 403 (vt_challenges.html fallback, error=not_found)
CD-03  Challenger participant → 200 (vt_challenge_detail.html)
CD-04  Challenged participant → 200 (vt_challenge_detail.html)
CD-05  is_challenger=True when viewer is challenger
CD-06  is_challenger=False when viewer is challenged
CD-07  challenge_category="virtual" in context
CD-08  is_forfeit=True when forfeit_user_id is set
CD-09  is_no_contest=True when forfeit_user_id set and winner_id=None
CD-10  is_no_contest=False when forfeit has a winner
CD-11  challenger_attempt loaded from DB when challenger_attempt_id is set
CD-12  challenged_attempt is None when challenged_attempt_id=None
CD-13  Unauthenticated/onboarding guard fires before DB lookup
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi.responses import HTMLResponse, RedirectResponse

from app.models.vt_challenge import ChallengeStatus, VirtualTrainingChallenge
from app.models.virtual_training import VirtualTrainingAttempt

_BASE = "app.api.web_routes.vt_challenges"


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _user(uid=1):
    u = MagicMock()
    u.id        = uid
    u.email     = f"user{uid}@lfa.com"
    u.nickname  = None
    u.is_active = True
    return u


def _game(gid=1, code="memory_sequence"):
    g = MagicMock()
    g.id           = gid
    g.code         = code
    g.name         = code.replace("_", " ").title()
    g.config       = {}
    g.skill_targets = {}
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
    c.difficulty_level      = None
    c.message               = None
    c.created_at            = datetime.now(timezone.utc)
    # ORM relationships
    c.challenger = _user(uid=challenger_id)
    c.challenged = _user(uid=challenged_id)
    c.winner     = _user(uid=winner_id) if winner_id else None
    c.forfeit_user = _user(uid=forfeit_user_id) if forfeit_user_id else None
    g = _game(gid=game_id)
    c.game       = g
    return c


def _attempt(aid=100, is_valid=True, score=75.0, skill_deltas=None):
    a = MagicMock(spec=VirtualTrainingAttempt)
    a.id               = aid
    a.is_valid         = is_valid
    a.score_normalized = score
    a.skill_deltas     = skill_deltas or {}
    a.stimuli_count    = 36
    a.correct_count    = 34
    a.wrong_click_count = 2
    a.error_count      = 2
    a.avg_reaction_ms  = 300.0
    a.raw_metrics      = {}
    return a


def _db():
    return MagicMock()


def _run(coro):
    return asyncio.run(coro)


def _request():
    req = MagicMock()
    return req


# ── Helper: call challenge_detail route ───────────────────────────────────────

def _call_detail(challenge_id=10, user=None, db=None, ch=None):
    from app.api.web_routes.vt_challenges import challenge_detail

    user = user or _user(uid=1)
    db   = db   or _db()

    captured_ctx = {}

    def _mock_tmpl(template_name, context, **kwargs):
        captured_ctx["template"] = template_name
        captured_ctx["context"]  = context
        resp = MagicMock(spec=HTMLResponse)
        resp.status_code = kwargs.get("status_code", 200)
        return resp

    db.query.return_value.filter.return_value.first.return_value = ch

    with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
         patch(f"{_BASE}._spec_ctx", return_value={}), \
         patch(f"{_BASE}.templates.TemplateResponse", side_effect=_mock_tmpl):
        resp = _run(challenge_detail(
            challenge_id=challenge_id,
            request=_request(),
            db=db,
            user=user,
        ))

    return resp, captured_ctx


# ══════════════════════════════════════════════════════════════════════════════
# CD-01..CD-02: Access guard tests
# ══════════════════════════════════════════════════════════════════════════════

class TestChallengeDetailGuards:

    def test_cd01_not_found_returns_404(self):
        db  = _db()
        db.query.return_value.filter.return_value.first.return_value = None

        captured = {}

        def _mock_tmpl(template_name, context, **kwargs):
            captured["template"]     = template_name
            captured["status_code"]  = kwargs.get("status_code", 200)
            captured["error"]        = context.get("error")
            resp = MagicMock()
            resp.status_code = kwargs.get("status_code", 200)
            return resp

        with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_BASE}._spec_ctx", return_value={}), \
             patch(f"{_BASE}.templates.TemplateResponse", side_effect=_mock_tmpl):
            from app.api.web_routes.vt_challenges import challenge_detail
            _run(challenge_detail(
                challenge_id=999, request=_request(), db=db, user=_user(uid=1)
            ))

        assert captured["template"]    == "vt_challenges.html"
        assert captured["status_code"] == 404
        assert captured["error"]       == "challenge_not_found"

    def test_cd02_non_participant_returns_403(self):
        ch  = _challenge(cid=10, challenger_id=3, challenged_id=4)
        db  = _db()
        db.query.return_value.filter.return_value.first.return_value = ch

        captured = {}

        def _mock_tmpl(template_name, context, **kwargs):
            captured["template"]    = template_name
            captured["status_code"] = kwargs.get("status_code", 200)
            captured["error"]       = context.get("error")
            resp = MagicMock()
            resp.status_code = kwargs.get("status_code", 200)
            return resp

        with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_BASE}._spec_ctx", return_value={}), \
             patch(f"{_BASE}.templates.TemplateResponse", side_effect=_mock_tmpl):
            from app.api.web_routes.vt_challenges import challenge_detail
            # user.id=1 is NOT 3 or 4
            _run(challenge_detail(
                challenge_id=10, request=_request(), db=db, user=_user(uid=1)
            ))

        assert captured["template"]    == "vt_challenges.html"
        assert captured["status_code"] == 403
        assert captured["error"]       == "not_found"


# ══════════════════════════════════════════════════════════════════════════════
# CD-03..CD-10: Template context tests
# ══════════════════════════════════════════════════════════════════════════════

class TestChallengeDetailContext:

    def _detail_ctx(self, ch, user_uid=1, extra_db_side_effects=None):
        """Call detail route and return the context dict passed to TemplateResponse."""
        from app.api.web_routes.vt_challenges import challenge_detail

        user = _user(uid=user_uid)
        db   = _db()

        captured = {}

        def _mock_tmpl(template_name, context, **kwargs):
            captured["template"] = template_name
            captured["context"]  = context
            resp = MagicMock()
            resp.status_code = kwargs.get("status_code", 200)
            return resp

        # First .first() returns the challenge; subsequent ones return attempts
        side_effects = [ch]
        if extra_db_side_effects:
            side_effects.extend(extra_db_side_effects)
        db.query.return_value.filter.return_value.first.side_effect = side_effects

        with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_BASE}._spec_ctx", return_value={}), \
             patch(f"{_BASE}.templates.TemplateResponse", side_effect=_mock_tmpl):
            _run(challenge_detail(
                challenge_id=ch.id, request=_request(), db=db, user=user
            ))

        return captured.get("context", {}), captured.get("template", "")

    def test_cd03_challenger_gets_200_detail(self):
        ch = _challenge(cid=10, challenger_id=1, challenged_id=2)
        ctx, tmpl = self._detail_ctx(ch, user_uid=1)
        assert tmpl == "vt_challenge_detail.html"

    def test_cd04_challenged_gets_200_detail(self):
        ch = _challenge(cid=10, challenger_id=1, challenged_id=2)
        ctx, tmpl = self._detail_ctx(ch, user_uid=2)
        assert tmpl == "vt_challenge_detail.html"

    def test_cd05_is_challenger_true_for_challenger(self):
        ch = _challenge(cid=10, challenger_id=1, challenged_id=2)
        ctx, _ = self._detail_ctx(ch, user_uid=1)
        assert ctx["is_challenger"] is True

    def test_cd06_is_challenger_false_for_challenged(self):
        ch = _challenge(cid=10, challenger_id=1, challenged_id=2)
        ctx, _ = self._detail_ctx(ch, user_uid=2)
        assert ctx["is_challenger"] is False

    def test_cd07_challenge_category_is_virtual(self):
        ch = _challenge(cid=10, challenger_id=1, challenged_id=2)
        ctx, _ = self._detail_ctx(ch, user_uid=1)
        assert ctx["challenge_category"] == "virtual"

    def test_cd08_is_forfeit_true_when_forfeit_user_set(self):
        ch = _challenge(
            cid=10, challenger_id=1, challenged_id=2,
            status=ChallengeStatus.COMPLETED,
            winner_id=1,
            forfeit_user_id=2,
        )
        ctx, _ = self._detail_ctx(ch, user_uid=1)
        assert ctx["is_forfeit"] is True

    def test_cd09_is_no_contest_true_when_forfeit_no_winner(self):
        ch = _challenge(
            cid=10, challenger_id=1, challenged_id=2,
            status=ChallengeStatus.COMPLETED,
            winner_id=None,
            forfeit_user_id=2,
        )
        ctx, _ = self._detail_ctx(ch, user_uid=1)
        assert ctx["is_no_contest"] is True

    def test_cd10_is_no_contest_false_when_forfeit_has_winner(self):
        ch = _challenge(
            cid=10, challenger_id=1, challenged_id=2,
            status=ChallengeStatus.COMPLETED,
            winner_id=1,
            forfeit_user_id=2,
        )
        ctx, _ = self._detail_ctx(ch, user_uid=1)
        assert ctx["is_no_contest"] is False


# ══════════════════════════════════════════════════════════════════════════════
# CD-11..CD-12: Attempt loading
# ══════════════════════════════════════════════════════════════════════════════

class TestChallengeDetailAttemptLoading:

    def _detail_ctx(self, ch, attempts_side_effects, user_uid=1):
        from app.api.web_routes.vt_challenges import challenge_detail

        user = _user(uid=user_uid)
        db   = _db()

        captured = {}

        def _mock_tmpl(template_name, context, **kwargs):
            captured["context"] = context
            resp = MagicMock()
            resp.status_code = kwargs.get("status_code", 200)
            return resp

        # First call: challenge lookup; rest: attempt lookups
        db.query.return_value.filter.return_value.first.side_effect = [ch] + attempts_side_effects

        with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
             patch(f"{_BASE}._spec_ctx", return_value={}), \
             patch(f"{_BASE}.templates.TemplateResponse", side_effect=_mock_tmpl):
            _run(challenge_detail(
                challenge_id=ch.id, request=_request(), db=db, user=user
            ))

        return captured.get("context", {})

    def test_cd11_challenger_attempt_loaded_when_id_set(self):
        att = _attempt(aid=100, skill_deltas={})
        ch  = _challenge(
            cid=10, challenger_id=1, challenged_id=2,
            challenger_attempt_id=100,
            challenged_attempt_id=None,
        )
        # ch.challenger_attempt_id is set → first attempt query returns att
        ctx = self._detail_ctx(ch, attempts_side_effects=[att])
        assert ctx["challenger_attempt"] is att

    def test_cd12_challenged_attempt_none_when_id_not_set(self):
        ch = _challenge(
            cid=10, challenger_id=1, challenged_id=2,
            challenger_attempt_id=None,
            challenged_attempt_id=None,
        )
        ctx = self._detail_ctx(ch, attempts_side_effects=[])
        assert ctx["challenged_attempt"] is None


# ══════════════════════════════════════════════════════════════════════════════
# CD-13: Onboarding guard fires before DB lookup
# ══════════════════════════════════════════════════════════════════════════════

class TestChallengeDetailOnboardingGuard:

    def test_cd13_onboarding_guard_fires_before_db(self):
        from app.api.web_routes.vt_challenges import challenge_detail

        guard_response = RedirectResponse(url="/onboarding", status_code=303)
        user = _user(uid=1)
        db   = _db()

        with patch(f"{_BASE}.require_student_onboarding", return_value=guard_response), \
             patch(f"{_BASE}.templates.TemplateResponse") as mock_tmpl:
            resp = _run(challenge_detail(
                challenge_id=10, request=_request(), db=db, user=user
            ))

        # Template should never be called — guard short-circuits
        mock_tmpl.assert_not_called()
        assert resp is guard_response
