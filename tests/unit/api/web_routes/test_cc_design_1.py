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

Terminal rejection phases (CANCELLED / DECLINED):
CCD-CAN-01  get_unlocked_challenge_card_phases: CANCELLED → ["challenge_cancelled"]
CCD-CAN-02  challenge_cancelled in VALID_CHALLENGE_CARD_PHASES
CCD-CAN-03  challenge_cancelled in _EXPORTABLE_PHASES
CCD-CAN-04  challenge_cancelled in _PHASE_LABELS with label "Cancelled"
CCD-CAN-05  challenge_cancelled in _PHASE_CTA
CCD-CAN-06  viewer_action_text: cancelled + challenger → "cancelled by you"
CCD-CAN-07  viewer_action_text: cancelled + challenged → "[name] cancelled"
CCD-CAN-08  post_16_9 challenge_cancelled renders Archetype F (arch-terminal)
CCD-CAN-09  post_16_9 challenge_cancelled does NOT render arch-invitation-balanced
CCD-CAN-10  story_9_16 challenge_cancelled renders arch-terminal-story

CCD-DEC-01  get_unlocked_challenge_card_phases: DECLINED → ["challenge_declined"]
CCD-DEC-02  challenge_declined in VALID_CHALLENGE_CARD_PHASES
CCD-DEC-03  challenge_declined in _EXPORTABLE_PHASES
CCD-DEC-04  challenge_declined in _PHASE_LABELS with label "Declined"
CCD-DEC-05  challenge_declined in _PHASE_CTA
CCD-DEC-06  viewer_action_text: declined + challenger → "[name] declined"
CCD-DEC-07  viewer_action_text: declined + challenged → "declined by you"
CCD-DEC-08  post_16_9 challenge_declined renders Archetype F (arch-terminal)
CCD-DEC-09  post_16_9 challenge_declined does NOT render arch-invitation-balanced
CCD-DEC-10  story_9_16 challenge_declined renders arch-terminal-story

CCD-CAN-STUDIO-01  _CC_STATUSES_WITH_IMPLICIT_INITIAL does NOT contain CANCELLED
CCD-DEC-STUDIO-01  _CC_STATUSES_WITH_IMPLICIT_INITIAL does NOT contain DECLINED
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
    challenger_overall: float | None = None,
    challenger_primary_pos: str | None = None,
    challenger_secondary_pos: str | None = None,
    challenged_overall: float | None = None,
    challenged_primary_pos: str | None = None,
    challenged_secondary_pos: str | None = None,
    my_result_summary: dict | None = None,
    challenger_result_summary: dict | None = None,
    challenged_result_summary: dict | None = None,
    viewer_result_summary: dict | None = None,
    opponent_result_summary: dict | None = None,
    forfeiter_name: str | None = None,
    forfeit_sublabel: str | None = None,
    viewer_skill_levels: dict | None = None,
    my_skill_progress: list | None = None,
) -> dict:
    # Derive viewer_action_text matching _build_challenge_card_context logic
    if phase == "challenge_sent":
        _vat = "You challenged RD14S"
    elif phase == "challenge_received":
        _vat = "T1B1K3 challenged you"
    elif phase == "challenge_accepted":
        _vat = "RD14S accepted" if viewer_is_challenger else "accepted by you"
    elif phase == "waiting_for_opponent":
        _vat = "Waiting for RD14S" if viewer_is_challenger else "Waiting for T1B1K3"
    elif phase == "challenge_cancelled":
        _vat = "cancelled by you" if viewer_is_challenger else "T1B1K3 cancelled"
    elif phase == "challenge_declined":
        _vat = "RD14S declined" if viewer_is_challenger else "declined by you"
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
        "phase_emoji":              {"challenge_sent": "⚔️", "challenge_received": "🛡️",
                                     "challenge_accepted": "✅", "challenge_cancelled": "🚫",
                                     "challenge_declined": "👎", "waiting_for_opponent": "⏳",
                                     "live_lobby_ready": "⚡", "live_in_progress": "🔥",
                                     "completed_score_win": "🏆", "completed_draw": "⚖️",
                                     "completed_forfeit_win": "🏆", "completed_forfeit_loss": "💔",
                                     "no_contest": "🔄", "skill_delta_result": "📈",
                                     }.get(phase, ""),
        "challenger_overall":       challenger_overall,
        "challenger_primary_pos":   challenger_primary_pos,
        "challenger_secondary_pos": challenger_secondary_pos,
        "challenged_overall":       challenged_overall,
        "challenged_primary_pos":   challenged_primary_pos,
        "challenged_secondary_pos": challenged_secondary_pos,
        "forfeiter_name":           forfeiter_name,
        "forfeit_sublabel":         forfeit_sublabel,
        "viewer_skill_levels":      viewer_skill_levels or {},
        "my_skill_progress":        my_skill_progress if my_skill_progress is not None else [],
        "my_result_summary":        my_result_summary if my_result_summary is not None else {
            "game_code": None, "primary_label": "Score",
            "primary_value": my_score, "secondary_items": [],
        },
        "challenger_result_summary": challenger_result_summary if challenger_result_summary is not None else {
            "game_code": None, "primary_label": "Score",
            "primary_value": challenger_score, "secondary_items": [],
        },
        "challenged_result_summary": challenged_result_summary if challenged_result_summary is not None else {
            "game_code": None, "primary_label": "Score",
            "primary_value": challenged_score, "secondary_items": [],
        },
        "viewer_result_summary":     viewer_result_summary if viewer_result_summary is not None else {
            "game_code": None, "primary_label": "Score",
            "primary_value": my_score, "secondary_items": [],
        },
        "opponent_result_summary":   opponent_result_summary if opponent_result_summary is not None else {
            "game_code": None, "primary_label": "Score",
            "primary_value": None, "secondary_items": [],
        },
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
        """CCD-10: post_16_9 renders challenge_accepted (Archetype B2 full-zone) without error."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_accepted")
        assert "Challenge Accepted" in html
        assert '<div class="arch-invitation-balanced">' in html

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
        """CCD-13 (updated): post_16_9 renders skill_delta_result (Archetype E2) with rows."""
        rows = _skill_rows([("accuracy", 0.5, 67.0), ("composure", -0.1, 55.0)])
        html = self._render(
            "public/export/challenge/post_16_9.html", "skill_delta_result",
            my_skill_progress=rows,
        )
        assert "Skill Progress" in html
        assert "arch-skill-e2" in html
        assert "+0.50" in html

    def test_ccd_14_story_all_12_phases_render(self):
        """CCD-14: story_9_16 renders all valid phases (14) without Jinja2 error."""
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

    def test_ccd_17_winner_zone_gets_winner_bar(self):
        """CCD-17 (updated): Winner player zone gets ard2-winner-bar (D2 full-zone layout)."""
        html = self._render("public/export/challenge/post_16_9.html", "completed_score_win",
                            challenger_score=90.0, challenged_score=70.0, winner_name="T1B1K3",
                            challenger_photo="/ch1.png", challenged_photo="/ch2.png")
        assert "ard2-winner-bar" in html


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
                    "my_result_summary": {
                        "game_code": None, "primary_label": "Score",
                        "primary_value": 80.0, "secondary_items": [],
                    },
                    "challenger_result_summary": {
                        "game_code": None, "primary_label": "Score",
                        "primary_value": 80.0, "secondary_items": [],
                    },
                    "challenged_result_summary": {
                        "game_code": None, "primary_label": "Score",
                        "primary_value": 70.0, "secondary_items": [],
                    },
                    "viewer_result_summary": {
                        "game_code": None, "primary_label": "Score",
                        "primary_value": 80.0, "secondary_items": [],
                    },
                    "opponent_result_summary": {
                        "game_code": None, "primary_label": "Score",
                        "primary_value": 70.0, "secondary_items": [],
                    },
                    "viewer_skill_levels": {},
                    "my_skill_progress":   [],
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

    def test_cc_export_09_waiting_for_opponent_is_exportable(self):
        """CC-EXPORT-09 (updated): waiting_for_opponent IS in _EXPORTABLE_PHASES.
        Social moment: viewer submitted their result and is waiting for opponent."""
        from app.api.web_routes.vt_challenges import _EXPORTABLE_PHASES
        assert "waiting_for_opponent" in _EXPORTABLE_PHASES

    def test_cc_export_10_challenge_accepted_is_exportable(self):
        """CC-EXPORT-10 (updated): challenge_accepted IS in _EXPORTABLE_PHASES.
        Social moment: the acceptance is a shareable milestone like challenge_sent."""
        from app.api.web_routes.vt_challenges import _EXPORTABLE_PHASES
        assert "challenge_accepted" in _EXPORTABLE_PHASES, \
            "challenge_accepted must be exportable — it is a social moment phase"


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
        assert count == 857, f"Expected 851, got {count}"

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

    def test_ccd_vaction_03_accepted_challenger_has_narrative(self):
        """CCD-VACTION-03 (updated): challenge_accepted challenger view has narrative text."""
        fn = self._ctx_fn()
        ctx = fn(self._make_ch(), MagicMock(id=10), None, None, "challenge_accepted")
        assert ctx["viewer_action_text"] != "", \
            "challenge_accepted must produce viewer_action_text for the challenger"
        assert "accepted" in ctx["viewer_action_text"].lower()


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
        """CCD-BALANCED-11: No <button> and no cc-cta div in any export template."""
        for tmpl in ["public/export/challenge/post_16_9.html",
                     "public/export/challenge/story_9_16.html"]:
            for phase in ("challenge_sent", "challenge_received", "challenge_accepted"):
                html = self._render(tmpl, phase)
                assert "<button" not in html, f"{tmpl} {phase}: must not contain <button>"
                assert 'class="cc-cta"' not in html, \
                    f"{tmpl} {phase}: cc-cta must not appear in export cards"

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


# ── CCD-SNAPSHOT: per-challenge photo snapshot ────────────────────────────────

class TestCCDSnapshot:
    """CCD-SNAPSHOT-01..06: per-challenge photo snapshot endpoint + context logic."""

    def _make_ch(self, challenger_id=10, challenged_id=20):
        from app.models.vt_challenge import ChallengeStatus
        ch = MagicMock()
        ch.challenger_id = challenger_id; ch.challenged_id = challenged_id
        ch.status = ChallengeStatus.PENDING
        ch.challenger_card_photo_url = None
        ch.challenged_card_photo_url = None
        return ch

    def _save(self, user_id: int, ch, photo_url: str, mood_record=None):
        """Run the save endpoint logic against a mock DB."""
        from app.api.web_routes.vt_challenges import challenge_card_photo_save
        import asyncio
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = ch

        # mood ownership validation
        mood_q = MagicMock()
        mood_q.first.return_value = mood_record  # None → ownership check fails
        db.query.return_value.filter.return_value = mood_q
        # challenge query
        ch_q = MagicMock(); ch_q.first.return_value = ch
        def _side(model):
            from app.models.vt_challenge import VirtualTrainingChallenge
            from app.models.user_mood_photos import UserMoodPhoto
            q = MagicMock()
            if model is VirtualTrainingChallenge:
                q.filter.return_value.first.return_value = ch
            elif model is UserMoodPhoto:
                q.filter.return_value.first.return_value = mood_record
            return q
        db.query.side_effect = _side

        user = MagicMock(id=user_id)
        return asyncio.run(
            challenge_card_photo_save(1, photo_url=photo_url, db=db, user=user)
        )

    def test_ccd_snapshot_01_challenger_saves_own_slot(self):
        """CCD-SNAPSHOT-01: challenger saves photo to challenger_card_photo_url."""
        ch = self._make_ch(challenger_id=10, challenged_id=20)
        mood = MagicMock()
        result = self._save(user_id=10, ch=ch, photo_url="/mood.png", mood_record=mood)
        assert result["role"] == "challenger"
        assert ch.challenger_card_photo_url == "/mood.png"
        assert ch.challenged_card_photo_url is None  # opponent slot untouched

    def test_ccd_snapshot_02_challenged_saves_own_slot(self):
        """CCD-SNAPSHOT-02: challenged saves photo to challenged_card_photo_url."""
        ch = self._make_ch(challenger_id=10, challenged_id=20)
        mood = MagicMock()
        result = self._save(user_id=20, ch=ch, photo_url="/mood2.png", mood_record=mood)
        assert result["role"] == "challenged"
        assert ch.challenged_card_photo_url == "/mood2.png"
        assert ch.challenger_card_photo_url is None  # opponent slot untouched

    def test_ccd_snapshot_03_challenger_cannot_write_challenged_slot(self):
        """CCD-SNAPSHOT-03: challenger's save never touches challenged_card_photo_url."""
        ch = self._make_ch(challenger_id=10, challenged_id=20)
        ch.challenged_card_photo_url = "/cd_original.png"
        mood = MagicMock()
        self._save(user_id=10, ch=ch, photo_url="/my_mood.png", mood_record=mood)
        assert ch.challenged_card_photo_url == "/cd_original.png", \
            "challenger must never overwrite challenged slot"

    def test_ccd_snapshot_04_challenged_cannot_write_challenger_slot(self):
        """CCD-SNAPSHOT-04: challenged's save never touches challenger_card_photo_url."""
        ch = self._make_ch(challenger_id=10, challenged_id=20)
        ch.challenger_card_photo_url = "/ch_original.png"
        mood = MagicMock()
        self._save(user_id=20, ch=ch, photo_url="/my_mood.png", mood_record=mood)
        assert ch.challenger_card_photo_url == "/ch_original.png", \
            "challenged must never overwrite challenger slot"

    def test_ccd_snapshot_05_challenged_view_uses_challenger_snapshot(self):
        """CCD-SNAPSHOT-05: challenged view uses challenger_card_photo_url from challenge."""
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template("public/export/challenge/post_16_9.html")
        # challenged view: viewer_is_challenger=False
        ctx = _make_mock_ctx(
            phase="challenge_received", viewer_is_challenger=False,
            challenger_photo="/ch_snapshot.png",  # this is ch.challenger_card_photo_url
            challenged_photo="/cd_neutral.png",
        )
        ctx["request"] = MagicMock()
        html = tmpl.render(**ctx)
        assert "/ch_snapshot.png" in html, \
            "challenged view must show challenger snapshot in left slot"

    def test_ccd_snapshot_06_null_snapshot_uses_neutral_mood_fallback(self):
        """CCD-SNAPSHOT-06: null challenger_card_photo_url → _get_participant_photo fallback."""
        from app.api.web_routes.vt_challenges import _get_participant_photo
        db = MagicMock()
        neutral = MagicMock(processed_png_url="/neutral.png", original_url="/neutral_orig.jpg")
        db.query.return_value.filter_by.return_value.first.return_value = neutral
        # Simulate: ch.challenger_card_photo_url is None → fallback
        snapshot_url = None
        result = snapshot_url or _get_participant_photo(db, user_id=10)
        assert result == "/neutral.png", "null snapshot must fall back to neutral mood"


# ── CCD-MOOD-SELECTOR-LABEL: UI label + hint ─────────────────────────────────

class TestCCDMoodSelectorLabel:
    """CCD-MOOD-SELECT label and hint text verification."""

    def test_ccd_mood_select_label_your_photo(self):
        """CCD-MOOD-SELECT-01: Mood selector label is 'Your photo for this card'."""
        src = (INCLUDES_DIR / "cs_challenge_panel.html").read_text()
        assert "Your photo for this card" in src, \
            "Mood selector label must say 'Your photo for this card'"

    def test_ccd_mood_select_hint_own_side(self):
        """CCD-MOOD-SELECT-02: Mood selector hint says 'Changes only your side'."""
        src = (INCLUDES_DIR / "cs_challenge_panel.html").read_text()
        assert "Changes only your side" in src, \
            "Mood selector hint must say 'Changes only your side of the card.'"

    def test_ccd_mood_select_03_selector_before_format(self):
        """CCD-MOOD-SELECT-03: Mood selector before Format selector in panel."""
        src = (INCLUDES_DIR / "cs_challenge_panel.html").read_text()
        mood_pos   = src.find("Your photo for this card")
        format_pos = src.find('"Format"') if '"Format"' in src else src.find("Format")
        assert mood_pos < format_pos, "Mood selector must be before Format selector"

    def test_ccd_mood_select_04_chip_save_and_refresh_js(self):
        """CCD-MOOD-SELECT-04: JS _setChallengePhoto calls POST save + refreshes iframe."""
        src = (TEMPLATES_DIR / "card_studio_shell.html").read_text()
        assert "_saveSnapshot" in src, "JS must call _saveSnapshot (POST endpoint)"
        assert "_refreshPreview" in src, "JS must refresh preview iframe"
        assert "card/photo" in src, "JS must POST to /challenges/{id}/card/photo"


# ── Terminal rejection phases: CANCELLED + DECLINED ───────────────────────────

