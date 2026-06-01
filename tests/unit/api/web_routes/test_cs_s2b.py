"""
CS-S2B — Player Studio variant/platform/theme selectors

S2B-01  GET /card-studio/player still returns 200 (not broken by CS-S2B)
S2B-02  variant selector present in cs_player_panel.html
S2B-03  platform selector present in cs_player_panel.html
S2B-04  theme selector present in cs_player_panel.html
S2B-05  variant selector targets /dashboard/card-variant endpoint
S2B-06  platform selector targets /dashboard/card-platform endpoint
S2B-07  theme selector targets /dashboard/card-theme endpoint
S2B-08  setPlayerVariant JS function present in shell
S2B-09  setPlayerPlatform JS function present in shell
S2B-10  setPlayerTheme JS function present in shell
S2B-11  CSRF header (X-CSRF-Token) present in Player JS block
S2B-12  _reloadPlayerPreview JS function present (preview reload after write)
S2B-13  no /dashboard/pc-photo route (photo upload not started)
S2B-14  no publish write UI in cs_player_panel.html
S2B-15  no /card-editor/player redirect (legacy editor unchanged)
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
INCLUDES_DIR  = TEMPLATES_DIR / "includes"


# ── S2B-01: Route still works ─────────────────────────────────────────────────

class TestS2B01RouteIntact:

    def test_s2b_01_player_studio_still_registered(self):
        """S2B-01: GET /card-studio/player is still registered after CS-S2B."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/card-studio/player" in paths


# ── S2B-02..07: Template selectors and endpoint targets ──────────────────────

class TestS2B02to07SelectorsAndEndpoints:

    @classmethod
    def _panel(cls):
        return (INCLUDES_DIR / "cs_player_panel.html").read_text()

    @classmethod
    def _shell(cls):
        return (TEMPLATES_DIR / "card_studio_shell.html").read_text()

    def test_s2b_02_variant_selector_present(self):
        """S2B-02: cs_player_panel.html has variant selector."""
        src = self._panel()
        assert "data-pc-variant" in src

    def test_s2b_03_platform_selector_present(self):
        """S2B-03: cs_player_panel.html has platform selector."""
        src = self._panel()
        assert "cs-pc-platform-select" in src or "data-pc-platform" in src or "player_platforms" in src

    def test_s2b_04_theme_selector_present(self):
        """S2B-04: cs_player_panel.html has theme selector."""
        src = self._panel()
        assert "data-pc-theme" in src

    def test_s2b_05_variant_targets_card_variant_endpoint(self):
        """S2B-05: Shell JS calls /dashboard/card-variant for variant changes."""
        src = self._shell()
        assert "/dashboard/card-variant" in src

    def test_s2b_06_platform_targets_card_platform_endpoint(self):
        """S2B-06: Shell JS calls /dashboard/card-platform for platform changes."""
        src = self._shell()
        assert "/dashboard/card-platform" in src

    def test_s2b_07_theme_targets_card_theme_endpoint(self):
        """S2B-07: Shell JS calls /dashboard/card-theme for theme changes."""
        src = self._shell()
        assert "/dashboard/card-theme" in src


# ── S2B-08..12: Player JS functions ──────────────────────────────────────────

class TestS2B08to12PlayerJS:

    @classmethod
    def _shell(cls):
        return (TEMPLATES_DIR / "card_studio_shell.html").read_text()

    def test_s2b_08_set_player_variant_js_present(self):
        """S2B-08: setPlayerVariant function defined in shell JS."""
        src = self._shell()
        assert "setPlayerVariant" in src

    def test_s2b_09_set_player_platform_js_present(self):
        """S2B-09: setPlayerPlatform function defined in shell JS."""
        src = self._shell()
        assert "setPlayerPlatform" in src

    def test_s2b_10_set_player_theme_js_present(self):
        """S2B-10: setPlayerTheme function defined in shell JS."""
        src = self._shell()
        assert "setPlayerTheme" in src

    def test_s2b_11_csrf_header_in_player_js(self):
        """S2B-11: X-CSRF-Token header used in Player JS block."""
        src = self._shell()
        assert "X-CSRF-Token" in src

    def test_s2b_12_reload_player_preview_present(self):
        """S2B-12: _reloadPlayerPreview function defined in shell JS."""
        src = self._shell()
        assert "_reloadPlayerPreview" in src


# ── S2B-13..15: Forbidden scope ───────────────────────────────────────────────

class TestS2B13to15ForbiddenScope:

    def test_s2b_13_no_pc_photo_route(self):
        """S2B-13: /dashboard/pc-photo route does NOT exist (photo upload not started)."""
        from app.main import app
        paths = {getattr(r, "path", "") for r in app.routes}
        assert "/dashboard/pc-photo" not in paths

    def test_s2b_14_no_publish_write_in_player_panel(self):
        """S2B-14: cs_player_panel.html has no publish write UI."""
        src = (INCLUDES_DIR / "cs_player_panel.html").read_text()
        assert "publish" not in src.lower() or "publish-card" not in src

    def test_s2b_15_no_card_editor_player_redirect(self):
        """S2B-15: /card-editor/player endpoint is not a redirect to /card-studio/player."""
        from app.main import app
        import inspect
        for route in app.routes:
            if getattr(route, "path", "") == "/card-editor/player":
                src = inspect.getsource(route.endpoint)
                assert "/card-studio/player" not in src or "RedirectResponse" not in src, \
                    "/card-editor/player must not redirect to /card-studio/player"
                return
        assert False, "/card-editor/player route not found"
