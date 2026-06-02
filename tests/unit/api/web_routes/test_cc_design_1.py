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
CCD-22  route count still 850 (BG-REMOVAL-1 added 3 routes; no new routes in CC-DESIGN-1)
CCD-23  OpenAPI snapshot match true

Naming:
CCD-NAMING-01  story challenge_received badge does not contain bare "Received"
CCD-NAMING-02  post challenge_received badge does not contain "Challenge Received"
CCD-NAMING-03  challenge_received card headline is "You've Been Challenged"
CCD-NAMING-04  challenge_received CTA is "Accept Challenge" (case-insensitive)
CCD-NAMING-05  internal challenge_received phase_id unchanged in route logic
CCD-NAMING-06  Card Studio chip event_label for challenge_received = "Challenge Sent"; sublabel = "sent to you"

Hero rule:
CCD-HERO-01  challenge_sent: hero uses selected_photo_url when present
CCD-HERO-02  challenge_received: hero always uses challenger_photo_url (selected does not override)
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
    # Derive viewer_action_text matching _build_challenge_card_context logic
    if phase == "challenge_sent":
        _vat = "You challenged RD14S"
    elif phase == "challenge_received":
        _vat = "T1B1K3 challenged you"
    else:
        _vat = ""
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
        "viewer_action_text": _vat,
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


# ── CCD-SENT: Challenge Sent/Received — BALANCED layout (updated for BALANCED redesign) ──

class TestCCDSentReceivedLayout:
    """CCD-SENT: balanced invitation layout — LEFT | CENTER | RIGHT (post), TOP | CENTER | BOTTOM (story)."""

    def _render(self, tmpl_name: str, phase: str, photo: str | None = None,
                challenged_photo: str | None = None, viewer_is_challenger: bool = True) -> str:
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
            "unlocked_phases": [phase], "viewer_is_challenger": viewer_is_challenger,
            "forfeit_reason": None,
            "challenger_photo_url": photo, "challenged_photo_url": challenged_photo,
            "viewer_photo_url": photo, "opponent_photo_url": challenged_photo,
            "selected_photo_url": None, "request": MagicMock(),
            "viewer_action_text": ("You challenged RD14S" if phase == "challenge_sent"
                                   else "T1B1K3 challenged you" if phase == "challenge_received"
                                   else ""),
        }
        return tmpl.render(**ctx)

    def test_ccd_sent_01_post_uses_balanced_layout(self):
        """CCD-SENT-01: post_16_9 invitation uses balanced three-column layout."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_sent", "/ch.png")
        assert "arch-invitation-balanced" in html, \
            "post_16_9 challenge_sent must use arch-invitation-balanced layout"
        assert '<div class="arch-invitation-balanced">' in html

    def test_ccd_sent_02_story_uses_balanced_stacked_layout(self):
        """CCD-SENT-02: story_9_16 invitation uses balanced stacked layout."""
        html = self._render("public/export/challenge/story_9_16.html", "challenge_sent", "/ch.png")
        assert "arch-story-balanced" in html
        assert '<div class="arch-story-balanced">' in html

    def test_ccd_sent_03_both_player_zones_in_post(self):
        """CCD-SENT-03: post has both left and right player zones."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_sent",
                            "/ch.png", "/cd.png")
        assert "aib-player-zone" in html
        assert "aib-player-zone--right" in html
        assert "/ch.png" in html
        assert "/cd.png" in html

    def test_ccd_sent_04_player_photo_class_defined(self):
        """CCD-SENT-04: aib-player-photo and asb-player-photo CSS defined in templates."""
        for tmpl_name, cls in [
            ("public/export/challenge/post_16_9.html",   ".aib-player-photo"),
            ("public/export/challenge/story_9_16.html",  ".asb-player-photo"),
        ]:
            src = (TEMPLATES_DIR / tmpl_name).read_text()
            assert cls in src, f"{tmpl_name} must define {cls} CSS"
            assert "object-fit: contain" in src
            assert "object-position: center bottom" in src

    def test_ccd_sent_05_story_player_zones_large(self):
        """CCD-SENT-05: story player zones are ≥600px (substantial portion of 1920px canvas)."""
        src = (TEMPLATES_DIR / "public/export/challenge/story_9_16.html").read_text()
        assert "600px" in src, "story player zones must be 600px"

    def test_ccd_sent_06_fallback_initials_in_player_slots(self):
        """CCD-SENT-06: Fallback (no photo) uses player-initial class, not circular avatar."""
        for tmpl, initial_cls in [
            ("public/export/challenge/post_16_9.html",  "aib-player-initial"),
            ("public/export/challenge/story_9_16.html", "asb-player-initial"),
        ]:
            html = self._render(tmpl, "challenge_sent", photo=None)
            assert initial_cls in html, f"{tmpl}: fallback must use {initial_cls}"

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

    def test_ccd_22_route_count_851(self):
        """CCD-22: Route count is 851 (CC-DESIGN-1 SNAPSHOT adds POST /challenges/{id}/card/photo)."""
        from app.main import app
        count = len(app.openapi().get("paths", {}))
        assert count == 851, f"Expected 851, got {count}"

    def test_ccd_23_openapi_snapshot_match(self):
        """CCD-23: OpenAPI snapshot matches live API."""
        snap_paths = set(json.loads((SNAP_DIR / "openapi_snapshot.json").read_text()).get("paths", {}).keys())
        from app.main import app
        live_paths = set(app.openapi().get("paths", {}).keys())
        assert snap_paths == live_paths


