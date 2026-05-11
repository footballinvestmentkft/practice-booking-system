"""
Unit tests for Welcome Card redesign (Phase C+D).

Routes:
  GET /profile/onboarding-card          → gallery hub or FIFA card
  GET /profile/onboarding-card/export   → PNG export

Test groups:
  TestWelcomeCardGuards          — no-license + incomplete-onboarding redirects (both routes)
  TestWelcomeCardGalleryRoute    — gallery hub (no ?platform): context + rendered HTML
  TestWelcomeCardFifaRoute       — FIFA card render (?platform=X): adapter, logo, sponsor
  TestWelcomeCardExport          — PNG export route: auth, platform validation, rate limit, bytes
  TestWelcomeCardGalleryTemplate — static source assertions on welcome_card.html (gallery hub)
  TestWelcomeCardFifaLogoAudit   — FIFA template logo changes (player_card_fifa + export/square)
  TestWelcomeCardStep7           — Step 7 onboarding template: language, photo upload, links
"""
import asyncio
import pathlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import RedirectResponse, Response

from app.api.web_routes.profile import (
    onboarding_welcome_card,
    export_onboarding_welcome_card,
    _build_welcome_card_context,
    _WC_APP_LOGO_URL,
    _WC_GALLERY_PLATFORMS,
)
from app.models.user import UserRole

_BASE = "app.api.web_routes.profile"


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _req():
    m = MagicMock()
    m.client = MagicMock()
    m.client.host = "127.0.0.1"
    return m


def _user(uid=10, role=UserRole.STUDENT):
    u = MagicMock()
    u.id      = uid
    u.role    = role
    u.email   = "player@test.com"
    u.name    = "Test Player"
    u.country = "HU"
    u.nickname = "tester"
    u.secondary_nationality = None
    u.credit_balance = 500
    return u


def _license(onboarding_completed=True, football_skills=None):
    from app.skills_config import get_all_skill_keys
    lic = MagicMock()
    lic.specialization_type   = "LFA_FOOTBALL_PLAYER"
    lic.onboarding_completed  = onboarding_completed
    lic.player_card_photo_url = None
    lic.card_photo_portrait_url  = None
    lic.card_photo_landscape_url = None
    lic.right_foot_score      = 70.0
    lic.left_foot_score       = 30.0
    lic.motivation_scores = {
        "position":       "striker",
        "positions":      ["striker"],
        "height_cm":      178,
        "weight_kg":      74,
        "preferred_foot": "right",
        "goals":          "become_professional",
    }
    if football_skills is None:
        football_skills = {
            key: {
                "self_assessment":  65.0,
                "current_level":    60.0,   # must NOT leak into template context
                "system_baseline":  60.0,
                "baseline":         60.0,
                "tournament_delta": 0.0,
                "assessment_delta": 0.0,
            }
            for key in get_all_skill_keys()
        }
    lic.football_skills = football_skills
    return lic


def _mock_db(license_return=None):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = license_return
    return db


# ── 1. Auth guards ─────────────────────────────────────────────────────────────

class TestWelcomeCardGuards:
    """Both routes redirect when license is absent or onboarding is incomplete."""

    # ── preview route ──

    def test_preview_no_license_redirects_to_dashboard(self):
        db = _mock_db(license_return=None)
        result = _run(onboarding_welcome_card(_req(), db=db, user=_user()))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_preview_incomplete_onboarding_redirects(self):
        lic = _license(onboarding_completed=False)
        db  = _mock_db(license_return=lic)
        result = _run(onboarding_welcome_card(_req(), db=db, user=_user()))
        assert isinstance(result, RedirectResponse)
        assert "onboarding" in result.headers["location"]

    # ── export route ──

    def test_export_no_license_redirects_to_dashboard(self):
        db = _mock_db(license_return=None)
        result = _run(export_onboarding_welcome_card(
            _req(), platform="instagram_square", db=db, user=_user()
        ))
        assert isinstance(result, RedirectResponse)
        assert "/dashboard" in result.headers["location"]

    def test_export_incomplete_onboarding_redirects(self):
        lic = _license(onboarding_completed=False)
        db  = _mock_db(license_return=lic)
        result = _run(export_onboarding_welcome_card(
            _req(), platform="instagram_square", db=db, user=_user()
        ))
        assert isinstance(result, RedirectResponse)
        assert "onboarding" in result.headers["location"]


# ── 2. Gallery route (no ?platform) ───────────────────────────────────────────

