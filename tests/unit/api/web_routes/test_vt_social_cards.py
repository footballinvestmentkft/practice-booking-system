"""
SC-01  preview: participant → 200 HTML
SC-02  preview: non-participant → 403
SC-03  preview: invalid platform → 422
SC-04  export: participant → PNG bytes (mock Playwright)
SC-05  export: non-participant → 403
SC-06  export: rate limit 6th request → 429
SC-07  Content-Disposition contains challenge id, phase, and platform
SC-08  context score_win → is_viewer_winner=True when winner_id==viewer.id
SC-09  context forfeit loser → my_score=None
SC-10  context skill deltas come from viewer's attempt
SC-11  context draw → is_draw=True, winner_name=None
SC-12  context pending → challenger_score=None, challenged_score=None
SC-13  CTA: phase "completed_score_win" → "Play again"; phase "challenge_sent" → "View challenge"
SC-14  post_16_9 template contains phase-guarded challenger/challenged scores
SC-15  story_9_16 template contains my_skill_scores and skill-data message
SC-16  both templates contain LFA branding marker

SC-AUDIT-01  render_token=valid JWT + user=None → template rendered (not 401)
SC-AUDIT-02  export builds render URL containing render_token= param
SC-AUDIT-03  preview route signature contains a "phase" parameter
SC-AUDIT-04  preview: phase="bogus_phase" → 422
SC-AUDIT-05  export: locked phase (challenge_accepted) → 403
SC-AUDIT-06  preview: completed_forfeit_win for winner → 200
SC-AUDIT-12  render_token resolved to None → 401 (wrong challenge / expired)
SC-AUDIT-13  SCORE WIN badge in post_16_9.html is behind phase == "completed_score_win" guard
SC-AUDIT-14  empty skill_deltas → skill_delta_result not in unlocked phases
SC-AUDIT-14b non-empty skill_deltas → skill_delta_result in unlocked phases
"""
from __future__ import annotations

import asyncio
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.responses import HTMLResponse, Response

from app.models.vt_challenge import ChallengeStatus, VirtualTrainingChallenge
from app.models.virtual_training import VirtualTrainingAttempt

_BASE = "app.api.web_routes.vt_challenges"
_POST_TEMPLATE = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "../../../../app/templates/public/export/challenge/post_16_9.html",
))
_STORY_TEMPLATE = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "../../../../app/templates/public/export/challenge/story_9_16.html",
))
_CC_COLLECTION_TEMPLATE = os.path.abspath(os.path.join(
    os.path.dirname(__file__),
    "../../../../app/templates/my_cards_challenge_card.html",
))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user(uid=1):
    u = MagicMock()
    u.id       = uid
    u.email    = f"u{uid}@lfa.com"
    u.nickname = None
    u.is_active = True
    return u


def _game():
    g = MagicMock()
    g.id   = 7
    g.code = "target_tracking"
    g.name = "Target Tracking"
    g.config = {}
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
    challenge_mode="live",
):
    from datetime import datetime, timezone
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
    ch.challenge_mode        = challenge_mode
    ch.completion_deadline   = None
    ch.completed_at          = datetime.now(timezone.utc) if status == ChallengeStatus.COMPLETED else None
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
    a.skill_deltas     = skill_deltas or {}
    return a


# ── Shared call helpers ────────────────────────────────────────────────────────

def _call_preview(
    *, ch, user_id=1, platform="challenge_post_16_9",
    phase="completed_score_win",
    challenger_attempt=None, challenged_attempt=None,
):
    from app.api.web_routes.vt_challenges import challenge_card_preview

    user   = _user(uid=user_id)
    db     = MagicMock()
    firsts = iter([ch, challenger_attempt, challenged_attempt])
    db.query.return_value.filter.return_value.first.side_effect = lambda: next(firsts, None)

    captured = {}

    def _capture(tmpl, ctx, **kw):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(spec=HTMLResponse)

    with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
         patch(f"{_BASE}.templates.TemplateResponse", side_effect=_capture):
        try:
            asyncio.run(challenge_card_preview(
                challenge_id=ch.id,
                request=MagicMock(),
                platform=platform,
                phase=phase,
                render_token=None,
                export=False,
                db=db,
                user=user,
            ))
        except Exception as exc:
            captured["exc"] = exc

    return captured