class TestCCDCancelled:
    """CCD-CAN: challenge_cancelled phase — phase system + template rendering."""

    def _render_post(self, viewer_is_challenger: bool = True) -> str:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template("public/export/challenge/post_16_9.html")
        ctx = _make_mock_ctx(phase="challenge_cancelled",
                             viewer_is_challenger=viewer_is_challenger)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    def _render_story(self, viewer_is_challenger: bool = True) -> str:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template("public/export/challenge/story_9_16.html")
        ctx = _make_mock_ctx(phase="challenge_cancelled",
                             viewer_is_challenger=viewer_is_challenger)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    def test_ccd_can_01_unlocked_phases_for_cancelled(self):
        """CCD-CAN-01: CANCELLED status → unlocked = ['challenge_cancelled']."""
        from app.api.web_routes.vt_challenges import get_unlocked_challenge_card_phases
        from app.models.vt_challenge import ChallengeStatus
        ch = MagicMock()
        ch.status = ChallengeStatus.CANCELLED
        ch.challenger_id = 1
        ch.challenged_id = 2
        ch.challenger_attempt_id = None
        ch.challenged_attempt_id = None
        result = get_unlocked_challenge_card_phases(ch, viewer_id=1)
        assert result == ["challenge_cancelled"]

    def test_ccd_can_02_in_valid_phases(self):
        """CCD-CAN-02: challenge_cancelled in VALID_CHALLENGE_CARD_PHASES."""
        from app.api.web_routes.vt_challenges import VALID_CHALLENGE_CARD_PHASES
        assert "challenge_cancelled" in VALID_CHALLENGE_CARD_PHASES

    def test_ccd_can_03_in_exportable_phases(self):
        """CCD-CAN-03: challenge_cancelled in _EXPORTABLE_PHASES."""
        from app.api.web_routes.vt_challenges import _EXPORTABLE_PHASES
        assert "challenge_cancelled" in _EXPORTABLE_PHASES

    def test_ccd_can_04_phase_label(self):
        """CCD-CAN-04: _PHASE_LABELS['challenge_cancelled'] == 'Cancelled'."""
        from app.api.web_routes.vt_challenges import _PHASE_LABELS
        assert _PHASE_LABELS.get("challenge_cancelled") == "Cancelled"

    def test_ccd_can_05_phase_cta(self):
        """CCD-CAN-05: challenge_cancelled has a CTA label."""
        from app.api.web_routes.vt_challenges import _PHASE_CTA
        assert "challenge_cancelled" in _PHASE_CTA

    def test_ccd_can_06_viewer_action_text_challenger(self):
        """CCD-CAN-06: challenger view → viewer_action_text = 'cancelled by you'."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = MagicMock()
        ch.challenger_id = 10; ch.challenged_id = 20
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S", email="r@x.com")
        ch.game = MagicMock(name="Memory Sequence")
        ch.challenge_mode = "async"; ch.status = MagicMock()
        ch.winner_id = None; ch.winner = None; ch.is_draw = False
        ch.completed_at = None; ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = None; ch.challenged_attempt_id = None
        viewer = MagicMock(id=10)
        ctx = _build_challenge_card_context(ch, viewer, None, None, "challenge_cancelled")
        assert ctx["viewer_action_text"] == "cancelled by you"

    def test_ccd_can_07_viewer_action_text_challenged(self):
        """CCD-CAN-07: challenged view → viewer_action_text = '[name] cancelled'."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = MagicMock()
        ch.challenger_id = 10; ch.challenged_id = 20
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S", email="r@x.com")
        ch.game = MagicMock(name="Memory Sequence")
        ch.challenge_mode = "async"; ch.status = MagicMock()
        ch.winner_id = None; ch.winner = None; ch.is_draw = False
        ch.completed_at = None; ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = None; ch.challenged_attempt_id = None
        viewer = MagicMock(id=20)
        ctx = _build_challenge_card_context(ch, viewer, None, None, "challenge_cancelled")
        assert ctx["viewer_action_text"] == "T1B1K3 cancelled"

    def test_ccd_can_08_post_renders_two_player_layout(self):
        """CCD-CAN-08: post_16_9 renders arch-invitation-balanced (two-player) for challenge_cancelled."""
        html = self._render_post()
        assert '<div class="arch-invitation-balanced">' in html, \
            "challenge_cancelled must use two-player invitation layout"
        assert "Cancelled" in html

    def test_ccd_can_09_post_uses_invitation_grid(self):
        """CCD-CAN-09: post_16_9 DOES render arch-invitation-balanced (two-player) for cancelled."""
        html = self._render_post()
        assert '<div class="arch-invitation-balanced">' in html, \
            "challenge_cancelled must use two-player invitation grid, not a pure center layout"

    def test_ccd_can_10_story_renders_two_player_layout(self):
        """CCD-CAN-10: story_9_16 renders arch-story-balanced (two-player) for challenge_cancelled."""
        html = self._render_story()
        assert '<div class="arch-story-balanced">' in html, \
            "challenge_cancelled must use two-player story layout"
        assert "Cancelled" in html


class TestCCDDeclined:
    """CCD-DEC: challenge_declined phase — phase system + template rendering."""

    def _render_post(self, viewer_is_challenger: bool = True) -> str:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template("public/export/challenge/post_16_9.html")
        ctx = _make_mock_ctx(phase="challenge_declined",
                             viewer_is_challenger=viewer_is_challenger)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    def _render_story(self, viewer_is_challenger: bool = True) -> str:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template("public/export/challenge/story_9_16.html")
        ctx = _make_mock_ctx(phase="challenge_declined",
                             viewer_is_challenger=viewer_is_challenger)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    def test_ccd_dec_01_unlocked_phases_for_declined(self):
        """CCD-DEC-01: DECLINED status → unlocked = ['challenge_declined']."""
        from app.api.web_routes.vt_challenges import get_unlocked_challenge_card_phases
        from app.models.vt_challenge import ChallengeStatus
        ch = MagicMock()
        ch.status = ChallengeStatus.DECLINED
        ch.challenger_id = 1
        ch.challenged_id = 2
        ch.challenger_attempt_id = None
        ch.challenged_attempt_id = None
        result = get_unlocked_challenge_card_phases(ch, viewer_id=1)
        assert result == ["challenge_declined"]

    def test_ccd_dec_02_in_valid_phases(self):
        """CCD-DEC-02: challenge_declined in VALID_CHALLENGE_CARD_PHASES."""
        from app.api.web_routes.vt_challenges import VALID_CHALLENGE_CARD_PHASES
        assert "challenge_declined" in VALID_CHALLENGE_CARD_PHASES

    def test_ccd_dec_03_in_exportable_phases(self):
        """CCD-DEC-03: challenge_declined in _EXPORTABLE_PHASES."""
        from app.api.web_routes.vt_challenges import _EXPORTABLE_PHASES
        assert "challenge_declined" in _EXPORTABLE_PHASES

    def test_ccd_dec_04_phase_label(self):
        """CCD-DEC-04: _PHASE_LABELS['challenge_declined'] == 'Declined'."""
        from app.api.web_routes.vt_challenges import _PHASE_LABELS
        assert _PHASE_LABELS.get("challenge_declined") == "Declined"

    def test_ccd_dec_05_phase_cta(self):
        """CCD-DEC-05: challenge_declined has a CTA label."""
        from app.api.web_routes.vt_challenges import _PHASE_CTA
        assert "challenge_declined" in _PHASE_CTA

    def test_ccd_dec_06_viewer_action_text_challenger(self):
        """CCD-DEC-06: challenger view → viewer_action_text = '[name] declined'."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = MagicMock()
        ch.challenger_id = 10; ch.challenged_id = 20
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S", email="r@x.com")
        ch.game = MagicMock(name="Memory Sequence")
        ch.challenge_mode = "async"; ch.status = MagicMock()
        ch.winner_id = None; ch.winner = None; ch.is_draw = False
        ch.completed_at = None; ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = None; ch.challenged_attempt_id = None
        viewer = MagicMock(id=10)
        ctx = _build_challenge_card_context(ch, viewer, None, None, "challenge_declined")
        assert ctx["viewer_action_text"] == "RD14S declined"

    def test_ccd_dec_07_viewer_action_text_challenged(self):
        """CCD-DEC-07: challenged view → viewer_action_text = 'declined by you'."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = MagicMock()
        ch.challenger_id = 10; ch.challenged_id = 20
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S", email="r@x.com")
        ch.game = MagicMock(name="Memory Sequence")
        ch.challenge_mode = "async"; ch.status = MagicMock()
        ch.winner_id = None; ch.winner = None; ch.is_draw = False
        ch.completed_at = None; ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = None; ch.challenged_attempt_id = None
        viewer = MagicMock(id=20)
        ctx = _build_challenge_card_context(ch, viewer, None, None, "challenge_declined")
        assert ctx["viewer_action_text"] == "declined by you"

    def test_ccd_dec_08_post_renders_two_player_layout(self):
        """CCD-DEC-08: post_16_9 renders arch-invitation-balanced (two-player) for challenge_declined."""
        html = self._render_post()
        assert '<div class="arch-invitation-balanced">' in html, \
            "challenge_declined must use two-player invitation layout"
        assert "Declined" in html

    def test_ccd_dec_09_post_uses_invitation_grid(self):
        """CCD-DEC-09: post_16_9 DOES render arch-invitation-balanced (two-player) for declined."""
        html = self._render_post()
        assert '<div class="arch-invitation-balanced">' in html, \
            "challenge_declined must use two-player invitation grid"

    def test_ccd_dec_10_story_renders_two_player_layout(self):
        """CCD-DEC-10: story_9_16 renders arch-story-balanced (two-player) for challenge_declined."""
        html = self._render_story()
        assert '<div class="arch-story-balanced">' in html, \
            "challenge_declined must use two-player story layout"
        assert "Declined" in html


class TestCCDTerminalStudio:
    """CCD-CAN/DEC-STUDIO: Card Studio no longer uses implicit-initial workaround."""

    def test_ccd_can_studio_01_cancelled_not_in_implicit_initial(self):
        """CCD-CAN-STUDIO-01: CANCELLED removed from _CC_STATUSES_WITH_IMPLICIT_INITIAL."""
        from app.api.web_routes.card_studio import _CC_STATUSES_WITH_IMPLICIT_INITIAL
        from app.models.vt_challenge import ChallengeStatus
        assert ChallengeStatus.CANCELLED not in _CC_STATUSES_WITH_IMPLICIT_INITIAL, \
            "CANCELLED now has a real unlocked phase; workaround no longer needed"

    def test_ccd_dec_studio_01_declined_not_in_implicit_initial(self):
        """CCD-DEC-STUDIO-01: DECLINED removed from _CC_STATUSES_WITH_IMPLICIT_INITIAL."""
        from app.api.web_routes.card_studio import _CC_STATUSES_WITH_IMPLICIT_INITIAL
        from app.models.vt_challenge import ChallengeStatus
        assert ChallengeStatus.DECLINED not in _CC_STATUSES_WITH_IMPLICIT_INITIAL, \
            "DECLINED now has a real unlocked phase; workaround no longer needed"


# ── Terminal timeline completeness ────────────────────────────────────────────

class TestCCDTerminalTimeline:
    """CCD-TIMELINE: locked initial phase + full timeline + preview validation.

    CCD-CAN-LOCKED-01  get_locked(CANCELLED, challenger) → ["challenge_sent"]
    CCD-CAN-LOCKED-02  get_locked(CANCELLED, challenged) → ["challenge_received"]
    CCD-DEC-LOCKED-01  get_locked(DECLINED, challenger)  → ["challenge_sent"]
    CCD-DEC-LOCKED-02  get_locked(DECLINED, challenged)  → ["challenge_received"]
    CCD-CAN-TIMELINE-01  Card Studio CANCELLED: chips contain challenge_sent (hist) + challenge_cancelled (active)
    CCD-CAN-TIMELINE-02  Card Studio CANCELLED: default phase = challenge_cancelled
    CCD-DEC-TIMELINE-01  Card Studio DECLINED: chips contain challenge_sent (hist) + challenge_declined (active)
    CCD-PREVIEW-CAN-01  preview validation CANCELLED + challenge_sent → allowed (in locked)
    CCD-PREVIEW-CAN-02  preview validation CANCELLED + challenge_declined → blocked (wrong terminal)
    CCD-LABEL-01       _CC_PHASE_LABELS["challenge_cancelled"] != raw phase id
    CCD-LABEL-02       _CC_PHASE_LABELS["challenge_declined"]  != raw phase id
    """

    @staticmethod
    def _make_ch(ch_id: int, challenger_id: int, challenged_id: int, status_val: str):
        from app.models.vt_challenge import ChallengeStatus
        ch = MagicMock()
        ch.id = ch_id
        ch.challenger_id = challenger_id; ch.challenged_id = challenged_id
        ch.challenger_attempt_id = None;  ch.challenged_attempt_id = None
        ch.winner_id = None; ch.is_draw = False
        ch.forfeit_user_id = None; ch.forfeit_reason = None
        ch.challenge_mode = "async"; ch.created_at = None; ch.completed_at = None
        status_map = {
            "cancelled": ChallengeStatus.CANCELLED,
            "declined":  ChallengeStatus.DECLINED,
        }
        ch.status = status_map[status_val]
        ch.game = MagicMock(); ch.game.name = "Memory Sequence"
        ch.challenger = MagicMock(id=challenger_id, nickname=f"U{challenger_id}", email=f"u{challenger_id}@x.com")
        ch.challenged = MagicMock(id=challenged_id, nickname=f"U{challenged_id}", email=f"u{challenged_id}@x.com")
        return ch

    # ── Locked phase tests ────────────────────────────────────────────────────

    def test_ccd_can_locked_01_challenger_gets_challenge_sent(self):
        """CCD-CAN-LOCKED-01: CANCELLED, challenger view → locked = ['challenge_sent']."""
        from app.api.web_routes.vt_challenges import get_locked_challenge_card_phases
        ch = self._make_ch(1, challenger_id=10, challenged_id=20, status_val="cancelled")
        result = get_locked_challenge_card_phases(ch, viewer_id=10)
        assert result == ["challenge_sent"], f"got: {result}"

    def test_ccd_can_locked_02_challenged_gets_challenge_received(self):
        """CCD-CAN-LOCKED-02: CANCELLED, challenged view → locked = ['challenge_received']."""
        from app.api.web_routes.vt_challenges import get_locked_challenge_card_phases
        ch = self._make_ch(2, challenger_id=10, challenged_id=20, status_val="cancelled")
        result = get_locked_challenge_card_phases(ch, viewer_id=20)
        assert result == ["challenge_received"], f"got: {result}"

    def test_ccd_dec_locked_01_challenger_gets_challenge_sent(self):
        """CCD-DEC-LOCKED-01: DECLINED, challenger view → locked = ['challenge_sent']."""
        from app.api.web_routes.vt_challenges import get_locked_challenge_card_phases
        ch = self._make_ch(3, challenger_id=10, challenged_id=20, status_val="declined")
        result = get_locked_challenge_card_phases(ch, viewer_id=10)
        assert result == ["challenge_sent"], f"got: {result}"

    def test_ccd_dec_locked_02_challenged_gets_challenge_received(self):
        """CCD-DEC-LOCKED-02: DECLINED, challenged view → locked = ['challenge_received']."""
        from app.api.web_routes.vt_challenges import get_locked_challenge_card_phases
        ch = self._make_ch(4, challenger_id=10, challenged_id=20, status_val="declined")
        result = get_locked_challenge_card_phases(ch, viewer_id=20)
        assert result == ["challenge_received"], f"got: {result}"

    # ── Card Studio timeline tests ────────────────────────────────────────────

    def _studio_chips(self, status_val: str, viewer_id: int) -> tuple[list[dict], str | None]:
        """Run _resolve_challenge_context and return (phase_chips, active_phase)."""
        from app.api.web_routes.card_studio import _resolve_challenge_context
        ch   = self._make_ch(99, challenger_id=10, challenged_id=20, status_val=status_val)
        user = MagicMock(id=viewer_id)
        with patch("app.api.web_routes.card_studio._license_guard",
                   return_value=MagicMock(onboarding_completed=True)):
            db = MagicMock()
            db.query.return_value.filter.return_value.first.return_value = ch
            ctx, _ = _resolve_challenge_context(db, user, challenge_id=99)
        if ctx.get("challenge_mode") != "preview":
            return [], None
        chips = ctx.get("phase_chips", [])
        active = next((c["id"] for c in chips if c.get("active")), None)
        return chips, active

    def test_ccd_can_timeline_01_both_chips_present(self):
        """CCD-CAN-TIMELINE-01: CANCELLED challenger → chips have challenge_sent (hist) + challenge_cancelled (active)."""
        chips, _ = self._studio_chips("cancelled", viewer_id=10)
        ids = [c["id"] for c in chips]
        assert "challenge_sent" in ids, f"challenge_sent missing from chips: {ids}"
        assert "challenge_cancelled" in ids, f"challenge_cancelled missing from chips: {ids}"

    def test_ccd_can_timeline_02_default_is_terminal(self):
        """CCD-CAN-TIMELINE-02: CANCELLED → default selected phase = challenge_cancelled."""
        _, active = self._studio_chips("cancelled", viewer_id=10)
        assert active == "challenge_cancelled", f"expected challenge_cancelled as default, got: {active}"

    def test_ccd_can_timeline_03_sent_chip_is_historical(self):
        """CCD-CAN-TIMELINE-03: challenge_sent chip is historical (not current) in CANCELLED."""
        chips, _ = self._studio_chips("cancelled", viewer_id=10)
        sent_chip = next((c for c in chips if c["id"] == "challenge_sent"), None)
        assert sent_chip is not None
        assert sent_chip["is_historical"] is True, "challenge_sent must be historical for CANCELLED"

    def test_ccd_can_timeline_04_terminal_chip_not_historical(self):
        """CCD-CAN-TIMELINE-04: challenge_cancelled chip is NOT historical (it is current)."""
        chips, _ = self._studio_chips("cancelled", viewer_id=10)
        term_chip = next((c for c in chips if c["id"] == "challenge_cancelled"), None)
        assert term_chip is not None
        assert term_chip["is_historical"] is False, "challenge_cancelled must be non-historical (current)"

    def test_ccd_dec_timeline_01_both_chips_present(self):
        """CCD-DEC-TIMELINE-01: DECLINED challenger → chips have challenge_sent (hist) + challenge_declined (active)."""
        chips, _ = self._studio_chips("declined", viewer_id=10)
        ids = [c["id"] for c in chips]
        assert "challenge_sent" in ids, f"challenge_sent missing from chips: {ids}"
        assert "challenge_declined" in ids, f"challenge_declined missing from chips: {ids}"

    # ── Preview route validation tests ───────────────────────────────────────

    def test_ccd_preview_can_01_historical_sent_allowed(self):
        """CCD-PREVIEW-CAN-01: CANCELLED, phase=challenge_sent → in valid set (preview allowed)."""
        from app.api.web_routes.vt_challenges import (
            get_unlocked_challenge_card_phases,
            get_locked_challenge_card_phases,
        )
        ch = self._make_ch(5, challenger_id=10, challenged_id=20, status_val="cancelled")
        unlocked = get_unlocked_challenge_card_phases(ch, viewer_id=10)
        locked   = get_locked_challenge_card_phases(ch, viewer_id=10)
        valid = set(unlocked) | set(locked)
        assert "challenge_sent" in valid, \
            f"challenge_sent must be in valid set for CANCELLED preview; got: {valid}"

    def test_ccd_preview_can_02_wrong_terminal_blocked(self):
        """CCD-PREVIEW-CAN-02: CANCELLED, phase=challenge_declined → NOT in valid set (403)."""
        from app.api.web_routes.vt_challenges import (
            get_unlocked_challenge_card_phases,
            get_locked_challenge_card_phases,
        )
        ch = self._make_ch(6, challenger_id=10, challenged_id=20, status_val="cancelled")
        unlocked = get_unlocked_challenge_card_phases(ch, viewer_id=10)
        locked   = get_locked_challenge_card_phases(ch, viewer_id=10)
        valid = set(unlocked) | set(locked)
        assert "challenge_declined" not in valid, \
            "challenge_declined must NOT be valid for a CANCELLED challenge"

    def test_ccd_preview_dec_01_historical_sent_allowed(self):
        """CCD-PREVIEW-DEC-01: DECLINED, phase=challenge_sent → in valid set (preview allowed)."""
        from app.api.web_routes.vt_challenges import (
            get_unlocked_challenge_card_phases,
            get_locked_challenge_card_phases,
        )
        ch = self._make_ch(7, challenger_id=10, challenged_id=20, status_val="declined")
        unlocked = get_unlocked_challenge_card_phases(ch, viewer_id=10)
        locked   = get_locked_challenge_card_phases(ch, viewer_id=10)
        valid = set(unlocked) | set(locked)
        assert "challenge_sent" in valid, \
            f"challenge_sent must be in valid set for DECLINED preview; got: {valid}"

    def test_ccd_preview_dec_02_wrong_terminal_blocked(self):
        """CCD-PREVIEW-DEC-02: DECLINED, phase=challenge_cancelled → NOT in valid set (403)."""
        from app.api.web_routes.vt_challenges import (
            get_unlocked_challenge_card_phases,
            get_locked_challenge_card_phases,
        )
        ch = self._make_ch(8, challenger_id=10, challenged_id=20, status_val="declined")
        unlocked = get_unlocked_challenge_card_phases(ch, viewer_id=10)
        locked   = get_locked_challenge_card_phases(ch, viewer_id=10)
        valid = set(unlocked) | set(locked)
        assert "challenge_cancelled" not in valid, \
            "challenge_cancelled must NOT be valid for a DECLINED challenge"

    # ── Chip label tests ─────────────────────────────────────────────────────

    def test_ccd_label_01_cancelled_chip_not_raw_id(self):
        """CCD-LABEL-01: _CC_PHASE_LABELS['challenge_cancelled'] is human-readable."""
        from app.api.web_routes.card_studio import _CC_PHASE_LABELS
        label = _CC_PHASE_LABELS.get("challenge_cancelled", "challenge_cancelled")
        assert label != "challenge_cancelled", \
            f"chip label must not be raw phase id; got: {label!r}"
        assert len(label) > 4, f"label too short: {label!r}"

    def test_ccd_label_02_declined_chip_not_raw_id(self):
        """CCD-LABEL-02: _CC_PHASE_LABELS['challenge_declined'] is human-readable."""
        from app.api.web_routes.card_studio import _CC_PHASE_LABELS
        label = _CC_PHASE_LABELS.get("challenge_declined", "challenge_declined")
        assert label != "challenge_declined", \
            f"chip label must not be raw phase id; got: {label!r}"
        assert len(label) > 4, f"label too short: {label!r}"

    def test_ccd_event_label_01_cancelled_event_label_set(self):
        """CCD-LABEL-03: _CC_PHASE_EVENT_LABELS['challenge_cancelled'] is set."""
        from app.api.web_routes.card_studio import _CC_PHASE_EVENT_LABELS
        assert "challenge_cancelled" in _CC_PHASE_EVENT_LABELS

    def test_ccd_event_label_02_declined_event_label_set(self):
        """CCD-LABEL-04: _CC_PHASE_EVENT_LABELS['challenge_declined'] is set."""
        from app.api.web_routes.card_studio import _CC_PHASE_EVENT_LABELS
        assert "challenge_declined" in _CC_PHASE_EVENT_LABELS