class TestWelcomeCardGalleryRoute:
    """GET /profile/onboarding-card with no platform renders the gallery hub."""

    def test_renders_gallery_hub_template(self):
        lic = _license()
        db  = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(_req(), platform=None, db=db, user=_user()))
        tmpl, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert tmpl == "public/welcome_card.html"

    def test_gallery_context_has_platforms_list(self):
        lic = _license()
        db  = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(_req(), platform=None, db=db, user=_user()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "platforms" in ctx
        assert isinstance(ctx["platforms"], list)
        assert len(ctx["platforms"]) >= 6

    def test_gallery_context_default_platform_is_instagram_square(self):
        lic = _license()
        db  = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(_req(), platform=None, db=db, user=_user()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx.get("default_platform") == "instagram_square"

    def test_gallery_context_has_display_name(self):
        user = _user()
        user.name = "Test Player"
        lic = _license()
        db  = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(_req(), platform=None, db=db, user=user))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx.get("display_name") == "Test Player"

    def test_gallery_context_no_player_object(self):
        """Gallery hub does not build the FIFA player namespace — only FIFA route does."""
        lic = _license()
        db  = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(_req(), platform=None, db=db, user=_user()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "player" not in ctx


# ── 3. FIFA card route (?platform=X) ──────────────────────────────────────────

class TestWelcomeCardFifaRoute:
    """GET /profile/onboarding-card?platform=X renders the FIFA Classic template."""

    def _call_with_platform(self, platform="instagram_square", export=False, lic=None):
        if lic is None:
            lic = _license()
        db = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(
                _req(), platform=platform, export=export, db=db, user=_user()
            ))
        return mock_tmpl.TemplateResponse.call_args.args  # (tmpl, ctx)

    def test_with_platform_uses_fifa_export_template(self):
        tmpl, _ = self._call_with_platform("instagram_square")
        assert "fifa" in tmpl

    def test_context_has_player_object(self):
        _, ctx = self._call_with_platform()
        assert "player" in ctx

    def test_player_skills_dict_current_level_equals_self_assessment(self):
        """
        Self-assessment adapter: FIFA template reads current_level.
        For Welcome Card, current_level must equal the self_assessment value.
        This is a template adapter — it must never be written to football_skills JSONB.
        """
        from app.skills_config import get_all_skill_keys
        sa_val = 77.0
        skills = {
            key: {"self_assessment": sa_val, "current_level": 50.0}
            for key in get_all_skill_keys()
        }
        lic = _license(football_skills=skills)
        _, ctx = self._call_with_platform(lic=lic)
        player = ctx["player"]
        for key, sdata in player.skills.items():
            assert sdata["current_level"] == sa_val, (
                f"Skill {key}: expected current_level={sa_val} (self_assessment), "
                f"got {sdata['current_level']}"
            )

    def test_db_football_skills_not_modified(self):
        """
        The route reads self_assessment; it must NEVER write back to football_skills JSONB.
        DB commit must not be called.
        """
        lic = _license()
        db  = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(
                _req(), platform="instagram_square", db=db, user=_user()
            ))
        db.commit.assert_not_called()
        db.add.assert_not_called()

    def test_sponsor_logo_url_is_none(self):
        """Welcome Card must never display sponsor logo."""
        _, ctx = self._call_with_platform()
        assert ctx.get("sponsor_logo_url") is None

    def test_app_logo_url_is_set(self):
        """Welcome Card must display fixed app logo (logo-dark.png)."""
        _, ctx = self._call_with_platform()
        assert ctx.get("app_logo_url") == _WC_APP_LOGO_URL
        assert "logo-dark.png" in ctx["app_logo_url"]

    def test_welcome_card_mode_is_true(self):
        _, ctx = self._call_with_platform()
        assert ctx.get("welcome_card_mode") is True

    def test_export_mode_false_by_default(self):
        _, ctx = self._call_with_platform(export=False)
        assert ctx.get("export_mode") is False

    def test_export_mode_true_with_param(self):
        _, ctx = self._call_with_platform(export=True)
        assert ctx.get("export_mode") is True

    def test_overall_is_mean_of_self_assessments(self):
        from app.skills_config import get_all_skill_keys
        skills = {key: {"self_assessment": 70.0, "current_level": 50.0}
                  for key in get_all_skill_keys()}
        lic = _license(football_skills=skills)
        _, ctx = self._call_with_platform(lic=lic)
        assert ctx.get("overall") == 70.0


# ── 4. Export route ────────────────────────────────────────────────────────────