def _call_export(
    *, ch, user_id=1, platform="challenge_post_16_9",
    phase="completed_score_win",
    challenger_attempt=None, challenged_attempt=None,
    rate_ok=True, png_bytes=b"PNG",
):
    from app.api.web_routes.vt_challenges import challenge_card_export

    user   = _user(uid=user_id)
    db     = MagicMock()
    firsts = iter([ch, challenger_attempt, challenged_attempt])
    db.query.return_value.filter.return_value.first.side_effect = lambda: next(firsts, None)

    captured = {}

    async def _mock_to_thread(fn, *args, **kw):
        return png_bytes

    with patch(f"{_BASE}.require_student_onboarding", return_value=None), \
         patch(f"{_BASE}._export_svc.check_export_rate_limit", return_value=rate_ok), \
         patch(f"{_BASE}.asyncio.to_thread", side_effect=_mock_to_thread), \
         patch("app.core.auth.create_challenge_render_token", return_value="mock_render_tok"), \
         patch("app.config.settings") as mock_settings:
        mock_settings.APP_INTERNAL_PORT = 8000
        try:
            result = asyncio.run(challenge_card_export(
                challenge_id=ch.id,
                request=MagicMock(),
                platform=platform,
                phase=phase,
                db=db,
                user=user,
            ))
            captured["result"] = result
        except Exception as exc:
            captured["exc"] = exc

    return captured


# ══════════════════════════════════════════════════════════════════════════════
# SC-01..03  preview route
# ══════════════════════════════════════════════════════════════════════════════

class TestChallengeCardPreview:

    def test_sc01_participant_gets_200(self):
        ch  = _challenge(challenger_id=1, challenged_id=2)
        cap = _call_preview(ch=ch, user_id=1)
        assert "exc" not in cap, f"Unexpected exception: {cap.get('exc')}"
        assert "template" in cap

    def test_sc01_template_name_post_16_9(self):
        ch  = _challenge(challenger_id=1, challenged_id=2)
        cap = _call_preview(ch=ch, user_id=1, platform="challenge_post_16_9")
        assert cap["template"] == "public/export/challenge/post_16_9.html"

    def test_sc01_template_name_story_9_16(self):
        ch  = _challenge(challenger_id=1, challenged_id=2)
        cap = _call_preview(ch=ch, user_id=1, platform="challenge_story_9_16")
        assert cap["template"] == "public/export/challenge/story_9_16.html"

    def test_sc02_non_participant_raises_403(self):
        ch  = _challenge(challenger_id=1, challenged_id=2)
        cap = _call_preview(ch=ch, user_id=99)
        exc = cap.get("exc")
        assert exc is not None
        from fastapi import HTTPException
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 403

    def test_sc03_invalid_platform_raises_422(self):
        ch  = _challenge(challenger_id=1, challenged_id=2)
        cap = _call_preview(ch=ch, user_id=1, platform="instagram_portrait")
        exc = cap.get("exc")
        assert exc is not None
        from fastapi import HTTPException
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# SC-04..07  export route
# ══════════════════════════════════════════════════════════════════════════════

class TestChallengeCardExport:

    def test_sc04_participant_gets_png(self):
        ch  = _challenge(challenger_id=1, challenged_id=2)
        cap = _call_export(ch=ch, user_id=1, png_bytes=b"FAKEPNG")
        assert "exc" not in cap, f"Unexpected exception: {cap.get('exc')}"
        result = cap["result"]
        assert isinstance(result, Response)
        assert result.media_type == "image/png"
        assert result.body == b"FAKEPNG"

    def test_sc05_non_participant_raises_403(self):
        ch  = _challenge(challenger_id=1, challenged_id=2)
        cap = _call_export(ch=ch, user_id=99)
        exc = cap.get("exc")
        assert exc is not None
        from fastapi import HTTPException
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 403

    def test_sc06_rate_limit_raises_429(self):
        ch  = _challenge(challenger_id=1, challenged_id=2)
        cap = _call_export(ch=ch, user_id=1, rate_ok=False)
        exc = cap.get("exc")
        assert exc is not None
        from fastapi import HTTPException
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 429

    def test_sc07_content_disposition_contains_id_phase_platform(self):
        ch  = _challenge(cid=42, challenger_id=1, challenged_id=2)
        cap = _call_export(
            ch=ch, user_id=1,
            phase="completed_score_win",
            platform="challenge_post_16_9",
            png_bytes=b"X",
        )
        result = cap["result"]
        cd = result.headers.get("Content-Disposition", "")
        assert "42"                   in cd
        assert "completed_score_win"  in cd
        assert "challenge_post_16_9"  in cd


