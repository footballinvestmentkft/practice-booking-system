"""Welcome Card Editor route tests — WCE-01..WCE-10.

WCE-01  Owned user GET /card-editor/welcome/instagram_portrait → 200, correct template
WCE-02  Unowned user (no CDO) → redirect /shop/cards/welcome?error=not_owned
WCE-03  Unknown format_id → 404
WCE-04  Template contains preview iframe with correct platform param
WCE-05  Template contains Download PNG link with correct export URL
WCE-06  Template contains Back to My Cards link (/my-cards/welcome)
WCE-07  My Cards Welcome CTA is "Customize / Export →" (not "Download PNG")
WCE-08  My Cards Welcome grid has no direct "Download PNG" CTA
WCE-09  Admin bypass: admin user with no CDO → 200 (ownership guard skipped)
WCE-10  No-license user → redirect (no LFA_FOOTBALL_PLAYER license)
"""
from __future__ import annotations

import asyncio
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

_BASE    = "app.api.web_routes.card_editor"
_MYCARDS = "app.api.web_routes.my_cards"

_TEMPLATE_BASE = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates"
)


def _run(coro):
    return asyncio.run(coro)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user(balance=500, role=None):
    from app.models.user import UserRole
    u = MagicMock()
    u.id = 42
    u.credit_balance = balance
    u.role = role or UserRole.STUDENT
    return u


def _admin_user():
    from app.models.user import UserRole
    return _user(role=UserRole.ADMIN)


def _license(onboarding_completed=True):
    lic = MagicMock()
    lic.onboarding_completed = onboarding_completed
    return lic


def _db():
    return MagicMock()


def _req(path="/card-editor/welcome/instagram_portrait"):
    r = MagicMock()
    r.url.path = path
    return r


def _call_editor(format_id="instagram_portrait", user=None, owned=True, license=None):
    """Call welcome_card_editor and return captured template name + context dict."""
    from app.api.web_routes.card_editor import welcome_card_editor

    user    = user    or _user()
    license = license or _license()

    captured = {}

    def _fake_tmpl(tmpl, ctx):
        captured["template"] = tmpl
        captured["context"]  = ctx
        return MagicMock(status_code=200)

    with patch(f"{_BASE}.templates.TemplateResponse", side_effect=_fake_tmpl), \
         patch(f"{_BASE}.is_design_accessible", return_value=owned), \
         patch(
             f"{_BASE}.db.query",
             side_effect=lambda m: MagicMock(
                 filter=lambda *a, **kw: MagicMock(first=lambda: license)
             ),
         ) if False else patch(f"{_BASE}.db", create=True):

        # Patch the DB query inside the route via dependency injection
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = license

        with patch(f"{_BASE}.templates.TemplateResponse", side_effect=_fake_tmpl), \
             patch(f"{_BASE}.is_design_accessible", return_value=owned):
            result = _run(welcome_card_editor(
                format_id=format_id,
                request=_req(f"/card-editor/welcome/{format_id}"),
                db=db,
                user=user,
            ))

    return captured, result


# ── WCE-01  Owned user → 200 + correct template ───────────────────────────────

class TestWCE01OwnedSuccess:

    def test_wce01_owned_returns_correct_template(self):
        """WCE-01: owned user → template card_editor_welcome.html."""
        cap, _ = _call_editor(format_id="instagram_portrait", owned=True)
        assert cap["template"] == "card_editor_welcome.html"

    def test_wce01_context_contains_fmt(self):
        """WCE-01b: context fmt.design_id matches requested format_id."""
        cap, _ = _call_editor(format_id="instagram_portrait", owned=True)
        assert cap["context"]["fmt"].design_id == "instagram_portrait"

    def test_wce01_context_contains_preview_url(self):
        """WCE-01c: context preview_url contains the correct platform param."""
        cap, _ = _call_editor(format_id="instagram_portrait", owned=True)
        assert "platform=instagram_portrait" in cap["context"]["preview_url"]

    def test_wce01_context_contains_export_url(self):
        """WCE-01d: context export_url points to the export endpoint."""
        cap, _ = _call_editor(format_id="instagram_portrait", owned=True)
        assert "/profile/onboarding-card/export" in cap["context"]["export_url"]
        assert "platform=instagram_portrait" in cap["context"]["export_url"]

    def test_wce01_all_7_formats_return_200(self):
        """WCE-01e: all 7 valid Welcome Card format IDs resolve to 200."""
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        for fmt in WELCOME_CARD_FORMATS:
            cap, _ = _call_editor(format_id=fmt.design_id, owned=True)
            assert cap["template"] == "card_editor_welcome.html", \
                f"Format {fmt.design_id!r} did not return the expected template"