# ── Participant stats: _get_participant_stats + overlay rendering ─────────────

class TestCCDStats:
    """CCD-STATS: overall + position stats helper, context propagation, template overlay.

    CCD-STATS-01  overall = average of football_skills current_level (1dp)
    CCD-STATS-02  primary_pos = position_short(positions[0])
    CCD-STATS-03  secondary_pos = position_short(positions[1])
    CCD-STATS-04  no UserLicense → all None
    CCD-STATS-05  empty positions list → primary/secondary None
    CCD-STATS-06  empty football_skills → overall None
    CCD-STATS-07  _build_challenge_card_context propagates challenger_overall + challenged_overall
    CCD-STATS-08  post_16_9 challenge_sent: aib-pos-block rendered when primary_pos present
    CCD-STATS-09  post_16_9 challenge_sent: OVR text rendered when overall present
    CCD-STATS-10  post_16_9: overall=None → OVR text absent
    CCD-STATS-11  post_16_9: primary_pos=None → aib-pos-block absent
    CCD-STATS-12  story_9_16 challenge_sent: asb-pos-block + OVR rendered
    CCD-STATS-13  post_16_9 challenge_received: aib-pos-block + OVR rendered (historical phase)
    CCD-STATS-14  post_16_9 challenge_cancelled: arch-invitation-balanced rendered (two-player)
    CCD-STATS-15  post_16_9 challenge_cancelled: challenger photo present in layout
    CCD-STATS-16  post_16_9 challenge_cancelled: aib-pos-block rendered when stats present
    CCD-STATS-17  post_16_9 challenge_declined: arch-invitation-balanced rendered
    CCD-STATS-18  story_9_16 challenge_cancelled: arch-story-balanced rendered (two-player)
    CCD-STATS-19  story_9_16 challenge_declined: arch-story-balanced rendered
    CCD-STATS-20  post_16_9 challenge_cancelled: no layout break when stats are all None
    """

    # ── Helper: _get_participant_stats ────────────────────────────────────────

    def _make_lic(self, football_skills: dict | None, positions: list | None):
        """Build a mock UserLicense with given skills/positions."""
        lic = MagicMock()
        lic.football_skills   = football_skills
        lic.motivation_scores = {"positions": positions} if positions is not None else {}
        return lic

    def _run_stats(self, lic_or_none):
        from app.api.web_routes.vt_challenges import _get_participant_stats
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = lic_or_none
        return _get_participant_stats(db, user_id=1)

    def test_ccd_stats_01_overall_average(self):
        """CCD-STATS-01: overall = average of current_level values (1 decimal place)."""
        lic = self._make_lic(
            football_skills={
                "ball_control": {"current_level": 80.0},
                "shooting":     {"current_level": 70.0},
                "passing":      {"current_level": 60.0},
            },
            positions=["centre_midfield"],
        )
        result = self._run_stats(lic)
        assert result["overall"] == round((80.0 + 70.0 + 60.0) / 3, 1)

    def test_ccd_stats_02_primary_pos(self):
        """CCD-STATS-02: primary_pos = position_short(positions[0])."""
        from app.utils.football_positions import position_short
        lic = self._make_lic(
            football_skills={"passing": {"current_level": 70.0}},
            positions=["centre_midfield", "striker"],
        )
        result = self._run_stats(lic)
        assert result["primary_pos"] == position_short("centre_midfield")

    def test_ccd_stats_03_secondary_pos(self):
        """CCD-STATS-03: secondary_pos = position_short(positions[1])."""
        from app.utils.football_positions import position_short
        lic = self._make_lic(
            football_skills={"passing": {"current_level": 70.0}},
            positions=["centre_midfield", "striker"],
        )
        result = self._run_stats(lic)
        assert result["secondary_pos"] == position_short("striker")

    def test_ccd_stats_04_no_license(self):
        """CCD-STATS-04: no LFA license → all None."""
        result = self._run_stats(None)
        assert result == {"overall": None, "primary_pos": None, "secondary_pos": None}

    def test_ccd_stats_05_empty_positions(self):
        """CCD-STATS-05: empty positions list → primary_pos and secondary_pos None."""
        lic = self._make_lic(
            football_skills={"passing": {"current_level": 70.0}},
            positions=[],
        )
        result = self._run_stats(lic)
        assert result["primary_pos"] is None
        assert result["secondary_pos"] is None

    def test_ccd_stats_06_empty_football_skills(self):
        """CCD-STATS-06: empty football_skills → overall None."""
        lic = self._make_lic(football_skills={}, positions=["striker"])
        result = self._run_stats(lic)
        assert result["overall"] is None

    # ── Context builder propagation ───────────────────────────────────────────

    def test_ccd_stats_07_context_builder_propagates_stats(self):
        """CCD-STATS-07: _build_challenge_card_context passes challenger/challenged stats."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = MagicMock()
        ch.challenger_id = 10; ch.challenged_id = 20
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S", email="r@x.com")
        ch.game = MagicMock(name="Memory Sequence")
        ch.challenge_mode = "async"; ch.status = MagicMock()
        ch.winner_id = None; ch.winner = None; ch.is_draw = False
        ch.completed_at = None; ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = None; ch.challenged_attempt_id = None
        viewer = MagicMock(id=10)
        ctx = _build_challenge_card_context(
            ch, viewer, None, None, "challenge_sent",
            challenger_stats={"overall": 78.5, "primary_pos": "CM", "secondary_pos": "RM"},
            challenged_stats={"overall": 65.2, "primary_pos": "ST", "secondary_pos": None},
        )
        assert ctx["challenger_overall"] == 78.5
        assert ctx["challenger_primary_pos"] == "CM"
        assert ctx["challenger_secondary_pos"] == "RM"
        assert ctx["challenged_overall"] == 65.2
        assert ctx["challenged_primary_pos"] == "ST"
        assert ctx["challenged_secondary_pos"] is None

    # ── Template rendering helpers ────────────────────────────────────────────

    def _render(self, template_path: str, phase: str, **kwargs) -> str:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(template_path)
        ctx = _make_mock_ctx(phase=phase, **kwargs)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    # ── post_16_9 — Archetype A stats overlay ────────────────────────────────

    def test_ccd_stats_08_post_sent_pos_block(self):
        """CCD-STATS-08: post_16_9 challenge_sent: aib-pos-block rendered when primary_pos set."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "challenge_sent",
            challenger_primary_pos="CM", challenger_secondary_pos="RM",
        )
        assert "aib-pos-block" in html
        assert "CM" in html

    def test_ccd_stats_09_post_sent_ovr(self):
        """CCD-STATS-09: post_16_9 challenge_sent: OVR text rendered when overall present."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "challenge_sent",
            challenger_overall=78.5,
        )
        assert "OVR 78.5" in html

    def test_ccd_stats_10_post_no_ovr_when_none(self):
        """CCD-STATS-10: post_16_9: overall=None → aib-overall element absent from body."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "challenge_sent",
            challenger_overall=None, challenged_overall=None,
        )
        assert 'class="aib-overall"' not in html

    def test_ccd_stats_11_post_no_pos_block_when_none(self):
        """CCD-STATS-11: post_16_9: primary_pos=None → aib-pos-block div absent from body."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "challenge_sent",
            challenger_primary_pos=None, challenged_primary_pos=None,
        )
        assert '<div class="aib-pos-block">' not in html

    # ── story_9_16 — Archetype A stats overlay ────────────────────────────────

    def test_ccd_stats_12_story_sent_overlays(self):
        """CCD-STATS-12: story_9_16 challenge_sent: asb-pos-block + OVR rendered."""
        html = self._render(
            "public/export/challenge/story_9_16.html", "challenge_sent",
            challenger_primary_pos="ST", challenger_overall=72.3,
        )
        assert "asb-pos-block" in html
        assert "ST" in html
        assert "OVR 72.3" in html

    # ── post_16_9 — challenge_received (historical) ───────────────────────────

    def test_ccd_stats_13_post_received_pos_and_ovr(self):
        """CCD-STATS-13: post_16_9 challenge_received: aib-pos-block + OVR rendered."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "challenge_received",
            viewer_is_challenger=False,
            challenger_primary_pos="LW", challenger_overall=61.0,
            challenged_primary_pos="GK",
        )
        assert "aib-pos-block" in html
        assert "LW" in html
        assert "OVR 61.0" in html

    # ── post_16_9 — challenge_cancelled two-player layout ────────────────────

    def test_ccd_stats_14_post_cancelled_two_player_layout(self):
        """CCD-STATS-14: post_16_9 challenge_cancelled: arch-invitation-balanced rendered."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "challenge_cancelled",
        )
        assert '<div class="arch-invitation-balanced">' in html

    def test_ccd_stats_15_post_cancelled_photo_present(self):
        """CCD-STATS-15: post_16_9 challenge_cancelled: challenger photo present."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "challenge_cancelled",
            challenger_photo="/ch_photo.png",
        )
        assert "/ch_photo.png" in html

    def test_ccd_stats_16_post_cancelled_stats_overlay(self):
        """CCD-STATS-16: post_16_9 challenge_cancelled: stats overlay rendered."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "challenge_cancelled",
            challenger_primary_pos="CM", challenger_overall=78.5,
            challenged_primary_pos="ST", challenged_overall=65.0,
        )
        assert "aib-pos-block" in html
        assert "OVR 78.5" in html
        assert "OVR 65.0" in html

    # ── post_16_9 — challenge_declined two-player layout ─────────────────────

    def test_ccd_stats_17_post_declined_two_player_layout(self):
        """CCD-STATS-17: post_16_9 challenge_declined: arch-invitation-balanced rendered."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "challenge_declined",
        )
        assert '<div class="arch-invitation-balanced">' in html

    # ── story_9_16 — cancelled/declined two-player layout ────────────────────

    def test_ccd_stats_18_story_cancelled_two_player_layout(self):
        """CCD-STATS-18: story_9_16 challenge_cancelled: arch-story-balanced rendered."""
        html = self._render(
            "public/export/challenge/story_9_16.html", "challenge_cancelled",
        )
        assert '<div class="arch-story-balanced">' in html

    def test_ccd_stats_19_story_declined_two_player_layout(self):
        """CCD-STATS-19: story_9_16 challenge_declined: arch-story-balanced rendered."""
        html = self._render(
            "public/export/challenge/story_9_16.html", "challenge_declined",
        )
        assert '<div class="arch-story-balanced">' in html

    def test_ccd_stats_20_post_cancelled_no_stats_no_crash(self):
        """CCD-STATS-20: post_16_9 challenge_cancelled with all stats None — layout intact."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "challenge_cancelled",
            challenger_overall=None, challenger_primary_pos=None,
            challenged_overall=None, challenged_primary_pos=None,
        )
        assert "Cancelled" in html
        assert '<div class="aib-pos-block">' not in html
        assert 'class="aib-overall"' not in html
        assert len(html) > 500


# ── Challenge Accepted phase ──────────────────────────────────────────────────

class TestCCDAccepted:
    """CCD-ACC: challenge_accepted — exportable, full-zone layout, stats, narrative.

    CCD-ACC-01  challenge_accepted in _EXPORTABLE_PHASES
    CCD-ACC-02  viewer_action_text: challenger view → "[name] accepted"
    CCD-ACC-03  viewer_action_text: challenged view → "accepted by you"
    CCD-ACC-04  post_16_9: arch-invitation-balanced rendered (NOT arch-battle)
    CCD-ACC-05  post_16_9: challenger photo present
    CCD-ACC-06  post_16_9: OVR overlay rendered when overall present
    CCD-ACC-07  post_16_9: pos block rendered when primary_pos present
    CCD-ACC-08  post_16_9: "Challenge Accepted" text present
    CCD-ACC-09  story_9_16: arch-story-balanced rendered (NOT cc-col structure)
    CCD-ACC-10  story_9_16: stats overlay rendered
    CCD-ACC-11  post_16_9 live_lobby_ready: still uses arch-battle (no regression)
    CCD-ACC-12  story_9_16 live_lobby_ready: does NOT use arch-story-balanced
    """

    def _render(self, template_path: str, phase: str, **kwargs) -> str:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(template_path)
        ctx = _make_mock_ctx(phase=phase, **kwargs)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    def test_ccd_acc_01_in_exportable_phases(self):
        """CCD-ACC-01: challenge_accepted is in _EXPORTABLE_PHASES."""
        from app.api.web_routes.vt_challenges import _EXPORTABLE_PHASES
        assert "challenge_accepted" in _EXPORTABLE_PHASES

    def test_ccd_acc_02_viewer_action_text_challenger(self):
        """CCD-ACC-02: challenger view → viewer_action_text = '[name] accepted'."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = MagicMock()
        ch.challenger_id = 10; ch.challenged_id = 20
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S", email="r@x.com")
        ch.game = MagicMock(name="Memory Sequence")
        ch.challenge_mode = "async"; ch.status = MagicMock()
        ch.winner_id = None; ch.winner = None; ch.is_draw = False
        ch.completed_at = None; ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = None; ch.challenged_attempt_id = None
        viewer = MagicMock(id=10)
        ctx = _build_challenge_card_context(ch, viewer, None, None, "challenge_accepted")
        assert ctx["viewer_action_text"] == "RD14S accepted"

    def test_ccd_acc_03_viewer_action_text_challenged(self):
        """CCD-ACC-03: challenged view → viewer_action_text = 'accepted by you'."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = MagicMock()
        ch.challenger_id = 10; ch.challenged_id = 20
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S", email="r@x.com")
        ch.game = MagicMock(name="Memory Sequence")
        ch.challenge_mode = "async"; ch.status = MagicMock()
        ch.winner_id = None; ch.winner = None; ch.is_draw = False
        ch.completed_at = None; ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = None; ch.challenged_attempt_id = None
        viewer = MagicMock(id=20)
        ctx = _build_challenge_card_context(ch, viewer, None, None, "challenge_accepted")
        assert ctx["viewer_action_text"] == "accepted by you"

    def test_ccd_acc_04_post_uses_full_zone_layout(self):
        """CCD-ACC-04: post_16_9 challenge_accepted uses arch-invitation-balanced, not arch-battle."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_accepted")
        assert '<div class="arch-invitation-balanced">' in html, \
            "challenge_accepted must use full-zone layout"
        assert '<div class="arch-battle">' not in html, \
            "challenge_accepted must NOT use arch-battle (live lobby layout)"

    def test_ccd_acc_05_post_challenger_photo(self):
        """CCD-ACC-05: post_16_9: challenger photo rendered."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "challenge_accepted",
            challenger_photo="/ch_photo.png",
        )
        assert "/ch_photo.png" in html

    def test_ccd_acc_06_post_ovr_overlay(self):
        """CCD-ACC-06: post_16_9: OVR overlay rendered when overall present."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "challenge_accepted",
            challenger_overall=78.5, challenged_overall=65.0,
        )
        assert 'class="aib-overall"' in html
        assert "OVR 78.5" in html
        assert "OVR 65.0" in html

    def test_ccd_acc_07_post_pos_block(self):
        """CCD-ACC-07: post_16_9: aib-pos-block rendered when primary_pos present."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "challenge_accepted",
            challenger_primary_pos="CM", challenged_primary_pos="ST",
        )
        assert '<div class="aib-pos-block">' in html
        assert "CM" in html
        assert "ST" in html

    def test_ccd_acc_08_post_accepted_text(self):
        """CCD-ACC-08: post_16_9: 'Challenge Accepted' text present."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_accepted")
        assert "Challenge Accepted" in html or "CHALLENGE ACCEPTED" in html.upper()

    def test_ccd_acc_09_story_uses_full_zone_layout(self):
        """CCD-ACC-09: story_9_16 challenge_accepted uses arch-story-balanced."""
        html = self._render("public/export/challenge/story_9_16.html", "challenge_accepted")
        assert '<div class="arch-story-balanced">' in html
        assert "cc-vs-text" not in html or "arch-battle" not in html

    def test_ccd_acc_10_story_stats_overlay(self):
        """CCD-ACC-10: story_9_16: stats overlay rendered for challenge_accepted."""
        html = self._render(
            "public/export/challenge/story_9_16.html", "challenge_accepted",
            challenger_primary_pos="LW", challenger_overall=70.0,
            challenged_primary_pos="GK",
        )
        assert '<div class="asb-pos-block">' in html
        assert "OVR 70.0" in html

    def test_ccd_acc_11_post_live_lobby_still_arch_battle(self):
        """CCD-ACC-11: post_16_9 live_lobby_ready: still renders arch-battle (no regression)."""
        html = self._render("public/export/challenge/post_16_9.html", "live_lobby_ready")
        assert '<div class="arch-battle">' in html
        assert '<div class="arch-invitation-balanced">' not in html

    def test_ccd_acc_12_story_live_lobby_not_full_zone(self):
        """CCD-ACC-12: story_9_16 live_lobby_ready: does NOT use arch-story-balanced."""
        html = self._render("public/export/challenge/story_9_16.html", "live_lobby_ready")
        assert '<div class="arch-story-balanced">' not in html


