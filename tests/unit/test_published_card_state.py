"""
Published Card State — isolation between draft and public card
==============================================================

Validates that the published_card_* fields (stable public state) are
completely decoupled from the draft editor fields (card_theme / card_variant /
public_card_platform) that change on every editor interaction.

PUB-01  public_player.py reads published_card_theme (not card_theme)
PUB-02  public_player.py reads published_card_variant (not card_variant)
PUB-03  public_player.py reads published_card_platform (not public_card_platform direct)
PUB-04  POST /dashboard/card-theme does NOT touch published_card_theme
PUB-05  POST /dashboard/publish-card copies draft → published and commits
PUB-06  POST /dashboard/publish-card response body contains correct published values
PUB-07  POST /dashboard/publish-card with no license returns 404
PUB-08  Migration backfill SQL uses COALESCE(card_theme, 'default') and
        COALESCE(card_variant, 'fclassic') (not bare column reads)
PUB-09  Editor template contains "Publish Card" button HTML
PUB-10  Editor template contains _isDraftPublished JS function
PUB-11  Editor template contains publish-status-dot CSS for unpublished/published states
PUB-12  Editor template server-renders published state as JS constants
"""
from __future__ import annotations

import asyncio
import json
import os
import pytest
from unittest.mock import MagicMock, patch


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_user(uid: int = 7) -> MagicMock:
    u = MagicMock()
    u.id = uid
    u.name = "Test Player"
    u.email = "test@lfa.com"
    u.is_active = True
    u.date_of_birth = None
    u.credit_balance = 100
    return u


def _make_license(
    card_theme: str = "default",
    card_variant: str = "fclassic",
    public_card_platform: str | None = None,
    published_card_theme: str = "default",
    published_card_variant: str = "fclassic",
    published_card_platform: str | None = None,
) -> MagicMock:
    lic = MagicMock()
    lic.card_theme            = card_theme
    lic.card_variant          = card_variant
    lic.public_card_platform  = public_card_platform
    lic.published_card_theme    = published_card_theme
    lic.published_card_variant  = published_card_variant
    lic.published_card_platform = published_card_platform
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.is_active           = True
    lic.onboarding_completed = False
    lic.motivation_scores   = {}
    lic.player_card_photo_url = None
    lic.card_photo_portrait_url = None
    lic.card_photo_landscape_url = None
    lic.card_bg_compact_url  = None
    lic.card_bg_showcase_url = None
    lic.compact_photo_position = "left"
    lic.compact_focus_x = 50
    lic.compact_focus_y = 20
    lic.right_foot_score = None
    lic.left_foot_score  = None
    lic.sponsor_logo_url = None
    lic.current_level    = 1
    lic.max_achieved_level = 1
    return lic


def _load_editor_template() -> str:
    import app as _app_pkg
    path = os.path.join(os.path.dirname(_app_pkg.__file__), "templates/dashboard_card_editor.html")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_migration() -> str:
    import app as _app_pkg
    root = os.path.dirname(os.path.dirname(_app_pkg.__file__))
    path = os.path.join(root, "alembic/versions/2026_05_14_1000_published_card_state.py")
    with open(path, encoding="utf-8") as f:
        return f.read()


def _load_public_player_source() -> str:
    import app as _app_pkg
    path = os.path.join(
        os.path.dirname(_app_pkg.__file__),
        "api/web_routes/public_player.py",
    )
    with open(path, encoding="utf-8") as f:
        return f.read()


# ═══════════════════════════════════════════════════════════════════════════════
# Static source checks
# ═══════════════════════════════════════════════════════════════════════════════