# ── WCE-02  Unowned user → redirect shop ─────────────────────────────────────

class TestWCE02UnownedRedirect:

    def test_wce02_unowned_redirects_to_shop(self):
        """WCE-02: unowned user → 303 redirect to /shop/cards/welcome?error=not_owned."""
        from fastapi.responses import RedirectResponse
        db  = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = _license()

        with patch(f"{_BASE}.is_design_accessible", return_value=False):
            from app.api.web_routes.card_editor import welcome_card_editor
            result = _run(welcome_card_editor(
                format_id="instagram_portrait",
                request=_req(),
                db=db,
                user=_user(),
            ))

        assert isinstance(result, RedirectResponse)
        assert result.status_code == 303
        assert "error=not_owned" in result.headers["location"]
        assert "/shop?type=welcome_card" in result.headers["location"]


# ── WCE-03  Unknown format_id → 404 ──────────────────────────────────────────

class TestWCE03UnknownFormat:

    def test_wce03_unknown_format_raises_404(self):
        """WCE-03: unknown format_id → HTTPException 404."""
        db  = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = _license()

        from app.api.web_routes.card_editor import welcome_card_editor
        with pytest.raises(HTTPException) as exc_info:
            _run(welcome_card_editor(
                format_id="nonexistent_format_xyz",
                request=_req("/card-editor/welcome/nonexistent_format_xyz"),
                db=db,
                user=_user(),
            ))
        assert exc_info.value.status_code == 404

    def test_wce03_empty_string_raises_404(self):
        """WCE-03b: empty-like string not in WELCOME_CARD_FORMATS → 404."""
        db  = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = _license()

        from app.api.web_routes.card_editor import welcome_card_editor
        with pytest.raises(HTTPException) as exc_info:
            _run(welcome_card_editor(
                format_id="challenge_post_16_9",  # valid challenge format, wrong family
                request=_req("/card-editor/welcome/challenge_post_16_9"),
                db=db,
                user=_user(),
            ))
        assert exc_info.value.status_code == 404


# ── WCE-04  Template preview iframe ──────────────────────────────────────────

class TestWCE04PreviewIframe:

    def _render(self, format_id="instagram_portrait"):
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(_TEMPLATE_BASE)), autoescape=True)

        from app.services.card_design_service import WELCOME_CARD_FORMATS
        _wc = {f.design_id: f for f in WELCOME_CARD_FORMATS}
        fmt = _wc[format_id]

        user = MagicMock()
        user.credit_balance = 500

        ctx = {
            "request":     MagicMock(),
            "user":        user,
            "fmt":         fmt,
            "format_id":   format_id,
            "ratio_class": "mfg-ratio-45",
            "preview_url": f"/profile/onboarding-card?platform={format_id}",
            "export_url":  f"/profile/onboarding-card/export?platform={format_id}",
        }
        return env.get_template("card_editor_welcome.html").render(**ctx)

    def test_wce04_preview_iframe_present(self):
        """WCE-04: rendered template contains an iframe element."""
        html = self._render()
        assert "<iframe" in html

    def test_wce04_preview_iframe_src_has_platform_param(self):
        """WCE-04b: iframe src contains correct platform param."""
        html = self._render(format_id="instagram_story")
        assert "platform=instagram_story" in html

    def test_wce04_preview_url_points_to_onboarding_card(self):
        """WCE-04c: preview src points to /profile/onboarding-card."""
        html = self._render()
        assert "/profile/onboarding-card" in html


# ── WCE-05  Export CTA URL ────────────────────────────────────────────────────