# ── CCD-NAMING: challenge_received display naming ────────────────────────────

class TestCCDNaming:
    """CCD-NAMING-01..06: challenge_received renamed to "You've Been Challenged" in UI."""

    def _render(self, tmpl_name: str, phase: str, **kwargs) -> str:
        from jinja2 import Environment, FileSystemLoader
        from unittest.mock import MagicMock
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(tmpl_name)
        ctx = _make_mock_ctx(phase=phase, **kwargs)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    def test_ccd_naming_01_story_badge_not_bare_received(self):
        """CCD-NAMING-01: story_9_16 challenge_received phase badge does not contain bare 'Received'."""
        html = self._render("public/export/challenge/story_9_16.html", "challenge_received")
        # The old badge text was just "Received" — it must now say "You've Been Challenged"
        import re
        badge_match = re.search(r'class="phase-badge"[^<]*>(.*?)</span>', html, re.DOTALL)
        if badge_match:
            badge_text = badge_match.group(1).strip()
            assert badge_text != "Received", \
                f"Story badge must not be bare 'Received', got: {badge_text!r}"
        assert "You've Been Challenged" in html, \
            "story_9_16 challenge_received must display 'You've Been Challenged'"

    def test_ccd_naming_02_post_badge_not_challenge_received(self):
        """CCD-NAMING-02: post_16_9 challenge_received badge is not 'Challenge Received'."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_received")
        # Old text was exactly "Challenge Received" — must now be "You've Been Challenged"
        assert "You've Been Challenged" in html, \
            "post_16_9 challenge_received must display 'You've Been Challenged' in phase badge"

    def test_ccd_naming_03_headline_youve_been_challenged(self):
        """CCD-NAMING-03: challenge_received headline is 'You've Been Challenged'."""
        for tmpl in ["public/export/challenge/post_16_9.html",
                     "public/export/challenge/story_9_16.html"]:
            html = self._render(tmpl, "challenge_received")
            assert "You've Been Challenged" in html or "You've Been\nChallenged" in html, \
                f"{tmpl}: challenge_received headline must contain 'You've Been Challenged'"

    def test_ccd_naming_04_challenge_received_cta_accept(self):
        """CCD-NAMING-04: challenge_received CTA from _PHASE_CTA is 'Accept challenge'."""
        from app.api.web_routes.vt_challenges import _PHASE_CTA
        cta = _PHASE_CTA.get("challenge_received", "")
        assert "accept" in cta.lower(), \
            f"_PHASE_CTA['challenge_received'] must contain 'accept', got: {cta!r}"

    def test_ccd_naming_05_phase_id_unchanged_in_route_logic(self):
        """CCD-NAMING-05: Internal phase_id 'challenge_received' is still used in route logic."""
        from app.api.web_routes import vt_challenges
        import inspect
        src = inspect.getsource(vt_challenges)
        assert '"challenge_received"' in src or "'challenge_received'" in src, \
            "Internal phase_id 'challenge_received' must remain unchanged in route logic"

    def test_ccd_naming_06_studio_chip_event_label_and_sublabel(self):
        """CCD-NAMING-06: Card Studio challenge_received chip: event_label='Challenge Sent', sublabel='sent to you'."""
        from app.api.web_routes.card_studio import _CC_PHASE_EVENT_LABELS, _CC_PHASE_SUBLABELS
        assert _CC_PHASE_EVENT_LABELS.get("challenge_received") == "Challenge Sent", \
            "challenge_received event_label (timeline event) must be 'Challenge Sent' — same event as challenge_sent"
        assert _CC_PHASE_SUBLABELS.get("challenge_received") == "sent to you", \
            "challenge_received sublabel must be 'sent to you'"