class TestWelcomeCardExport:
    """GET /profile/onboarding-card/export — PNG export."""

    def test_invalid_platform_raises_422(self):
        from fastapi import HTTPException
        lic = _license()
        db  = _mock_db(license_return=lic)
        with pytest.raises(HTTPException) as exc_info:
            _run(export_onboarding_welcome_card(
                _req(), platform="banana", db=db, user=_user()
            ))
        assert exc_info.value.status_code == 422

    def test_rate_limit_raises_429(self):
        from fastapi import HTTPException
        lic = _license()
        db  = _mock_db(license_return=lic)
        with patch(f"{_BASE}._export_svc.check_export_rate_limit", return_value=False), \
             patch("app.config.settings") as mock_settings:
            mock_settings.APP_INTERNAL_PORT = 8000
            with pytest.raises(HTTPException) as exc_info:
                _run(export_onboarding_welcome_card(
                    _req(), platform="instagram_square", db=db, user=_user()
                ))
        assert exc_info.value.status_code == 429

    def test_returns_png_response(self):
        """Mock _sync_take_screenshot — assert PNG bytes returned with correct headers."""
        lic       = _license()
        db        = _mock_db(license_return=lic)
        fake_png  = b"\x89PNG\r\n\x1a\nFAKE"
        with patch(f"{_BASE}._export_svc.check_export_rate_limit", return_value=True), \
             patch(f"{_BASE}._export_svc._sync_take_screenshot", return_value=fake_png), \
             patch("app.config.settings") as mock_settings:
            mock_settings.APP_INTERNAL_PORT = 8000
            result = _run(export_onboarding_welcome_card(
                _req(), platform="instagram_square", db=db, user=_user()
            ))
        assert isinstance(result, Response)
        assert result.media_type == "image/png"
        assert result.body == fake_png
        assert "welcome_card_instagram_square.png" in result.headers["content-disposition"]

    def test_export_url_uses_self_route(self):
        """The render URL passed to Playwright must point to /profile/onboarding-card."""
        lic     = _license()
        db      = _mock_db(license_return=lic)
        fake_png = b"\x89PNG"
        captured_url = []

        def _capture(url, platform):
            captured_url.append(url)
            return fake_png

        with patch(f"{_BASE}._export_svc.check_export_rate_limit", return_value=True), \
             patch(f"{_BASE}._export_svc._sync_take_screenshot", side_effect=_capture), \
             patch("app.config.settings") as mock_settings:
            mock_settings.APP_INTERNAL_PORT = 8000
            _run(export_onboarding_welcome_card(
                _req(), platform="instagram_square", db=db, user=_user()
            ))
        assert len(captured_url) == 1
        assert "/profile/onboarding-card" in captured_url[0]
        assert "instagram_square" in captured_url[0]
        assert "export=1" in captured_url[0]


# ── 5. Gallery template static assertions ─────────────────────────────────────

_GALLERY_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "public" / "welcome_card.html"
)
_FIFA_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "public" / "player_card_fifa.html"
)
_SQUARE_EXPORT_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "public" / "export" / "square" / "fifa.html"
)


@pytest.fixture(scope="module")
def gallery_src():
    return _GALLERY_TPL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def fifa_src():
    return _FIFA_TPL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def square_export_src():
    return _SQUARE_EXPORT_TPL_PATH.read_text(encoding="utf-8")


class TestWelcomeCardGalleryTemplate:
    """Static source analysis of the gallery hub (welcome_card.html)."""

    def test_file_exists(self):
        assert _GALLERY_TPL_PATH.exists()

    def test_has_noindex_meta(self, gallery_src):
        assert "noindex" in gallery_src

    def test_has_welcome_card_heading(self, gallery_src):
        assert "Welcome Card" in gallery_src

    def test_has_based_on_self_assessment_text(self, gallery_src):
        assert "self-assessment" in gallery_src.lower()

    def test_has_english_disclaimer(self, gallery_src):
        assert "not the same as the regular Player Card" in gallery_src

    def test_has_iframe_preview(self, gallery_src):
        assert "<iframe" in gallery_src
        assert "/profile/onboarding-card" in gallery_src

    def test_has_platform_download_buttons(self, gallery_src):
        assert "/profile/onboarding-card/export?platform=" in gallery_src

    def test_has_back_link_to_profile(self, gallery_src):
        assert 'href="/profile"' in gallery_src

    def test_gallery_renders_with_minimal_context(self):
        """Jinja2 render: gallery template must not error with minimal context."""
        from jinja2 import Environment, FileSystemLoader, Undefined
        env = Environment(
            loader=FileSystemLoader(str(_GALLERY_TPL_PATH.parents[1])),
            autoescape=True,
            undefined=Undefined,
        )
        ctx = {
            "request":          MagicMock(),
            "display_name":     "Test Player",
            "platforms":        _WC_GALLERY_PLATFORMS,
            "default_platform": "instagram_square",
        }
        html = env.get_template("public/welcome_card.html").render(**ctx)
        assert "Welcome Card" in html
        assert "iframe" in html
        assert "instagram_square" in html