class TestWCE05ExportCTA:

    def _render(self, format_id="tiktok"):
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(_TEMPLATE_BASE)), autoescape=True)

        from app.services.card_design_service import WELCOME_CARD_FORMATS
        _wc = {f.design_id: f for f in WELCOME_CARD_FORMATS}
        fmt = _wc[format_id]
        user = MagicMock(); user.credit_balance = 500
        ctx = {
            "request": MagicMock(), "user": user, "fmt": fmt,
            "format_id": format_id, "ratio_class": "mfg-ratio-916",
            "preview_url": f"/profile/onboarding-card?platform={format_id}",
            "export_url":  f"/profile/onboarding-card/export?platform={format_id}",
        }
        return env.get_template("card_editor_welcome.html").render(**ctx)

    def test_wce05_download_link_present(self):
        """WCE-05: rendered template contains 'Download PNG' text."""
        html = self._render()
        assert "Download PNG" in html

    def test_wce05_export_href_correct(self):
        """WCE-05b: export href points to /profile/onboarding-card/export."""
        html = self._render(format_id="tiktok")
        assert "/profile/onboarding-card/export" in html
        assert "platform=tiktok" in html


# ── WCE-06  Back to My Cards link ─────────────────────────────────────────────

class TestWCE06BackLink:

    def _render(self):
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(_TEMPLATE_BASE)), autoescape=True)
        from app.services.card_design_service import WELCOME_CARD_FORMATS
        fmt = WELCOME_CARD_FORMATS[0]
        user = MagicMock(); user.credit_balance = 500
        ctx = {
            "request": MagicMock(), "user": user, "fmt": fmt,
            "format_id": fmt.design_id, "ratio_class": "mfg-ratio-45",
            "preview_url": f"/profile/onboarding-card?platform={fmt.design_id}",
            "export_url":  f"/profile/onboarding-card/export?platform={fmt.design_id}",
        }
        return env.get_template("card_editor_welcome.html").render(**ctx)

    def test_wce06_back_link_present(self):
        """WCE-06: template contains a link to /my-cards/welcome."""
        html = self._render()
        assert "/my-cards/welcome" in html

    def test_wce06_back_link_text(self):
        """WCE-06b: template contains 'Back to My' text."""
        html = self._render()
        assert "Back to My" in html

    def test_wce06_shop_link_present(self):
        """WCE-06c: template contains Browse Welcome Cards link."""
        html = self._render()
        assert "/shop?type=welcome_card" in html
        assert "Browse Welcome Cards" in html


# ── WCE-07  My Cards Welcome CTA text ────────────────────────────────────────

class TestWCE07MyCardsCTA:

    def _render_my_cards(self):
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(_TEMPLATE_BASE)), autoescape=True)

        user = MagicMock(); user.credit_balance = 500
        format_rows = [
            {
                "design_id":        "instagram_portrait",
                "label":            "Instagram Portrait",
                "style_tag":        "IDENTITY CARD",
                "dims":             "1080 × 1350",
                "credit_cost":      75,
                "preview_platform": "instagram_portrait",
                "state":            "owned",
                "preview_url":      "/profile/onboarding-card?platform=instagram_portrait",
                "export_url":       "/profile/onboarding-card/export?platform=instagram_portrait",
            }
        ]
        ctx = {
            "request": MagicMock(), "user": user,
            "format_rows": format_rows, "owned_count": 1, "total_count": 1,
            "flash_purchased": None, "flash_error": None,
            "spec_dashboard_url": "/dashboard/lfa-football-player",
        }
        return env.get_template("my_cards_welcome_card.html").render(**ctx)

    def test_wce07_customize_export_cta_present(self):
        """WCE-07: My Cards WC grid shows 'Customize / Export →' CTA."""
        html = self._render_my_cards()
        assert "Customize / Export" in html

    def test_wce07_cta_href_points_to_editor(self):
        """WCE-07b: CTA href targets /card-editor/welcome/{design_id}."""
        html = self._render_my_cards()
        assert "/card-editor/welcome/instagram_portrait" in html


# ── WCE-08  No direct Download PNG in My Cards grid ──────────────────────────

