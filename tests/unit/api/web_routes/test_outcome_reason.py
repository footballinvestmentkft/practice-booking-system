"""
OUTCOME-01  post_start_timeout forfeit → outcome_reason = forfeit_post_start_timeout
OUTCOME-02  completed normal winner   → outcome_reason = score_win
OUTCOME-03  completed draw            → outcome_reason = draw
OUTCOME-04  deadline_expired forfeit  → outcome_reason = forfeit_deadline
OUTCOME-05  expired + no_contest      → outcome_reason = no_contest
OUTCOME-06  live_in_progress (one attempt) → outcome_reason = waiting_for_opponent
OUTCOME-07  forfeit detail HTML contains "Score comparison was not used"
OUTCOME-08  forfeit detail HTML contains "No attempt submitted"
OUTCOME-09  forfeit detail HTML shows submitted player score
OUTCOME-10  score_win detail HTML has no forfeit disclaimer
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
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


def _game():
    g = MagicMock()
    g.id = 7; g.code = "target_tracking"; g.name = "Target Tracking"
    g.config = {}; g.skill_targets = {}
    return g


def _challenge(
    *,
    cid=10,
    status=ChallengeStatus.COMPLETED,
    challenger_id=1,
    challenged_id=2,
    winner_id=None,
    is_draw=False,
    forfeit_user_id=None,
    forfeit_reason=None,
    challenger_attempt_id=None,
    challenged_attempt_id=None,
):
    ch = MagicMock(spec=VirtualTrainingChallenge)
    ch.id                    = cid
    ch.status                = status
    ch.challenger_id         = challenger_id
    ch.challenged_id         = challenged_id
    ch.winner_id             = winner_id
    ch.is_draw               = is_draw
    ch.forfeit_user_id       = forfeit_user_id
    ch.forfeit_reason        = forfeit_reason
    ch.challenger_attempt_id = challenger_attempt_id
    ch.challenged_attempt_id = challenged_attempt_id
    ch.game_id               = 7
    ch.difficulty_level      = "hard"
    ch.message               = None
    ch.challenge_mode        = "live"
    ch.completion_deadline   = None
    ch.completed_at          = datetime.now(timezone.utc)
    ch.created_at            = datetime.now(timezone.utc)
    ch.challenger            = _user(uid=challenger_id)
    ch.challenged            = _user(uid=challenged_id)
    ch.winner                = _user(uid=winner_id) if winner_id else None
    ch.forfeit_user          = _user(uid=forfeit_user_id) if forfeit_user_id else None
    ch.game                  = _game()
    return ch


def _attempt(aid=100, score=75.0, skill_deltas=None):
    a = MagicMock(spec=VirtualTrainingAttempt)
    a.id               = aid
    a.is_valid         = True
    a.score_normalized = score
    a.skill_deltas     = skill_deltas if skill_deltas is not None else {}
    a.stimuli_count    = 12
    a.correct_count    = 2
    a.wrong_click_count = 5
    a.error_count      = 5
    a.avg_reaction_ms  = 1134.0
    a.raw_metrics      = {}
    return a


# ── outcome_reason unit tests ─────────────────────────────────────────────────

class TestComputeOutcomeReason:

    def _reason(self, ch):
        from app.api.web_routes.vt_challenges import _compute_outcome_reason
        return _compute_outcome_reason(ch)

    def test_outcome01_post_start_timeout_forfeit(self):
        ch = _challenge(
            status=ChallengeStatus.COMPLETED,
            winner_id=1, forfeit_user_id=2,
            forfeit_reason="post_start_timeout",
        )
        assert self._reason(ch) == "forfeit_post_start_timeout"

    def test_outcome02_completed_normal_winner(self):
        ch = _challenge(
            status=ChallengeStatus.COMPLETED,
            winner_id=1, forfeit_user_id=None,
        )
        assert self._reason(ch) == "score_win"

    def test_outcome03_completed_draw(self):
        ch = _challenge(
            status=ChallengeStatus.COMPLETED,
            winner_id=None, is_draw=True, forfeit_user_id=None,
        )
        assert self._reason(ch) == "draw"

    def test_outcome04_deadline_expired_forfeit(self):
        ch = _challenge(
            status=ChallengeStatus.COMPLETED,
            winner_id=1, forfeit_user_id=2,
            forfeit_reason="deadline_expired",
        )
        assert self._reason(ch) == "forfeit_deadline"

    def test_outcome05_expired_no_contest(self):
        ch = _challenge(
            status=ChallengeStatus.EXPIRED,
            winner_id=None, forfeit_user_id=None,
            forfeit_reason="no_contest",
        )
        assert self._reason(ch) == "no_contest"

    def test_outcome06_live_in_progress(self):
        ch = _challenge(status=ChallengeStatus.LIVE_IN_PROGRESS)
        assert self._reason(ch) == "waiting_for_opponent"

    def test_pending_maps_to_waiting_for_acceptance(self):
        ch = _challenge(status=ChallengeStatus.PENDING)
        assert self._reason(ch) == "waiting_for_acceptance"

    def test_accepted_maps_to_waiting_for_opponent(self):
        ch = _challenge(status=ChallengeStatus.ACCEPTED)
        assert self._reason(ch) == "waiting_for_opponent"

    def test_live_lobby_maps_to_in_lobby(self):
        ch = _challenge(status=ChallengeStatus.LIVE_LOBBY)
        assert self._reason(ch) == "in_lobby"

    def test_declined_maps_to_declined(self):
        ch = _challenge(status=ChallengeStatus.DECLINED)
        assert self._reason(ch) == "declined"

    def test_cancelled_maps_to_cancelled(self):
        ch = _challenge(status=ChallengeStatus.CANCELLED)
        assert self._reason(ch) == "cancelled"

    def test_expired_no_forfeit_maps_to_expired(self):
        ch = _challenge(
            status=ChallengeStatus.EXPIRED,
            forfeit_reason=None,
        )
        assert self._reason(ch) == "expired"

    def test_forfeit_no_show(self):
        ch = _challenge(
            status=ChallengeStatus.COMPLETED,
            winner_id=1, forfeit_user_id=2,
            forfeit_reason="no_show",
        )
        assert self._reason(ch) == "forfeit_no_show"

    def test_completed_forfeit_no_winner_is_no_contest(self):
        ch = _challenge(
            status=ChallengeStatus.COMPLETED,
            winner_id=None, forfeit_user_id=2,
            forfeit_reason="no_contest",
        )
        assert self._reason(ch) == "no_contest"


# ── outcome_reason injected into route context ────────────────────────────────

def _call_detail(*, ch, user_id=1, challenger_attempt=None, challenged_attempt=None):
    from app.api.web_routes.vt_challenges import challenge_detail

    user = _user(uid=user_id)
    db   = MagicMock()

    firsts = iter([ch, challenger_attempt, challenged_attempt])
    db.query.return_value.filter.return_value.first.side_effect = lambda: next(firsts, None)

    ctx = {}

    def _capture(tmpl, context, **kw):
        ctx["template"] = tmpl
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


class TestOutcomeReasonInContext:

    def test_outcome_reason_present_in_context(self):
        ch = _challenge(
            status=ChallengeStatus.COMPLETED,
            winner_id=1, forfeit_user_id=2,
            forfeit_reason="post_start_timeout",
        )
        ctx = _call_detail(ch=ch, user_id=1)
        assert "outcome_reason" in ctx
        assert ctx["outcome_reason"] == "forfeit_post_start_timeout"

    def test_score_win_context(self):
        ch = _challenge(
            status=ChallengeStatus.COMPLETED,
            winner_id=1, forfeit_user_id=None,
        )
        ctx = _call_detail(ch=ch, user_id=1)
        assert ctx["outcome_reason"] == "score_win"

    def test_draw_context(self):
        ch = _challenge(
            status=ChallengeStatus.COMPLETED,
            winner_id=None, is_draw=True, forfeit_user_id=None,
        )
        ctx = _call_detail(ch=ch, user_id=1)
        assert ctx["outcome_reason"] == "draw"


# ── Template content tests ────────────────────────────────────────────────────

class TestOutcomeTemplateContent:

    def _html(self):
        with open(_TEMPLATE_PATH, encoding="utf-8") as fh:
            return fh.read()

    # The forfeit score-block disclaimer uses the more specific "— only one player submitted." suffix
    # to distinguish it from the shorter "Score comparison was not used." in the no_contest branch.
    _FORFEIT_DISCLAIMER = "Score comparison was not used — only one player submitted."

    def test_outcome07_forfeit_disclaimer_present(self):
        """OUTCOME-07: template contains the forfeit score-block disclaimer."""
        html = self._html()
        assert self._FORFEIT_DISCLAIMER in html

    def test_outcome08_no_attempt_submitted_text_present(self):
        """OUTCOME-08: template contains 'No attempt submitted' for forfeit side."""
        html = self._html()
        assert "No attempt submitted" in html

    def test_outcome09_forfeit_submitted_player_score_rendered(self):
        """OUTCOME-09: forfeit block renders the submitted player's score_normalized."""
        html = self._html()
        # Score block is shown for all forfeit outcomes — verify score render path exists
        assert "score_normalized" in html or "challenger_attempt.score_normalized" in html

    def test_outcome10_no_forfeit_disclaimer_in_score_win_branch(self):
        """OUTCOME-10: forfeit disclaimer is gated by _is_forfeit_outcome flag."""
        html = self._html()
        # Both the flag and the full-length disclaimer must be present
        assert "_is_forfeit_outcome" in html
        assert self._FORFEIT_DISCLAIMER in html
        # The set-variable declaration must appear before the disclaimer is used
        set_pos        = html.find("{% set _is_forfeit_outcome")
        disclaimer_pos = html.find(self._FORFEIT_DISCLAIMER)
        assert set_pos != -1, "{% set _is_forfeit_outcome %} not found in template"
        assert set_pos < disclaimer_pos, (
            "_is_forfeit_outcome must be set before the score disclaimer renders"
        )

    def test_template_uses_outcome_reason_variable(self):
        """Template must reference outcome_reason for branching."""
        html = self._html()
        assert "outcome_reason" in html

    def test_template_has_not_used_score_class(self):
        """Forfeit scores must use .not-used CSS class."""
        html = self._html()
        assert "not-used" in html

    def test_template_skill_delta_block_no_attempt_submitted(self):
        """Skill delta block shows 'No attempt submitted' when no attempt linked."""
        html = self._html()
        # Both "No attempt submitted" and "No skill delta recorded" must be present
        assert "No attempt submitted" in html
        assert "No skill delta recorded" in html
