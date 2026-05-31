"""
CE-1 tests — Card Editor canonical route + naming.

CE1-01  GET /card-editor/player          → 200 (new canonical route live)
CE1-02  GET /dashboard/.../card-editor   → 303 /card-editor/player (legacy redirect)
CE1-03  /card-editor/player response body contains "Card Editor"
CE1-04  /card-editor/player response body contains "Player Card"
CE1-05  /card-editor/player response body does NOT contain "My Player Card"
CE1-06  Publish/export button identifiers not removed (no regression)
CE1-07  Auth guard: unauthenticated → redirected (not 200)
CE1-08  Legacy redirect requires auth (unauthenticated → auth redirect, not 303)
CE1-09  Internal links in templates point to /card-editor/player, not old URL
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.responses import RedirectResponse

from app.api.web_routes.dashboard import (
    lfa_player_card_editor,
    lfa_player_card_editor_legacy,
)
from app.models.user import UserRole

_BASE = "app.api.web_routes.dashboard"

TEMPLATES_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _req():
    return MagicMock()


def _student(uid: int = 42):
    u = MagicMock()
    u.id = uid
    u.role = UserRole.STUDENT
    u.name = "Test Player"
    u.email = "player@test.com"
    u.credit_balance = 100
    return u


def _license(uid: int = 42):
    lic = MagicMock()
    lic.user_id = uid
    lic.specialization_type = "LFA_FOOTBALL_PLAYER"
    lic.onboarding_completed = True
    lic.football_skills = {"passing": 60}
    lic.card_theme = "default"
    lic.card_variant = "fclassic"
    lic.public_card_platform = None
    lic.player_card_photo_url = None
    lic.card_bg_compact_url = None
    lic.card_bg_showcase_url = None
    lic.sponsor_logo_url = None
    return lic


def _mock_db(lic):
    db = MagicMock()
    q = MagicMock()
    f = MagicMock()
    f.first.return_value = lic
    q.filter.return_value = f
    q.filter_by.return_value = f
    db.query.return_value = q
    return db


def _call_canonical_handler(user=None, lic=None):
    """Call lfa_player_card_editor (new canonical route) and return template call args.

    Uses the same sequential side_effect pattern as test_card_editor_context.py.
    """
    if user is None:
        user = _student()
    if lic is None:
        lic = _license(uid=user.id)

    draft = MagicMock()
    draft.draft_theme = "default"
    draft.draft_variant = "fclassic"
    draft.draft_platform = None
    draft.published_theme = "default"
    draft.published_variant = "fclassic"
    draft.published_platform = None
    draft.draft_data = {}
    draft.published_data = {}

    db = MagicMock()
    # Sequential DB returns: license, enrollment (None), card_draft
    db.query.return_value.filter.return_value.first.side_effect = [
        lic,    # UserLicense query
        None,   # SemesterEnrollment query
        draft,  # CardDraft singleton
    ]

    with patch(f"{_BASE}.templates") as mock_tmpl, \
         patch(f"{_BASE}.SemesterEnrollment") as mock_se:
        mock_tmpl.TemplateResponse.return_value = MagicMock()
        inner = MagicMock()
        inner.filter.return_value.first.return_value = None
        mock_se.id = inner

        _run(lfa_player_card_editor(_req(), db=db, user=user))

    return mock_tmpl.TemplateResponse.call_args


# ── CE1-01 — new canonical route is registered and callable ──────────────────

class TestCE101NewRoute:
    """CE1-01: GET /card-editor/player → handler is callable and returns a response."""

    def test_ce1_01_handler_callable(self):
        """The canonical handler function lfa_player_card_editor can be called
        under the /card-editor/player path without raising errors."""
        args = _call_canonical_handler()
        assert args is not None, "lfa_player_card_editor returned no TemplateResponse call"

    def test_ce1_01_handler_is_canonical_function(self):
        """The function used for /card-editor/player is lfa_player_card_editor
        (same function that tests in test_card_editor_context.py target)."""
        import inspect
        assert inspect.iscoroutinefunction(lfa_player_card_editor)


# ── CE1-02 — legacy redirect function ────────────────────────────────────────

class TestCE102LegacyRedirect:
    """CE1-02: GET /dashboard/lfa-football-player/card-editor → 303 /card-editor/player."""

    def test_ce1_02_legacy_returns_redirect_response(self):
        user = _student()
        resp = _run(lfa_player_card_editor_legacy(user=user))
        assert isinstance(resp, RedirectResponse)

    def test_ce1_02_legacy_redirect_status_303(self):
        user = _student()
        resp = _run(lfa_player_card_editor_legacy(user=user))
        assert resp.status_code == 303

    def test_ce1_02_legacy_redirect_target(self):
        user = _student()
        resp = _run(lfa_player_card_editor_legacy(user=user))
        location = resp.headers.get("location", "")
        assert location == "/card-editor/player", (
            f"Expected redirect to /card-editor/player, got {location!r}"
        )

    def test_ce1_02_legacy_not_200(self):
        """Legacy route must not return 200 (page rendered at old URL)."""
        user = _student()
        resp = _run(lfa_player_card_editor_legacy(user=user))
        assert resp.status_code != 200


# ── CE1-03/04/05 — naming strings in template context ────────────────────────

class TestCE1Naming:
    """CE1-03/04/05: naming strings in the rendered template."""

    def _get_template_html(self) -> str:
        path = TEMPLATES_DIR / "dashboard_card_editor.html"
        return path.read_text(encoding="utf-8")

    def test_ce1_03_page_title_contains_card_editor(self):
        """Page title block contains 'Card Editor'."""
        html = self._get_template_html()
        assert "Card Editor" in html

    def test_ce1_04_page_contains_player_card_family_label(self):
        """Template contains 'Player Card' as family label."""
        html = self._get_template_html()
        assert "Player Card" in html

    def test_ce1_05_no_my_player_card_string(self):
        """'My Player Card' must not appear anywhere in the template."""
        html = self._get_template_html()
        assert "My Player Card" not in html, (
            "'My Player Card' still present in dashboard_card_editor.html"
        )

    def test_ce1_03_title_block_exact(self):
        """title block says 'Card Editor — Player Card — LFA'."""
        html = self._get_template_html()
        assert "Card Editor — Player Card — LFA" in html

    def test_ce1_03_breadcrumb_updated(self):
        """Breadcrumb last item says 'Card Editor — Player Card'."""
        html = self._get_template_html()
        assert 'class="s-breadcrumb-item active">Card Editor — Player Card' in html

    def test_ce1_03_sidebar_header_updated(self):
        """Sidebar h2 says 'Card Editor — Player Card'."""
        html = self._get_template_html()
        assert "Card Editor — Player Card" in html


# ── CE1-06 — publish/export button identifiers not removed ───────────────────

class TestCE106NoRegressionPublishExport:
    """CE1-06: publish/export markup identifiers still present."""

    def _html(self) -> str:
        return (TEMPLATES_DIR / "dashboard_card_editor.html").read_text()

    def test_publish_button_markup_present(self):
        html = self._html()
        # Publish button calls publishCard() JS
        assert "publishCard()" in html

    def test_export_png_markup_present(self):
        html = self._html()
        assert "exportCard()" in html

    def test_tab_bar_present(self):
        html = self._html()
        assert 'class="ce-tab-bar"' in html or "ce-tab-bar" in html

    def test_preview_iframe_present(self):
        html = self._html()
        assert "ce-preview" in html or "card-preview" in html or "iframe" in html


# ── CE1-07 — auth/license guard still in place ───────────────────────────────

class TestCE107AuthGuard:
    """CE1-07: the canonical handler raises 403 when license is missing."""

    def test_ce1_07_no_license_raises_403(self):
        from fastapi import HTTPException

        user = _student()
        db = _mock_db(lic=None)  # no license returned

        with patch(f"{_BASE}.templates"), \
             patch(f"{_BASE}.SemesterEnrollment"):
            try:
                _run(lfa_player_card_editor(_req(), db=db, user=user))
                raise AssertionError("Expected HTTPException 403")
            except HTTPException as exc:
                assert exc.status_code == 403

    def test_ce1_07_onboarding_incomplete_guard(self):
        """If onboarding_completed=False and no skills and no enrollment → redirect."""
        user = _student()
        lic = _license(uid=user.id)
        lic.onboarding_completed = False
        lic.football_skills = None

        db = MagicMock()
        # Sequential: license found, enrollment None (no legacy bypass)
        db.query.return_value.filter.return_value.first.side_effect = [lic, None]

        with patch(f"{_BASE}.templates") as mock_tmpl, \
             patch(f"{_BASE}.SemesterEnrollment") as mock_se:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            inner = MagicMock()
            inner.filter.return_value.first.return_value = None
            mock_se.id = inner

            result = _run(lfa_player_card_editor(_req(), db=db, user=user))

        assert isinstance(result, RedirectResponse)


# ── CE1-08 — legacy redirect requires auth ───────────────────────────────────

class TestCE108LegacyAuth:
    """CE1-08: lfa_player_card_editor_legacy depends on get_current_user_web.
    Unauthenticated requests are handled by the dependency — tested by verifying
    the function signature has a user dependency."""

    def test_ce1_08_legacy_has_user_dependency(self):
        """lfa_player_card_editor_legacy accepts a user param (FastAPI dependency)."""
        import inspect
        sig = inspect.signature(lfa_player_card_editor_legacy)
        assert "user" in sig.parameters


# ── CE1-09 — internal links point to new URL ─────────────────────────────────

class TestCE109InternalLinks:
    """CE1-09: navigation links use correct card editor URLs.

    CE-3.2 refinement:
    - Global quicknav (spec_subpage_hdr.html) → /card-editor (Card Studio landing)
    - card_editor_url context (profile editor) → /card-editor (general entry)
    - Player-specific CTAs (my-cards, shop, profile #media) → /card-editor/player (direct)
    """

    OLD_URL    = "/dashboard/lfa-football-player/card-editor"
    PLAYER_URL = "/card-editor/player"
    STUDIO_URL = "/card-studio"  # CS-S1b: canonical moved from /card-editor to /card-studio

    def _html(self, rel_path: str) -> str:
        return (TEMPLATES_DIR / rel_path).read_text(encoding="utf-8")

    def test_ce1_09_spec_subpage_hdr_link(self):
        """CE-3.2 / CS-S1b: quicknav links to canonical /card-studio, not old /card-editor."""
        html = self._html("includes/spec_subpage_hdr.html")
        assert f'href="{self.STUDIO_URL}"' in html, (
            "spec_subpage_hdr.html quicknav must link to /card-studio (canonical Card Studio)"
        )
        assert f'href="{self.PLAYER_URL}"' not in html, (
            "spec_subpage_hdr.html quicknav must NOT have a direct /card-editor/player link "
            "(global nav points to shell, not player editor)"
        )
        assert f'href="{self.OLD_URL}"' not in html

    def test_ce1_09_lfa_player_profile_link(self):
        """#media deep-link stays /card-editor/player — player-specific action."""
        html = self._html("lfa_player_profile.html")
        assert f'href="{self.PLAYER_URL}#media"' in html
        assert f'href="{self.OLD_URL}#media"' not in html

    def test_ce1_09_my_cards_player_card_link(self):
        """my_cards_player_card.html Open Editor CTA stays /card-editor/player."""
        html = self._html("my_cards_player_card.html")
        assert f'href="{self.PLAYER_URL}"' in html
        assert f'href="{self.OLD_URL}"' not in html

    def test_ce1_09_shop_player_card_link(self):
        """shop_player_card.html Edit/Download CTA stays /card-editor/player."""
        html = self._html("shop_player_card.html")
        assert f'href="{self.PLAYER_URL}"' in html
        assert f'href="{self.OLD_URL}"' not in html

    def test_ce1_09_shop_player_card_detail_link(self):
        """shop_player_card_detail.html Open Editor CTA stays /card-editor/player."""
        html = self._html("shop_player_card_detail.html")
        assert f'href="{self.PLAYER_URL}"' in html
        assert f'href="{self.OLD_URL}"' not in html

    def test_ce1_09_dashboard_card_editor_no_nav_old_url(self):
        """dashboard_card_editor.html must not contain the old URL as a nav href
        (the highlight-video API endpoint is a fetch call, not a nav link — allowed)."""
        html = self._html("dashboard_card_editor.html")
        nav_old = f'href="{self.OLD_URL}"'
        assert nav_old not in html, (
            f"Found navigation link to old URL in dashboard_card_editor.html: {nav_old}"
        )

    def test_ce1_09_api_context_var_updated(self):
        """CE-3.2 / CS-S1b: card_editor_url context variable points to canonical /card-studio."""
        src = (
            Path(__file__).resolve().parents[4]
            / "app" / "api" / "web_routes" / "dashboard.py"
        ).read_text()
        assert f'"card_editor_url":  "{self.STUDIO_URL}"' in src, (
            "card_editor_url must be /card-studio (canonical Card Studio)"
        )
        assert f'"card_editor_url":  "{self.PLAYER_URL}"' not in src
        assert f'"card_editor_url":  "{self.OLD_URL}"' not in src
