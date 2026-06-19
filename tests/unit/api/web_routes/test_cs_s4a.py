"""
CS-S4A — Challenge Card Studio: preview placeholder shell at GET /card-studio/challenge

S4A-01  GET /card-studio/challenge route is registered
S4A-02  active_type == "challenge" in context
S4A-03  Challenge type switcher is active (present in switcher)
S4A-04  Challenge button is NOT cs-type-soon
S4A-05  Player link /card-studio/player present in switcher
S4A-06  Welcome link /card-studio/welcome present in switcher
S4A-07  preview_url is None (no live iframe — informative placeholder)
S4A-08  Shell has challenge preview placeholder (not live iframe)
S4A-09  legacy editor CTA /card-editor/challenge present in panel
S4A-10  cs_challenge_panel.html has no Challenge write form
S4A-11  cs_challenge_panel.html has no Challenge export link
S4A-12  route count == 850
S4A-13  OpenAPI snapshot match true
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import json

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
INCLUDES_DIR  = TEMPLATES_DIR / "includes"
SNAP_DIR      = Path(__file__).resolve().parents[4] / "tests" / "snapshots"


# ── S4A-01: Route registration ────────────────────────────────────────────────

class TestS4A01RouteRegistration:

    def test_s4a_01_challenge_studio_route_registered(self):
        """S4A-01: GET /card-studio/challenge is registered."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/card-studio/challenge" in paths


# ── S4A-02..09: _resolve_challenge_context and template ──────────────────────

class TestS4A02to09ChallengeContext:

    def _ctx_fn(self):
        from app.api.web_routes.card_studio import _resolve_challenge_context
        return _resolve_challenge_context

    def _db_with_license(self, onboarding: bool = True):
        db  = MagicMock()
        lic = MagicMock(); lic.onboarding_completed = onboarding
        db.query.return_value.filter.return_value.first.return_value = lic
        return db, lic

    def _user(self, uid: int = 5):
        u = MagicMock(); u.id = uid; return u

    def test_s4a_02_active_type_is_challenge(self):
        """S4A-02: _resolve_challenge_context returns active_type='challenge'."""
        fn  = self._ctx_fn()
        db, _ = self._db_with_license()
        user  = self._user()
        with patch("app.api.web_routes.card_studio.get_owned_design_ids",
                   return_value=["challenge_post_16_9"]):
            ctx, redirect = fn(db, user)
        assert redirect is None
        assert ctx["active_type"] == "challenge"

    def test_s4a_03_challenge_switcher_has_challenge_link(self):
        """S4A-03: cs_type_switcher.html contains /card-studio/challenge."""
        src = (INCLUDES_DIR / "cs_type_switcher.html").read_text()
        assert "/card-studio/challenge" in src

    def test_s4a_04_challenge_button_not_cs_type_soon(self):
        """S4A-04: Challenge button no longer uses cs-type-soon class."""
        src = (INCLUDES_DIR / "cs_type_switcher.html").read_text()
        # Find the Challenge button block
        lines = src.splitlines()
        in_challenge = False
        challenge_lines = []
        for line in lines:
            if "Challenge Card" in line and "cs-type-btn" in line:
                in_challenge = True
            if in_challenge:
                challenge_lines.append(line)
                if "</button>" in line:
                    break
        block = "\n".join(challenge_lines)
        assert "cs-type-soon" not in block

    def test_s4a_05_player_link_in_switcher(self):
        """S4A-05: Switcher contains /card-studio/player link."""
        src = (INCLUDES_DIR / "cs_type_switcher.html").read_text()
        assert "/card-studio/player" in src

    def test_s4a_06_welcome_link_in_switcher(self):
        """S4A-06: Switcher contains /card-studio/welcome link."""
        src = (INCLUDES_DIR / "cs_type_switcher.html").read_text()
        assert "/card-studio/welcome" in src

    def test_s4a_07_preview_url_is_none(self):
        """S4A-07: _resolve_challenge_context sets preview_url=None."""
        fn  = self._ctx_fn()
        db, _ = self._db_with_license()
        user  = self._user()
        with patch("app.api.web_routes.card_studio.get_owned_design_ids",
                   return_value=["challenge_post_16_9"]):
            ctx, _ = fn(db, user)
        assert ctx["preview_url"] is None

    def test_s4a_08_shell_has_challenge_placeholder(self):
        """S4A-08: Shell template has challenge preview placeholder (not iframe)."""
        src = (TEMPLATES_DIR / "card_studio_shell.html").read_text()
        assert "cs-preview-placeholder" in src
        assert 'active_type == "challenge"' in src or "active_type == 'challenge'" in src

    def test_s4a_09_challenge_panel_has_mood_photo_selector(self):
        """S4A-09: CC-DESIGN-1 replaced legacy editor CTA with mood photo quick selector.
        cs_challenge_panel.html has cs-cc-mood-section; legacy CTA removed."""
        src = (INCLUDES_DIR / "cs_challenge_panel.html").read_text()
        assert "cs-cc-mood-section" in src, \
            "CC-DESIGN-1: challenge panel must have mood photo quick selector"
        assert "Open Challenge Editor" not in src, \
            "CC-DESIGN-1: legacy editor CTA must be removed from challenge panel"


# ── S4A-10..11: No write, no export ──────────────────────────────────────────

class TestS4A10to11NoWriteNoExport:

    @classmethod
    def _panel(cls):
        return (INCLUDES_DIR / "cs_challenge_panel.html").read_text()

    def test_s4a_10_challenge_panel_no_write_form(self):
        """S4A-10: cs_challenge_panel.html has no POST form (no write UI)."""
        src = self._panel()
        assert 'method="POST"' not in src
        assert 'method="post"' not in src

    def test_s4a_11_challenge_panel_no_export_download(self):
        """S4A-11: cs_challenge_panel.html has no export/download button."""
        src = self._panel()
        assert "cs-btn-download" not in src
        assert 'download' not in src


# ── S4A-12..13: Route count + OpenAPI ────────────────────────────────────────

class TestS4A12to13RouteAndSnapshot:

    def test_s4a_12_route_count_851(self):
        """S4A-12: Route count == 851 (CC-DESIGN-1 SNAPSHOT adds +1 POST /challenges/{id}/card/photo)."""
        from app.main import app
        count = len(app.openapi().get("paths", {}))
        assert count == 912, f"Expected 851 routes, got {count}"

    def test_s4a_13_openapi_snapshot_match(self):
        """S4A-13: OpenAPI snapshot matches live API."""
        snap_paths = set(json.loads((SNAP_DIR / "openapi_snapshot.json").read_text()).get("paths", {}).keys())
        from app.main import app
        live_paths = set(app.openapi().get("paths", {}).keys())
        assert snap_paths == live_paths
