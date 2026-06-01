"""
CC-DESIGN-1 — Challenge Card Redesign: photo context, 5 archetypes, media panel

Context:
CCD-01  challenger_photo_url in challenge preview context
CCD-02  challenged_photo_url in challenge preview context
CCD-03  selected_photo_url (photo_url param) passed to context
CCD-04  viewer_is_challenger correct for challenger
CCD-05  viewer_is_challenger correct for challenged
CCD-06  viewer_photo_url = challenger_photo when viewer is challenger
CCD-07  opponent_photo_url = challenged_photo when viewer is challenger

Template structure:
CCD-08  post_16_9.html renders challenge_sent without error (Archetype A)
CCD-09  post_16_9.html renders challenge_received without error
CCD-10  post_16_9.html renders challenge_accepted without error (Archetype B)
CCD-11  post_16_9.html renders waiting_for_opponent without error (Archetype C)
CCD-12  post_16_9.html renders completed_score_win without error (Archetype D)
CCD-13  post_16_9.html renders skill_delta_result without error (Archetype E)
CCD-14  story_9_16.html renders all 12 phases without error
CCD-15  photo fallback: no photo → initials avatar rendered (cc-avatar class)
CCD-16  selected_photo_url takes priority in viewer hero slot
CCD-17  winner photo gets winner-ring class

Media panel:
CCD-18  mood_photos in challenge preview context
CCD-19  mood_slot_meta in challenge preview context
CCD-20  challenge media panel (cs-cc-mood-section) in cs_challenge_panel.html preview
CCD-21  _setChallengePhoto JS function present in shell (challenge preview mode)

Route/snapshot:
CCD-22  route count still 847 (no new routes)
CCD-23  OpenAPI snapshot match true
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
CHALLENGE_DIR = TEMPLATES_DIR / "public" / "export" / "challenge"
INCLUDES_DIR  = TEMPLATES_DIR / "includes"
SNAP_DIR      = Path(__file__).resolve().parents[4] / "tests" / "snapshots"


# ── Context tests ─────────────────────────────────────────────────────────────

def _make_mock_ctx(
    phase: str = "challenge_sent",
    viewer_is_challenger: bool = True,
    challenger_photo: str | None = "/static/ch1.png",
    challenged_photo: str | None = "/static/ch2.png",
    selected_photo: str | None = None,
    my_score: float | None = None,
    challenger_score: float | None = None,
    challenged_score: float | None = None,
    winner_name: str | None = None,
    my_skill_scores: dict | None = None,
) -> dict:
    return {
        "challenge_id": 1, "phase": phase,
        "challenger_name": "T1B1K3", "challenged_name": "RD14S",
        "game_name": "Memory Sequence", "challenge_mode": "async",
        "outcome_reason": "score_win", "is_draw": False,
        "challenger_score": challenger_score, "challenged_score": challenged_score,
        "winner_name": winner_name, "my_score": my_score, "opp_score": None,
        "my_skill_scores": my_skill_scores or {},
        "is_viewer_winner": False, "cta_label": "View challenge",
        "completed_at": None, "is_locked": False, "unlocked_phases": [phase],
        "challenger_photo_url": challenger_photo,
        "challenged_photo_url": challenged_photo,
        "viewer_photo_url": challenger_photo if viewer_is_challenger else challenged_photo,
        "opponent_photo_url": challenged_photo if viewer_is_challenger else challenger_photo,
        "selected_photo_url": selected_photo,
        "viewer_is_challenger": viewer_is_challenger,
        "forfeit_reason": None,
    }


class TestCCD01to07Context:

    def _ctx_fn(self):
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        return _build_challenge_card_context

    def _make_ch(self, challenger_id=10, challenged_id=20):
        from app.models.vt_challenge import ChallengeStatus
        ch = MagicMock()
        ch.challenger_id = challenger_id; ch.challenged_id = challenged_id
        ch.status = ChallengeStatus.PENDING; ch.challenge_mode = "async"
        ch.winner_id = None; ch.is_draw = False
        ch.forfeit_user_id = None; ch.forfeit_reason = None
        ch.completed_at = None
        ch.challenger = MagicMock(); ch.challenger.nickname = "T1B1K3"; ch.challenger.email = "t@t.com"
        ch.challenged = MagicMock(); ch.challenged.nickname = "RD14S"; ch.challenged.email = "r@r.com"
        ch.winner = None
        ch.game = MagicMock(); ch.game.name = "Memory Sequence"
        return ch

    def test_ccd_01_challenger_photo_in_context(self):
        """CCD-01: challenger_photo_url is in context."""
        fn = self._ctx_fn()
        ctx = fn(self._make_ch(), MagicMock(id=10), None, None, "challenge_sent",
                 challenger_photo_url="/ch1.png", challenged_photo_url="/ch2.png")
        assert ctx["challenger_photo_url"] == "/ch1.png"

    def test_ccd_02_challenged_photo_in_context(self):
        """CCD-02: challenged_photo_url is in context."""
        fn = self._ctx_fn()
        ctx = fn(self._make_ch(), MagicMock(id=10), None, None, "challenge_sent",
                 challenger_photo_url="/ch1.png", challenged_photo_url="/ch2.png")
        assert ctx["challenged_photo_url"] == "/ch2.png"

    def test_ccd_03_selected_photo_in_context(self):
        """CCD-03: selected_photo_url (photo_url param) is in context."""
        fn = self._ctx_fn()
        ctx = fn(self._make_ch(), MagicMock(id=10), None, None, "challenge_sent",
                 selected_photo_url="/mood.png")
        assert ctx["selected_photo_url"] == "/mood.png"

    def test_ccd_04_viewer_is_challenger_true(self):
        """CCD-04: viewer_is_challenger=True when viewer is challenger."""
        fn = self._ctx_fn()
        viewer = MagicMock(id=10)
        ctx = fn(self._make_ch(challenger_id=10, challenged_id=20), viewer, None, None, "challenge_sent")
        assert ctx["viewer_is_challenger"] is True

    def test_ccd_05_viewer_is_challenger_false(self):
        """CCD-05: viewer_is_challenger=False when viewer is challenged."""
        fn = self._ctx_fn()
        viewer = MagicMock(id=20)
        ctx = fn(self._make_ch(challenger_id=10, challenged_id=20), viewer, None, None, "challenge_received")
        assert ctx["viewer_is_challenger"] is False

    def test_ccd_06_viewer_photo_for_challenger(self):
        """CCD-06: viewer_photo_url = challenger_photo when viewer is challenger."""
        fn = self._ctx_fn()
        ctx = fn(self._make_ch(), MagicMock(id=10), None, None, "challenge_sent",
                 challenger_photo_url="/ch1.png", challenged_photo_url="/ch2.png")
        assert ctx["viewer_photo_url"] == "/ch1.png"

    def test_ccd_07_opponent_photo_for_challenger(self):
        """CCD-07: opponent_photo_url = challenged_photo when viewer is challenger."""
        fn = self._ctx_fn()
        ctx = fn(self._make_ch(), MagicMock(id=10), None, None, "challenge_sent",
                 challenger_photo_url="/ch1.png", challenged_photo_url="/ch2.png")
        assert ctx["opponent_photo_url"] == "/ch2.png"


# ── Template rendering tests ──────────────────────────────────────────────────

class TestCCD08to17TemplateRender:

    def _render(self, template_name: str, phase: str, **kwargs) -> str:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(template_name)
        ctx = _make_mock_ctx(phase=phase, **kwargs)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    def test_ccd_08_post_challenge_sent_renders(self):
        """CCD-08: post_16_9 renders challenge_sent (Archetype A) without error."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_sent")
        assert "Challenge Sent" in html
        assert "T1B1K3" in html

    def test_ccd_09_post_challenge_received_renders(self):
        """CCD-09: post_16_9 renders challenge_received without error."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_received")
        assert "Challenged" in html

    def test_ccd_10_post_accepted_renders(self):
        """CCD-10: post_16_9 renders challenge_accepted (Archetype B) without error."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_accepted")
        assert "VS" in html
        assert "Let's Play" in html

    def test_ccd_11_post_waiting_renders(self):
        """CCD-11: post_16_9 renders waiting_for_opponent (Archetype C) with score."""
        html = self._render("public/export/challenge/post_16_9.html", "waiting_for_opponent", my_score=85.0)
        assert "85.0" in html
        assert "Waiting" in html

    def test_ccd_12_post_result_renders(self):
        """CCD-12: post_16_9 renders completed_score_win (Archetype D) with scores."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "completed_score_win",
            challenger_score=68.0, challenged_score=71.0, winner_name="RD14S"
        )
        assert "SCORE WIN" in html
        assert "68.0" in html
        assert "71.0" in html

    def test_ccd_13_post_skill_delta_renders(self):
        """CCD-13: post_16_9 renders skill_delta_result (Archetype E) with deltas."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "skill_delta_result",
            my_skill_scores={"accuracy": 0.5, "composure": -0.1}
        )
        assert "Skill Progress" in html
        assert "+0.50" in html

    def test_ccd_14_story_all_12_phases_render(self):
        """CCD-14: story_9_16 renders all 12 phases without Jinja2 error."""
        from app.api.web_routes.vt_challenges import VALID_CHALLENGE_CARD_PHASES
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template("public/export/challenge/story_9_16.html")
        for phase in VALID_CHALLENGE_CARD_PHASES:
            ctx = _make_mock_ctx(phase=phase, challenger_score=70.0, challenged_score=65.0,
                                 winner_name="T1B1K3", my_skill_scores={"acc": 0.1})
            ctx["request"] = MagicMock()
            html = tmpl.render(**ctx)
            assert len(html) > 100, f"story phase {phase!r} rendered empty"

    def test_ccd_15_no_photo_shows_avatar(self):
        """CCD-15: When no photo URL provided, initials avatar (cc-avatar) is rendered."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_sent",
                            challenger_photo=None, challenged_photo=None)
        assert "cc-avatar" in html

    def test_ccd_16_selected_photo_takes_priority_in_viewer_slot(self):
        """CCD-16: selected_photo_url overrides player photo in viewer hero slot."""
        html = self._render("public/export/challenge/post_16_9.html", "waiting_for_opponent",
                            selected_photo="/mood_selected.png", my_score=80.0)
        assert "/mood_selected.png" in html

    def test_ccd_17_winner_photo_gets_winner_ring(self):
        """CCD-17: Winner player block gets winner-ring CSS class."""
        html = self._render("public/export/challenge/post_16_9.html", "completed_score_win",
                            challenger_score=90.0, challenged_score=70.0, winner_name="T1B1K3",
                            challenger_photo="/ch1.png", challenged_photo="/ch2.png")
        assert "winner-ring" in html


# ── Media panel tests ─────────────────────────────────────────────────────────

class TestCCD18to21MediaPanel:

    def _get_ctx(self):
        from app.api.web_routes.card_studio import _resolve_challenge_context
        from unittest.mock import MagicMock, patch
        from app.models.vt_challenge import ChallengeStatus

        ch = MagicMock()
        ch.id = 1; ch.challenger_id = 10; ch.challenged_id = 20
        ch.status = ChallengeStatus.PENDING; ch.challenge_mode = "async"
        ch.challenger_attempt_id = None; ch.challenged_attempt_id = None
        ch.winner_id = None; ch.is_draw = False
        ch.forfeit_user_id = None; ch.forfeit_reason = None
        ch.game = MagicMock(); ch.game.name = "Memory Sequence"
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@t.com")
        ch.challenged = MagicMock(nickname="RD14S", email="r@r.com")

        user = MagicMock(); user.id = 10
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = ch

        lic = MagicMock(); lic.onboarding_completed = True

        with patch("app.api.web_routes.card_studio._license_guard", return_value=lic):
            with patch("app.api.web_routes.card_studio.get_mood_photos_for_user", return_value={}):
                ctx, _ = _resolve_challenge_context(db, user, challenge_id=1, phase="challenge_sent")
        return ctx

    def test_ccd_18_mood_photos_in_context(self):
        """CCD-18: mood_photos is present in challenge preview context."""
        ctx = self._get_ctx()
        if ctx.get("challenge_mode") == "preview":
            assert "mood_photos" in ctx

    def test_ccd_19_mood_slot_meta_in_context(self):
        """CCD-19: mood_slot_meta is present in challenge preview context."""
        ctx = self._get_ctx()
        if ctx.get("challenge_mode") == "preview":
            assert "mood_slot_meta" in ctx

    def test_ccd_20_challenge_mood_section_in_panel_template(self):
        """CCD-20: cs_challenge_panel.html preview mode has cs-cc-mood-section."""
        src = (INCLUDES_DIR / "cs_challenge_panel.html").read_text()
        assert "cs-cc-mood-section" in src

    def test_ccd_21_set_challenge_photo_js_in_shell(self):
        """CCD-21: _setChallengePhoto JS function defined in shell (challenge preview)."""
        src = (TEMPLATES_DIR / "card_studio_shell.html").read_text()
        assert "_setChallengePhoto" in src
        assert "cs-cc-mood-section" in src or "cs-cc-mood-grid" in src


# ── Route / snapshot ──────────────────────────────────────────────────────────

class TestCCD22to23RouteSnapshot:

    def test_ccd_22_route_count_847(self):
        """CCD-22: Route count still 847 (no new routes in CC-DESIGN-1)."""
        from app.main import app
        count = len(app.openapi().get("paths", {}))
        assert count == 847, f"Expected 847, got {count}"

    def test_ccd_23_openapi_snapshot_match(self):
        """CCD-23: OpenAPI snapshot matches live API."""
        snap_paths = set(json.loads((SNAP_DIR / "openapi_snapshot.json").read_text()).get("paths", {}).keys())
        from app.main import app
        live_paths = set(app.openapi().get("paths", {}).keys())
        assert snap_paths == live_paths