# ══════════════════════════════════════════════════════════════════════════════
# SC-08..13  context builder
# ══════════════════════════════════════════════════════════════════════════════

class TestChallengeCardContext:

    def _ctx(self, ch, challenger_attempt=None, challenged_attempt=None,
             viewer_id=1, phase="completed_score_win"):
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        viewer = _user(uid=viewer_id)
        return _build_challenge_card_context(
            ch, viewer, challenger_attempt, challenged_attempt, phase
        )

    def test_sc08_score_win_viewer_is_winner(self):
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            winner_id=1, status=ChallengeStatus.COMPLETED,
        )
        ctx = self._ctx(ch, viewer_id=1, phase="completed_score_win")
        assert ctx["is_viewer_winner"] is True

    def test_sc08_score_win_loser_not_winner(self):
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            winner_id=1, status=ChallengeStatus.COMPLETED,
        )
        ctx = self._ctx(ch, viewer_id=2, phase="completed_score_win")
        assert ctx["is_viewer_winner"] is False

    def test_sc09_forfeit_loser_my_score_none(self):
        """Forfeit: challenged (uid=2) submitted nothing → my_score=None when viewer=2."""
        ch_attempt = _attempt(aid=10, score=80.0)
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            challenger_attempt_id=10, challenged_attempt_id=None,
            forfeit_user_id=2, winner_id=1,
        )
        ctx = self._ctx(
            ch,
            challenger_attempt=ch_attempt,
            challenged_attempt=None,
            viewer_id=2,
            phase="completed_forfeit_loss",
        )
        assert ctx["my_score"] is None

    def test_sc10_skill_deltas_from_viewer_attempt(self):
        """my_skill_scores = viewer's attempt.skill_deltas."""
        deltas = {"reactions": 0.08, "decision_making": -0.03}
        ch_attempt = _attempt(aid=20, score=70.0, skill_deltas=deltas)
        ch = _challenge(challenger_id=1, challenged_id=2, challenger_attempt_id=20)
        ctx = self._ctx(
            ch, challenger_attempt=ch_attempt, challenged_attempt=None,
            viewer_id=1, phase="completed_score_win",
        )
        assert ctx["my_skill_scores"].get("reactions") == pytest.approx(0.08)
        assert ctx["my_skill_scores"].get("decision_making") == pytest.approx(-0.03)

    def test_sc11_draw_context(self):
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            is_draw=True, winner_id=None,
        )
        ctx = self._ctx(ch, viewer_id=1, phase="completed_draw")
        assert ctx["is_draw"] is True
        assert ctx["winner_name"] is None

    def test_sc12_pending_scores_are_none(self):
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.PENDING,
            challenger_attempt_id=None,
            challenged_attempt_id=None,
        )
        ctx = self._ctx(ch, viewer_id=1, phase="challenge_sent")
        assert ctx["challenger_score"] is None
        assert ctx["challenged_score"] is None

    def test_sc13_cta_completed_score_win_play_again(self):
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            winner_id=1, status=ChallengeStatus.COMPLETED,
        )
        ctx = self._ctx(ch, viewer_id=1, phase="completed_score_win")
        assert ctx["cta_label"] == "Play again"

    def test_sc13_cta_challenge_sent_view_challenge(self):
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.PENDING,
        )
        ctx = self._ctx(ch, viewer_id=1, phase="challenge_sent")
        assert ctx["cta_label"] == "View challenge"


# ══════════════════════════════════════════════════════════════════════════════
# SC-14..16  template content
# ══════════════════════════════════════════════════════════════════════════════