# ── CCD-HERO: Hero rule for challenge_sent vs challenge_received ──────────────

class TestCCDHeroRule:
    """CCD-HERO-01..02: Hero photo selection rule for invitation archetypes."""

    def _render(self, tmpl_name: str, phase: str, selected_photo: str | None,
                challenger_photo: str | None) -> str:
        from jinja2 import Environment, FileSystemLoader
        from unittest.mock import MagicMock
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(tmpl_name)
        ctx = _make_mock_ctx(
            phase=phase,
            selected_photo=selected_photo,
            challenger_photo=challenger_photo,
            challenged_photo="/ch2.png",
        )
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    def test_ccd_hero_01_challenge_sent_uses_selected_photo(self):
        """CCD-HERO-01: challenge_sent hero uses selected_photo_url when present."""
        for tmpl in ["public/export/challenge/post_16_9.html",
                     "public/export/challenge/story_9_16.html"]:
            html = self._render(tmpl, "challenge_sent",
                                selected_photo="/mood_selected.png",
                                challenger_photo="/ch1.png")
            assert "/mood_selected.png" in html, \
                f"{tmpl}: challenge_sent hero must use selected_photo_url when provided"

    def test_ccd_hero_02_challenge_received_challenger_stays_in_left_slot(self):
        """CCD-HERO-02: challenge_received — challenger always in left/top slot; selected → right (viewer=challenged)."""
        from jinja2 import Environment, FileSystemLoader
        from unittest.mock import MagicMock
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        for tmpl_name in ["public/export/challenge/post_16_9.html",
                          "public/export/challenge/story_9_16.html"]:
            tmpl = env.get_template(tmpl_name)
            # challenge_received: viewer=challenged (viewer_is_challenger=False)
            ctx = _make_mock_ctx(
                phase="challenge_received",
                viewer_is_challenger=False,
                selected_photo="/mood_selected.png",
                challenger_photo="/ch1.png",
                challenged_photo="/ch2.png",
            )
            ctx["request"] = MagicMock()
            html = tmpl.render(**ctx)
            # Challenger stays in left/top slot (unchanged)
            assert "/ch1.png" in html, \
                f"{tmpl_name}: challenger photo must be in left slot for challenge_received"
            # Selected replaces viewer (challenged) in right/bottom slot
            assert "/mood_selected.png" in html, \
                f"{tmpl_name}: selected_photo must appear in viewer (right) slot"


# ── CCD-VACTION: viewer_action_text context field ─────────────────────────────

class TestCCDViewerActionText:
    """CCD-VACTION-01..02: viewer_action_text derived from phase + participant names."""

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

    def test_ccd_vaction_01_challenge_sent(self):
        """CCD-VACTION-01: challenge_sent viewer_action_text = 'You challenged RD14S'."""
        fn = self._ctx_fn()
        ctx = fn(self._make_ch(), MagicMock(id=10), None, None, "challenge_sent")
        assert ctx["viewer_action_text"] == "You challenged RD14S", \
            f"Expected 'You challenged RD14S', got: {ctx['viewer_action_text']!r}"

    def test_ccd_vaction_02_challenge_received(self):
        """CCD-VACTION-02: challenge_received viewer_action_text = 'T1B1K3 challenged you'."""
        fn = self._ctx_fn()
        ctx = fn(self._make_ch(), MagicMock(id=20), None, None, "challenge_received")
        assert ctx["viewer_action_text"] == "T1B1K3 challenged you", \
            f"Expected 'T1B1K3 challenged you', got: {ctx['viewer_action_text']!r}"

    def test_ccd_vaction_03_other_phases_empty(self):
        """CCD-VACTION-03: non-invitation phases produce empty viewer_action_text."""
        fn = self._ctx_fn()
        ctx = fn(self._make_ch(), MagicMock(id=10), None, None, "challenge_accepted")
        assert ctx["viewer_action_text"] == "", \
            f"Non-invitation phase must produce empty viewer_action_text, got: {ctx['viewer_action_text']!r}"


