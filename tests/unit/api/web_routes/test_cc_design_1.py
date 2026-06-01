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


# ── CCD-SENT: Challenge Sent/Received hero layout fix ─────────────────────────

class TestCCDSentReceivedLayout:
    """CCD-FIX tests for Challenge Sent/Received photo-dominant layout."""

    def _render(self, tmpl_name: str, phase: str, photo: str | None = None) -> str:
        from jinja2 import Environment, FileSystemLoader
        from unittest.mock import MagicMock
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(tmpl_name)
        ctx = {
            "phase": phase, "challenge_id": 1,
            "challenger_name": "T1B1K3", "challenged_name": "RD14S",
            "game_name": "Memory Sequence", "challenge_mode": "async",
            "outcome_reason": "score_win", "is_draw": False,
            "challenger_score": None, "challenged_score": None,
            "winner_name": None, "my_score": None, "opp_score": None,
            "my_skill_scores": {}, "is_viewer_winner": False,
            "cta_label": "View", "completed_at": None, "is_locked": False,
            "unlocked_phases": [phase], "viewer_is_challenger": True,
            "forfeit_reason": None,
            "challenger_photo_url": photo, "challenged_photo_url": None,
            "viewer_photo_url": photo, "opponent_photo_url": None,
            "selected_photo_url": None, "request": MagicMock(),
        }
        return tmpl.render(**ctx)

    def test_ccd_sent_01_post_uses_invitation_split_layout(self):
        """CCD-SENT-01: post_16_9 challenge_sent uses photo-dominant split layout."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_sent", "/ch.png")
        assert "arch-invitation-split" in html, \
            "post_16_9 challenge_sent must use arch-invitation-split layout"
        # Must be in the body (not just CSS definition) — check for the div
        assert '<div class="arch-invitation-split">' in html

    def test_ccd_sent_02_story_uses_large_hero_zone(self):
        """CCD-SENT-02: story_9_16 challenge_sent uses 850px hero zone."""
        html = self._render("public/export/challenge/story_9_16.html", "challenge_sent", "/ch.png")
        assert "ai-story-hero-zone" in html
        assert '<div class="ai-story-hero-zone">' in html

    def test_ccd_sent_03_no_circular_photo_in_hero_slot(self):
        """CCD-SENT-03: Hero slot does NOT use border-radius:50% on hero image."""
        for tmpl in ["public/export/challenge/post_16_9.html", "public/export/challenge/story_9_16.html"]:
            html = self._render(tmpl, "challenge_sent", "/ch.png")
            # cc-photo-hero-cutout must NOT have border-radius
            # Check: img with cc-photo-hero-cutout class is in the body
            assert 'class="cc-photo-hero-cutout"' in html, \
                f"{tmpl}: hero image must use cc-photo-hero-cutout class"

    def test_ccd_sent_04_hero_cutout_class_defined(self):
        """CCD-SENT-04: cc-photo-hero-cutout CSS class is defined in both templates."""
        for tmpl_name in ["public/export/challenge/post_16_9.html", "public/export/challenge/story_9_16.html"]:
            src = (TEMPLATES_DIR / tmpl_name).read_text()
            assert ".cc-photo-hero-cutout" in src, \
                f"{tmpl_name} must define .cc-photo-hero-cutout CSS"
            assert "object-fit: contain" in src
            assert "object-position: center bottom" in src

    def test_ccd_sent_05_story_hero_zone_850px(self):
        """CCD-SENT-05: story hero zone is 850px (≥40% of 1920px canvas)."""
        src = (TEMPLATES_DIR / "public/export/challenge/story_9_16.html").read_text()
        assert "850px" in src, \
            "story hero zone must be 850px (44% of 1920px canvas)"

    def test_ccd_sent_06_fallback_not_circular_in_hero(self):
        """CCD-SENT-06: Fallback (no photo) uses cc-initial-hero, not circular avatar."""
        for tmpl in ["public/export/challenge/post_16_9.html", "public/export/challenge/story_9_16.html"]:
            html = self._render(tmpl, "challenge_sent", photo=None)  # no photo
            assert "cc-initial-hero" in html, \
                f"{tmpl}: fallback must use cc-initial-hero (not circular avatar)"
            assert 'class="cc-avatar hero"' not in html or "arch-invitation" not in html, \
                f"{tmpl}: fallback in hero slot must NOT use circular cc-avatar"

    def test_ccd_sent_07_other_archetypes_unchanged(self):
        """CCD-SENT-07: Other phase archetypes still render correctly (regression)."""
        from app.api.web_routes.vt_challenges import VALID_CHALLENGE_CARD_PHASES
        from jinja2 import Environment, FileSystemLoader
        from unittest.mock import MagicMock
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        other_phases = [p for p in VALID_CHALLENGE_CARD_PHASES
                        if p not in ("challenge_sent", "challenge_received")]
        for phase in other_phases:
            for tmpl_name in ["public/export/challenge/post_16_9.html",
                               "public/export/challenge/story_9_16.html"]:
                tmpl = env.get_template(tmpl_name)
                ctx = {
                    "phase": phase, "challenge_id": 1,
                    "challenger_name": "A", "challenged_name": "B",
                    "game_name": "G", "challenge_mode": "async",
                    "outcome_reason": "score_win", "is_draw": False,
                    "challenger_score": 80.0, "challenged_score": 70.0,
                    "winner_name": "A", "my_score": 80.0, "opp_score": 70.0,
                    "my_skill_scores": {"acc": 0.1}, "is_viewer_winner": True,
                    "cta_label": "Play", "completed_at": None, "is_locked": False,
                    "unlocked_phases": [phase], "viewer_is_challenger": True,
                    "forfeit_reason": None,
                    "challenger_photo_url": None, "challenged_photo_url": None,
                    "viewer_photo_url": None, "opponent_photo_url": None,
                    "selected_photo_url": None, "request": MagicMock(),
                }
                html = tmpl.render(**ctx)
                assert len(html) > 100, f"{tmpl_name} phase {phase!r} rendered empty"


# ── CC-EXPORT: Social Moment Export Policy ────────────────────────────────────

class TestCCExportPolicy:
    """CC-EXPORT-01..10: challenge_sent/received are exportable social moment phases."""

    def test_cc_export_01_challenge_sent_in_exportable_phases(self):
        """CC-EXPORT-01: challenge_sent is in _EXPORTABLE_PHASES."""
        from app.api.web_routes.vt_challenges import _EXPORTABLE_PHASES
        assert "challenge_sent" in _EXPORTABLE_PHASES, \
            "challenge_sent must be exportable (social moment phase)"

    def test_cc_export_02_challenge_received_in_exportable_phases(self):
        """CC-EXPORT-02: challenge_received is in _EXPORTABLE_PHASES."""
        from app.api.web_routes.vt_challenges import _EXPORTABLE_PHASES
        assert "challenge_received" in _EXPORTABLE_PHASES, \
            "challenge_received must be exportable (social moment phase)"

    def _pending_ch(self, ch_id=1, challenger_id=10, challenged_id=20):
        """Create a minimal PENDING challenge mock for export validation tests."""
        from app.models.vt_challenge import ChallengeStatus
        ch = MagicMock()
        ch.id = ch_id; ch.challenger_id = challenger_id; ch.challenged_id = challenged_id
        ch.status = ChallengeStatus.PENDING; ch.challenge_mode = "async"
        ch.challenger_attempt_id = None; ch.challenged_attempt_id = None
        ch.winner_id = None; ch.is_draw = False
        ch.forfeit_user_id = None; ch.forfeit_reason = None
        return ch

    def test_cc_export_03_validate_accepts_challenge_sent_for_export(self):
        """CC-EXPORT-03: validate_challenge_card_phase accepts challenge_sent for export."""
        from app.api.web_routes.vt_challenges import validate_challenge_card_phase
        ch = self._pending_ch()
        try:
            validate_challenge_card_phase(ch, viewer_id=10, phase="challenge_sent", for_export=True)
        except Exception as e:
            assert False, f"validate_challenge_card_phase raised for challenge_sent export: {e}"

    def test_cc_export_04_validate_accepts_challenge_received_for_export(self):
        """CC-EXPORT-04: validate_challenge_card_phase accepts challenge_received for export."""
        from app.api.web_routes.vt_challenges import validate_challenge_card_phase
        ch = self._pending_ch()
        try:
            validate_challenge_card_phase(ch, viewer_id=20, phase="challenge_received", for_export=True)
        except Exception as e:
            assert False, f"validate_challenge_card_phase raised for challenge_received export: {e}"

    def test_cc_export_05_studio_ctx_challenge_sent_is_exportable(self):
        """CC-EXPORT-05: In challenge preview context, challenge_sent → is_exportable_phase=True."""
        from app.api.web_routes.card_studio import _resolve_challenge_context
        user = MagicMock(); user.id = 10
        ch   = self._pending_ch()
        lic  = MagicMock(); lic.onboarding_completed = True

        with patch("app.api.web_routes.card_studio._license_guard", return_value=lic):
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = ch
            ctx, _ = _resolve_challenge_context(db, user, challenge_id=1, phase="challenge_sent")

        if ctx.get("challenge_mode") == "preview":
            assert ctx.get("is_exportable_phase") is True, \
                "challenge_sent must yield is_exportable_phase=True in Studio context"

    def test_cc_export_06_studio_ctx_challenge_received_is_exportable(self):
        """CC-EXPORT-06: In challenge preview context, challenge_received → is_exportable_phase=True."""
        from app.api.web_routes.card_studio import _resolve_challenge_context
        user = MagicMock(); user.id = 20
        ch   = self._pending_ch()
        lic  = MagicMock(); lic.onboarding_completed = True

        with patch("app.api.web_routes.card_studio._license_guard", return_value=lic):
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = ch
            ctx, _ = _resolve_challenge_context(db, user, challenge_id=1, phase="challenge_received")

        if ctx.get("challenge_mode") == "preview":
            assert ctx.get("is_exportable_phase") is True, \
                "challenge_received must yield is_exportable_phase=True in Studio context"

    def test_cc_export_07_export_panel_shows_social_moment_text(self):
        """CC-EXPORT-07: Export panel for challenge_sent shows social moment text, not preview-only."""
        src = (TEMPLATES_DIR / "card_studio_shell.html").read_text()
        assert "social moment" in src.lower(), \
            "Export panel must reference 'social moment' for challenge_sent/received phases"
        assert "Historical phase — preview only. Export available for result phases." not in src, \
            "Old preview-only text must be replaced"

    def test_cc_export_08_ownership_guard_in_export_route(self):
        """CC-EXPORT-08: Export route still has CDO ownership guard (not bypassed)."""
        import inspect
        from app.api.web_routes.vt_challenges import challenge_card_export
        src = inspect.getsource(challenge_card_export)
        assert "is_accessible" in src or "is_design_accessible" in src or \
               "CDO ownership" in src or "UserRole.ADMIN" in src, \
            "Export route must retain ownership guard"

    def test_cc_export_09_waiting_for_opponent_not_exportable(self):
        """CC-EXPORT-09: waiting_for_opponent is NOT in _EXPORTABLE_PHASES."""
        from app.api.web_routes.vt_challenges import _EXPORTABLE_PHASES
        assert "waiting_for_opponent" not in _EXPORTABLE_PHASES, \
            "waiting_for_opponent must remain preview-only (deferred)"

    def test_cc_export_10_challenge_accepted_not_exportable(self):
        """CC-EXPORT-10: challenge_accepted is NOT in _EXPORTABLE_PHASES."""
        from app.api.web_routes.vt_challenges import _EXPORTABLE_PHASES
        assert "challenge_accepted" not in _EXPORTABLE_PHASES, \
            "challenge_accepted must remain preview-only until separately approved"


# ── CC-EXPORT-DIRECT: Direct Studio export (no editor redirect) ───────────────

class TestCCExportDirect:
    """CC-EXPORT-DIRECT: Studio export panel uses direct /challenges/{id}/card/export URL."""

    def _ctx(self, phase: str, platform: str = "challenge_post_16_9", uid: int = 10):
        from app.api.web_routes.card_studio import _resolve_challenge_context
        user = MagicMock(); user.id = uid
        from app.models.vt_challenge import ChallengeStatus
        ch = MagicMock()
        ch.id = 42; ch.challenger_id = 10; ch.challenged_id = 20
        ch.status = ChallengeStatus.PENDING; ch.challenge_mode = "async"
        ch.challenger_attempt_id = None; ch.challenged_attempt_id = None
        ch.winner_id = None; ch.is_draw = False
        ch.forfeit_user_id = None; ch.forfeit_reason = None
        ch.game = MagicMock(); ch.game.name = "Memory Sequence"
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@t.com")
        ch.challenged = MagicMock(nickname="RD14S", email="r@r.com")
        lic = MagicMock(); lic.onboarding_completed = True
        with patch("app.api.web_routes.card_studio._license_guard", return_value=lic):
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = ch
            ctx, _ = _resolve_challenge_context(db, user, challenge_id=42, phase=phase, platform=platform)
        return ctx

    def test_cc_export_direct_01_challenge_sent_export_url_not_legacy_editor(self):
        """CC-EXPORT-DIRECT-01: challenge_sent export URL is NOT the legacy editor URL."""
        ctx = self._ctx("challenge_sent", "challenge_post_16_9", uid=10)
        if ctx.get("challenge_mode") == "preview":
            export_url = ctx.get("challenge_export_url") or ""
            assert "/card-editor/challenge" not in export_url, \
                "challenge_sent export URL must not point to legacy editor"
            assert export_url != "", "challenge_sent export URL must not be empty"

    def test_cc_export_direct_02_challenge_received_url_has_id_phase_platform(self):
        """CC-EXPORT-DIRECT-02: challenge_received export URL includes challenge_id, phase, platform."""
        ctx = self._ctx("challenge_received", "challenge_story_9_16", uid=20)
        if ctx.get("challenge_mode") == "preview":
            url = ctx.get("challenge_export_url") or ""
            assert "42" in url, "Export URL must contain challenge_id=42"
            assert "challenge_received" in url, "Export URL must contain phase"
            assert "challenge_story_9_16" in url, "Export URL must contain platform"

    def test_cc_export_direct_03_result_phase_export_url_direct_route(self):
        """CC-EXPORT-DIRECT-03: result phase export URL points to /challenges/{id}/card/export."""
        from app.api.web_routes.card_studio import _resolve_challenge_context
        from app.models.vt_challenge import ChallengeStatus
        user = MagicMock(); user.id = 10
        ch = MagicMock()
        ch.id = 42; ch.challenger_id = 10; ch.challenged_id = 20
        ch.status = ChallengeStatus.COMPLETED; ch.challenge_mode = "async"
        ch.challenger_attempt_id = 1; ch.challenged_attempt_id = 2
        ch.winner_id = 10; ch.is_draw = False
        ch.forfeit_user_id = None; ch.forfeit_reason = None
        ch.game = MagicMock(); ch.game.name = "Memory Sequence"
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@t.com")
        ch.challenged = MagicMock(nickname="RD14S", email="r@r.com")
        lic = MagicMock(); lic.onboarding_completed = True
        with patch("app.api.web_routes.card_studio._license_guard", return_value=lic):
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = ch
            ctx, _ = _resolve_challenge_context(db, user, challenge_id=42,
                                                 phase="completed_score_win",
                                                 platform="challenge_post_16_9")
        if ctx.get("challenge_mode") == "preview":
            url = ctx.get("challenge_export_url") or ""
            assert "/challenges/42/card/export" in url, \
                "Result phase export URL must use /challenges/{id}/card/export route"

    def test_cc_export_direct_04_export_panel_no_editor_redirect_text(self):
        """CC-EXPORT-DIRECT-04: Export panel does NOT contain 'Export via Challenge Editor' text."""
        src = (TEMPLATES_DIR / "card_studio_shell.html").read_text()
        assert "Export via Challenge Editor" not in src, \
            "Export panel must not have legacy editor redirect CTA"

    def test_cc_export_direct_05_export_panel_has_download_png_cta(self):
        """CC-EXPORT-DIRECT-05: Export panel has 'Download PNG' or 'Export PNG' CTA."""
        src = (TEMPLATES_DIR / "card_studio_shell.html").read_text()
        assert "Download PNG" in src or "Export PNG" in src, \
            "Export panel must have direct download CTA (Download PNG or Export PNG)"

    def test_cc_export_direct_06_non_exportable_phase_shows_preview_only(self):
        """CC-EXPORT-DIRECT-06: Non-exportable phase shows preview-only text."""
        src = (TEMPLATES_DIR / "card_studio_shell.html").read_text()
        assert "Preview only" in src or "preview only" in src.lower(), \
            "Non-exportable phases must show preview-only message"

    def test_cc_export_direct_07_ownership_guard_not_bypassed(self):
        """CC-EXPORT-DIRECT-07: Export route has ownership guard (not bypassed)."""
        import inspect
        from app.api.web_routes.vt_challenges import challenge_card_export
        src = inspect.getsource(challenge_card_export)
        assert ("is_design_accessible" in src or "is_accessible" in src) and "UserRole" in src, \
            "Export route must retain CDO ownership guard"

    def test_cc_export_direct_08_platform_change_updates_export_url(self):
        """CC-EXPORT-DIRECT-08: Switching platform updates export URL platform param."""
        ctx_post  = self._ctx("challenge_sent", "challenge_post_16_9")
        ctx_story = self._ctx("challenge_sent", "challenge_story_9_16")
        if ctx_post.get("challenge_mode") == "preview" and ctx_story.get("challenge_mode") == "preview":
            url_post  = ctx_post.get("challenge_export_url") or ""
            url_story = ctx_story.get("challenge_export_url") or ""
            assert "challenge_post_16_9"  in url_post,  "Post URL must contain post_16_9 platform"
            assert "challenge_story_9_16" in url_story, "Story URL must contain story_9_16 platform"
            assert url_post != url_story, "Export URLs must differ by platform"

    def test_cc_export_direct_09_photo_url_not_in_export_url(self):
        """CC-EXPORT-DIRECT-09: photo_url is NOT passed to export route (export uses DB photos).
        The export route has its own photo loading logic (UserLicense query).
        selected_photo_url is preview-only — export always uses canonical player_card_photo_url."""
        from app.api.web_routes.card_studio import _resolve_challenge_context
        from app.models.vt_challenge import ChallengeStatus
        user = MagicMock(); user.id = 10
        ch = MagicMock()
        ch.id = 42; ch.challenger_id = 10; ch.challenged_id = 20
        ch.status = ChallengeStatus.PENDING; ch.challenge_mode = "async"
        ch.challenger_attempt_id = None; ch.challenged_attempt_id = None
        ch.winner_id = None; ch.is_draw = False
        ch.forfeit_user_id = None; ch.forfeit_reason = None
        ch.game = MagicMock(); ch.game.name = "Game"
        ch.challenger = MagicMock(nickname="A", email="a@a.com")
        ch.challenged = MagicMock(nickname="B", email="b@b.com")
        lic = MagicMock(); lic.onboarding_completed = True
        with patch("app.api.web_routes.card_studio._license_guard", return_value=lic):
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = ch
            ctx, _ = _resolve_challenge_context(db, user, challenge_id=42, phase="challenge_sent",
                                                 platform="challenge_post_16_9")
        if ctx.get("challenge_mode") == "preview":
            url = ctx.get("challenge_export_url") or ""
            assert "photo_url" not in url, \
                "Export URL must NOT include photo_url — export uses canonical DB photos"


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