class TestChallengeCardTemplates:

    def _post(self):
        with open(_POST_TEMPLATE, encoding="utf-8") as fh:
            return fh.read()

    def _story(self):
        with open(_STORY_TEMPLATE, encoding="utf-8") as fh:
            return fh.read()

    def test_sc14_post_template_has_phase_variable(self):
        assert "phase" in self._post()

    def test_sc14_post_template_has_challenger_score(self):
        assert "challenger_score" in self._post()

    def test_sc14_post_template_has_challenged_score(self):
        assert "challenged_score" in self._post()

    def test_sc15_story_template_has_phase_variable(self):
        assert "phase" in self._story()

    def test_sc15_story_template_has_my_skill_scores(self):
        assert "my_skill_scores" in self._story()

    def test_sc15_story_no_skill_data_message(self):
        assert "No skill data recorded" in self._story()

    def test_sc16_post_has_lfa_branding(self):
        assert "LFA" in self._post()

    def test_sc16_story_has_lfa_branding(self):
        assert "LFA" in self._story()

    def test_sc16_both_templates_are_standalone_html(self):
        for html in (self._post(), self._story()):
            assert "<!DOCTYPE html>" in html


# ══════════════════════════════════════════════════════════════════════════════
# SC-AUDIT-01..14  phase-based card system audit
# ══════════════════════════════════════════════════════════════════════════════