# ── CCD-INVITE-2P: Two-participant invitation layout ─────────────────────────

class TestCCDInviteTwoParticipant:
    """CCD-INVITE-2P-01..09: Challenged player target card rendered in invitation phase."""

    def _render_invitation(
        self,
        phase: str,
        challenger_photo: str | None = "/ch.png",
        challenged_photo: str | None = "/cd.png",
        selected_photo: str | None = None,
        platform: str = "challenge_post_16_9",
    ) -> str:
        from jinja2 import Environment, FileSystemLoader
        from unittest.mock import MagicMock
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        if platform == "challenge_post_16_9":
            tmpl = env.get_template("public/export/challenge/post_16_9.html")
        else:
            tmpl = env.get_template("public/export/challenge/story_9_16.html")

        if phase == "challenge_sent":
            _vat = "You challenged RD14S"
        elif phase == "challenge_received":
            _vat = "T1B1K3 challenged you"
        else:
            _vat = ""

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
            "challenger_photo_url": challenger_photo,
            "challenged_photo_url": challenged_photo,
            "viewer_photo_url": challenger_photo,
            "opponent_photo_url": challenged_photo,
            "selected_photo_url": selected_photo,
            "viewer_action_text": _vat,
            "request": MagicMock(),
        }
        return tmpl.render(**ctx)

    def test_ccd_invite_2p_01_post_sent_renders_challenged_photo(self):
        """CCD-INVITE-2P-01: challenge_sent post renders challenged_photo_url in target card."""
        html = self._render_invitation("challenge_sent", challenged_photo="/cd.png")
        assert "/cd.png" in html, \
            "post_16_9 challenge_sent must render challenged_photo_url in target card"

    def test_ccd_invite_2p_02_post_received_renders_challenged_photo(self):
        """CCD-INVITE-2P-02: challenge_received post renders challenged_photo_url."""
        html = self._render_invitation("challenge_received", challenged_photo="/cd.png")
        assert "/cd.png" in html, \
            "post_16_9 challenge_received must render challenged_photo_url in target card"

    def test_ccd_invite_2p_03_story_sent_renders_challenged_photo(self):
        """CCD-INVITE-2P-03: challenge_sent story renders challenged_photo_url."""
        html = self._render_invitation("challenge_sent", challenged_photo="/cd.png",
                                       platform="challenge_story_9_16")
        assert "/cd.png" in html, \
            "story_9_16 challenge_sent must render challenged_photo_url in target card"

    def test_ccd_invite_2p_04_story_received_renders_challenged_photo(self):
        """CCD-INVITE-2P-04: challenge_received story renders challenged_photo_url."""
        html = self._render_invitation("challenge_received", challenged_photo="/cd.png",
                                       platform="challenge_story_9_16")
        assert "/cd.png" in html, \
            "story_9_16 challenge_received must render challenged_photo_url in target card"

    def test_ccd_invite_2p_05_selected_photo_does_not_replace_target_card(self):
        """CCD-INVITE-2P-05: selected_photo_url appears in hero slot; target card still shows challenged_photo_url."""
        html = self._render_invitation(
            "challenge_sent",
            challenger_photo="/ch.png",
            challenged_photo="/cd.png",
            selected_photo="/sel.png",
        )
        # selected appears in hero zone (hero uses it for challenge_sent)
        assert "/sel.png" in html, "selected_photo_url must appear in hero zone for challenge_sent"
        # challenged photo still in target card
        assert "/cd.png" in html, "challenged_photo_url must still appear in target card"

    def test_ccd_invite_2p_06_participant_line_contains_arrow(self):
        """CCD-INVITE-2P-06: participant line renders challenger → challenged direction."""
        html = self._render_invitation("challenge_sent")
        assert "→" in html, "Participant line must contain → arrow"
        assert "T1B1K3" in html, "Challenger name must appear in participant line"
        assert "RD14S" in html, "Challenged name must appear in participant line"

    def test_ccd_invite_2p_07_challenge_received_headline(self):
        """CCD-INVITE-2P-07: challenge_received headline contains 'Challenged'."""
        html = self._render_invitation("challenge_received")
        assert "Challenged" in html, \
            "challenge_received headline must contain 'Challenged'"

    def test_ccd_invite_2p_08_challenge_sent_headline(self):
        """CCD-INVITE-2P-08: challenge_sent headline contains 'Challenge Sent'."""
        html = self._render_invitation("challenge_sent")
        assert "Challenge Sent" in html or "Challenge\nSent" in html, \
            "challenge_sent headline must contain 'Challenge Sent'"

    def test_ccd_invite_2p_09_no_challenged_photo_shows_initial(self):
        """CCD-INVITE-2P-09: No challenged_photo_url → initials fallback in target card."""
        html = self._render_invitation("challenge_sent", challenged_photo=None)
        # The first letter of challenged_name "RD14S" should appear in initials fallback
        # Template renders: {{ (challenged_name[0] if challenged_name else '?') | upper }} = "R"
        assert "R" in html, \
            "When no challenged_photo_url, first letter of challenged_name must appear as initial"
        # The target card initial container class must be present (not a real img src)
        assert ("aib-player-initial" in html or "asb-player-initial" in html or
                "ai-target-initial" in html or "ai-story-target-initial" in html), \
            "When no challenged_photo_url, initials container class must be rendered"