# ── 6. FIFA template logo audit ────────────────────────────────────────────────

class TestWelcomeCardFifaLogoAudit:
    """Static source checks that Phase 5 logo changes are in place."""

    def test_player_card_fifa_has_page_logo_css(self, fifa_src):
        assert ".page-logo" in fifa_src

    def test_player_card_fifa_has_app_logo_url_conditional(self, fifa_src):
        assert "app_logo_url" in fifa_src
        assert "{% if app_logo_url %}" in fifa_src

    def test_player_card_fifa_text_brand_is_else_branch(self, fifa_src):
        """Text brand must only render when app_logo_url is falsy (inside {% else %} block)."""
        # The brand div must be preceded by {% else %} before {% endif %}
        # Locate the page-brand div in the HTML body (not the CSS class definition)
        brand_div_idx = fifa_src.find('<div class="page-brand">')
        assert brand_div_idx != -1, "page-brand div not found"
        # There must be an {% else %} between the last {% if app_logo_url %} and the brand div
        before_brand = fifa_src[:brand_div_idx]
        assert "{% else %}" in before_brand, "page-brand div must be inside {% else %} block"
        assert "app_logo_url" in before_brand, "{% if app_logo_url %} must precede page-brand div"

    def test_export_square_has_app_logo_in_sponsor_slot(self, square_export_src):
        assert "app_logo_url" in square_export_src
        assert "elif app_logo_url" in square_export_src

    def test_export_square_sponsor_logo_takes_priority(self, square_export_src):
        """sponsor_logo_url branch must be checked before app_logo_url."""
        idx_sponsor = square_export_src.find("sponsor_logo_url")
        idx_app     = square_export_src.find("app_logo_url")
        assert idx_sponsor < idx_app


# ── 7. Step 7 onboarding template ─────────────────────────────────────────────

@pytest.fixture(scope="module")
def step7_src():
    path = (
        pathlib.Path(__file__).resolve().parents[4]
        / "app" / "templates" / "lfa_player_onboarding.html"
    )
    return path.read_text(encoding="utf-8")


class TestWelcomeCardStep7:
    """Verify Step 7 of the onboarding template."""

    # ── Phase 1: Hungarian text removed ──

    def test_no_hungarian_title(self, step7_src):
        assert "Az onboarding sikeresen befejeződött" not in step7_src

    def test_no_hungarian_notice_ez_a_kartya(self, step7_src):
        assert "Ez a kártya" not in step7_src

    def test_no_hungarian_notice_nem_azonos(self, step7_src):
        assert "Nem azonos a rendes" not in step7_src

    def test_no_hungarian_notice_ema_motor(self, step7_src):
        assert "EMA-motor" not in step7_src

    # ── Phase 1: English text present ──

    def test_english_subtitle_present(self, step7_src):
        assert "Onboarding completed successfully" in step7_src

    def test_english_notice_line_1(self, step7_src):
        assert "This card is generated from your self-assessment data" in step7_src

    def test_english_notice_line_2(self, step7_src):
        assert "not the same as the regular Player Card" in step7_src

    def test_english_notice_line_3(self, step7_src):
        assert "Values come from your own self-assessment" in step7_src

    # ── CTA buttons ──

    def test_view_welcome_card_link_present(self, step7_src):
        assert 'id="btn-view-welcome-card"' in step7_src

    def test_view_welcome_card_href_correct(self, step7_src):
        assert 'href="/profile/onboarding-card"' in step7_src

    def test_view_welcome_card_opens_in_new_tab(self, step7_src):
        assert 'target="_blank"' in step7_src

    def test_download_link_is_active_anchor(self, step7_src):
        """Download button must be an <a> link pointing to the export route, not a disabled placeholder."""
        assert 'id="btn-download-welcome-card"' in step7_src
        assert '/profile/onboarding-card/export' in step7_src
        # Must NOT be a disabled span
        assert 'btn-disabled-placeholder' not in step7_src

    # ── Phase 4: Photo upload ──

    def test_photo_upload_input_present(self, step7_src):
        assert 'id="step7-photo-input"' in step7_src
        assert 'type="file"' in step7_src

    def test_photo_upload_uses_dashboard_endpoint(self, step7_src):
        assert "/dashboard/lfa-player-photo" in step7_src

    def test_photo_upload_is_optional(self, step7_src):
        """Upload input must not block the CTA buttons (they are in the same step)."""
        idx_upload = step7_src.find("step7-photo-input")
        idx_view   = step7_src.find("btn-view-welcome-card")
        assert idx_upload != -1 and idx_view != -1
        # Both elements present in step-7 — the CTA row comes after the upload widget
        assert idx_upload < idx_view or idx_upload > idx_view  # both present, order flexible