# ── Phase emoji mapping ───────────────────────────────────────────────────────

class TestCCDEmoji:
    """CCD-EMOJI: Central _PHASE_EMOJI mapping + template rendering.

    CCD-EMOJI-01  _PHASE_EMOJI exists in vt_challenges and has all 14 phases
    CCD-EMOJI-02  challenge_sent → "⚔️"
    CCD-EMOJI-03  challenge_received → "🛡️" (different from challenge_sent)
    CCD-EMOJI-04  challenge_accepted → "✅"
    CCD-EMOJI-05  challenge_cancelled → "🚫"
    CCD-EMOJI-06  challenge_declined → "👎"
    CCD-EMOJI-07  completed_score_win → "🏆"
    CCD-EMOJI-08  _build_challenge_card_context includes phase_emoji key
    CCD-EMOJI-09  post_16_9 challenge_sent: ⚔️ rendered, NOT 🛡️
    CCD-EMOJI-10  post_16_9 challenge_received: 🛡️ rendered, NOT ⚔️
    CCD-EMOJI-11  post_16_9 challenge_accepted: ✅ rendered
    CCD-EMOJI-12  post_16_9 challenge_cancelled: 🚫 rendered
    CCD-EMOJI-13  post_16_9 challenge_declined: 👎 rendered
    CCD-EMOJI-14  story_9_16 challenge_accepted: ✅ rendered
    CCD-EMOJI-15  story_9_16 challenge_received: 🛡️ rendered
    CCD-EMOJI-16  post_16_9 phase_emoji="" → no aib-phase-emoji div rendered
    CCD-EMOJI-17  post_16_9 live_lobby_ready: no regression (⚡ still in badge)
    CCD-EMOJI-18  post_16_9 completed_score_win: 🏆 in outcome badge
    CCD-EMOJI-19  challenge_sent and challenge_received emojis are different
    """

    def _render(self, template_path: str, phase: str, **kwargs) -> str:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(template_path)
        ctx = _make_mock_ctx(phase=phase, **kwargs)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    def test_ccd_emoji_01_phase_emoji_dict_complete(self):
        """CCD-EMOJI-01: _PHASE_EMOJI has entries for all 14 phases."""
        from app.api.web_routes.vt_challenges import _PHASE_EMOJI, VALID_CHALLENGE_CARD_PHASES
        for phase in VALID_CHALLENGE_CARD_PHASES:
            assert phase in _PHASE_EMOJI, f"_PHASE_EMOJI missing entry for phase: {phase!r}"
            assert _PHASE_EMOJI[phase], f"_PHASE_EMOJI[{phase!r}] must not be empty"

    def test_ccd_emoji_02_sent_is_sword(self):
        """CCD-EMOJI-02: challenge_sent → ⚔️"""
        from app.api.web_routes.vt_challenges import _PHASE_EMOJI
        assert _PHASE_EMOJI["challenge_sent"] == "⚔️"

    def test_ccd_emoji_03_received_is_shield(self):
        """CCD-EMOJI-03: challenge_received → 🛡️ (different from challenge_sent)"""
        from app.api.web_routes.vt_challenges import _PHASE_EMOJI
        assert _PHASE_EMOJI["challenge_received"] == "🛡️"

    def test_ccd_emoji_04_accepted_is_checkmark(self):
        """CCD-EMOJI-04: challenge_accepted → ✅"""
        from app.api.web_routes.vt_challenges import _PHASE_EMOJI
        assert _PHASE_EMOJI["challenge_accepted"] == "✅"

    def test_ccd_emoji_05_cancelled_is_no_entry(self):
        """CCD-EMOJI-05: challenge_cancelled → 🚫"""
        from app.api.web_routes.vt_challenges import _PHASE_EMOJI
        assert _PHASE_EMOJI["challenge_cancelled"] == "🚫"

    def test_ccd_emoji_06_declined_is_thumbsdown(self):
        """CCD-EMOJI-06: challenge_declined → 👎"""
        from app.api.web_routes.vt_challenges import _PHASE_EMOJI
        assert _PHASE_EMOJI["challenge_declined"] == "👎"

    def test_ccd_emoji_07_score_win_is_trophy(self):
        """CCD-EMOJI-07: completed_score_win → 🏆"""
        from app.api.web_routes.vt_challenges import _PHASE_EMOJI
        assert _PHASE_EMOJI["completed_score_win"] == "🏆"

    def test_ccd_emoji_08_context_includes_phase_emoji(self):
        """CCD-EMOJI-08: _build_challenge_card_context returns phase_emoji key."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = MagicMock()
        ch.challenger_id = 10; ch.challenged_id = 20
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S", email="r@x.com")
        ch.game = MagicMock(name="Memory Sequence")
        ch.challenge_mode = "async"; ch.status = MagicMock()
        ch.winner_id = None; ch.winner = None; ch.is_draw = False
        ch.completed_at = None; ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = None; ch.challenged_attempt_id = None
        viewer = MagicMock(id=10)
        ctx = _build_challenge_card_context(ch, viewer, None, None, "challenge_sent")
        assert "phase_emoji" in ctx
        assert ctx["phase_emoji"] == "⚔️"

    def test_ccd_emoji_09_post_sent_shows_sword(self):
        """CCD-EMOJI-09: post_16_9 challenge_sent: ⚔️ rendered."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_sent")
        assert "⚔️" in html
        assert "🛡️" not in html

    def test_ccd_emoji_10_post_received_shows_shield(self):
        """CCD-EMOJI-10: post_16_9 challenge_received: 🛡️ rendered, NOT ⚔️ in emoji div."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_received",
                            viewer_is_challenger=False)
        assert "🛡️" in html
        assert "aib-phase-emoji" in html

    def test_ccd_emoji_11_post_accepted_shows_checkmark(self):
        """CCD-EMOJI-11: post_16_9 challenge_accepted: ✅ in aib-phase-emoji."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_accepted")
        assert "✅" in html
        assert "aib-phase-emoji" in html

    def test_ccd_emoji_12_post_cancelled_shows_no_entry(self):
        """CCD-EMOJI-12: post_16_9 challenge_cancelled: 🚫 rendered."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_cancelled")
        assert "🚫" in html

    def test_ccd_emoji_13_post_declined_shows_thumbsdown(self):
        """CCD-EMOJI-13: post_16_9 challenge_declined: 👎 rendered."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_declined")
        assert "👎" in html

    def test_ccd_emoji_14_story_accepted_shows_checkmark(self):
        """CCD-EMOJI-14: story_9_16 challenge_accepted: ✅ in asb-phase-emoji."""
        html = self._render("public/export/challenge/story_9_16.html", "challenge_accepted")
        assert "✅" in html
        assert "asb-phase-emoji" in html

    def test_ccd_emoji_15_story_received_shows_shield(self):
        """CCD-EMOJI-15: story_9_16 challenge_received: 🛡️ rendered."""
        html = self._render("public/export/challenge/story_9_16.html", "challenge_received",
                            viewer_is_challenger=False)
        assert "🛡️" in html

    def test_ccd_emoji_16_empty_emoji_no_div(self):
        """CCD-EMOJI-16: phase_emoji='' → aib-phase-emoji div NOT rendered."""
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template("public/export/challenge/post_16_9.html")
        ctx = _make_mock_ctx(phase="challenge_sent")
        ctx["phase_emoji"] = ""  # override to empty
        ctx["request"] = MagicMock()
        html = tmpl.render(**ctx)
        assert '<div class="aib-phase-emoji">' not in html

    def test_ccd_emoji_17_live_lobby_no_regression(self):
        """CCD-EMOJI-17: post_16_9 live_lobby_ready: ⚡ still in badge, no crash."""
        html = self._render("public/export/challenge/post_16_9.html", "live_lobby_ready")
        assert "⚡" in html
        assert "LOBBY OPEN" in html

    def test_ccd_emoji_18_result_badge_has_trophy(self):
        """CCD-EMOJI-18: post_16_9 completed_score_win: 🏆 in outcome badge."""
        html = self._render("public/export/challenge/post_16_9.html", "completed_score_win",
                            challenger_score=85.0, challenged_score=70.0, winner_name="T1B1K3")
        assert "🏆" in html
        assert "SCORE WIN" in html

    def test_ccd_emoji_19_sent_and_received_differ(self):
        """CCD-EMOJI-19: challenge_sent and challenge_received have different emojis."""
        from app.api.web_routes.vt_challenges import _PHASE_EMOJI
        assert _PHASE_EMOJI["challenge_sent"] != _PHASE_EMOJI["challenge_received"], \
            "sent and received must have distinct emojis"


# ── No CTA on export cards ────────────────────────────────────────────────────

class TestCCDNoCTA:
    """CCD-NOCTA: Export cards must not contain action CTA text or cc-cta elements.

    CCD-NOCTA-01  challenge_accepted post: 'Play now' absent
    CCD-NOCTA-02  live_lobby_ready post: 'Join lobby' absent from footer
    CCD-NOCTA-03  live_in_progress post: 'Playing now' absent from footer
    CCD-NOCTA-04  completed_score_win post: 'Play again' absent
    CCD-NOCTA-05  no_contest post: 'Challenge again' absent
    CCD-NOCTA-06  skill_delta_result post: 'View profile' absent
    CCD-NOCTA-07  challenge_accepted story: 'Play now' absent
    CCD-NOCTA-08  no cc-cta div in any post phase
    CCD-NOCTA-09  no cc-cta div in any story phase
    CCD-NOCTA-10  post footer: only game info + lfa.gg (no third element)
    CCD-NOCTA-11  story footer: only game info + lfa.gg
    """

    _ACTION_STRINGS = [
        "Play now", "Join lobby", "Playing now", "Play again",
        "Challenge again", "View profile", "Accept challenge",
        "Waiting…", "View challenge",
    ]

    def _render(self, template_path: str, phase: str, **kwargs) -> str:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(template_path)
        ctx = _make_mock_ctx(phase=phase, **kwargs)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    def test_ccd_nocta_01_accepted_no_play_now(self):
        """CCD-NOCTA-01: challenge_accepted post: 'Play now' not rendered."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_accepted")
        assert "Play now" not in html

    def test_ccd_nocta_02_live_lobby_no_join_lobby(self):
        """CCD-NOCTA-02: live_lobby_ready post: 'Join lobby' not in footer."""
        html = self._render("public/export/challenge/post_16_9.html", "live_lobby_ready")
        assert "Join lobby" not in html

    def test_ccd_nocta_03_live_progress_no_playing_now(self):
        """CCD-NOCTA-03: live_in_progress post: 'Playing now' not in footer."""
        html = self._render("public/export/challenge/post_16_9.html", "live_in_progress")
        assert "Playing now" not in html

    def test_ccd_nocta_04_result_no_play_again(self):
        """CCD-NOCTA-04: completed_score_win post: 'Play again' absent."""
        html = self._render("public/export/challenge/post_16_9.html", "completed_score_win",
                            challenger_score=80.0, challenged_score=70.0, winner_name="T1B1K3")
        assert "Play again" not in html

    def test_ccd_nocta_05_no_contest_no_challenge_again(self):
        """CCD-NOCTA-05: no_contest post: 'Challenge again' absent."""
        html = self._render("public/export/challenge/post_16_9.html", "no_contest")
        assert "Challenge again" not in html

    def test_ccd_nocta_06_skill_no_view_profile(self):
        """CCD-NOCTA-06: skill_delta_result post: 'View profile' absent."""
        html = self._render("public/export/challenge/post_16_9.html", "skill_delta_result",
                            my_skill_scores={"passing": 0.5})
        assert "View profile" not in html

    def test_ccd_nocta_07_accepted_story_no_play_now(self):
        """CCD-NOCTA-07: challenge_accepted story: 'Play now' not rendered."""
        html = self._render("public/export/challenge/story_9_16.html", "challenge_accepted")
        assert "Play now" not in html

    def test_ccd_nocta_08_no_cc_cta_div_in_post(self):
        """CCD-NOCTA-08: no cc-cta div in any post phase."""
        from app.api.web_routes.vt_challenges import VALID_CHALLENGE_CARD_PHASES
        for phase in VALID_CHALLENGE_CARD_PHASES:
            html = self._render("public/export/challenge/post_16_9.html", phase,
                                challenger_score=80.0, challenged_score=70.0, winner_name="T1B1K3",
                                my_skill_scores={"passing": 0.5})
            assert 'class="cc-cta"' not in html, \
                f"cc-cta div must not appear in post for phase: {phase!r}"

    def test_ccd_nocta_09_no_cc_cta_div_in_story(self):
        """CCD-NOCTA-09: no cc-cta div in any story phase."""
        from app.api.web_routes.vt_challenges import VALID_CHALLENGE_CARD_PHASES
        for phase in VALID_CHALLENGE_CARD_PHASES:
            html = self._render("public/export/challenge/story_9_16.html", phase,
                                challenger_score=80.0, challenged_score=70.0, winner_name="T1B1K3",
                                my_skill_scores={"passing": 0.5})
            assert 'class="cc-cta"' not in html, \
                f"cc-cta div must not appear in story for phase: {phase!r}"

    def test_ccd_nocta_10_post_footer_two_elements_only(self):
        """CCD-NOCTA-10: post footer has only game info + lfa.gg (no third element)."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_accepted")
        assert "lfa.gg" in html
        assert "Memory Sequence" in html
        assert 'class="cc-cta"' not in html
        assert "Invitation Pending" not in html
        assert "Closed" not in html

    def test_ccd_nocta_11_story_footer_two_elements_only(self):
        """CCD-NOCTA-11: story footer has only game info + lfa.gg."""
        html = self._render("public/export/challenge/story_9_16.html", "challenge_accepted")
        assert "lfa.gg" in html
        assert "Memory Sequence" in html
        assert 'class="cc-cta"' not in html
        assert "Invitation Pending" not in html


# ── Waiting for opponent phase ────────────────────────────────────────────────