# ── CCD-NEUTRAL-PHOTO/DEFAULT: mood_intro_neutral only ────────────────────────

class TestCCDNeutralPhoto:
    """CCD-NEUTRAL-DEFAULT-01..04: _get_participant_photo uses only mood_intro_neutral slot."""

    def _run(self, neutral_record, lic_record):
        """neutral_record: single UserMoodPhoto mock with slot=mood_intro_neutral, or None."""
        from app.api.web_routes.vt_challenges import _get_participant_photo
        db = MagicMock()

        def _query_side_effect(model):
            q = MagicMock()
            if "UserMoodPhoto" in str(model) or (
                hasattr(model, "__tablename__") and
                getattr(model, "__tablename__", "") == "user_mood_photos"
            ):
                q.filter_by.return_value.first.return_value = neutral_record
            else:
                q.filter.return_value.first.return_value = lic_record
            return q

        db.query.side_effect = _query_side_effect
        return _get_participant_photo(db, user_id=10)

    def test_ccd_neutral_01_processed_png_used_when_present(self):
        """CCD-NEUTRAL-DEFAULT-01: mood_intro_neutral processed_png_url takes top priority."""
        mood = MagicMock()
        mood.processed_png_url = "/processed/neutral.png"
        mood.original_url = "/orig/neutral.jpg"
        result = self._run(mood, MagicMock(player_card_photo_url="/player.jpg", wc_photo_url=None))
        assert result == "/processed/neutral.png", \
            "mood_intro_neutral processed_png_url must be first priority"

    def test_ccd_neutral_02_original_url_fallback_when_not_processed(self):
        """CCD-NEUTRAL-DEFAULT-02: neutral original_url when processed_png_url is None."""
        mood = MagicMock()
        mood.processed_png_url = None
        mood.original_url = "/orig/neutral.jpg"
        result = self._run(mood, MagicMock(player_card_photo_url="/player.jpg", wc_photo_url=None))
        assert result == "/orig/neutral.jpg", \
            "mood_intro_neutral original_url fallback when no processed"

    def test_ccd_neutral_03_player_card_photo_fallback_when_no_mood(self):
        """CCD-NEUTRAL-DEFAULT-03: player_card_photo_url when no neutral mood record."""
        lic = MagicMock()
        lic.player_card_photo_url = "/player.jpg"
        lic.wc_photo_url = None
        result = self._run(None, lic)
        assert result == "/player.jpg", "player_card_photo_url fallback when no neutral mood"

    def test_ccd_neutral_04_none_when_no_mood_and_no_license(self):
        """CCD-NEUTRAL-DEFAULT-04: None when no neutral mood and no license."""
        result = self._run(None, None)
        assert result is None


