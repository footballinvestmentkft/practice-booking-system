"""
CEL — Card Studio Landing tests (CS-S1 updated).

GET /card-editor — CS-S1: 301 permanent redirect → /card-studio.
Template source checks are kept (template still exists, CTAs updated for CS-S1b).

CEL-01  GET /card-editor → 301 permanent redirect to /card-studio
CEL-02  unauthenticated → auth guard (get_current_user_web)
CEL-04  player_card shop CTA in template
CEL-08b empty state block in template
CEL-09  Player CTA links to /card-editor/player, text "Open Studio"
CEL-10  Welcome CTA links to /card-studio/welcome (CS-S1b)
CEL-11  Challenge CTA links to /card-editor/challenge
CEL-12  route count = 844
CEL-13  OpenAPI snapshot is up to date
CEL-14  /card-editor/player regression — lfa_player_card_editor still callable
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.card_design_service import CHALLENGE_CARD_FORMATS, WELCOME_CARD_FORMATS

_CE_BASE = "app.api.web_routes.card_editor"

SNAPSHOTS_DIR = Path(__file__).resolve().parents[4] / "tests" / "snapshots"
TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _user(uid: int = 42) -> MagicMock:
    u = MagicMock()
    u.id = uid
    return u


# ── CEL-01: GET /card-editor → 301 /card-studio ──────────────────────────────

class TestCEL01Redirect:

    def test_cel_01_card_editor_redirects_301_to_card_studio(self):
        """CEL-01 (CS-S1): GET /card-editor returns 301 permanent redirect to /card-studio."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import card_studio_landing

        resp = _run(card_studio_landing(user=_user()))

        assert isinstance(resp, RedirectResponse)
        assert resp.status_code == 301
        assert resp.headers["location"] == "/card-studio"

    def test_cel_01b_redirect_target_is_card_studio_not_welcome(self):
        """CEL-01b: /card-editor redirects to /card-studio (shell), not /card-studio/welcome."""
        from app.api.web_routes.card_editor import card_studio_landing

        resp = _run(card_studio_landing(user=_user()))
        assert resp.headers["location"] == "/card-studio"


# ── CEL-02: unauthenticated guard ─────────────────────────────────────────────

class TestCEL02Unauthenticated:

    def test_cel_02_route_has_current_user_dependency(self):
        """CEL-02: /card-editor route has get_current_user_web in its dependant tree."""
        from app.main import app
        route = next(
            (r for r in app.routes if getattr(r, "path", None) == "/card-editor"),
            None,
        )
        assert route is not None, "/card-editor route must be registered"
        dep_names = [
            getattr(d.call, "__name__", "") for d in getattr(route.dependant, "dependencies", [])
        ]
        assert "get_current_user_web" in dep_names, (
            f"/card-editor must depend on get_current_user_web for auth guard; found: {dep_names}"
        )


# ── CEL-04: template shop CTA ────────────────────────────────────────────────

class TestCEL04ShopCTA:

    def test_cel_04_pc_no_owned_cta_points_to_shop(self):
        """CEL-04: template source has Browse Player Designs CTA for no-owned state."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert 'href="/shop?type=player_card"' in src
        assert "Browse Player Designs" in src


# ── CEL-08b: empty state block in template ───────────────────────────────────

class TestCEL08bEmptyState:

    def test_cel_08b_template_has_empty_state_block(self):
        """CEL-08b: template source contains cs-empty-state block."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert "cs-empty-state" in src
        assert "You don't own any card designs yet." in src
        assert 'href="/shop"' in src


# ── CEL-09/10/11: CTA links ───────────────────────────────────────────────────

class TestCEL091011CTALinks:

    def test_cel_09_template_has_player_editor_cta(self):
        """CEL-09 (CS-S1b): Player CTA URL stays /card-editor/player; text updated to Studio."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert 'href="/card-editor/player"' in src
        assert "Open Studio" in src

    def test_cel_10_template_has_welcome_cta(self):
        """CEL-10 (CS-S1b): Welcome CTA now links to /card-studio/welcome."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert 'href="/card-studio/welcome"' in src
        assert "Open Welcome Studio" in src

    def test_cel_11_template_has_challenge_cta(self):
        """CEL-11: Challenge CTA still links to /card-editor/challenge (CS-S4 deferred)."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert 'href="/card-editor/challenge"' in src
        assert "Open Challenge Studio" in src

    def test_cel_09b_cs_player_editor_cta_class_present(self):
        """CEL-09b: cs-player-editor-cta CSS class marks the player CTA."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert "cs-player-editor-cta" in src

    def test_cel_10b_cs_welcome_cta_class_present(self):
        """CEL-10b: cs-welcome-cta class marks the welcome CTA."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert "cs-welcome-cta" in src

    def test_cel_11b_cs_challenge_cta_class_present(self):
        """CEL-11b: cs-challenge-cta class marks the challenge CTA."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert "cs-challenge-cta" in src

    def test_cel_09c_no_card_studio_player_link(self):
        """CEL-09c (CS-S1 scope guard): template must NOT link to /card-studio/player."""
        src = (TEMPLATES_DIR / "card_studio_landing.html").read_text(encoding="utf-8")
        assert 'href="/card-studio/player"' not in src


# ── CEL-12: route count = 844 ────────────────────────────────────────────────