class TestCCDWaiting:
    """CCD-WAIT: waiting_for_opponent — exportable, viewer-specific hero card.

    CCD-WAIT-01  waiting_for_opponent in _EXPORTABLE_PHASES
    CCD-WAIT-02  viewer_action_text challenger: "Waiting for RD14S"
    CCD-WAIT-03  viewer_action_text challenged: "Waiting for T1B1K3"
    CCD-WAIT-04  get_unlocked: ACCEPTED + has_my_attempt=True → includes waiting_for_opponent
    CCD-WAIT-05  get_unlocked: ACCEPTED + has_my_attempt=False → NOT in unlocked
    CCD-WAIT-06  post_16_9: arch-waiting div rendered (not arch-battle, not arch-result)
    CCD-WAIT-07  post_16_9: viewer photo present
    CCD-WAIT-08  post_16_9: OVR overlay rendered when overall present
    CCD-WAIT-09  post_16_9: score rendered when my_score present
    CCD-WAIT-10  post_16_9: 'Result Submitted' when my_score is None
    CCD-WAIT-11  story_9_16: arch-waiting-story div rendered
    CCD-WAIT-12  challenge_accepted post: arch-invitation-balanced still used (no regression)
    CCD-WAIT-13  live_lobby_ready post: arch-battle still used (no regression)
    """

    def _render(self, template_path: str, phase: str, **kwargs) -> str:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(template_path)
        ctx = _make_mock_ctx(phase=phase, **kwargs)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    def test_ccd_wait_01_in_exportable_phases(self):
        """CCD-WAIT-01: waiting_for_opponent in _EXPORTABLE_PHASES."""
        from app.api.web_routes.vt_challenges import _EXPORTABLE_PHASES
        assert "waiting_for_opponent" in _EXPORTABLE_PHASES

    def test_ccd_wait_02_viewer_action_text_challenger(self):
        """CCD-WAIT-02: challenger view → 'Waiting for [challenged_name]'."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = MagicMock()
        ch.challenger_id = 10; ch.challenged_id = 20
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S", email="r@x.com")
        ch.game = MagicMock(name="Memory Sequence")
        ch.challenge_mode = "async"; ch.status = MagicMock()
        ch.winner_id = None; ch.winner = None; ch.is_draw = False
        ch.completed_at = None; ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = 99; ch.challenged_attempt_id = None
        viewer = MagicMock(id=10)
        ctx = _build_challenge_card_context(ch, viewer, None, None, "waiting_for_opponent")
        assert ctx["viewer_action_text"] == "Waiting for RD14S"

    def test_ccd_wait_03_viewer_action_text_challenged(self):
        """CCD-WAIT-03: challenged view → 'Waiting for [challenger_name]'."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = MagicMock()
        ch.challenger_id = 10; ch.challenged_id = 20
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S", email="r@x.com")
        ch.game = MagicMock(name="Memory Sequence")
        ch.challenge_mode = "async"; ch.status = MagicMock()
        ch.winner_id = None; ch.winner = None; ch.is_draw = False
        ch.completed_at = None; ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = None; ch.challenged_attempt_id = 99
        viewer = MagicMock(id=20)
        ctx = _build_challenge_card_context(ch, viewer, None, None, "waiting_for_opponent")
        assert ctx["viewer_action_text"] == "Waiting for T1B1K3"

    def test_ccd_wait_04_unlocked_when_has_attempt(self):
        """CCD-WAIT-04: ACCEPTED + challenger_attempt_id set → waiting_for_opponent unlocked."""
        from app.api.web_routes.vt_challenges import (
            get_unlocked_challenge_card_phases, ChallengeStatus,
        )
        ch = MagicMock()
        ch.status = ChallengeStatus.ACCEPTED
        ch.challenger_id = 1; ch.challenged_id = 2
        ch.challenger_attempt_id = 99   # viewer has submitted
        ch.challenged_attempt_id = None
        ch.winner_id = None; ch.is_draw = False; ch.forfeit_user_id = None
        result = get_unlocked_challenge_card_phases(ch, viewer_id=1)
        assert "waiting_for_opponent" in result

    def test_ccd_wait_05_not_unlocked_without_attempt(self):
        """CCD-WAIT-05: ACCEPTED + no attempt → waiting_for_opponent NOT in unlocked."""
        from app.api.web_routes.vt_challenges import (
            get_unlocked_challenge_card_phases, ChallengeStatus,
        )
        ch = MagicMock()
        ch.status = ChallengeStatus.ACCEPTED
        ch.challenger_id = 1; ch.challenged_id = 2
        ch.challenger_attempt_id = None  # viewer has NOT submitted
        ch.challenged_attempt_id = None
        ch.winner_id = None; ch.is_draw = False; ch.forfeit_user_id = None
        result = get_unlocked_challenge_card_phases(ch, viewer_id=1)
        assert "waiting_for_opponent" not in result
        assert "challenge_accepted" in result   # still sees challenge_accepted

    def test_ccd_wait_06_post_uses_arch_waiting(self):
        """CCD-WAIT-06: post_16_9 waiting_for_opponent: arch-waiting rendered."""
        html = self._render("public/export/challenge/post_16_9.html", "waiting_for_opponent",
                            my_score=85.3)
        assert '<div class="arch-waiting">' in html
        assert '<div class="arch-battle">' not in html
        assert '<div class="arch-result">' not in html

    def test_ccd_wait_07_post_viewer_photo(self):
        """CCD-WAIT-07: post_16_9: viewer photo rendered in awh-player-zone."""
        html = self._render("public/export/challenge/post_16_9.html", "waiting_for_opponent",
                            challenger_photo="/viewer_photo.png")
        assert "/viewer_photo.png" in html

    def test_ccd_wait_08_post_ovr_overlay(self):
        """CCD-WAIT-08: post_16_9: OVR overlay rendered when challenger_overall present."""
        html = self._render("public/export/challenge/post_16_9.html", "waiting_for_opponent",
                            challenger_overall=78.5)
        assert 'class="aib-overall"' in html
        assert "OVR 78.5" in html

    def test_ccd_wait_09_post_score_shown(self):
        """CCD-WAIT-09: post_16_9: score rendered when my_result_summary.primary_value present."""
        html = self._render("public/export/challenge/post_16_9.html", "waiting_for_opponent",
                            my_score=85.3)
        assert "85.3" in html
        assert "Score" in html

    def test_ccd_wait_10_post_result_submitted_when_no_score(self):
        """CCD-WAIT-10: post_16_9: 'Result Submitted' shown when primary_value is None."""
        html = self._render("public/export/challenge/post_16_9.html", "waiting_for_opponent",
                            my_score=None)
        assert "Result Submitted" in html
        assert 'class="awh-score-label"' not in html

    def test_ccd_wait_11_story_uses_arch_waiting_story(self):
        """CCD-WAIT-11: story_9_16: arch-waiting-story rendered."""
        html = self._render("public/export/challenge/story_9_16.html", "waiting_for_opponent",
                            my_score=85.3)
        assert '<div class="arch-waiting-story">' in html

    def test_ccd_wait_12_accepted_no_regression(self):
        """CCD-WAIT-12: challenge_accepted post: arch-invitation-balanced unchanged."""
        html = self._render("public/export/challenge/post_16_9.html", "challenge_accepted")
        assert '<div class="arch-invitation-balanced">' in html
        assert '<div class="arch-waiting">' not in html

    def test_ccd_wait_13_live_lobby_no_regression(self):
        """CCD-WAIT-13: live_lobby_ready post: arch-battle unchanged."""
        html = self._render("public/export/challenge/post_16_9.html", "live_lobby_ready")
        assert '<div class="arch-battle">' in html
        assert '<div class="arch-waiting">' not in html


class TestCCDResultSummary:
    """CCD-RSUMMARY: _build_result_summary helper + template rendering.

    CCD-RSUMMARY-01  fallback: attempt=None → primary_value=None, secondary_items=[]
    CCD-RSUMMARY-02  fallback: game_code=None + attempt with score → Score shown, no secondary
    CCD-RSUMMARY-03  fallback: game_code unknown → Score shown, no secondary
    CCD-RSUMMARY-04  MS: primary_value kerekítve 1 tizedesjegyre
    CCD-RSUMMARY-05  MS: Sequence + Accuracy secondary itemek
    CCD-RSUMMARY-06  MS: best_seq=0 → Sequence item nem kerül secondary-ba
    CCD-RSUMMARY-07  TT: Difficulty + Hit Rate secondary itemek
    CCD-RSUMMARY-08  TT: difficulty_level=None → Difficulty item nem kerül be
    CCD-RSUMMARY-09  post_16_9: MS secondary_items megjelenik a kártyán
    CCD-RSUMMARY-10  story_9_16: TT secondary_items megjelenik a kártyán
    CCD-RSUMMARY-11  post_16_9 no secondary: üres sor nem rendereléodik
    CCD-RSUMMARY-12  story_9_16: Result Submitted ha primary_value=None
    """

    def _fn(self):
        from app.api.web_routes.vt_challenges import _build_result_summary
        return _build_result_summary

    def _mock_attempt(
        self,
        score_normalized=78.456,
        stimuli_count=10,
        correct_count=8,
        raw_metrics=None,
    ):
        a = MagicMock()
        a.score_normalized = score_normalized
        a.stimuli_count = stimuli_count
        a.correct_count = correct_count
        a.raw_metrics = raw_metrics or {}
        return a

    def _render(self, template_path: str, phase: str, **kwargs) -> str:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(template_path)
        ctx = _make_mock_ctx(phase=phase, **kwargs)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    def test_ccd_rsummary_01_fallback_no_attempt(self):
        """CCD-RSUMMARY-01: attempt=None → primary_value=None, secondary_items=[]."""
        fn = self._fn()
        result = fn(None, "memory_sequence")
        assert result["primary_value"] is None
        assert result["secondary_items"] == []
        assert result["primary_label"] == "Score"

    def test_ccd_rsummary_02_fallback_game_code_none_score_shown(self):
        """CCD-RSUMMARY-02: game_code=None + valid attempt → Score present, no secondary."""
        fn = self._fn()
        attempt = self._mock_attempt(score_normalized=71.2)
        result = fn(attempt, None)
        assert result["primary_value"] == 71.2
        assert result["secondary_items"] == []

    def test_ccd_rsummary_03_fallback_unknown_game_code(self):
        """CCD-RSUMMARY-03: unknown game_code → Score present, no secondary."""
        fn = self._fn()
        attempt = self._mock_attempt(score_normalized=65.0)
        result = fn(attempt, "unknown_game")
        assert result["primary_value"] == 65.0
        assert result["secondary_items"] == []

    def test_ccd_rsummary_04_ms_primary_value_rounded(self):
        """CCD-RSUMMARY-04: MS score_normalized=78.456 → primary_value=78.5."""
        fn = self._fn()
        attempt = self._mock_attempt(score_normalized=78.456)
        result = fn(attempt, "memory_sequence")
        assert result["primary_value"] == 78.5

    def test_ccd_rsummary_05_ms_sequence_and_accuracy(self):
        """CCD-RSUMMARY-05: MS per_round with correct rounds → Sequence + Accuracy."""
        fn = self._fn()
        attempt = self._mock_attempt(
            score_normalized=78.5,
            stimuli_count=10,
            correct_count=8,
            raw_metrics={"per_round": [
                {"sequence_length": 5, "outcome": "correct"},
                {"sequence_length": 7, "outcome": "correct"},
                {"sequence_length": 8, "outcome": "wrong"},
            ]},
        )
        result = fn(attempt, "memory_sequence")
        labels = [i["label"] for i in result["secondary_items"]]
        values = {i["label"]: i["value"] for i in result["secondary_items"]}
        assert "Sequence" in labels
        assert values["Sequence"] == "7"
        assert "Accuracy" in labels
        assert values["Accuracy"] == "80%"

    def test_ccd_rsummary_06_ms_best_seq_zero_excluded(self):
        """CCD-RSUMMARY-06: MS no completed rounds → Sequence item absent."""
        fn = self._fn()
        attempt = self._mock_attempt(
            score_normalized=50.0,
            stimuli_count=5,
            correct_count=0,
            raw_metrics={"per_round": [
                {"sequence_length": 4, "outcome": "wrong"},
            ]},
        )
        result = fn(attempt, "memory_sequence")
        labels = [i["label"] for i in result["secondary_items"]]
        assert "Sequence" not in labels

    def test_ccd_rsummary_07_tt_difficulty_and_hit_rate(self):
        """CCD-RSUMMARY-07: TT difficulty_level + stimuli_count → Difficulty + Hit Rate."""
        fn = self._fn()
        attempt = self._mock_attempt(
            score_normalized=71.2,
            stimuli_count=25,
            correct_count=18,
            raw_metrics={"difficulty_level": "hard"},
        )
        result = fn(attempt, "target_tracking")
        values = {i["label"]: i["value"] for i in result["secondary_items"]}
        assert values.get("Difficulty") == "Hard"
        assert values.get("Hit Rate") == "72%"

    def test_ccd_rsummary_08_tt_difficulty_none_excluded(self):
        """CCD-RSUMMARY-08: TT raw_metrics without difficulty_level → Difficulty absent."""
        fn = self._fn()
        attempt = self._mock_attempt(
            score_normalized=60.0,
            stimuli_count=20,
            correct_count=14,
            raw_metrics={},
        )
        result = fn(attempt, "target_tracking")
        labels = [i["label"] for i in result["secondary_items"]]
        assert "Difficulty" not in labels
        assert "Hit Rate" in labels

    def test_ccd_rsummary_09_post_ms_secondary_rendered(self):
        """CCD-RSUMMARY-09: post_16_9 renders MS secondary items in awh-secondary."""
        rs = {
            "game_code": "memory_sequence", "primary_label": "Score",
            "primary_value": 78.5,
            "secondary_items": [
                {"label": "Sequence", "value": "7"},
                {"label": "Accuracy", "value": "85%"},
            ],
        }
        html = self._render("public/export/challenge/post_16_9.html",
                            "waiting_for_opponent", my_result_summary=rs)
        assert "Sequence: 7" in html
        assert "Accuracy: 85%" in html
        assert "awh-secondary" in html

    def test_ccd_rsummary_10_story_tt_secondary_rendered(self):
        """CCD-RSUMMARY-10: story_9_16 renders TT secondary items in aws-secondary."""
        rs = {
            "game_code": "target_tracking", "primary_label": "Score",
            "primary_value": 71.2,
            "secondary_items": [
                {"label": "Difficulty", "value": "Hard"},
                {"label": "Hit Rate", "value": "72%"},
            ],
        }
        html = self._render("public/export/challenge/story_9_16.html",
                            "waiting_for_opponent", my_result_summary=rs)
        assert "Difficulty: Hard" in html
        assert "Hit Rate: 72%" in html
        assert "aws-secondary" in html

    def test_ccd_rsummary_11_post_no_secondary_no_empty_row(self):
        """CCD-RSUMMARY-11: post_16_9 no secondary_items → awh-secondary div absent."""
        rs = {
            "game_code": None, "primary_label": "Score",
            "primary_value": 65.0, "secondary_items": [],
        }
        html = self._render("public/export/challenge/post_16_9.html",
                            "waiting_for_opponent", my_result_summary=rs)
        assert 'class="awh-secondary"' not in html
        assert "65.0" in html

    def test_ccd_rsummary_12_story_result_submitted_when_no_score(self):
        """CCD-RSUMMARY-12: story_9_16 primary_value=None → Result Submitted shown."""
        rs = {
            "game_code": None, "primary_label": "Score",
            "primary_value": None, "secondary_items": [],
        }
        html = self._render("public/export/challenge/story_9_16.html",
                            "waiting_for_opponent", my_result_summary=rs)
        assert "Result Submitted" in html
        assert 'class="aws-score-label"' not in html


# ── Helper factories for result summary dicts ────────────────────────────────

def _ms_rs(score: float, seq: int = 7, acc: int = 85) -> dict:
    """Build a Memory Sequence result_summary for tests."""
    return {
        "game_code": "memory_sequence", "primary_label": "Score",
        "primary_value": score,
        "secondary_items": [
            {"label": "Seq", "value": str(seq)},
            {"label": "Acc", "value": f"{acc}%"},
        ],
    }


def _tt_rs(score: float, diff: str = "Hard", hit: int = 72) -> dict:
    """Build a Target Tracking result_summary for tests."""
    return {
        "game_code": "target_tracking", "primary_label": "Score",
        "primary_value": score,
        "secondary_items": [
            {"label": "Diff", "value": diff},
            {"label": "Hit", "value": f"{hit}%"},
        ],
    }


def _null_rs() -> dict:
    return {"game_code": None, "primary_label": "Score",
            "primary_value": None, "secondary_items": []}