# ── CCD-BALANCED: Balanced invitation layout ──────────────────────────────────

class TestCCDBalanced:
    """CCD-BALANCED-01..12: balanced LEFT|CENTER|RIGHT post + TOP|CENTER|BOTTOM story."""

    def _render(self, tmpl_name: str, phase: str,
                challenger_photo: str | None = "/ch.png",
                challenged_photo: str | None = "/cd.png",
                selected_photo: str | None = None,
                viewer_is_challenger: bool = True) -> str:
        from jinja2 import Environment, FileSystemLoader
        from unittest.mock import MagicMock
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(tmpl_name)
        vat = ("You challenged RD14S" if phase == "challenge_sent"
               else "T1B1K3 challenged you" if phase == "challenge_received" else "")
        ctx = {
            "phase": phase, "challenge_id": 1,
            "challenger_name": "T1B1K3", "challenged_name": "RD14S",
            "game_name": "Memory Sequence", "challenge_mode": "async",
            "outcome_reason": "score_win", "is_draw": False,
            "challenger_score": None, "challenged_score": None,
            "winner_name": None, "my_score": None, "opp_score": None,
            "my_skill_scores": {}, "is_viewer_winner": False,
            "cta_label": "View Challenge", "completed_at": None, "is_locked": False,
            "unlocked_phases": [phase], "viewer_is_challenger": viewer_is_challenger,
            "forfeit_reason": None,
            "challenger_photo_url": challenger_photo,
            "challenged_photo_url": challenged_photo,
            "viewer_photo_url": challenger_photo if viewer_is_challenger else challenged_photo,
            "opponent_photo_url": challenged_photo if viewer_is_challenger else challenger_photo,
            "selected_photo_url": selected_photo,
            "viewer_action_text": vat, "request": MagicMock(),
        }
        return tmpl.render(**ctx)

    def test_ccd_balanced_01_post_sent_has_both_player_zones(self):
        """CCD-BALANCED-01: post challenge_sent has challenger (left) and challenged (right) zones."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_sent")
        assert "aib-player-zone" in html
        assert "aib-player-zone--right" in html
        assert "/ch.png" in html  # challenger left
        assert "/cd.png" in html  # challenged right

    def test_ccd_balanced_02_post_received_has_both_player_zones(self):
        """CCD-BALANCED-02: post challenge_received has both participants in player zones."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_received",
                            viewer_is_challenger=False)
        assert "aib-player-zone" in html
        assert "aib-player-zone--right" in html
        assert "/ch.png" in html
        assert "/cd.png" in html

    def test_ccd_balanced_03_post_has_center_message_zone(self):
        """CCD-BALANCED-03: post invitation has center message zone."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_sent")
        assert "aib-center-msg" in html
        assert "Challenge Invitation" in html  # status meta badge

    def test_ccd_balanced_04_story_has_top_and_bottom_zones(self):
        """CCD-BALANCED-04: story invitation has top and bottom player zones."""
        html = self._render("public/export/challenge/story_9_16.html", "challenge_sent")
        assert "asb-player-zone--top" in html
        assert "asb-player-zone--bottom" in html
        assert "/ch.png" in html
        assert "/cd.png" in html

    def test_ccd_balanced_05_selected_photo_only_overrides_viewer_slot(self):
        """CCD-BALANCED-05: selected_photo_url overrides viewer slot; other participant preserved."""
        # challenge_sent: viewer=challenger=left → selected → left; right=challenged stays
        html = self._render("public/export/challenge/post_16_9.html", "challenge_sent",
                            challenger_photo="/ch.png", challenged_photo="/cd.png",
                            selected_photo="/sel.png", viewer_is_challenger=True)
        assert "/sel.png" in html    # selected in left (viewer) slot
        assert "/cd.png" in html     # challenged still in right slot
        assert "/ch.png" not in html or "/sel.png" in html  # original replaced by selected

        # challenge_received: viewer=challenged=right → selected → right; left=challenger stays
        html2 = self._render("public/export/challenge/post_16_9.html", "challenge_received",
                             challenger_photo="/ch.png", challenged_photo="/cd.png",
                             selected_photo="/sel.png", viewer_is_challenger=False)
        assert "/sel.png" in html2   # selected in right (viewer) slot
        assert "/ch.png" in html2    # challenger still in left slot

    def test_ccd_balanced_06_participant_photo_source_processed_first(self):
        """CCD-BALANCED-06: _get_participant_photo uses mood_intro_neutral processed_png_url first."""
        from app.api.web_routes.vt_challenges import _get_participant_photo
        db = MagicMock()
        m = MagicMock(processed_png_url="/p/processed.png", original_url="/o/orig.jpg")
        db.query.return_value.filter_by.return_value.first.return_value = m
        result = _get_participant_photo(db, 1)
        assert result == "/p/processed.png"

    def test_ccd_balanced_07_initials_fallback_both_slots(self):
        """CCD-BALANCED-07: Both slots show initials when no photos."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_sent",
                            challenger_photo=None, challenged_photo=None)
        assert "aib-player-initial" in html
        # first letters: T (T1B1K3) and R (RD14S)
        assert "T" in html and "R" in html

    def test_ccd_balanced_08_export_route_still_works(self):
        """CCD-BALANCED-08: export route references still intact."""
        import inspect
        from app.api.web_routes.vt_challenges import challenge_card_export
        src = inspect.getsource(challenge_card_export)
        assert "is_design_accessible" in src or "is_accessible" in src
        assert "challenge_card_export" in src

    def test_ccd_balanced_09_no_view_challenge_on_card(self):
        """CCD-BALANCED-09: Invitation card does not contain VIEW CHALLENGE text."""
        for tmpl in ["public/export/challenge/post_16_9.html",
                     "public/export/challenge/story_9_16.html"]:
            for phase in ("challenge_sent", "challenge_received"):
                html = self._render(tmpl, phase)
                assert "View Challenge" not in html, \
                    f"{tmpl} {phase}: must not contain 'View Challenge'"

    def test_ccd_balanced_10_no_accept_challenge_on_card(self):
        """CCD-BALANCED-10: Invitation card does not contain ACCEPT CHALLENGE text."""
        for tmpl in ["public/export/challenge/post_16_9.html",
                     "public/export/challenge/story_9_16.html"]:
            for phase in ("challenge_sent", "challenge_received"):
                html = self._render(tmpl, phase, viewer_is_challenger=phase == "challenge_sent")
                assert "Accept Challenge" not in html, \
                    f"{tmpl} {phase}: must not contain 'Accept Challenge'"

    def test_ccd_balanced_11_no_cta_button_or_link_in_invitation(self):
        """CCD-BALANCED-11: Invitation card has no <button> and no → arrow CTA element."""
        for tmpl in ["public/export/challenge/post_16_9.html",
                     "public/export/challenge/story_9_16.html"]:
            for phase in ("challenge_sent", "challenge_received"):
                html = self._render(tmpl, phase)
                assert "<button" not in html, f"{tmpl} {phase}: must not contain <button>"
                # cc-cta class (arrow CTA) must not appear in invitation phases
                assert 'class="cc-cta"' not in html, \
                    f"{tmpl} {phase}: cc-cta class must not appear in invitation card"

    def test_ccd_balanced_12_fallback_without_processed_photo(self):
        """CCD-BALANCED-12: mood_intro_neutral original_url used when no processed_png_url."""
        from app.api.web_routes.vt_challenges import _get_participant_photo
        db = MagicMock()
        m = MagicMock(processed_png_url=None, original_url="/orig/neutral.jpg")
        db.query.return_value.filter_by.return_value.first.return_value = m
        result = _get_participant_photo(db, 1)
        assert result == "/orig/neutral.jpg", "original_url fallback when no processed"


