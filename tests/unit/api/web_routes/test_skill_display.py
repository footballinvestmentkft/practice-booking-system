"""
SKILL-DISPLAY-01  _skill_scores() passes attempt.skill_deltas values into context
SKILL-DISPLAY-02  challenge detail template shows +/- delta with correct CSS class
SKILL-DISPLAY-03  forfeit / no-attempt side shows "No skill delta recorded"
SKILL-DISPLAY-04  VTSkillScorer.score_all() is NOT called — performance scores
                  are not mixed in for the Skill Impact section
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.responses import HTMLResponse

from app.models.vt_challenge import ChallengeStatus, VirtualTrainingChallenge
from app.models.virtual_training import VirtualTrainingAttempt

_BASE = "app.api.web_routes.vt_challenges"
_TEMPLATE_PATH = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "../../../../app/templates/vt_challenge_detail.html",
    )
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user(uid=1):
    u = MagicMock()
    u.id       = uid
    u.email    = f"u{uid}@lfa.com"
    u.nickname = None
    return u


def _challenge(cid=10, challenger_id=1, challenged_id=2,
               challenger_attempt_id=None, challenged_attempt_id=None):
    ch = MagicMock(spec=VirtualTrainingChallenge)
    ch.id                     = cid
    ch.challenger_id          = challenger_id
    ch.challenged_id          = challenged_id
    ch.game_id                = 1
    ch.status                 = ChallengeStatus.COMPLETED
    ch.winner_id              = None
    ch.is_draw                = False
    ch.challenger_attempt_id  = challenger_attempt_id
    ch.challenged_attempt_id  = challenged_attempt_id
    ch.forfeit_user_id        = None
    ch.difficulty_level       = None
    ch.message                = None
    ch.challenger             = _user(uid=challenger_id)
    ch.challenged             = _user(uid=challenged_id)
    ch.winner                 = None
    ch.forfeit_user           = None
    game = MagicMock()
    game.id = 1; game.code = "memory_sequence"; game.name = "Memory Sequence"
    game.config = {}; game.skill_targets = {}
    ch.game = game
    return ch


def _attempt(aid=100, skill_deltas=None):
    a = MagicMock(spec=VirtualTrainingAttempt)
    a.id               = aid
    a.is_valid         = True
    a.score_normalized = 75.0
    a.skill_deltas     = skill_deltas if skill_deltas is not None else {}
    a.stimuli_count    = 36
    a.correct_count    = 34
    a.wrong_click_count = 2
    a.error_count      = 2
    a.avg_reaction_ms  = 300.0
    a.raw_metrics      = {}
    return a


def _call_detail(*, user_id=1, ch, db_attempts=None):
    """Run challenge_detail and return captured template context."""
    from app.api.web_routes.vt_challenges import challenge_detail

    user = _user(uid=user_id)
    db   = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = (
        _db_side_effect(ch, db_attempts or {})
    )

    ctx = {}

    def _capture(tmpl_name, context, **kw):
        ctx["template"] = tmpl_name
        ctx["context"]  = context
        r = MagicMock(spec=HTMLResponse)
        r.status_code = kw.get("status_code", 200)
        return r

    with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
         patch(f"{_BASE}._spec_ctx", return_value={}), \
         patch(f"{_BASE}.templates.TemplateResponse", side_effect=_capture):
        asyncio.run(challenge_detail(
            challenge_id=ch.id,
            request=MagicMock(),
            db=db,
            user=user,
        ))

    return ctx.get("context", {})


def _db_side_effect(ch, attempt_map: dict):
    """Return a side_effect callable that maps attempt id → attempt object."""
    call_count = [0]

    def _first():
        call_count[0] += 1
        if call_count[0] == 1:
            return ch
        # Subsequent calls return attempt by id from attempt_map
        return None

    # We need a richer mock for the chained calls.
    # Simpler: just return the challenge, and patch attempt fetching separately.
    return _first


# ── Better call helper that patches attempt lookups ───────────────────────────

def _call_detail_full(*, ch, challenger_attempt=None, challenged_attempt=None):
    from app.api.web_routes.vt_challenges import challenge_detail

    user = _user(uid=ch.challenger_id)
    db   = MagicMock()

    # First .first() → challenge; subsequent → attempts by position
    firsts = iter([ch, challenger_attempt, challenged_attempt])
    db.query.return_value.filter.return_value.first.side_effect = lambda: next(firsts, None)

    ctx = {}

    def _capture(tmpl_name, context, **kw):
        ctx["template"] = tmpl_name
        ctx["context"]  = context
        return MagicMock(spec=HTMLResponse)

    with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
         patch(f"{_BASE}._spec_ctx", return_value={}), \
         patch(f"{_BASE}.templates.TemplateResponse", side_effect=_capture):
        asyncio.run(challenge_detail(
            challenge_id=ch.id,
            request=MagicMock(),
            db=db,
            user=user,
        ))

    return ctx.get("context", {})


# ══════════════════════════════════════════════════════════════════════════════
# SKILL-DISPLAY-01  skill_deltas values are passed into context unchanged
# ══════════════════════════════════════════════════════════════════════════════

class TestSkillDisplayDeltas:

    def test_sd01_skill_deltas_passed_as_context(self):
        """challenger_skill_scores in context equals attempt.skill_deltas (float cast)."""
        deltas = {"reactions": 0.09, "anticipation": -0.14, "decision_making": 0.03}
        ch_attempt = _attempt(aid=10, skill_deltas=deltas)

        ch = _challenge(challenger_attempt_id=10, challenged_attempt_id=None)
        ctx = _call_detail_full(
            ch=ch,
            challenger_attempt=ch_attempt,
            challenged_attempt=None,
        )

        scores = ctx.get("challenger_skill_scores", {})
        assert scores.get("reactions") == pytest.approx(0.09)
        assert scores.get("anticipation") == pytest.approx(-0.14)
        assert scores.get("decision_making") == pytest.approx(0.03)

    def test_sd01_no_attempt_returns_empty_dict(self):
        ch = _challenge(challenger_attempt_id=None, challenged_attempt_id=None)
        ctx = _call_detail_full(ch=ch)

        assert ctx.get("challenger_skill_scores") == {}
        assert ctx.get("challenged_skill_scores") == {}


# ══════════════════════════════════════════════════════════════════════════════
# SKILL-DISPLAY-02  template marks positive/negative delta correctly
# ══════════════════════════════════════════════════════════════════════════════

class TestSkillDisplayTemplate:

    def _read_template(self):
        with open(_TEMPLATE_PATH, encoding="utf-8") as fh:
            return fh.read()

    def test_sd02_negative_delta_uses_neg_class(self):
        html = self._read_template()
        # Template must apply 'neg' CSS class when delta < 0
        assert "{% if delta < 0 %}neg{% endif %}" in html

    def test_sd02_positive_delta_shows_plus_sign(self):
        html = self._read_template()
        # Template must prefix '+' when delta > 0
        assert "{% if delta > 0 %}+{% endif %}" in html

    def test_sd02_iterates_skill_delta_directly(self):
        html = self._read_template()
        # Must unpack (skill, delta) — NOT (skill, info)
        assert "{% for skill, delta in challenger_skill_scores.items() %}" in html
        assert "{% for skill, delta in challenged_skill_scores.items() %}" in html

    def test_sd03_empty_side_shows_no_delta_message(self):
        html = self._read_template()
        assert "No skill delta recorded" in html


# ══════════════════════════════════════════════════════════════════════════════
# SKILL-DISPLAY-03  forfeit side (no attempt) shows fallback message via route
# ══════════════════════════════════════════════════════════════════════════════

class TestSkillDisplayForfeit:

    def test_sd03_forfeit_challenged_has_empty_scores(self):
        """Forfeit user has no attempt → challenged_skill_scores == {}."""
        ch_attempt = _attempt(aid=20, skill_deltas={"reactions": 0.05})
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            challenger_attempt_id=20, challenged_attempt_id=None,
        )
        ch.forfeit_user_id = 2  # challenged forfeited

        ctx = _call_detail_full(
            ch=ch,
            challenger_attempt=ch_attempt,
            challenged_attempt=None,
        )

        assert ctx.get("challenged_skill_scores") == {}


# ══════════════════════════════════════════════════════════════════════════════
# SKILL-DISPLAY-04  VTSkillScorer.score_all() is NOT called
# ══════════════════════════════════════════════════════════════════════════════

class TestSkillDisplayNoScorer:

    def test_sd04_vt_skill_scorer_not_imported_in_module(self):
        """VTSkillScorer must not be in vt_challenges module namespace.

        The old broken _skill_scores() imported and called VTSkillScorer.score_all()
        which returns 0-1 performance floats instead of stored skill deltas. Verify
        the module no longer imports the scorer so the regression cannot be reintroduced
        without this test failing.
        """
        import app.api.web_routes.vt_challenges as mod
        assert not hasattr(mod, "VTSkillScorer"), (
            "VTSkillScorer found in vt_challenges — risk of performance score being "
            "mixed in for Skill Impact display. Remove import or revert _skill_scores()."
        )

    def test_sd04_negative_delta_preserved_in_context(self):
        """Negative deltas (impossible for 0-1 scorer) must survive into context unchanged."""
        deltas = {"reactions": -0.14}
        ch_attempt = _attempt(aid=31, skill_deltas=deltas)
        ch = _challenge(challenger_attempt_id=31, challenged_attempt_id=None)

        ctx = _call_detail_full(
            ch=ch,
            challenger_attempt=ch_attempt,
            challenged_attempt=None,
        )

        scores = ctx.get("challenger_skill_scores", {})
        assert scores.get("reactions") == pytest.approx(-0.14), (
            "Negative delta was lost — VTSkillScorer (which clamps to 0-1) may be in use"
        )