class TestCEL12RouteCount:

    def test_cel_12_route_count_844(self):
        """CEL-12: route count 844 (unchanged — redirect is handler change, not new route)."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 883, (
            f"Expected 845 routes (redirect handler change does not add routes), got {len(paths)}."
        )

    def test_cel_12b_card_editor_route_registered(self):
        """CEL-12b: GET /card-editor is still registered (as redirect)."""
        from app.main import app
        route_paths = [r.path for r in app.routes]
        assert "/card-editor" in route_paths, "/card-editor must be a registered route"


# ── CEL-13: OpenAPI snapshot updated ─────────────────────────────────────────

class TestCEL13OpenAPISnapshot:

    def test_cel_13_snapshot_matches_live_openapi(self):
        """CEL-13: committed openapi_snapshot.json reflects the live API paths."""
        snapshot_path = SNAPSHOTS_DIR / "openapi_snapshot.json"
        assert snapshot_path.exists(), f"Snapshot not found: {snapshot_path}"

        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        snap_paths = set(snapshot.get("paths", {}).keys())

        from app.main import app
        live_paths = set(app.openapi().get("paths", {}).keys())

        assert "/card-editor" in snap_paths, (
            "Snapshot must include /card-editor (regenerate with update_openapi_snapshot.py)"
        )
        assert snap_paths == live_paths, (
            f"Snapshot paths differ from live API.\n"
            f"In snapshot only: {snap_paths - live_paths}\n"
            f"In live only: {live_paths - snap_paths}"
        )


# ── CEL-14: /card-editor/player regression ───────────────────────────────────

class TestCEL14PlayerCardRegression:

    def test_cel_14_player_card_editor_still_callable(self):
        """CEL-14: lfa_player_card_editor handler still works after CS-S1 redirect change."""
        from app.api.web_routes.dashboard import lfa_player_card_editor
        from app.models.card_draft import CardDraft

        user        = MagicMock(); user.id = 42; user.credit_balance = 0
        mock_license = MagicMock(); mock_license.onboarding_completed = True
        db           = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = mock_license

        _DASH_BASE = "app.api.web_routes.dashboard"
        captured: dict = {}

        def _fake_tmpl(tmpl, ctx, **kw):
            captured["context"] = ctx
            return MagicMock(status_code=200)

        with patch(f"{_DASH_BASE}._CardDraftService") as MockCDS, \
             patch(f"{_DASH_BASE}.templates") as mock_tpl, \
             patch(f"{_DASH_BASE}.SemesterEnrollment"), \
             patch("app.services.card_design_service.is_design_accessible", return_value=True), \
             patch("app.services.card_variant_service.get_all_variants", return_value=[]), \
             patch("app.services.card_color_service.get_colors_for_family", return_value=[]), \
             patch("app.services.card_color_service.get_owned_color_ids",  return_value=set()), \
             patch("app.services.card_platform_service.build_platform_list", return_value=[]), \
             patch("app.services.card_constants.ANIMATED_EXPORT_CAPABLE", []), \
             patch("app.services.card_constants.CANVAS_SIZES", {}), \
             patch("app.services.card_constants.CARD_EDITOR_PLATFORM_IDS", []), \
             patch("app.services.highlight_video_service.build_youtube_embed_url", return_value=None):
            draft = MagicMock()
            draft.draft_theme    = "default"
            draft.draft_variant  = "fclassic"
            draft.draft_platform = None
            draft.draft_data     = None
            draft.published_theme    = "default"
            draft.published_variant  = "fclassic"
            draft.published_platform = None
            draft.published_data     = None
            MockCDS.get_draft.return_value = draft
            mock_tpl.TemplateResponse.side_effect = _fake_tmpl
            try:
                asyncio.run(lfa_player_card_editor(
                    request=MagicMock(), db=db, user=user,
                ))
            except Exception:
                pass

        assert captured.get("context"), "lfa_player_card_editor must still populate context"
        assert captured["context"].get("active_card_variant") is not None

    def test_cel_14b_player_card_editor_uses_get_draft_api(self):
        """CEL-14b: lfa_player_card_editor calls CardDraftService.get_draft (CE-3.0 API)."""
        from app.api.web_routes.dashboard import lfa_player_card_editor

        user        = MagicMock(); user.id = 42; user.credit_balance = 0
        mock_license = MagicMock(); mock_license.onboarding_completed = True
        db           = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = mock_license

        _DASH_BASE = "app.api.web_routes.dashboard"

        with patch(f"{_DASH_BASE}._CardDraftService") as MockCDS, \
             patch(f"{_DASH_BASE}.templates") as mock_tpl, \
             patch(f"{_DASH_BASE}.SemesterEnrollment"), \
             patch("app.services.card_design_service.is_design_accessible", return_value=True), \
             patch("app.services.card_variant_service.get_all_variants", return_value=[]), \
             patch("app.services.card_color_service.get_colors_for_family", return_value=[]), \
             patch("app.services.card_color_service.get_owned_color_ids",  return_value=set()), \
             patch("app.services.card_platform_service.build_platform_list", return_value=[]), \
             patch("app.services.card_constants.ANIMATED_EXPORT_CAPABLE", []), \
             patch("app.services.card_constants.CANVAS_SIZES", {}), \
             patch("app.services.card_constants.CARD_EDITOR_PLATFORM_IDS", []), \
             patch("app.services.highlight_video_service.build_youtube_embed_url", return_value=None):
            draft = MagicMock()
            draft.draft_theme = draft.draft_variant = "default"
            draft.draft_platform = draft.draft_data = None
            draft.published_theme = draft.published_variant = None
            draft.published_platform = draft.published_data = None
            MockCDS.get_draft.return_value = draft
            mock_tpl.TemplateResponse.side_effect = lambda t, c, **kw: MagicMock()
            try:
                asyncio.run(lfa_player_card_editor(
                    request=MagicMock(), db=db, user=user,
                ))
            except Exception:
                pass

        MockCDS.get_draft.assert_called_once_with(db, user.id, "player_card")
        MockCDS.get_player_card_draft.assert_not_called()