class TestWCE08NoDirectDownload:

    def _render_my_cards(self):
        from jinja2 import Environment, FileSystemLoader
        env = Environment(loader=FileSystemLoader(str(_TEMPLATE_BASE)), autoescape=True)

        user = MagicMock(); user.credit_balance = 500
        format_rows = [
            {
                "design_id": "instagram_story", "label": "Instagram Story",
                "style_tag": "IDENTITY CARD",   "dims": "1080 × 1920",
                "credit_cost": 75, "preview_platform": "instagram_story",
                "state": "owned",
                "preview_url": "/profile/onboarding-card?platform=instagram_story",
                "export_url":  "/profile/onboarding-card/export?platform=instagram_story",
            }
        ]
        ctx = {
            "request": MagicMock(), "user": user,
            "format_rows": format_rows, "owned_count": 1, "total_count": 1,
            "flash_purchased": None, "flash_error": None,
            "spec_dashboard_url": "/dashboard/lfa-football-player",
        }
        return env.get_template("my_cards_welcome_card.html").render(**ctx)

    def test_wce08_no_direct_download_png_cta(self):
        """WCE-08: My Cards WC grid CTA does NOT read 'Download PNG'."""
        html = self._render_my_cards()
        # Check element attribute usage — CSS definition of .mfg-btn-download is still
        # present in mc_format_grid.html's <style> block, but no element should carry it.
        assert 'mfg-btn-download"' not in html, \
            "mfg-btn-download class should not appear on any element in the My Cards WC grid"

    def test_wce08_export_url_not_direct_cta(self):
        """WCE-08b: the direct export URL is not an href on a CTA button in the grid."""
        html = self._render_my_cards()
        # The export URL should not be a direct button href — it's now inside the editor
        assert 'href="/profile/onboarding-card/export' not in html


# ── WCE-09  Admin bypass ──────────────────────────────────────────────────────

class TestWCE09AdminBypass:

    def test_wce09_admin_no_cdo_returns_200(self):
        """WCE-09: admin user with no CDO ownership → 200 (guard skipped)."""
        from app.api.web_routes.card_editor import welcome_card_editor

        db  = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = _license()

        captured = {}
        def _fake_tmpl(tmpl, ctx):
            captured["template"] = tmpl
            return MagicMock(status_code=200)

        with patch(f"{_BASE}.templates.TemplateResponse", side_effect=_fake_tmpl), \
             patch(f"{_BASE}.is_design_accessible", return_value=False):
            _run(welcome_card_editor(
                format_id="instagram_portrait",
                request=_req(),
                db=db,
                user=_admin_user(),
            ))

        assert captured.get("template") == "card_editor_welcome.html"

    def test_wce09_is_design_accessible_not_called_for_admin(self):
        """WCE-09b: is_design_accessible is NOT called when user is ADMIN."""
        from app.api.web_routes.card_editor import welcome_card_editor

        db  = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = _license()

        with patch(f"{_BASE}.templates.TemplateResponse", return_value=MagicMock()), \
             patch(f"{_BASE}.is_design_accessible") as mock_check:
            _run(welcome_card_editor(
                format_id="instagram_portrait",
                request=_req(),
                db=db,
                user=_admin_user(),
            ))

        mock_check.assert_not_called()


# ── WCE-10  No-license user → redirect ───────────────────────────────────────

class TestWCE10NoLicense:

    def test_wce10_no_license_redirects(self):
        """WCE-10: user without LFA_FOOTBALL_PLAYER license → redirect."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import welcome_card_editor

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None  # no license

        result = _run(welcome_card_editor(
            format_id="instagram_portrait",
            request=_req(),
            db=db,
            user=_user(),
        ))

        assert isinstance(result, RedirectResponse)
        assert result.status_code == 303

    def test_wce10_incomplete_onboarding_redirects(self):
        """WCE-10b: license exists but onboarding_completed=False → redirect."""
        from fastapi.responses import RedirectResponse
        from app.api.web_routes.card_editor import welcome_card_editor

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = _license(
            onboarding_completed=False
        )

        result = _run(welcome_card_editor(
            format_id="instagram_portrait",
            request=_req(),
            db=db,
            user=_user(),
        ))

        assert isinstance(result, RedirectResponse)
        assert result.status_code == 303
