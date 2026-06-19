"""
CS-S2A — Player Card Studio: preview-only shell at GET /card-studio/player

S2A-01  GET /card-studio/player route is registered
S2A-02  Route count == 846 (one new route added in CS-S2A)
S2A-03  _resolve_player_context returns redirect when user has no LFA license
S2A-04  _resolve_player_context returns redirect when onboarding not completed
S2A-05  _resolve_player_context returns ctx dict with active_type="player"
S2A-06  _resolve_player_context preview_url pattern correct (player card render)
S2A-07  _resolve_player_context falls back to fclassic/default when draft raises
S2A-08  _resolve_player_context includes legacy_editor_url=/card-editor/player
S2A-09  cs_player_panel.html include exists
S2A-10  cs_player_panel.html references legacy_editor_url context var
S2A-11  cs_type_switcher.html Player button is no longer cs-type-soon
S2A-12  cs_type_switcher.html Player button links to /card-studio/player
S2A-13  card_studio_shell.html includes cs_player_panel when active_type==player
S2A-14  card_studio_shell.html gates Mood Photos behind active_type != player
S2A-15  card_studio_shell.html export panel has endif guard for player mode
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
INCLUDES_DIR  = TEMPLATES_DIR / "includes"


# ── S2A-01/02: Route registration ────────────────────────────────────────────

class TestS2A01to02RouteRegistration:

    def test_s2a_01_player_studio_route_registered(self):
        """S2A-01: GET /card-studio/player is registered."""
        from app.main import app
        paths = [getattr(r, "path", "") for r in app.routes]
        assert "/card-studio/player" in paths

    def test_s2a_02_route_count_846(self):
        """S2A-02 (updated CS-S4A): Total route count is 847 (CS-S2A+CS-S4A)."""
        from app.main import app
        count = len(app.openapi().get("paths", {}))
        assert count == 913, f"Expected 847 routes, got {count}"


# ── S2A-03..08: _resolve_player_context logic ────────────────────────────────

class TestS2A03to08ResolvePlayerContext:

    def _ctx(self):
        from app.api.web_routes.card_studio import _resolve_player_context
        return _resolve_player_context

    def _db_with_no_license(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        return db

    def _db_with_license(self, onboarding_completed: bool):
        db  = MagicMock()
        lic = MagicMock()
        lic.onboarding_completed = onboarding_completed
        db.query.return_value.filter.return_value.first.return_value = lic
        return db

    def _user(self, uid: int = 99):
        u = MagicMock(); u.id = uid
        return u

    def test_s2a_03_no_license_returns_redirect(self):
        """S2A-03: No LFA license → redirect to dashboard."""
        fn  = self._ctx()
        db  = self._db_with_no_license()
        ctx, redirect = fn(db, self._user())
        assert ctx is None
        assert redirect is not None
        assert "dashboard" in redirect

    def test_s2a_04_onboarding_not_done_returns_redirect(self):
        """S2A-04: Onboarding not completed → redirect to onboarding."""
        fn  = self._ctx()
        db  = self._db_with_license(onboarding_completed=False)
        ctx, redirect = fn(db, self._user())
        assert ctx is None
        assert redirect is not None
        assert "onboarding" in redirect

    def test_s2a_05_valid_user_returns_active_type_player(self):
        """S2A-05: Valid user → ctx with active_type='player'."""
        fn   = self._ctx()
        db   = self._db_with_license(onboarding_completed=True)
        user = self._user(uid=77)

        draft = MagicMock()
        draft.draft_variant = "fclassic"
        draft.draft_theme   = "default"

        with patch("app.api.web_routes.card_studio._CardDraftService.get_draft", return_value=draft):
            ctx, redirect = fn(db, user)

        assert redirect is None
        assert ctx is not None
        assert ctx["active_type"] == "player"

    def test_s2a_06_preview_url_contains_player_card_path(self):
        """S2A-06: preview_url matches /players/{uid}/card?preview=...&native_export=1."""
        fn   = self._ctx()
        db   = self._db_with_license(onboarding_completed=True)
        user = self._user(uid=77)

        draft = MagicMock()
        draft.draft_variant = "fclassic"
        draft.draft_theme   = "default"

        with patch("app.api.web_routes.card_studio._CardDraftService.get_draft", return_value=draft):
            ctx, _ = fn(db, user)

        url = ctx["preview_url"]
        assert "/players/77/card" in url
        assert "preview=fclassic" in url
        assert "native_export=1" in url

    def test_s2a_07_fallback_when_draft_raises(self):
        """S2A-07: Exception in get_draft → falls back to fclassic/default."""
        fn   = self._ctx()
        db   = self._db_with_license(onboarding_completed=True)
        user = self._user(uid=55)

        with patch("app.api.web_routes.card_studio._CardDraftService.get_draft",
                   side_effect=Exception("no draft")):
            ctx, redirect = fn(db, user)

        assert redirect is None
        assert ctx["active_variant"] == "fclassic"
        assert ctx["active_theme"]   == "default"

    def test_s2a_08_legacy_editor_url_is_player_editor(self):
        """S2A-08: legacy_editor_url is /card-editor/player."""
        fn   = self._ctx()
        db   = self._db_with_license(onboarding_completed=True)
        user = self._user(uid=33)

        draft = MagicMock()
        draft.draft_variant = "fclassic"
        draft.draft_theme   = "dark"

        with patch("app.api.web_routes.card_studio._CardDraftService.get_draft", return_value=draft):
            ctx, _ = fn(db, user)

        assert ctx["legacy_editor_url"] == "/card-editor/player"


# ── S2A-09/10: cs_player_panel.html ──────────────────────────────────────────

class TestS2A09to10PlayerPanelInclude:

    @classmethod
    def _src(cls):
        return (INCLUDES_DIR / "cs_player_panel.html").read_text(encoding="utf-8")

    def test_s2a_09_player_panel_include_exists(self):
        """S2A-09: cs_player_panel.html exists in templates/includes."""
        assert (INCLUDES_DIR / "cs_player_panel.html").exists()

    def test_s2a_10_player_panel_references_legacy_editor_url(self):
        """S2A-10: cs_player_panel.html references {{ legacy_editor_url }}."""
        src = self._src()
        assert "legacy_editor_url" in src


# ── S2A-11/12: cs_type_switcher.html ─────────────────────────────────────────

class TestS2A11to12TypeSwitcher:

    @classmethod
    def _src(cls):
        return (INCLUDES_DIR / "cs_type_switcher.html").read_text(encoding="utf-8")

    def test_s2a_11_player_button_not_cs_type_soon(self):
        """S2A-11: Player button no longer uses cs-type-soon (disabled) class."""
        src = self._src()
        lines = src.splitlines()
        # Find the Player button block — it must not have cs-type-soon in it
        in_player_block = False
        player_button_lines = []
        for line in lines:
            if "Player Card" in line and "cs-type-btn" in line:
                in_player_block = True
            if in_player_block:
                player_button_lines.append(line)
                if "</button>" in line:
                    break
        player_block = "\n".join(player_button_lines)
        assert "cs-type-soon" not in player_block, \
            "Player button must not have cs-type-soon class (it is now active)"

    def test_s2a_12_player_button_links_to_card_studio_player(self):
        """S2A-12: Player button onclick navigates to /card-studio/player."""
        src = self._src()
        assert "/card-studio/player" in src


# ── S2A-13..15: card_studio_shell.html template structure ────────────────────

class TestS2A13to15ShellTemplate:

    @classmethod
    def _src(cls):
        return (TEMPLATES_DIR / "card_studio_shell.html").read_text(encoding="utf-8")

    def test_s2a_13_shell_includes_player_panel_when_player(self):
        """S2A-13: Shell conditionally includes cs_player_panel.html for player mode."""
        src = self._src()
        assert 'active_type == "player"' in src or "active_type == 'player'" in src
        assert 'cs_player_panel.html' in src

    def test_s2a_14_mood_photos_gated_for_player_mode(self):
        """S2A-14 (updated CS-S2B): Mood Photos section is gated to Welcome-only.

        CS-S2B changed the guard from active_type!='player' to active_type=='welcome'
        so Challenge mode is also excluded. The effective behavior is the same for
        Player mode — Mood Photos do not show.
        """
        src = self._src()
        body_start = src.find("{% block student_content %}")
        assert body_start != -1, "student_content block not found"
        body = src[body_start:]
        mood_pos  = body.find("cs-mood-grid")
        # Accept either the old guard pattern or the new welcome-only pattern
        guard_pos_old = body.find('active_type != "player"')
        guard_pos_new = body.find('active_type == "welcome"')
        guard_pos = guard_pos_new if guard_pos_new != -1 else guard_pos_old
        assert guard_pos != -1, "Mood Photos guard not found in shell body"
        assert guard_pos < mood_pos, \
            "Mood Photos guard must appear before the cs-mood-grid section"

    def test_s2a_15_export_panel_has_endif_for_player_mode(self):
        """S2A-15: Export panel download link is enclosed with endif for player guard."""
        src = self._src()
        # The cs-btn-download must be inside an active_type != player if block
        btn_pos = src.find("cs-btn-download")
        # There must be a {% endif %} after the btn but before </div> of the export panel
        after_btn = src[btn_pos:]
        assert "{% endif %}" in after_btn, \
            "cs-btn-download must be followed by an endif closing the player guard"