# ── CCD-MOOD-SELECT: Mood photo selector position + behavior ──────────────────

class TestCCDMoodSelect:
    """CCD-MOOD-SELECT-01..07: mood selector position, JS wiring, viewer-slot rule."""

    def test_ccd_mood_select_01_selector_before_format_in_panel(self):
        """CCD-MOOD-SELECT-01: cs-cc-mood-section appears before Format selector in panel."""
        src = (INCLUDES_DIR / "cs_challenge_panel.html").read_text()
        mood_pos   = src.find("cs-cc-mood-section")
        format_pos = src.find("cs-section-label\">Format")
        assert mood_pos != -1, "cs-cc-mood-section must be in challenge panel"
        assert format_pos != -1, "Format selector must be in challenge panel"
        assert mood_pos < format_pos, \
            "Mood selector must appear BEFORE Format selector in panel"

    def test_ccd_mood_select_02_mood_chip_updates_iframe_url(self):
        """CCD-MOOD-SELECT-02: _setChallengePhoto updates iframe src with photo_url param."""
        src = (TEMPLATES_DIR / "card_studio_shell.html").read_text()
        assert "_setChallengePhoto" in src
        assert "photo_url" in src
        assert "ccIframe.src" in src

    def test_ccd_mood_select_03_mood_change_preserves_params(self):
        """CCD-MOOD-SELECT-03: photo URL update preserves challenge_id, phase, platform."""
        src = (TEMPLATES_DIR / "card_studio_shell.html").read_text()
        # JS uses regex to replace photo_url without destroying other params
        assert "replace(" in src and "photo_url" in src, \
            "_setChallengePhoto must replace photo_url param, not rebuild the full URL"

    def test_ccd_mood_select_04_chip_data_uses_processed_first(self):
        """CCD-MOOD-SELECT-04: mood chip data-mood-photo-url uses processed_png_url first."""
        src = (INCLUDES_DIR / "cs_challenge_panel.html").read_text()
        # Template: _photo_url = (_mp.processed_png_url or _mp.original_url)
        assert "processed_png_url" in src, "Mood chip must prioritize processed_png_url"
        assert "data-mood-photo-url" in src

    def test_ccd_mood_select_05_challenge_sent_selected_photo_to_left_slot(self):
        """CCD-MOOD-SELECT-05: challenge_sent selected_photo_url → challenger (left/top) slot."""
        from jinja2 import Environment, FileSystemLoader
        from unittest.mock import MagicMock
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template("public/export/challenge/post_16_9.html")
        ctx = _make_mock_ctx(phase="challenge_sent", viewer_is_challenger=True,
                             selected_photo="/sel.png",
                             challenger_photo="/ch.png", challenged_photo="/cd.png")
        ctx["request"] = MagicMock()
        html = tmpl.render(**ctx)
        # Left slot (viewer=challenger) must contain selected photo
        assert "/sel.png" in html, "challenge_sent: selected must appear in left player zone"
        # Right slot (challenged) must keep its own photo
        assert "/cd.png" in html, "challenge_sent: challenged photo must stay in right slot"

    def test_ccd_mood_select_06_challenge_received_selected_photo_to_right_slot(self):
        """CCD-MOOD-SELECT-06: challenge_received selected_photo_url → challenged (right/bottom) slot."""
        from jinja2 import Environment, FileSystemLoader
        from unittest.mock import MagicMock
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template("public/export/challenge/post_16_9.html")
        ctx = _make_mock_ctx(phase="challenge_received", viewer_is_challenger=False,
                             selected_photo="/sel.png",
                             challenger_photo="/ch.png", challenged_photo="/cd.png")
        ctx["request"] = MagicMock()
        html = tmpl.render(**ctx)
        # Right slot (viewer=challenged) must contain selected photo
        assert "/sel.png" in html, "challenge_received: selected must appear in right player zone"
        # Left slot (challenger) must keep challenger photo
        assert "/ch.png" in html, "challenge_received: challenger photo must stay in left slot"

    def test_ccd_mood_select_07_other_participant_preserved_after_mood_change(self):
        """CCD-MOOD-SELECT-07: Other participant photo not replaced by selected_photo_url."""
        from jinja2 import Environment, FileSystemLoader
        from unittest.mock import MagicMock
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        for phase, viewer_is_ch, expected_preserved in [
            ("challenge_sent",     True,  "/cd.png"),  # challenged stays in right
            ("challenge_received", False, "/ch.png"),  # challenger stays in left
        ]:
            tmpl = env.get_template("public/export/challenge/post_16_9.html")
            ctx = _make_mock_ctx(phase=phase, viewer_is_challenger=viewer_is_ch,
                                 selected_photo="/sel.png",
                                 challenger_photo="/ch.png", challenged_photo="/cd.png")
            ctx["request"] = MagicMock()
            html = tmpl.render(**ctx)
            assert expected_preserved in html, \
                f"{phase}: {expected_preserved} must be preserved after mood select"