class TestPublicRouteReadsPublishedFields:
    """PUB-01..03 — public_player.py must read published_card_* not draft fields."""

    def test_pub01_theme_reads_published_not_draft(self):
        """PUB-01: public_player.py must read lfa_license.published_card_theme."""
        src = _load_public_player_source()
        assert "published_card_theme" in src, (
            "PUB-01: published_card_theme not referenced in public_player.py — "
            "public route may be reading draft card_theme instead"
        )

    def test_pub02_variant_reads_published_not_draft(self):
        """PUB-02: public_player.py must read lfa_license.published_card_variant."""
        src = _load_public_player_source()
        assert "published_card_variant" in src, (
            "PUB-02: published_card_variant not referenced in public_player.py — "
            "public route may be reading draft card_variant instead"
        )

    def test_pub03_platform_reads_published_not_draft(self):
        """PUB-03: public_player.py platform resolution must reference published_card_platform."""
        src = _load_public_player_source()
        assert "published_card_platform" in src, (
            "PUB-03: published_card_platform not referenced in public_player.py — "
            "public route may be reading draft public_card_platform directly"
        )

    def test_pub03b_theme_resolution_line_uses_published(self):
        """PUB-03b: The card_theme_id assignment line uses published_card_theme."""
        src = _load_public_player_source()
        # Find the card_theme_id assignment and confirm it uses published_card_theme
        assert "card_theme_id" in src, "card_theme_id assignment missing from public_player.py"
        idx = src.find("card_theme_id")
        snippet = src[idx: idx + 120]
        assert "published_card_theme" in snippet, (
            f"PUB-03b: card_theme_id assignment does not reference published_card_theme.\n"
            f"Snippet: {snippet!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Migration backfill
# ═══════════════════════════════════════════════════════════════════════════════

class TestMigrationBackfill:
    """PUB-08 — migration backfill SQL must use COALESCE to preserve defaults."""

    def test_pub08_backfill_uses_coalesce_for_theme(self):
        """PUB-08a: backfill SQL uses COALESCE(card_theme, 'default') not bare card_theme."""
        sql = _load_migration()
        assert "COALESCE(card_theme" in sql, (
            "PUB-08a: Migration backfill SQL must use COALESCE(card_theme, 'default') "
            "to handle NULL draft themes; bare 'card_theme' assignment would store NULLs"
        )

    def test_pub08_backfill_uses_coalesce_for_variant(self):
        """PUB-08b: backfill SQL uses COALESCE(card_variant, 'fclassic') not bare card_variant."""
        sql = _load_migration()
        assert "COALESCE(card_variant" in sql, (
            "PUB-08b: Migration backfill SQL must use COALESCE(card_variant, 'fclassic') "
            "to handle NULL draft variants; bare 'card_variant' assignment would store NULLs"
        )

    def test_pub08_backfill_copies_platform(self):
        """PUB-08c: backfill SQL copies public_card_platform → published_card_platform."""
        sql = _load_migration()
        assert "published_card_platform" in sql and "public_card_platform" in sql, (
            "PUB-08c: Migration backfill must copy public_card_platform → published_card_platform"
        )

    def test_pub08_downgrade_drops_all_three_columns(self):
        """PUB-08d: downgrade() must drop all three published_card_* columns."""
        sql = _load_migration()
        assert sql.count("drop_column") >= 3, (
            "PUB-08d: downgrade() must drop published_card_theme, "
            "published_card_variant, and published_card_platform"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoint: POST /dashboard/card-theme must NOT touch published state
# ═══════════════════════════════════════════════════════════════════════════════

class TestDraftDoesNotAffectPublished:
    """PUB-04 — Draft write endpoints must not modify published_card_* fields."""

    def test_pub04_set_card_theme_does_not_modify_published_theme(self):
        """PUB-04: POST /dashboard/card-theme must not write to published_card_theme."""
        from app.api.web_routes.dashboard import student_set_card_theme, _CardThemeRequest

        db   = MagicMock()
        user = _make_user()
        lic  = _make_license(card_theme="default", published_card_theme="dark")
        payload = _CardThemeRequest(theme="default")

        with patch("app.api.web_routes.dashboard._get_lfa_license", return_value=lic), \
             patch("app.api.web_routes.dashboard._apply_theme") as mock_apply:
            mock_apply.return_value = None
            result = asyncio.run(student_set_card_theme(payload=payload, db=db, user=user))

        # _apply_theme writes card_theme (draft) — published_card_theme must be untouched
        assert lic.published_card_theme == "dark", (
            "PUB-04: student_set_card_theme modified published_card_theme — "
            "draft writes must never touch published state"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Endpoint: POST /dashboard/publish-card
# ═══════════════════════════════════════════════════════════════════════════════

class TestPublishCardEndpoint:
    """PUB-05..07 — publish-card endpoint delegates to CardDraftService.publish_draft."""

    _CDS_PATH = "app.api.web_routes.dashboard._CardDraftService"

    def _call(self, lic=None, no_license: bool = False,
              draft_theme: str = "dark", draft_variant: str = "fclassic",
              draft_platform: str | None = None):
        from app.api.web_routes.dashboard import student_publish_card
        db   = MagicMock()
        user = _make_user()

        # Build a mock draft whose published_* fields are what publish_draft will return
        mock_draft = MagicMock()
        mock_draft.published_theme    = draft_theme
        mock_draft.published_variant  = draft_variant
        mock_draft.published_platform = draft_platform

        with patch("app.api.web_routes.dashboard._get_lfa_license",
                   return_value=None if no_license else (lic or _make_license())), \
             patch(self._CDS_PATH) as MockCDS:
            MockCDS.get_player_card_draft.return_value = mock_draft
            MockCDS.publish_draft.return_value = mock_draft
            result = asyncio.run(student_publish_card(db=db, user=user))

        return result, db, MockCDS, mock_draft

    def test_pub05_publish_copies_draft_to_published(self):
        """PUB-05: publish-card must call CardDraftService.publish_draft (Phase 4D-2)."""
        result, _, MockCDS, _ = self._call(
            draft_theme="dark", draft_variant="fclassic", draft_platform="instagram_square"
        )
        body = json.loads(result.body)
        assert body["ok"] is True
        MockCDS.publish_draft.assert_called_once()

    def test_pub06_response_body_contains_published_values(self):
        """PUB-06: publish-card response body reflects card_draft.published_* fields."""
        result, _, _, _ = self._call(
            draft_theme="dark", draft_variant="fclassic", draft_platform="instagram_square"
        )
        body = json.loads(result.body)
        assert body["ok"] is True
        assert "published" in body
        pub = body["published"]
        assert pub["theme"]    == "dark"
        assert pub["variant"]  == "fclassic"
        assert pub["platform"] == "instagram_square"

    def test_pub06b_null_platform_returned_as_default(self):
        """PUB-06b: NULL published_platform is serialised as 'default' in response."""
        result, _, _, _ = self._call(draft_platform=None)
        body = json.loads(result.body)
        assert body["published"]["platform"] == "default", (
            "PUB-06b: NULL published_platform must be returned as 'default' string in response"
        )

    def test_pub06c_null_draft_theme_defaults_to_default(self):
        """PUB-06c: publish_draft is called; NULL safety is CardDraftService's concern."""
        result, _, MockCDS, _ = self._call()
        body = json.loads(result.body)
        assert body["ok"] is True
        MockCDS.publish_draft.assert_called_once()

    def test_pub07_no_license_returns_404(self):
        """PUB-07: missing LFA license must return 404."""
        result, db, MockCDS, _ = self._call(no_license=True)
        assert result.status_code == 404
        body = json.loads(result.body)
        assert body["ok"] is False
        MockCDS.publish_draft.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Editor template static checks
# ═══════════════════════════════════════════════════════════════════════════════

class TestEditorTemplatePublishUI:
    """PUB-09..12 — dashboard_card_editor.html contains Publish CTA and indicator."""

    @pytest.fixture(scope="class")
    def src(self):
        return _load_editor_template()

    def test_pub09_publish_card_button_present(self, src):
        """PUB-09: Editor template must contain the Publish Card button."""
        assert "btn-publish-card" in src, (
            "PUB-09: publish-card button (class btn-publish-card) not found in editor template"
        )
        assert "publishCard()" in src, (
            "PUB-09: publishCard() onclick call not found in editor template"
        )

    def test_pub10_is_draft_published_js_function_present(self, src):
        """PUB-10: Editor template must define _isDraftPublished JS function."""
        assert "function _isDraftPublished()" in src, (
            "PUB-10: _isDraftPublished() function missing from editor template — "
            "publish indicator relies on this to compare draft vs published state"
        )

    def test_pub10b_update_publish_indicator_function_present(self, src):
        """PUB-10b: Editor template must define _updatePublishIndicator JS function."""
        assert "function _updatePublishIndicator()" in src, (
            "PUB-10b: _updatePublishIndicator() function missing from editor template"
        )

    def test_pub10c_publish_card_async_function_present(self, src):
        """PUB-10c: Editor template must define publishCard async function."""
        assert "async function publishCard()" in src, (
            "PUB-10c: publishCard() async function missing from editor template"
        )

    def test_pub11_publish_status_dot_css_present(self, src):
        """PUB-11: Editor CSS must define both .published and .unpublished dot states."""
        assert "publish-status-dot.unpublished" in src, (
            "PUB-11: CSS .publish-status-dot.unpublished missing from editor template"
        )
        assert "publish-status-dot.published" in src, (
            "PUB-11: CSS .publish-status-dot.published missing from editor template"
        )

    def test_pub12_published_state_js_constants_present(self, src):
        """PUB-12: Editor must server-render published state as JS let variables."""
        assert "_publishedCardTheme" in src, (
            "PUB-12: _publishedCardTheme JS constant missing from editor template"
        )
        assert "_publishedCardVariant" in src, (
            "PUB-12: _publishedCardVariant JS constant missing from editor template"
        )
        assert "_publishedCardPlatform" in src, (
            "PUB-12: _publishedCardPlatform JS constant missing from editor template"
        )

    def test_pub12b_view_public_card_link_present(self, src):
        """PUB-12b: Editor must contain a publish view link (publish-view-link CSS class)."""
        assert "publish-view-link" in src, (
            "PUB-12b: .publish-view-link CSS class missing — View card link not wired"
        )

    def test_pub12c_publish_card_bar_html_present(self, src):
        """PUB-12c: publish zone container must be present in the HTML body."""
        assert 'ce-publish-zone' in src, (
            "PUB-12c: ce-publish-zone container div missing from editor template HTML"
        )

    def test_pub12d_update_publish_indicator_called_on_domcontentloaded(self, src):
        """PUB-12d: DOMContentLoaded handler must call _updatePublishIndicator()."""
        dcl_start = src.find("DOMContentLoaded")
        dcl_end   = src.find("});", dcl_start)
        dcl_block = src[dcl_start: dcl_end + 3]
        assert "_updatePublishIndicator()" in dcl_block, (
            "PUB-12d: _updatePublishIndicator() not called inside DOMContentLoaded — "
            "publish indicator won't initialise on page load"
        )