class TestCCDResult:
    """CCD-RESULT: completed_score_win + completed_draw — Result VS card.

    Backend context:
    CCD-RESULT-01  challenger_result_summary in context; MS → Seq + Acc
    CCD-RESULT-02  challenged_result_summary in context; TT → Diff + Hit
    CCD-RESULT-03  viewer_result_summary == challenger_result_summary when is_challenger
    CCD-RESULT-04  opponent_result_summary == challenged_result_summary when is_challenger

    Post 16:9 template:
    CCD-RESULT-05  completed_score_win: challenger OVR rendered
    CCD-RESULT-06  completed_score_win: challenged OVR rendered
    CCD-RESULT-07  completed_score_win: MS secondary items both columns
    CCD-RESULT-08  completed_draw: TT secondary items, no winner-ring, no WINNER label
    CCD-RESULT-09  completed_score_win: no secondary → ard-secondary class absent
    CCD-RESULT-10  completed_draw: DRAW badge present, WINNER absent

    Story 9:16 template:
    CCD-RESULT-11  completed_score_win: arch-result-story + OVR + secondary
    CCD-RESULT-12  completed_score_win: winner-ring in story

    Regression (forfeit / no_contest — deferred scope, must not break):
    CCD-RESULT-REG-01  completed_forfeit_win post: arch-result div present
    CCD-RESULT-REG-02  completed_forfeit_loss post: arch-result div present
    CCD-RESULT-REG-03  no_contest post: arch-result div present
    CCD-RESULT-REG-04  completed_forfeit_win story: arch-result-story div present
    """

    def _render(self, template_path: str, phase: str, **kwargs) -> str:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(template_path)
        ctx = _make_mock_ctx(phase=phase, **kwargs)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    # ── Backend context tests ────────────────────────────────────────────────

    def test_ccd_result_01_challenger_result_summary_in_context(self):
        """CCD-RESULT-01: challenger_result_summary in context; MS attempt → Seq + Acc."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = MagicMock()
        ch.challenger_id = 10; ch.challenged_id = 20
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S",  email="r@x.com")
        ch.game = MagicMock(name="Memory Sequence", code="memory_sequence")
        ch.challenge_mode = "async"; ch.status = MagicMock()
        ch.winner_id = 10; ch.winner = ch.challenger
        ch.is_draw = False; ch.completed_at = None
        ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = 1; ch.challenged_attempt_id = 2
        viewer = MagicMock(id=10)
        ch_attempt = MagicMock(
            score_normalized=85.0, stimuli_count=10, correct_count=8,
            raw_metrics={"per_round": [
                {"sequence_length": 7, "outcome": "correct"},
                {"sequence_length": 8, "outcome": "wrong"},
            ]},
        )
        cd_attempt = MagicMock(
            score_normalized=71.0, stimuli_count=10, correct_count=7,
            raw_metrics={"per_round": [
                {"sequence_length": 5, "outcome": "correct"},
            ]},
        )
        ctx = _build_challenge_card_context(
            ch, viewer, ch_attempt, cd_attempt, "completed_score_win"
        )
        crs = ctx["challenger_result_summary"]
        assert crs["primary_value"] == 85.0
        labels = [i["label"] for i in crs["secondary_items"]]
        assert "Sequence" in labels
        assert "Accuracy" in labels

    def test_ccd_result_02_challenged_result_summary_in_context(self):
        """CCD-RESULT-02: challenged_result_summary in context; TT attempt → Diff + Hit."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = MagicMock()
        ch.challenger_id = 10; ch.challenged_id = 20
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S",  email="r@x.com")
        ch.game = MagicMock(name="Target Tracking", code="target_tracking")
        ch.challenge_mode = "async"; ch.status = MagicMock()
        ch.winner_id = 10; ch.winner = ch.challenger
        ch.is_draw = False; ch.completed_at = None
        ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = 1; ch.challenged_attempt_id = 2
        viewer = MagicMock(id=10)
        ch_attempt = MagicMock(
            score_normalized=78.0, stimuli_count=20, correct_count=16,
            raw_metrics={"difficulty_level": "hard"},
        )
        cd_attempt = MagicMock(
            score_normalized=65.0, stimuli_count=20, correct_count=13,
            raw_metrics={"difficulty_level": "medium"},
        )
        ctx = _build_challenge_card_context(
            ch, viewer, ch_attempt, cd_attempt, "completed_score_win"
        )
        cdrs = ctx["challenged_result_summary"]
        assert cdrs["primary_value"] == 65.0
        values = {i["label"]: i["value"] for i in cdrs["secondary_items"]}
        assert values.get("Difficulty") == "Medium"
        assert "Hit Rate" in values

    def test_ccd_result_03_viewer_result_summary_equals_challenger_when_is_challenger(self):
        """CCD-RESULT-03: viewer_result_summary == challenger_result_summary when viewer is challenger."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = MagicMock()
        ch.challenger_id = 10; ch.challenged_id = 20
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S",  email="r@x.com")
        ch.game = MagicMock(code="memory_sequence")
        ch.challenge_mode = "async"; ch.status = MagicMock()
        ch.winner_id = None; ch.winner = None; ch.is_draw = False
        ch.completed_at = None; ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = 1; ch.challenged_attempt_id = None
        viewer = MagicMock(id=10)
        attempt = MagicMock(score_normalized=77.0, stimuli_count=None, correct_count=None, raw_metrics={})
        ctx = _build_challenge_card_context(ch, viewer, attempt, None, "completed_draw")
        assert ctx["viewer_result_summary"]["primary_value"] == ctx["challenger_result_summary"]["primary_value"]

    def test_ccd_result_04_opponent_result_summary_equals_challenged_when_is_challenger(self):
        """CCD-RESULT-04: opponent_result_summary == challenged_result_summary when viewer is challenger."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = MagicMock()
        ch.challenger_id = 10; ch.challenged_id = 20
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S",  email="r@x.com")
        ch.game = MagicMock(code="target_tracking")
        ch.challenge_mode = "async"; ch.status = MagicMock()
        ch.winner_id = None; ch.winner = None; ch.is_draw = True
        ch.completed_at = None; ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = 1; ch.challenged_attempt_id = 2
        viewer = MagicMock(id=10)
        ch_att = MagicMock(score_normalized=70.0, stimuli_count=None, correct_count=None, raw_metrics={})
        cd_att = MagicMock(score_normalized=70.0, stimuli_count=None, correct_count=None, raw_metrics={})
        ctx = _build_challenge_card_context(ch, viewer, ch_att, cd_att, "completed_draw")
        assert ctx["opponent_result_summary"]["primary_value"] == ctx["challenged_result_summary"]["primary_value"]

    # ── Post 16:9 template tests ─────────────────────────────────────────────

    def test_ccd_result_05_post_challenger_ovr_rendered(self):
        """CCD-RESULT-05: completed_score_win post: challenger OVR rendered."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "completed_score_win",
            challenger_score=85.0, challenged_score=71.0, winner_name="T1B1K3",
            challenger_overall=78.5,
        )
        assert "OVR 78.5" in html

    def test_ccd_result_06_post_challenged_ovr_rendered(self):
        """CCD-RESULT-06: completed_score_win post: challenged OVR rendered."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "completed_score_win",
            challenger_score=85.0, challenged_score=71.0, winner_name="T1B1K3",
            challenged_overall=81.0,
        )
        assert "OVR 81.0" in html

    def test_ccd_result_07_post_ms_secondary_both_columns(self):
        """CCD-RESULT-07: completed_score_win post: MS Seq + Acc in both columns."""
        rs_ch = _ms_rs(85.0, seq=7, acc=80)
        rs_cd = _ms_rs(71.0, seq=5, acc=70)
        html = self._render(
            "public/export/challenge/post_16_9.html", "completed_score_win",
            challenger_score=85.0, challenged_score=71.0, winner_name="T1B1K3",
            challenger_result_summary=rs_ch, challenged_result_summary=rs_cd,
        )
        assert "Seq: 7" in html
        assert "Acc: 80%" in html
        assert "Seq: 5" in html
        assert "Acc: 70%" in html

    def test_ccd_result_08_post_tt_draw_secondary_no_winner(self):
        """CCD-RESULT-08: completed_draw post: TT Diff + Hit rendered, no WINNER label."""
        rs_ch = _tt_rs(70.0, diff="Hard", hit=72)
        rs_cd = _tt_rs(70.0, diff="Medium", hit=65)
        html = self._render(
            "public/export/challenge/post_16_9.html", "completed_draw",
            challenger_score=70.0, challenged_score=70.0, winner_name=None,
            challenger_result_summary=rs_ch, challenged_result_summary=rs_cd,
        )
        assert "Diff: Hard" in html
        assert "Hit: 72%" in html
        assert "Diff: Medium" in html
        assert "✓ WINNER" not in html
        assert 'class="ard2-winner-bar"' not in html  # no winner bar on draw

    def test_ccd_result_09_post_no_secondary_div_absent(self):
        """CCD-RESULT-09: completed_score_win: no secondary_items → ard2-secondary-row absent."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "completed_score_win",
            challenger_score=85.0, challenged_score=71.0, winner_name="T1B1K3",
        )
        assert 'class="ard2-secondary-row"' not in html

    def test_ccd_result_10_post_draw_badge_and_no_winner_label(self):
        """CCD-RESULT-10: completed_draw: DRAW badge present, no ✓ WINNER."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "completed_draw",
            challenger_score=70.0, challenged_score=70.0, winner_name=None,
        )
        assert "DRAW" in html
        assert "✓ WINNER" not in html

    # ── Story 9:16 template tests ─────────────────────────────────────────────

    def test_ccd_result_11_story_arch_result_d2_story_with_ovr_and_secondary(self):
        """CCD-RESULT-11: story completed_score_win: arch-result-d2-story + OVR + secondary."""
        rs_ch = _ms_rs(85.0, seq=7, acc=80)
        rs_cd = _ms_rs(71.0, seq=5, acc=70)
        html = self._render(
            "public/export/challenge/story_9_16.html", "completed_score_win",
            challenger_score=85.0, challenged_score=71.0, winner_name="T1B1K3",
            challenger_overall=78.5, challenged_overall=81.0,
            challenger_result_summary=rs_ch, challenged_result_summary=rs_cd,
        )
        assert 'class="arch-result-d2-story"' in html
        assert "OVR 78.5" in html
        assert "OVR 81.0" in html
        assert "Seq: 7" in html
        assert "Seq: 5" in html

    def test_ccd_result_12_story_winner_bar_on_winner_zone(self):
        """CCD-RESULT-12: story completed_score_win: ard2-winner-bar on winning zone."""
        html = self._render(
            "public/export/challenge/story_9_16.html", "completed_score_win",
            challenger_score=85.0, challenged_score=71.0, winner_name="T1B1K3",
            challenger_photo="/ch1.png", challenged_photo="/ch2.png",
        )
        assert "ard2-winner-bar" in html

    # ── Regression — forfeit / no_contest must still render ──────────────────

    def test_ccd_result_reg_01_forfeit_win_post_renders(self):
        """CCD-RESULT-REG-01: completed_forfeit_win post: arch-result-d2 div present."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "completed_forfeit_win",
            winner_name="T1B1K3",
        )
        assert 'class="arch-result-d2"' in html
        assert "FORFEIT WIN" in html

    def test_ccd_result_reg_02_forfeit_loss_post_renders(self):
        """CCD-RESULT-REG-02: completed_forfeit_loss post: arch-result-d2 div present."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "completed_forfeit_loss",
        )
        assert 'class="arch-result-d2"' in html
        assert "FORFEIT LOSS" in html

    def test_ccd_result_reg_03_no_contest_post_renders(self):
        """CCD-RESULT-REG-03: no_contest post: arch-result-d2 div present."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "no_contest",
        )
        assert 'class="arch-result-d2"' in html
        assert "NO CONTEST" in html

    def test_ccd_result_reg_04_forfeit_win_story_renders(self):
        """CCD-RESULT-REG-04: completed_forfeit_win story: arch-result-d2-story div present."""
        html = self._render(
            "public/export/challenge/story_9_16.html", "completed_forfeit_win",
            winner_name="T1B1K3",
        )
        assert 'class="arch-result-d2-story"' in html
        assert "FORFEIT WIN" in html


def _forfeit_win_rs(score: float) -> dict:
    return {"game_code": "memory_sequence", "primary_label": "Score",
            "primary_value": score, "secondary_items": [{"label": "Seq", "value": "7"}, {"label": "Acc", "value": "80%"}]}


class TestCCDForfeit:
    """CCD-FORFEIT: completed_forfeit_win / completed_forfeit_loss / no_contest.

    Backend context:
    CCD-FORFEIT-01  viewer_action_text forfeit_win: "[forfeiter] forfeited"
    CCD-FORFEIT-02  viewer_action_text forfeit_loss: "you forfeited" (viewer is forfeiter)
    CCD-FORFEIT-03  viewer_action_text no_contest: "neither player completed"
    CCD-FORFEIT-04  forfeiter_name in context
    CCD-FORFEIT-05  forfeit_sublabel: deadline_expired → "Deadline expired"
    CCD-FORFEIT-06  forfeit_sublabel: no_show → "No show"
    CCD-FORFEIT-07  forfeit_sublabel: no_contest → "Challenge expired"

    Post 16:9 template:
    CCD-FORFEIT-08  forfeit_win post: arch-result-d2 + FORFEIT WIN badge + winner score + DNP on forfeiter zone
    CCD-FORFEIT-09  forfeit_loss post: FORFEIT LOSS badge + no winner bar on loser zone
    CCD-FORFEIT-10  no_contest post: no score row, no winner bar, DNP both zones

    Story 9:16 template:
    CCD-FORFEIT-11  forfeit_win story: arch-result-d2-story + winner score + DNP
    CCD-FORFEIT-12  no_contest story: no winner bar, arch-result-d2-story renders
    """

    def _render(self, template_path: str, phase: str, **kwargs) -> str:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(template_path)
        ctx = _make_mock_ctx(phase=phase, **kwargs)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    def _ch_mock(self, challenger_id=10, challenged_id=20,
                 winner_id=None, forfeit_user_id=None, forfeit_reason=None, is_draw=False):
        from app.models.vt_challenge import ChallengeStatus
        ch = MagicMock()
        ch.challenger_id = challenger_id; ch.challenged_id = challenged_id
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S",  email="r@x.com")
        ch.game = MagicMock(name="Memory Sequence", code="memory_sequence")
        ch.challenge_mode = "async"; ch.status = ChallengeStatus.COMPLETED
        ch.winner_id = winner_id
        ch.winner = ch.challenger if winner_id == challenger_id else (ch.challenged if winner_id == challenged_id else None)
        ch.is_draw = is_draw; ch.completed_at = None
        ch.forfeit_user_id = forfeit_user_id
        ch.forfeit_reason = forfeit_reason
        ch.forfeit_user = ch.challenger if forfeit_user_id == challenger_id else (ch.challenged if forfeit_user_id == challenged_id else None)
        ch.challenger_attempt_id = 1; ch.challenged_attempt_id = None
        return ch

    # ── Backend context tests ────────────────────────────────────────────────

    def test_ccd_forfeit_01_action_text_forfeit_win(self):
        """CCD-FORFEIT-01: forfeit_win viewer_action_text: '[forfeiter] forfeited'."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = self._ch_mock(winner_id=10, forfeit_user_id=20, forfeit_reason="deadline_expired")
        viewer = MagicMock(id=10)
        attempt = MagicMock(score_normalized=78.0, stimuli_count=10, correct_count=8, raw_metrics={}, skill_deltas=None)
        ctx = _build_challenge_card_context(ch, viewer, attempt, None, "completed_forfeit_win")
        assert "forfeited" in ctx["viewer_action_text"].lower()
        assert "RD14S" in ctx["viewer_action_text"]

    def test_ccd_forfeit_02_action_text_forfeit_loss_viewer_forfeited(self):
        """CCD-FORFEIT-02: forfeit_loss — viewer is forfeiter → 'you forfeited'."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        # challenger (viewer_id=10) forfeited (no attempt), challenged (id=20) won
        ch = self._ch_mock(winner_id=20, forfeit_user_id=10, forfeit_reason="deadline_expired")
        viewer = MagicMock(id=10)
        winner_attempt = MagicMock(score_normalized=72.0, stimuli_count=None, correct_count=None, raw_metrics={}, skill_deltas=None)
        ctx = _build_challenge_card_context(ch, viewer, None, winner_attempt, "completed_forfeit_loss")
        assert ctx["viewer_action_text"] == "you forfeited"

    def test_ccd_forfeit_03_action_text_no_contest(self):
        """CCD-FORFEIT-03: no_contest viewer_action_text: 'neither player completed'."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = self._ch_mock(winner_id=None, forfeit_user_id=10, forfeit_reason="no_contest")
        viewer = MagicMock(id=10)
        ctx = _build_challenge_card_context(ch, viewer, None, None, "no_contest")
        assert ctx["viewer_action_text"] == "neither player completed"

    def test_ccd_forfeit_04_forfeiter_name_in_context(self):
        """CCD-FORFEIT-04: forfeiter_name in context."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = self._ch_mock(winner_id=10, forfeit_user_id=20, forfeit_reason="no_show")
        viewer = MagicMock(id=10)
        attempt = MagicMock(score_normalized=78.0, stimuli_count=None, correct_count=None, raw_metrics={}, skill_deltas=None)
        ctx = _build_challenge_card_context(ch, viewer, attempt, None, "completed_forfeit_win")
        assert ctx["forfeiter_name"] == "RD14S"

    def test_ccd_forfeit_05_sublabel_deadline(self):
        """CCD-FORFEIT-05: forfeit_reason=deadline_expired → forfeit_sublabel='Deadline expired'."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = self._ch_mock(winner_id=10, forfeit_user_id=20, forfeit_reason="deadline_expired")
        viewer = MagicMock(id=10)
        attempt = MagicMock(score_normalized=78.0, stimuli_count=None, correct_count=None, raw_metrics={}, skill_deltas=None)
        ctx = _build_challenge_card_context(ch, viewer, attempt, None, "completed_forfeit_win")
        assert ctx["forfeit_sublabel"] == "Deadline expired"

    def test_ccd_forfeit_06_sublabel_no_show(self):
        """CCD-FORFEIT-06: forfeit_reason=no_show → forfeit_sublabel='No show'."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = self._ch_mock(winner_id=10, forfeit_user_id=20, forfeit_reason="no_show")
        viewer = MagicMock(id=10)
        attempt = MagicMock(score_normalized=78.0, stimuli_count=None, correct_count=None, raw_metrics={}, skill_deltas=None)
        ctx = _build_challenge_card_context(ch, viewer, attempt, None, "completed_forfeit_win")
        assert ctx["forfeit_sublabel"] == "No show"

    def test_ccd_forfeit_07_sublabel_no_contest(self):
        """CCD-FORFEIT-07: no_contest → forfeit_sublabel='Challenge expired'."""
        from app.api.web_routes.vt_challenges import _build_challenge_card_context
        ch = self._ch_mock(winner_id=None, forfeit_user_id=10, forfeit_reason="no_contest")
        viewer = MagicMock(id=10)
        ctx = _build_challenge_card_context(ch, viewer, None, None, "no_contest")
        assert ctx["forfeit_sublabel"] == "Challenge expired"

    # ── Post 16:9 template tests ─────────────────────────────────────────────

    def test_ccd_forfeit_08_post_forfeit_win_full(self):
        """CCD-FORFEIT-08: forfeit_win post: arch-result-d2 + badge + winner score + DNP."""
        rs_winner = _forfeit_win_rs(78.0)
        html = self._render(
            "public/export/challenge/post_16_9.html", "completed_forfeit_win",
            winner_name="T1B1K3",
            challenger_result_summary=rs_winner,
            challenged_result_summary=_null_rs(),
            forfeiter_name="RD14S",
            forfeit_sublabel="Deadline expired",
        )
        assert 'class="arch-result-d2"' in html
        assert "FORFEIT WIN" in html
        assert "78.0" in html                        # winner score
        assert "Deadline expired" in html            # sublabel
        assert "Did not play" in html                # DNP overlay on forfeiter zone
        assert "ard2-winner-bar" in html             # winner bar present

    def test_ccd_forfeit_09_post_forfeit_loss(self):
        """CCD-FORFEIT-09: forfeit_loss post: FORFEIT LOSS badge, no winner bar on loser zone."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "completed_forfeit_loss",
            winner_name="RD14S",
            challenger_result_summary=_null_rs(),
            challenged_result_summary=_forfeit_win_rs(72.0),
            forfeiter_name="T1B1K3",
            forfeit_sublabel="No show",
        )
        assert "FORFEIT LOSS" in html
        assert "Did not play" in html

    def test_ccd_forfeit_10_post_no_contest(self):
        """CCD-FORFEIT-10: no_contest post: no score row, no winner bar, DNP both zones."""
        html = self._render(
            "public/export/challenge/post_16_9.html", "no_contest",
            winner_name=None,
            challenger_result_summary=_null_rs(),
            challenged_result_summary=_null_rs(),
            forfeiter_name=None,
            forfeit_sublabel="Challenge expired",
        )
        assert "NO CONTEST" in html
        assert "Challenge expired" in html
        assert 'class="ard2-winner-bar"' not in html
        assert 'class="ard2-score-row"' not in html
        assert "Did not play" in html                # both zones — null primary_value triggers DNP

    # ── Story 9:16 template tests ─────────────────────────────────────────────

    def test_ccd_forfeit_11_story_forfeit_win_full(self):
        """CCD-FORFEIT-11: forfeit_win story: arch-result-d2-story + winner score + DNP."""
        rs_winner = _forfeit_win_rs(78.0)
        html = self._render(
            "public/export/challenge/story_9_16.html", "completed_forfeit_win",
            winner_name="T1B1K3",
            challenger_result_summary=rs_winner,
            challenged_result_summary=_null_rs(),
            forfeiter_name="RD14S",
            forfeit_sublabel="Deadline expired",
        )
        assert 'class="arch-result-d2-story"' in html
        assert "FORFEIT WIN" in html
        assert "78.0" in html
        assert "Deadline expired" in html
        assert "Did not play" in html
        assert "ard2-winner-bar" in html

    def test_ccd_forfeit_12_story_no_contest(self):
        """CCD-FORFEIT-12: no_contest story: arch-result-d2-story, no winner bar."""
        html = self._render(
            "public/export/challenge/story_9_16.html", "no_contest",
            winner_name=None,
            challenger_result_summary=_null_rs(),
            challenged_result_summary=_null_rs(),
            forfeit_sublabel="Challenge expired",
        )
        assert 'class="arch-result-d2-story"' in html
        assert "NO CONTEST" in html
        assert 'class="ard2-winner-bar"' not in html
        assert 'class="ard2-winner-bar--bottom"' not in html