class TestSCPhaseAudit:

    # ── Auth ──────────────────────────────────────────────────────────────────

    def test_sc_audit_01_render_token_bypass_auth(self):
        """render_token accepted even when user=None (Playwright path)."""
        from app.api.web_routes.vt_challenges import challenge_card_preview

        ch     = _challenge(challenger_id=1, challenged_id=2)
        db     = MagicMock()
        firsts = iter([ch, None, None])
        db.query.return_value.filter.return_value.first.side_effect = lambda: next(firsts, None)

        captured = {}

        def _cap(tmpl, ctx, **kw):
            captured["template"] = tmpl
            return MagicMock(spec=HTMLResponse)

        with patch(f"{_BASE}._resolve_challenge_render_token", return_value=_user(uid=1)), \
             patch(f"{_BASE}.templates.TemplateResponse", side_effect=_cap):
            try:
                asyncio.run(challenge_card_preview(
                    challenge_id=ch.id,
                    request=MagicMock(),
                    platform="challenge_post_16_9",
                    phase="completed_score_win",
                    render_token="fake_jwt_here",
                    export=False,
                    db=db,
                    user=None,
                ))
            except Exception as exc:
                captured["exc"] = exc

        assert "exc" not in captured, f"Expected 200, got: {captured.get('exc')}"
        assert "template" in captured

    def test_sc_audit_02_export_render_url_has_token(self):
        """challenge_card_export builds a render URL with render_token= query param."""
        from app.api.web_routes.vt_challenges import challenge_card_export

        ch     = _challenge(challenger_id=1, challenged_id=2)
        db     = MagicMock()
        firsts = iter([ch])
        db.query.return_value.filter.return_value.first.side_effect = lambda: next(firsts, None)

        render_url_captured: dict = {}

        async def _capture_to_thread(fn, render_url, platform_arg):
            render_url_captured["url"] = render_url
            return b"PNG"

        with patch(f"{_BASE}._export_svc.check_export_rate_limit", return_value=True), \
             patch(f"{_BASE}.asyncio.to_thread", side_effect=_capture_to_thread), \
             patch("app.core.auth.create_challenge_render_token", return_value="tok_abc123"), \
             patch("app.config.settings") as ms:
            ms.APP_INTERNAL_PORT = 8000
            asyncio.run(challenge_card_export(
                challenge_id=ch.id,
                request=MagicMock(),
                platform="challenge_post_16_9",
                phase="completed_score_win",
                db=db,
                user=_user(uid=1),
            ))

        url = render_url_captured.get("url", "")
        assert "render_token=" in url, f"render_token= not found in URL: {url!r}"
        assert "tok_abc123" in url

    # ── Parameter contract ────────────────────────────────────────────────────

    def test_sc_audit_03_phase_param_present_in_signature(self):
        """preview route must declare a 'phase' parameter."""
        import inspect
        from app.api.web_routes.vt_challenges import challenge_card_preview
        assert "phase" in inspect.signature(challenge_card_preview).parameters

    def test_sc_audit_04_invalid_phase_raises_422(self):
        ch  = _challenge(challenger_id=1, challenged_id=2)
        cap = _call_preview(ch=ch, user_id=1, phase="bogus_phase_xyz")
        exc = cap.get("exc")
        assert exc is not None
        from fastapi import HTTPException
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 422

    # ── Phase lock gate ───────────────────────────────────────────────────────

    def test_sc_audit_05_locked_phase_export_raises_403(self):
        """challenge_accepted is locked (preview-only) after challenge completes."""
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.COMPLETED,
            winner_id=1,
        )
        cap = _call_export(ch=ch, user_id=1, phase="challenge_accepted")
        exc = cap.get("exc")
        assert exc is not None
        from fastapi import HTTPException
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 403

    def test_sc_audit_06_forfeit_win_preview_succeeds(self):
        """completed_forfeit_win phase is unlocked for the winner → preview 200."""
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.COMPLETED,
            forfeit_user_id=2,
            winner_id=1,
            forfeit_reason="no_show",
        )
        cap = _call_preview(ch=ch, user_id=1, phase="completed_forfeit_win")
        assert "exc" not in cap, f"Unexpected: {cap.get('exc')}"
        assert "template" in cap

    # ── Render token security ─────────────────────────────────────────────────

    def test_sc_audit_12_bad_render_token_returns_401(self):
        """_resolve_challenge_render_token returning None triggers 401."""
        from app.api.web_routes.vt_challenges import challenge_card_preview

        ch     = _challenge(challenger_id=1, challenged_id=2)
        db     = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = ch

        captured: dict = {}

        with patch(f"{_BASE}._resolve_challenge_render_token", return_value=None):
            try:
                asyncio.run(challenge_card_preview(
                    challenge_id=ch.id,
                    request=MagicMock(),
                    platform="challenge_post_16_9",
                    phase="completed_score_win",
                    render_token="token_for_wrong_challenge",
                    export=False,
                    db=db,
                    user=None,
                ))
            except Exception as exc:
                captured["exc"] = exc

        exc = captured.get("exc")
        assert exc is not None
        from fastapi import HTTPException
        assert isinstance(exc, HTTPException)
        assert exc.status_code == 401

    # ── Template correctness ──────────────────────────────────────────────────

    def test_sc_audit_13_score_win_badge_is_phase_guarded(self):
        """SCORE WIN badge in post_16_9.html must appear after the phase guard."""
        with open(_POST_TEMPLATE, encoding="utf-8") as fh:
            html = fh.read()
        assert "SCORE WIN" in html, "SCORE WIN badge should be present in template"
        guard_idx = html.find('phase == "completed_score_win"')
        badge_idx = html.find("SCORE WIN")
        assert guard_idx != -1, "Template must contain phase == 'completed_score_win' guard"
        assert badge_idx > guard_idx, (
            "SCORE WIN badge must appear inside the phase guard block, "
            f"guard at {guard_idx}, badge at {badge_idx}"
        )

    def test_sc_audit_14_empty_skill_deltas_no_skill_delta_phase(self):
        """Empty skill_deltas → skill_delta_result not in unlocked phases."""
        from app.api.web_routes.vt_challenges import get_unlocked_challenge_card_phases
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.COMPLETED,
            challenger_attempt_id=100,
        )
        my_attempt = _attempt(aid=100, score=75.0, skill_deltas={})
        phases = get_unlocked_challenge_card_phases(ch, viewer_id=1, my_attempt=my_attempt)
        assert "skill_delta_result" not in phases

    def test_sc_audit_14b_nonzero_skill_deltas_adds_skill_delta_phase(self):
        """Non-empty skill_deltas → skill_delta_result in unlocked phases."""
        from app.api.web_routes.vt_challenges import get_unlocked_challenge_card_phases
        ch = _challenge(
            challenger_id=1, challenged_id=2,
            status=ChallengeStatus.COMPLETED,
            challenger_attempt_id=100,
        )
        my_attempt = _attempt(aid=100, score=75.0, skill_deltas={"reactions": 0.05})
        phases = get_unlocked_challenge_card_phases(ch, viewer_id=1, my_attempt=my_attempt)
        assert "skill_delta_result" in phases