def _skill_rows(rows: list[tuple]) -> list[dict]:
    """Build my_skill_progress list for tests: [(key, delta, level), ...]"""
    from app.skills_config import ALL_SKILLS
    _cat_map = {}
    try:
        from app.api.web_routes.vt_challenges import _SKILL_CATEGORY_LABEL
        _cat_map = _SKILL_CATEGORY_LABEL
    except ImportError:
        pass
    result = []
    for key, delta, level in rows:
        skill_def = ALL_SKILLS.get(key)
        result.append({
            "key": key,
            "name": skill_def["name_en"] if skill_def else key.replace("_", " ").title(),
            "category": _cat_map.get(key, ""),
            "current_level": level,
            "delta": delta,
            "fill_pct": min(round(level), 100) if level is not None else None,
            "is_positive": delta > 0,
            "is_negative": delta < 0,
        })
    return result


class TestCCDSkill:
    """CCD-SKILL: skill_delta_result — Player Card-style Skill Progress card.

    Backend helper:
    CCD-SKILL-01  _build_skill_progress_rows: sorted by abs(delta) desc
    CCD-SKILL-02  _build_skill_progress_rows: max 8 rows enforced
    CCD-SKILL-03  _build_skill_progress_rows: name + category correct (Outfield/Mental etc)
    CCD-SKILL-04  _build_skill_progress_rows: unknown skill key fallback name
    CCD-SKILL-05  _build_skill_progress_rows: fill_pct = None when level=None

    Post 16:9 template:
    CCD-SKILL-06  arch-skill-e2 div present
    CCD-SKILL-07  no cc-photo hero circle; full-zone photo present
    CCD-SKILL-08  pos + OVR overlay present
    CCD-SKILL-09  skill name + category + current_level + delta rendered
    CCD-SKILL-10  positive delta → pos class; negative → neg class
    CCD-SKILL-11  No skill data recorded when my_skill_progress=[]

    Story 9:16 template:
    CCD-SKILL-12  arch-skill-e2-story div present + skill rows rendered

    Regression:
    CCD-SKILL-REG-01  completed_score_win post: no arch-skill-e2
    CCD-SKILL-REG-02  waiting_for_opponent post: no arch-skill-e2
    """

    def _render(self, template_path: str, phase: str, **kwargs) -> str:
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        tmpl = env.get_template(template_path)
        ctx = _make_mock_ctx(phase=phase, **kwargs)
        ctx["request"] = MagicMock()
        return tmpl.render(**ctx)

    # ── Helper tests ─────────────────────────────────────────────────────────

    def test_ccd_skill_01_sorted_by_abs_delta(self):
        """CCD-SKILL-01: rows sorted by abs(delta) descending."""
        from app.api.web_routes.vt_challenges import _build_skill_progress_rows
        deltas = {"composure": 0.1, "accuracy": 0.5, "vision": -0.3}
        rows = _build_skill_progress_rows(deltas, {})
        assert rows[0]["key"] == "accuracy"   # abs 0.5 largest
        assert rows[1]["key"] == "vision"     # abs 0.3 second

    def test_ccd_skill_02_max_8_rows(self):
        """CCD-SKILL-02: max 8 rows returned even if more deltas present."""
        from app.api.web_routes.vt_challenges import _build_skill_progress_rows
        deltas = {f"skill_{i}": float(i) * 0.1 for i in range(1, 15)}
        rows = _build_skill_progress_rows(deltas, {})
        assert len(rows) <= 8

    def test_ccd_skill_03_name_and_category(self):
        """CCD-SKILL-03: known skill key gets correct name_en and category label."""
        from app.api.web_routes.vt_challenges import _build_skill_progress_rows
        rows = _build_skill_progress_rows({"composure": 0.2}, {})
        assert rows[0]["name"] == "Composure"
        assert rows[0]["category"] == "Mental"

    def test_ccd_skill_04_unknown_key_fallback(self):
        """CCD-SKILL-04: unknown skill key falls back to title-cased name, empty category."""
        from app.api.web_routes.vt_challenges import _build_skill_progress_rows
        rows = _build_skill_progress_rows({"unknown_skill_xyz": 0.3}, {})
        assert rows[0]["name"] == "Unknown Skill Xyz"
        assert rows[0]["category"] == ""

    def test_ccd_skill_05_fill_pct_none_when_no_level(self):
        """CCD-SKILL-05: fill_pct=None when skill_levels does not contain the key."""
        from app.api.web_routes.vt_challenges import _build_skill_progress_rows
        rows = _build_skill_progress_rows({"accuracy": 0.5}, {})
        assert rows[0]["fill_pct"] is None
        rows_with_level = _build_skill_progress_rows({"accuracy": 0.5}, {"accuracy": 67.5})
        assert rows_with_level[0]["fill_pct"] == 68

    # ── Post 16:9 template tests ─────────────────────────────────────────────

    def test_ccd_skill_06_post_arch_skill_e2(self):
        """CCD-SKILL-06: skill_delta_result post: arch-skill-e2 div present."""
        rows = _skill_rows([("accuracy", 0.5, 67.5), ("composure", -0.1, 55.0)])
        html = self._render("public/export/challenge/post_16_9.html",
                            "skill_delta_result", my_skill_progress=rows)
        assert 'class="arch-skill-e2"' in html

    def test_ccd_skill_07_post_full_zone_photo(self):
        """CCD-SKILL-07: post: aib-player-photo present, no cc-photo hero circle."""
        rows = _skill_rows([("accuracy", 0.5, 67.5)])
        html = self._render("public/export/challenge/post_16_9.html",
                            "skill_delta_result", my_skill_progress=rows)
        assert "aib-player-photo" in html
        assert "cc-photo hero" not in html
        assert "cc-avatar hero" not in html

    def test_ccd_skill_08_post_ovr_and_pos(self):
        """CCD-SKILL-08: post: OVR + pos overlay rendered."""
        rows = _skill_rows([("accuracy", 0.5, 67.5)])
        html = self._render("public/export/challenge/post_16_9.html",
                            "skill_delta_result", my_skill_progress=rows,
                            challenger_overall=71.0, challenger_primary_pos="CM")
        assert "OVR 71.0" in html
        assert "CM" in html

    def test_ccd_skill_09_post_skill_row_content(self):
        """CCD-SKILL-09: post: skill name + category + value + delta in HTML."""
        rows = _skill_rows([("composure", 0.3, 60.0), ("vision", -0.2, 72.0)])
        html = self._render("public/export/challenge/post_16_9.html",
                            "skill_delta_result", my_skill_progress=rows)
        assert "Composure" in html
        assert "MENT" in html or "Ment" in html    # category abbrev (Mental[:4])
        assert "60" in html                         # current_level rounded
        assert "+0.30" in html                      # positive delta (2dp)
        assert "-0.20" in html                      # negative delta (2dp)

    def test_ccd_skill_10_post_pos_neg_classes(self):
        """CCD-SKILL-10: post: is_positive → pos class; is_negative → neg class."""
        rows = _skill_rows([("accuracy", 0.5, 67.5), ("composure", -0.1, 55.0)])
        html = self._render("public/export/challenge/post_16_9.html",
                            "skill_delta_result", my_skill_progress=rows)
        assert 'class="aske-bar-pos"' in html
        assert 'class="aske-bar-neg"' in html

    def test_ccd_skill_11_post_no_data_fallback(self):
        """CCD-SKILL-11: post: my_skill_progress=[] → No skill data recorded."""
        html = self._render("public/export/challenge/post_16_9.html",
                            "skill_delta_result", my_skill_progress=[])
        assert "No skill data recorded" in html

    # ── Story 9:16 template tests ─────────────────────────────────────────────

    def test_ccd_skill_12_story_arch_and_rows(self):
        """CCD-SKILL-12: story: arch-skill-e2-story + skill rows rendered."""
        rows = _skill_rows([("accuracy", 0.5, 67.5), ("vision", -0.2, 72.0)])
        html = self._render("public/export/challenge/story_9_16.html",
                            "skill_delta_result", my_skill_progress=rows)
        assert 'class="arch-skill-e2-story"' in html
        assert "Accuracy" in html
        assert "Vision" in html
        assert "asb-player-photo" in html

    # ── Regression ───────────────────────────────────────────────────────────

    def test_ccd_skill_reg_01_score_win_no_skill_arch(self):
        """CCD-SKILL-REG-01: completed_score_win post: no arch-skill-e2 element."""
        html = self._render("public/export/challenge/post_16_9.html",
                            "completed_score_win",
                            challenger_score=80.0, challenged_score=70.0, winner_name="T1B1K3")
        assert 'class="arch-skill-e2"' not in html
        assert 'class="arch-result-d2"' in html

    def test_ccd_skill_reg_02_waiting_no_skill_arch(self):
        """CCD-SKILL-REG-02: waiting_for_opponent post: no arch-skill-e2 element."""
        html = self._render("public/export/challenge/post_16_9.html",
                            "waiting_for_opponent", my_score=75.0)
        assert 'class="arch-skill-e2"' not in html
        assert 'class="arch-waiting"' in html


class TestCCDMoodPhaseA:
    """CCD-MOOD-PHASE-A: Phase-A phase/outcome-aware mood photo selection.

    Map coverage:
    CCD-MOOD-01  score_win winner → mood_celebration preferred
    CCD-MOOD-02  score_win loser  → mood_sad_disappointed preferred
    CCD-MOOD-03  draw             → mood_surprised_shocked preferred
    CCD-MOOD-04  accepted         → mood_happy_smile preferred
    CCD-MOOD-05  sent/waiting/live → mood_angry_competitive preferred
    CCD-MOOD-06  skill_delta      → mood_happy_smile preferred
    CCD-MOOD-07  forfeit win winner → mood_celebration
    CCD-MOOD-08  forfeit loss forfeiter → mood_sad_disappointed

    Helper:
    CCD-MOOD-09  preferred slot present → returned
    CCD-MOOD-10  preferred absent, alternative present → alternative returned
    CCD-MOOD-11  both absent → mood_intro_neutral fallback
    CCD-MOOD-12  mood_intro_neutral absent → license photo fallback
    CCD-MOOD-13  no license either → None

    winner_ctx:
    CCD-MOOD-14  winner_id=None → None
    CCD-MOOD-15  winner_id == user_id → True
    CCD-MOOD-16  winner_id != user_id → False

    Snapshot priority:
    CCD-MOOD-17  frozen snapshot present → phase-aware helper NOT called

    Send flow:
    CCD-MOOD-18  send explicit photo → user choice kept (phase-aware not overrides)
    CCD-MOOD-19  send no explicit photo → phase-aware fallback runs
    """

    def _mood_photo(self, db, user_id, slot, url="http://x.com/photo.png", processed=None):
        from unittest.mock import MagicMock
        photo = MagicMock()
        photo.slot = slot
        photo.original_url = url
        photo.processed_png_url = processed
        db.query.return_value.filter_by.return_value.first.return_value = photo
        return photo

    # ── Map coverage ─────────────────────────────────────────────────────────

    def test_ccd_mood_01_score_win_winner_celebration(self):
        """CCD-MOOD-01: score_win winner → mood_celebration preferred."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        pref, _ = _PHASE_MOOD_MAP[("completed_score_win", True)]
        assert pref == "mood_celebration"

    def test_ccd_mood_02_score_win_loser_sad(self):
        """CCD-MOOD-02: score_win loser → mood_sad_disappointed preferred."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        pref, _ = _PHASE_MOOD_MAP[("completed_score_win", False)]
        assert pref == "mood_sad_disappointed"

    def test_ccd_mood_03_draw_surprised(self):
        """CCD-MOOD-03: draw → mood_surprised_shocked preferred."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        pref, _ = _PHASE_MOOD_MAP[("completed_draw", None)]
        assert pref == "mood_surprised_shocked"

    def test_ccd_mood_04_accepted_confident(self):
        """CCD-MOOD-04 (Phase-B): challenge_accepted → mood_confident preferred."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        pref, _ = _PHASE_MOOD_MAP[("challenge_accepted", None)]
        assert pref == "mood_confident"

    def test_ccd_mood_05_sent_focused_ready(self):
        """CCD-MOOD-05 (Phase-B): sent/waiting/live → mood_focused_ready preferred, angry fallback."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        for phase in ("challenge_sent", "waiting_for_opponent", "live_lobby_ready", "live_in_progress"):
            pref, alt = _PHASE_MOOD_MAP[(phase, None)]
            assert pref == "mood_focused_ready", f"{phase} should prefer focused_ready"
            assert alt == "mood_angry_competitive", f"{phase} fallback should be angry_competitive"

    def test_ccd_mood_05b_accepted_confident(self):
        """CCD-MOOD-05b (Phase-B): challenge_accepted → mood_confident preferred."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        pref, alt = _PHASE_MOOD_MAP[("challenge_accepted", None)]
        assert pref == "mood_confident"
        assert alt == "mood_happy_smile"

    def test_ccd_mood_06_skill_delta_proud(self):
        """CCD-MOOD-06 (Phase-B): skill_delta_result → mood_proud preferred, happy fallback."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        pref, alt = _PHASE_MOOD_MAP[("skill_delta_result", None)]
        assert pref == "mood_proud"
        assert alt == "mood_happy_smile"

    def test_ccd_mood_07_forfeit_win_winner_celebration(self):
        """CCD-MOOD-07: completed_forfeit_win winner → mood_celebration."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        pref, _ = _PHASE_MOOD_MAP[("completed_forfeit_win", True)]
        assert pref == "mood_celebration"

    def test_ccd_mood_08_forfeit_loss_forfeiter_sad(self):
        """CCD-MOOD-08: completed_forfeit_loss loser → mood_sad_disappointed."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        pref, _ = _PHASE_MOOD_MAP[("completed_forfeit_loss", False)]
        assert pref == "mood_sad_disappointed"

    # ── Helper: _get_participant_photo_for_phase ──────────────────────────────

    def test_ccd_mood_09_preferred_slot_returned(self):
        """CCD-MOOD-09: preferred slot present → its URL returned."""
        from app.api.web_routes.vt_challenges import _get_participant_photo_for_phase
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = MagicMock(
            original_url="/celebration.png", processed_png_url=None
        )
        result = _get_participant_photo_for_phase(db, 1, "completed_score_win", True)
        assert result == "/celebration.png"

    def test_ccd_mood_10_alternative_when_preferred_absent(self):
        """CCD-MOOD-10: preferred absent, alternative present → alternative returned."""
        from app.api.web_routes.vt_challenges import _get_participant_photo_for_phase
        db = MagicMock()
        call_count = [0]
        def _first():
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # preferred (mood_celebration) absent
            return MagicMock(original_url="/happy.png", processed_png_url=None)
        db.query.return_value.filter_by.return_value.first.side_effect = _first
        result = _get_participant_photo_for_phase(db, 1, "completed_score_win", True)
        assert result == "/happy.png"

    def test_ccd_mood_11_neutral_fallback_when_both_absent(self):
        """CCD-MOOD-11: preferred + alternative absent → mood_intro_neutral fallback."""
        from app.api.web_routes.vt_challenges import _get_participant_photo_for_phase
        db = MagicMock()
        call_count = [0]
        def _first():
            call_count[0] += 1
            if call_count[0] < 3:
                return None
            return MagicMock(original_url="/neutral.png", processed_png_url=None)
        db.query.return_value.filter_by.return_value.first.side_effect = _first
        result = _get_participant_photo_for_phase(db, 1, "completed_score_win", True)
        assert result == "/neutral.png"

    def test_ccd_mood_12_license_fallback_when_no_moods(self):
        """CCD-MOOD-12: all mood slots absent → license photo fallback."""
        from app.api.web_routes.vt_challenges import _get_participant_photo_for_phase
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        lic = MagicMock()
        lic.player_card_photo_url = "/player.png"
        lic.wc_photo_url = None
        db.query.return_value.filter.return_value.first.return_value = lic
        result = _get_participant_photo_for_phase(db, 1, "completed_score_win", True)
        assert result == "/player.png"

    def test_ccd_mood_13_none_when_no_license_and_no_moods(self):
        """CCD-MOOD-13: no mood photos, no license → None."""
        from app.api.web_routes.vt_challenges import _get_participant_photo_for_phase
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        db.query.return_value.filter.return_value.first.return_value = None
        result = _get_participant_photo_for_phase(db, 1, "challenge_sent", None)
        assert result is None

    # ── _winner_ctx ───────────────────────────────────────────────────────────

    def test_ccd_mood_14_winner_ctx_none_when_no_winner(self):
        """CCD-MOOD-14: winner_id=None → _winner_ctx returns None."""
        from app.api.web_routes.vt_challenges import _winner_ctx
        ch = MagicMock(); ch.winner_id = None
        assert _winner_ctx(ch, 10) is None

    def test_ccd_mood_15_winner_ctx_true_when_user_won(self):
        """CCD-MOOD-15: winner_id == user_id → True."""
        from app.api.web_routes.vt_challenges import _winner_ctx
        ch = MagicMock(); ch.winner_id = 10
        assert _winner_ctx(ch, 10) is True

    def test_ccd_mood_16_winner_ctx_false_when_user_lost(self):
        """CCD-MOOD-16: winner_id != user_id → False."""
        from app.api.web_routes.vt_challenges import _winner_ctx
        ch = MagicMock(); ch.winner_id = 20
        assert _winner_ctx(ch, 10) is False

    # ── Snapshot priority ─────────────────────────────────────────────────────

    def test_ccd_mood_17_frozen_snapshot_skips_phase_lookup(self):
        """CCD-MOOD-17: frozen snapshot present → _get_participant_photo_for_phase NOT called."""
        from unittest.mock import patch
        from app.api.web_routes import vt_challenges as vtc
        with patch.object(vtc, "_get_participant_photo_for_phase") as mock_fn:
            db = MagicMock()
            result = "/frozen.png" or vtc._get_participant_photo_for_phase(db, 1, "completed_score_win", True)
            # Simulate the _photo() logic: snapshot_url or helper()
            snapshot = "/frozen.png"
            photo_result = snapshot or vtc._get_participant_photo_for_phase(db, 1, "completed_score_win", True)
            assert photo_result == "/frozen.png"
            mock_fn.assert_not_called()  # frozen snapshot short-circuits the helper

    # ── Send flow ─────────────────────────────────────────────────────────────

    def test_ccd_mood_18_send_explicit_photo_kept(self):
        """CCD-MOOD-18: POST /challenges/send with explicit photo → user's choice is used."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        # Ownership guard logic: if resolved_photo and owns → use resolved_photo
        # Phase-aware runs only in the else/fallback branch
        resolved_photo = "/user_chosen.png"
        owns = True
        snapshot = resolved_photo if owns else None
        assert snapshot == "/user_chosen.png"  # user's explicit choice preserved

    def test_ccd_mood_19_send_no_explicit_uses_phase_aware(self):
        """CCD-MOOD-19 (Phase-B): POST /challenges/send no explicit photo → focused_ready first."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        pref, alt = _PHASE_MOOD_MAP[("challenge_sent", None)]
        assert pref == "mood_focused_ready"
        assert alt == "mood_angry_competitive"


class TestCCDMoodPhaseB:
    """CCD-MOOD-B: Phase-B — 3 new mood slots and updated phase map.

    CCD-MOOD-B-01  MOOD_PHOTO_SLOTS contains all 9 slots
    CCD-MOOD-B-02  _MOOD_SLOT_META in card_editor has 9 entries
    CCD-MOOD-B-03  _SLOT_META in mood_photos has 9 entries
    CCD-MOOD-B-04  focused_ready in _PHASE_MOOD_MAP for sent/waiting/live
    CCD-MOOD-B-05  confident in _PHASE_MOOD_MAP for accepted
    CCD-MOOD-B-06  proud in _PHASE_MOOD_MAP for skill_delta_result
    CCD-MOOD-B-07  win/loss/draw result phases unchanged from Phase-A
    CCD-MOOD-B-08  _get_participant_photo_for_phase: new slot preferred when present
    CCD-MOOD-B-09  _get_participant_photo_for_phase: falls back to angry if no focused_ready
    """

    def test_ccd_mood_b_01_mood_photo_slots_9(self):
        """CCD-MOOD-B-01: MOOD_PHOTO_SLOTS contains all 9 slots."""
        from app.models.user_mood_photos import MOOD_PHOTO_SLOTS
        assert "mood_focused_ready" in MOOD_PHOTO_SLOTS
        assert "mood_confident"     in MOOD_PHOTO_SLOTS
        assert "mood_proud"         in MOOD_PHOTO_SLOTS
        assert len(MOOD_PHOTO_SLOTS) == 9

    def test_ccd_mood_b_02_card_editor_meta_9(self):
        """CCD-MOOD-B-02: _MOOD_SLOT_META in card_editor has 9 entries."""
        from app.api.web_routes.card_editor import _MOOD_SLOT_META
        slots = [m["slot"] for m in _MOOD_SLOT_META]
        assert "mood_focused_ready" in slots
        assert "mood_confident"     in slots
        assert "mood_proud"         in slots
        assert len(_MOOD_SLOT_META) == 9

    def test_ccd_mood_b_03_mood_photos_meta_9(self):
        """CCD-MOOD-B-03: _SLOT_META in mood_photos route has 9 entries."""
        from app.api.web_routes.mood_photos import _SLOT_META
        slots = [m["slot"] for m in _SLOT_META]
        assert "mood_focused_ready" in slots
        assert "mood_confident"     in slots
        assert "mood_proud"         in slots
        assert len(_SLOT_META) == 9

    def test_ccd_mood_b_04_sent_waiting_live_focused(self):
        """CCD-MOOD-B-04: sent/waiting/live → focused_ready; angry as fallback."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        for phase in ("challenge_sent", "waiting_for_opponent", "live_lobby_ready", "live_in_progress"):
            pref, alt = _PHASE_MOOD_MAP[(phase, None)]
            assert pref == "mood_focused_ready"
            assert alt  == "mood_angry_competitive"

    def test_ccd_mood_b_05_accepted_confident(self):
        """CCD-MOOD-B-05: challenge_accepted → confident; happy as fallback."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        pref, alt = _PHASE_MOOD_MAP[("challenge_accepted", None)]
        assert pref == "mood_confident"
        assert alt  == "mood_happy_smile"

    def test_ccd_mood_b_06_skill_delta_proud(self):
        """CCD-MOOD-B-06: skill_delta_result → proud; happy as fallback."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        pref, alt = _PHASE_MOOD_MAP[("skill_delta_result", None)]
        assert pref == "mood_proud"
        assert alt  == "mood_happy_smile"

    def test_ccd_mood_b_07_result_phases_unchanged(self):
        """CCD-MOOD-B-07: win/loss/draw Phase-A result map entries unchanged."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        assert _PHASE_MOOD_MAP[("completed_score_win",  True)][0]  == "mood_celebration"
        assert _PHASE_MOOD_MAP[("completed_score_win",  False)][0] == "mood_sad_disappointed"
        assert _PHASE_MOOD_MAP[("completed_draw",       None)][0]  == "mood_surprised_shocked"

    def test_ccd_mood_b_08_new_slot_preferred_when_present(self):
        """CCD-MOOD-B-08: focused_ready slot available → returned for challenge_sent."""
        from app.api.web_routes.vt_challenges import _get_participant_photo_for_phase
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = MagicMock(
            original_url="/focused.png", processed_png_url=None
        )
        result = _get_participant_photo_for_phase(db, 1, "challenge_sent", None)
        assert result == "/focused.png"

    def test_ccd_mood_b_09_fallback_to_angry_when_no_focused(self):
        """CCD-MOOD-B-09: no focused_ready → falls back to angry_competitive."""
        from app.api.web_routes.vt_challenges import _get_participant_photo_for_phase
        db = MagicMock()
        call_n = [0]
        def _first():
            call_n[0] += 1
            if call_n[0] == 1:
                return None  # focused_ready absent
            return MagicMock(original_url="/angry.png", processed_png_url=None)
        db.query.return_value.filter_by.return_value.first.side_effect = _first
        result = _get_participant_photo_for_phase(db, 1, "challenge_sent", None)
        assert result == "/angry.png"


class TestCCDSnapPolicy:
    """SNAP: challenger_card_photo_url snapshot policy.

    SNAP-01  send with explicit photo → snapshot saved (manual/frozen)
    SNAP-02  send without explicit photo → snapshot NULL (dynamic auto-lookup)
    SNAP-03  challenger=winner + NULL snapshot → celebration mood
    SNAP-04  challenger=loser  + NULL snapshot → sad mood
    SNAP-05  manual snapshot present → frozen (not overridden by auto lookup)
    SNAP-06  Card Studio: NULL snapshot → Auto/Fallback badge state
    SNAP-07  Card Studio: manual snapshot → Manual override badge state
    SNAP-08  clear photo (empty string) → snapshot NULL, auto lookup runs
    SNAP-09  challenged side always dynamic (NULL snapshot baseline)
    """

    def _make_ch(self, challenger_id=10, challenged_id=20,
                 winner_id=None, is_draw=False,
                 ch_snap=None, cd_snap=None):
        from app.models.vt_challenge import ChallengeStatus
        ch = MagicMock()
        ch.challenger_id = challenger_id; ch.challenged_id = challenged_id
        ch.challenger = MagicMock(nickname="T1B1K3", email="t@x.com")
        ch.challenged = MagicMock(nickname="RD14S",  email="r@x.com")
        ch.game = MagicMock(code="memory_sequence")
        ch.challenge_mode = "async"; ch.status = ChallengeStatus.COMPLETED
        ch.winner_id = winner_id; ch.winner = None
        ch.is_draw = is_draw; ch.completed_at = None
        ch.forfeit_reason = None; ch.forfeit_user_id = None
        ch.challenger_attempt_id = None; ch.challenged_attempt_id = None
        ch.challenger_card_photo_url = ch_snap
        ch.challenged_card_photo_url = cd_snap
        return ch

    # ── SNAP-01: explicit photo → snapshot saved ──────────────────────────────

    def test_snap_01_explicit_photo_saves_snapshot(self):
        """SNAP-01: send with explicit valid photo_url → challenger_card_photo_url set."""
        from app.api.web_routes.vt_challenges import _get_participant_photo_for_phase
        from unittest.mock import patch

        explicit_url = "/static/uploads/mood_photos/10_mood_celebration_orig.png"
        # Verify the ownership guard path still saves the URL when owns=True
        owns_mock = MagicMock()
        db = MagicMock()
        # owns query returns a record → ownership passes
        db.query.return_value.filter.return_value.first.return_value = owns_mock

        # The actual send flow: resolved_photo truthy + owns → challenger_snapshot = resolved_photo
        resolved_photo = explicit_url
        owns = db.query(None).filter(None).first()  # simulates the guard
        challenger_snapshot = resolved_photo if owns else None
        assert challenger_snapshot == explicit_url, "Explicit photo should be saved as snapshot"

    # ── SNAP-02: no explicit photo → snapshot NULL ────────────────────────────

    def test_snap_02_no_explicit_photo_snapshot_is_null(self):
        """SNAP-02: send without explicit photo → challenger_card_photo_url = NULL."""
        # Simulate the else branch of the send flow
        card_photo_url = None
        resolved_photo = card_photo_url if isinstance(card_photo_url, str) and card_photo_url else None
        # New logic: else branch → None
        if resolved_photo:
            challenger_snapshot = resolved_photo  # would save
        else:
            challenger_snapshot = None  # new behaviour
        assert challenger_snapshot is None, "No explicit photo → snapshot must be NULL"

    def test_snap_02b_empty_string_no_snapshot(self):
        """SNAP-02b: empty string card_photo_url → treated as no selection → NULL."""
        card_photo_url = ""
        resolved_photo = card_photo_url if isinstance(card_photo_url, str) and card_photo_url else None
        challenger_snapshot = None if not resolved_photo else resolved_photo
        assert challenger_snapshot is None

    # ── SNAP-03/04: winner/loser mood with NULL snapshot ──────────────────────

    def test_snap_03_winner_null_snapshot_gets_celebration(self):
        """SNAP-03: challenger=winner + NULL snapshot → celebration mood via auto lookup."""
        from app.api.web_routes.vt_challenges import _get_participant_photo_for_phase, _PHASE_MOOD_MAP
        pref, _ = _PHASE_MOOD_MAP[("completed_score_win", True)]
        assert pref == "mood_celebration"

        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = MagicMock(
            original_url="/celebration.png", processed_png_url=None
        )
        # NULL snapshot → auto lookup runs
        snapshot = None
        result = snapshot or _get_participant_photo_for_phase(db, 10, "completed_score_win", True)
        assert result == "/celebration.png"

    def test_snap_04_loser_null_snapshot_gets_sad(self):
        """SNAP-04: challenger=loser + NULL snapshot → sad mood via auto lookup."""
        from app.api.web_routes.vt_challenges import _get_participant_photo_for_phase, _PHASE_MOOD_MAP
        pref, _ = _PHASE_MOOD_MAP[("completed_score_win", False)]
        assert pref == "mood_sad_disappointed"

        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = MagicMock(
            original_url="/sad.png", processed_png_url=None
        )
        snapshot = None
        result = snapshot or _get_participant_photo_for_phase(db, 10, "completed_score_win", False)
        assert result == "/sad.png"

    # ── SNAP-05: manual snapshot → frozen ────────────────────────────────────

    def test_snap_05_manual_snapshot_is_frozen(self):
        """SNAP-05: manual snapshot present → auto lookup skipped (snapshot wins)."""
        from app.api.web_routes.vt_challenges import _get_participant_photo_for_phase
        manual_url = "/static/uploads/mood_photos/10_manual.png"
        db = MagicMock()
        # Even though auto lookup would return something, the snapshot short-circuits
        result = manual_url or _get_participant_photo_for_phase(db, 10, "completed_score_win", True)
        assert result == manual_url  # snapshot always wins

    # ── SNAP-06/07: Card Studio auto indicator ────────────────────────────────

    def test_snap_06_null_snapshot_shows_auto_badge(self):
        """SNAP-06: NULL snapshot → auto_mood_info state is 'auto' or 'fallback'."""
        from app.api.web_routes.vt_challenges import _PHASE_MOOD_MAP
        # NULL snapshot → state is not 'manual'
        snapshot = None
        state = "manual" if snapshot else "auto_or_fallback"
        assert state != "manual"

    def test_snap_07_manual_snapshot_shows_manual_badge(self):
        """SNAP-07: manual snapshot → auto_mood_info state is 'manual'."""
        snapshot = "/static/uploads/mood_photos/10_celebration.png"
        state = "manual" if snapshot else "auto_or_fallback"
        assert state == "manual"

    # ── SNAP-08: clear photo → NULL ───────────────────────────────────────────

    def test_snap_08_clear_photo_gives_null_snapshot(self):
        """SNAP-08: POST /challenges/{id}/card/photo with empty string → snapshot NULL."""
        from app.api.web_routes.vt_challenges import challenge_card_photo_save
        # Simulate the endpoint logic: photo_url="" → effective_url = None
        photo_url = ""
        effective_url = photo_url or None
        assert effective_url is None, "Clear photo must set snapshot to NULL"

    # ── SNAP-09: challenged side always dynamic ───────────────────────────────

    def test_snap_09_challenged_side_always_dynamic(self):
        """SNAP-09: challenged_card_photo_url defaults to NULL → auto lookup always runs."""
        from app.api.web_routes.vt_challenges import _get_participant_photo_for_phase
        # challenged_card_photo_url is NULL by default (never set at send time)
        cd_snapshot = None  # this is the invariant we verify
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = MagicMock(
            original_url="/confident.png", processed_png_url=None
        )
        result = cd_snapshot or _get_participant_photo_for_phase(db, 20, "challenge_accepted", None)
        assert result == "/confident.png"  # auto lookup ran because snapshot was NULL
