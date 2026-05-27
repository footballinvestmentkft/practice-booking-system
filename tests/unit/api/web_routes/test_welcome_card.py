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
    profile_page,
    _build_welcome_card_context,
    _WC_APP_LOGO_URL,
    _WC_GALLERY_PLATFORMS,
)
from app.models.license import UserLicense
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
    u.id            = uid
    u.role          = role
    u.email         = "player@test.com"
    u.name          = "Test Player"
    u.country       = "HU"
    u.nickname      = "tester"
    u.secondary_nationality = None
    u.credit_balance        = 500
    u.date_of_birth         = None   # None → age_group defaults to AMATEUR
    return u


def _license(onboarding_completed=True, football_skills=None):
    from app.skills_config import get_all_skill_keys
    lic = MagicMock()
    lic.specialization_type   = "LFA_FOOTBALL_PLAYER"
    lic.onboarding_completed  = onboarding_completed
    lic.player_card_photo_url    = None
    lic.card_photo_portrait_url  = None
    lic.card_photo_landscape_url = None
    lic.wc_photo_url             = None
    lic.wc_photo_portrait_url    = None
    lic.wc_photo_landscape_url   = None
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

    # WC-CTX-01..03 — Phase 2: canvas_sizes context and platform order regression

    def test_wc_ctx01_canvas_sizes_keys_match_canvas_sizes_constant(self):
        """Gallery context canvas_sizes must cover all CANVAS_SIZES platforms."""
        from app.services.card_constants import CANVAS_SIZES
        lic = _license()
        db  = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(_req(), platform=None, db=db, user=_user()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "canvas_sizes" in ctx, "Gallery hub context must include 'canvas_sizes'"
        assert set(ctx["canvas_sizes"].keys()) == set(CANVAS_SIZES.keys()), (
            "canvas_sizes keys in context must match CANVAS_SIZES keys exactly"
        )

    def test_wc_ctx02_platforms_length_matches_wc_gallery_platform_ids(self):
        """Gallery platforms list length must equal WC_GALLERY_PLATFORM_IDS length."""
        from app.services.card_constants import WC_GALLERY_PLATFORM_IDS
        lic = _license()
        db  = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(_req(), platform=None, db=db, user=_user()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert len(ctx["platforms"]) == len(WC_GALLERY_PLATFORM_IDS), (
            f"Expected {len(WC_GALLERY_PLATFORM_IDS)} platforms, "
            f"got {len(ctx['platforms'])}"
        )

    def test_wc_ctx03_platforms_order_matches_wc_gallery_platform_ids(self):
        """Gallery platforms order must match WC_GALLERY_PLATFORM_IDS order."""
        from app.services.card_constants import WC_GALLERY_PLATFORM_IDS
        lic = _license()
        db  = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(_req(), platform=None, db=db, user=_user()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        actual_ids = [p["id"] for p in ctx["platforms"]]
        assert actual_ids == list(WC_GALLERY_PLATFORM_IDS), (
            f"Platform order mismatch.\n"
            f"Expected: {list(WC_GALLERY_PLATFORM_IDS)}\n"
            f"Actual:   {actual_ids}"
        )

    def test_sh04_gallery_context_has_spec_dashboard_url(self):
        """Gallery hub context must include spec_dashboard_url for spec_subpage_hdr.html."""
        lic = _license()
        db  = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(_req(), platform=None, db=db, user=_user()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx.get("spec_dashboard_url") == "/dashboard/lfa-football-player"


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

    def test_with_platform_uses_welcome_export_template(self):
        """Phase 4E: WC platform renders now route to welcome/ templates, not fifa/."""
        tmpl, _ = self._call_with_platform("instagram_square")
        assert "welcome" in tmpl

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
        """Welcome Card must display fixed app logo (logo-wc.png)."""
        _, ctx = self._call_with_platform()
        assert ctx.get("app_logo_url") == _WC_APP_LOGO_URL
        assert "logo-wc.png" in ctx["app_logo_url"]

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
        assert "render_token=" in captured_url[0]

    def test_export_render_token_is_valid_jwt(self):
        """render_token embedded in the render URL must be a valid wc_render JWT."""
        from jose import jwt as _jwt
        from app.config import settings
        lic = _license()
        db  = _mock_db(license_return=lic)
        u   = _user(uid=42)
        captured_url = []

        def _capture(url, platform):
            captured_url.append(url)
            return b"\x89PNG"

        with patch(f"{_BASE}._export_svc.check_export_rate_limit", return_value=True), \
             patch(f"{_BASE}._export_svc._sync_take_screenshot", side_effect=_capture), \
             patch("app.config.settings") as mock_settings:
            mock_settings.APP_INTERNAL_PORT = 8000
            _run(export_onboarding_welcome_card(
                _req(), platform="instagram_square", db=db, user=u
            ))

        url = captured_url[0]
        token = next(p.split("=", 1)[1] for p in url.split("&") if p.startswith("render_token="))
        payload = _jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        assert payload["purpose"] == "wc_render"
        assert payload["sub"] == "42"

    def test_export_render_token_expires_within_60s(self):
        """render_token exp claim must be no more than 60 s from now."""
        import time
        from jose import jwt as _jwt
        from app.config import settings
        lic = _license()
        db  = _mock_db(license_return=lic)
        captured_url = []

        def _capture(url, platform):
            captured_url.append(url)
            return b"\x89PNG"

        before = int(time.time())
        with patch(f"{_BASE}._export_svc.check_export_rate_limit", return_value=True), \
             patch(f"{_BASE}._export_svc._sync_take_screenshot", side_effect=_capture), \
             patch("app.config.settings") as mock_settings:
            mock_settings.APP_INTERNAL_PORT = 8000
            _run(export_onboarding_welcome_card(
                _req(), platform="instagram_square", db=db, user=_user()
            ))

        url = captured_url[0]
        token = next(p.split("=", 1)[1] for p in url.split("&") if p.startswith("render_token="))
        payload = _jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        assert payload["exp"] <= before + 60 + 2  # 2 s clock tolerance


# ── 5. Render token auth branch ───────────────────────────────────────────────

class TestWelcomeCardRenderTokenAuth:
    """Render-token auth branch of GET /profile/onboarding-card (Playwright export path)."""

    def _make_token(self, user_id: int = 10, purpose: str = "wc_render", expired: bool = False) -> str:
        from datetime import timedelta
        from app.core.auth import create_access_token
        delta = timedelta(seconds=-1) if expired else timedelta(seconds=60)
        return create_access_token(
            data={"sub": str(user_id), "purpose": purpose},
            expires_delta=delta,
        )

    def _db_user_then_license(self):
        """Mock db that returns _user() on first .first() call, _license() on second."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [_user(), _license()]
        return db

    def test_valid_token_renders_card(self):
        """Valid render_token with purpose=wc_render → card rendered (no cookie needed)."""
        token = self._make_token()
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            result = _run(onboarding_welcome_card(
                _req(), platform="instagram_square", export=True,
                render_token=token, db=self._db_user_then_license(), user=None,
            ))
        assert mock_tmpl.TemplateResponse.called

    def test_expired_token_raises_401(self):
        """Expired render_token → HTTPException 401."""
        from fastapi import HTTPException
        token = self._make_token(expired=True)
        with pytest.raises(HTTPException) as exc:
            _run(onboarding_welcome_card(
                _req(), platform="instagram_square", export=True,
                render_token=token, db=_mock_db(), user=None,
            ))
        assert exc.value.status_code == 401

    def test_wrong_purpose_token_raises_401(self):
        """Token with purpose != wc_render → HTTPException 401."""
        from fastapi import HTTPException
        token = self._make_token(purpose="access")
        with pytest.raises(HTTPException) as exc:
            _run(onboarding_welcome_card(
                _req(), platform="instagram_square", export=True,
                render_token=token, db=_mock_db(), user=None,
            ))
        assert exc.value.status_code == 401

    def test_invalid_signature_raises_401(self):
        """Tampered / non-JWT render_token → HTTPException 401."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _run(onboarding_welcome_card(
                _req(), platform="instagram_square", export=True,
                render_token="not.a.valid.jwt.token", db=_mock_db(), user=None,
            ))
        assert exc.value.status_code == 401

    def test_no_token_no_cookie_raises_401(self):
        """Neither render_token nor cookie user → HTTPException 401."""
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _run(onboarding_welcome_card(
                _req(), platform="instagram_square", export=True,
                render_token=None, db=_mock_db(), user=None,
            ))
        assert exc.value.status_code == 401

    def test_cookie_auth_still_works_without_token(self):
        """Backward compat: cookie user, no render_token → card rendered normally."""
        lic = _license()
        db  = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(
                _req(), platform="instagram_square", db=db, user=_user(),
            ))
        assert mock_tmpl.TemplateResponse.called


# ── 6. Gallery template static assertions ─────────────────────────────────────

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

    def test_inherits_app_navigation_via_student_base(self, gallery_src):
        # Navigation is provided by the student_base.html navbar (Option A).
        # The manual back-link was removed; student_base.html carries the LFA Player nav item.
        assert '{% extends "student_base.html" %}' in gallery_src

    # ── Theme token assertions (TT-01..TT-05) ────────────────────────────────

    def test_tt01_no_hardcoded_dark_body_background(self, gallery_src):
        # body background must use --hub-bg token, never the old hardcoded dark navy
        assert "#0f0f1a" not in gallery_src
        assert "var(--hub-bg)" in gallery_src

    def test_tt02_uses_hub_card_token_for_panels(self, gallery_src):
        assert "var(--hub-card-bg)" in gallery_src

    def test_tt03_uses_hub_btn_token_for_download_cta(self, gallery_src):
        assert "var(--hub-btn-bg)" in gallery_src

    def test_tt04_badge_has_no_purple_colour(self, gallery_src):
        # Style badge was purple (off-brand); must now be brand-yellow
        assert "167,139,250" not in gallery_src

    def test_tt05_wc_frame_bg_token_used_for_iframe_container(self, gallery_src):
        assert "var(--wc-frame-bg)" in gallery_src

    # ── Spec header assertions (SH-01..SH-03) ────────────────────────────────

    def test_sh01_overrides_student_header_block(self, gallery_src):
        assert "{% block student_header %}" in gallery_src

    def test_sh02_includes_spec_subpage_hdr(self, gallery_src):
        assert "spec_subpage_hdr.html" in gallery_src

    def test_sh03_active_page_is_lfa_player(self, gallery_src):
        assert "active_page %}lfa-player" in gallery_src

    # ── Watermark assertions (WM-01..WM-05) ──────────────────────────────────

    def test_wm01_preview_wrapper_has_before_pseudo(self, gallery_src):
        assert "wc-preview-frame-wrap::before" in gallery_src

    def test_wm02_preview_wrapper_has_after_pseudo(self, gallery_src):
        assert "wc-preview-frame-wrap::after" in gallery_src

    def test_wm03_logo_layer_uses_mask_image(self, gallery_src):
        assert "mask-image" in gallery_src
        assert "logo-wc.png" in gallery_src

    def test_wm04_text_layer_uses_svg_data_uri(self, gallery_src):
        assert "data:image/svg+xml;base64," in gallery_src

    def test_wm05_watermark_is_pointer_events_none(self, gallery_src):
        assert "pointer-events: none" in gallery_src

    def test_gallery_renders_with_minimal_context(self):
        """Jinja2 render: gallery template must not error with minimal context.

        canvas_sizes is required (Phase 2): the template uses {{ canvas_sizes | tojson }}
        to generate the JS CANVAS_SIZES object instead of a hardcoded literal.
        """
        from jinja2 import Environment, FileSystemLoader, Undefined
        from app.services.card_constants import CANVAS_SIZES
        env = Environment(
            loader=FileSystemLoader(str(_GALLERY_TPL_PATH.parents[1])),
            autoescape=True,
            undefined=Undefined,
        )
        canvas_sizes = {pid: {"w": w, "h": h} for pid, (w, h) in CANVAS_SIZES.items()}
        minimal_user = MagicMock()
        minimal_user.nickname = None
        ctx = {
            "request":          MagicMock(),
            "user":             minimal_user,
            "display_name":     "Test Player",
            "platforms":        _WC_GALLERY_PLATFORMS,
            "default_platform": "instagram_square",
            "canvas_sizes":     canvas_sizes,
            "use_nickname":     False,
        }
        html = env.get_template("public/welcome_card.html").render(**ctx)
        assert "Welcome Card" in html
        assert "iframe" in html
        assert "instagram_square" in html


# ── 7. FIFA template logo audit ────────────────────────────────────────────────

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
        # v8: combined `or` condition — sponsor takes priority via `sponsor_logo_url or app_logo_url`
        assert "sponsor_logo_url or app_logo_url" in square_export_src

    def test_export_square_sponsor_logo_takes_priority(self, square_export_src):
        """sponsor_logo_url branch must be checked before app_logo_url."""
        idx_sponsor = square_export_src.find("sponsor_logo_url")
        idx_app     = square_export_src.find("app_logo_url")
        assert idx_sponsor < idx_app


# ── 8. Step 7 onboarding template ─────────────────────────────────────────────

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
        assert "/dashboard/initial-player-photo" in step7_src

    def test_photo_upload_is_optional(self, step7_src):
        """Upload input must not block the CTA buttons (they are in the same step)."""
        idx_upload = step7_src.find("step7-photo-input")
        idx_view   = step7_src.find("btn-view-welcome-card")
        assert idx_upload != -1 and idx_view != -1
        # Both elements present in step-7 — the CTA row comes after the upload widget
        assert idx_upload < idx_view or idx_upload > idx_view  # both present, order flexible


# ── 9. Profile page Welcome Card section ──────────────────────────────────────

_PROFILE_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "profile.html"
)
_DASHBOARD_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "dashboard_student_new.html"
)


@pytest.fixture(scope="module")
def profile_src():
    return _PROFILE_TPL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def dashboard_src():
    return _DASHBOARD_TPL_PATH.read_text(encoding="utf-8")


def _wc_block(profile_src: str) -> str:
    """Extract and return the Welcome Card conditional block from profile.html."""
    start = profile_src.find("<!-- Welcome Card")
    end   = profile_src.find("<!-- Emergency Contact", start)
    assert start != -1 and end != -1, "Welcome Card block not found in profile.html"
    return profile_src[start:end]


def _render_wc_fragment(profile_src: str, lfa_license) -> str:
    """Render the Welcome Card conditional block with the given lfa_license value."""
    from jinja2 import Template
    fragment = _wc_block(profile_src)
    # Strip HTML comment lines so only the Jinja2 conditional block remains
    fragment = "\n".join(
        line for line in fragment.splitlines()
        if not line.strip().startswith("<!--")
    )
    return Template(fragment).render(lfa_license=lfa_license)


def _lic(spec_type="LFA_FOOTBALL_PLAYER", onboarding_completed=True):
    lic = MagicMock()
    lic.specialization_type  = spec_type
    lic.onboarding_completed = onboarding_completed
    return lic


class TestWelcomeCardProfileSection:
    """Verify the Welcome Card section in profile.html is gated correctly."""

    # ── T1: completed LFA Player sees the section ──

    def test_completed_lfa_player_sees_welcome_card_section(self, profile_src):
        html = _render_wc_fragment(profile_src, _lic())
        assert "/profile/onboarding-card" in html
        assert "View Welcome Card" in html
        assert "Download" in html

    # ── T2: incomplete onboarding hides the section ──
    # The route sets lfa_license=None when onboarding is not completed,
    # so the template receives None — not a license object.

    def test_incomplete_onboarding_hides_welcome_card(self, profile_src):
        html = _render_wc_fragment(profile_src, None)
        assert "/profile/onboarding-card" not in html
        assert "Welcome Card" not in html

    # ── T3: non-LFA spec hides the section ──
    # The route sets lfa_license=None when no completed LFA_FOOTBALL_PLAYER license
    # exists, regardless of what other licenses the user holds.

    def test_non_lfa_spec_hides_welcome_card(self, profile_src):
        html = _render_wc_fragment(profile_src, None)
        assert "/profile/onboarding-card" not in html

    # ── T4: dashboard mod-nav has no Welcome Card quick action ──

    def test_dashboard_mod_nav_has_no_welcome_card_link(self, dashboard_src):
        # Extract mod-nav-section block
        start = dashboard_src.find('class="mod-nav-section"')
        end   = dashboard_src.find("</section>", start)
        assert start != -1 and end != -1
        mod_nav = dashboard_src[start:end]
        assert "onboarding-card" not in mod_nav
        assert "Welcome Card" not in mod_nav

    # ── T5: dashboard Player Card iframe link unchanged ──

    def test_dashboard_player_card_link_unchanged(self, dashboard_src):
        assert '/players/{{ user.id }}/card' in dashboard_src
        assert 'dc-player-iframe' in dashboard_src


# ── 10. Preview / export rendering fixes (Fix A, B, C) ───────────────────────

_PUBLIC_PLAYER_PY_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "api" / "web_routes" / "public_player.py"
)
_STORY_EXPORT_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "public" / "export" / "story" / "fifa.html"
)
_TIKTOK_EXPORT_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "public" / "export" / "tiktok" / "fifa.html"
)


@pytest.fixture(scope="module")
def gallery_html_src():
    return _GALLERY_TPL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def public_player_src():
    return _PUBLIC_PLAYER_PY_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def story_export_src():
    return _STORY_EXPORT_TPL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def tiktok_export_src():
    return _TIKTOK_EXPORT_TPL_PATH.read_text(encoding="utf-8")


def _wc_context(right_foot=70.0, left_foot=30.0, motivation_scores=None):
    """Call _build_welcome_card_context with controllable foot scores and motivation data."""
    from app.api.web_routes.profile import _build_welcome_card_context
    lic = _license()
    lic.right_foot_score = right_foot
    lic.left_foot_score  = left_foot
    if motivation_scores is not None:
        lic.motivation_scores = motivation_scores
    return _build_welcome_card_context(_req(), _user(), lic, "instagram_square", False)


class TestWelcomeCardRenderingFixes:
    """Verify Fix A (iframe alignment), Fix B (canvas sizing), Fix C (physical context)."""

    # ── Fix A: no flex centering on the frame wrapper ──

    def test_gallery_frame_wrap_has_no_justify_content_center(self, gallery_html_src):
        """Fix A: justify-content:center must NOT appear in .wc-preview-frame-wrap block."""
        start = gallery_html_src.find(".wc-preview-frame-wrap {")
        end   = gallery_html_src.find("}", start)
        assert start != -1, ".wc-preview-frame-wrap rule not found"
        wrap_rule = gallery_html_src[start:end]
        assert "justify-content" not in wrap_rule
        assert "align-items" not in wrap_rule

    def test_gallery_iframe_is_position_absolute(self, gallery_html_src):
        """Fix A: iframe must be position:absolute so transform-origin:top-left works."""
        start = gallery_html_src.find("#wc-preview-iframe {")
        end   = gallery_html_src.find("}", start)
        assert start != -1
        iframe_rule = gallery_html_src[start:end]
        assert "position: absolute" in iframe_rule
        assert "top: 0" in iframe_rule
        assert "left: 0" in iframe_rule

    def test_gallery_frame_wrap_is_position_relative(self, gallery_html_src):
        """Fix A: wrapper must be position:relative to contain the absolute iframe."""
        start = gallery_html_src.find(".wc-preview-frame-wrap {")
        end   = gallery_html_src.find("}", start)
        wrap_rule = gallery_html_src[start:end]
        assert "position: relative" in wrap_rule

    # ── Fix B: platform-aware canvas sizing ──

    def test_canvas_sizes_is_server_rendered(self, gallery_html_src):
        """Fix B (Phase 2): JS CANVAS_SIZES must be server-rendered from context,
        not a hardcoded literal. Verifies the tojson injection marker is present."""
        assert "canvas_sizes | tojson" in gallery_html_src, (
            "welcome_card.html must use {{ canvas_sizes | tojson }} to populate "
            "the JS CANVAS_SIZES object — hardcoded platform literals must not appear."
        )

    def test_canvas_sizes_default_platform_used_in_iframe_src(self, gallery_html_src):
        """Iframe src and JS _activePlatform must use the server-supplied default_platform
        variable, not a hardcoded platform ID."""
        assert "platform={{ default_platform }}" in gallery_html_src, (
            "welcome_card.html iframe src must reference {{ default_platform }}, "
            "not a hardcoded platform ID."
        )
        assert "_activePlatform = '{{ default_platform }}'" in gallery_html_src, (
            "JS _activePlatform must be initialised from {{ default_platform }}, "
            "not a hardcoded string."
        )

    def test_select_platform_calls_scale_iframe(self, gallery_html_src):
        """Fix B: selectPlatform() must call scaleIframe(pid) after updating src."""
        func_start = gallery_html_src.find("function selectPlatform(")
        func_end   = gallery_html_src.find("\n}", func_start)
        assert func_start != -1
        func_body = gallery_html_src[func_start:func_end]
        assert "scaleIframe" in func_body

    def test_scale_iframe_uses_canvas_sizes(self, gallery_html_src):
        """Fix B: scaleIframe must reference CANVAS_SIZES, not a hardcoded 1080."""
        func_start = gallery_html_src.find("function scaleIframe(")
        func_end   = gallery_html_src.find("\n}", func_start)
        assert func_start != -1
        func_body = gallery_html_src[func_start:func_end]
        assert "CANVAS_SIZES" in func_body
        # Must not hardcode 1080 for both width and height
        assert "= '1080px'" not in func_body

    # ── Fix C: physical context keys ──

    def test_context_has_player_height_cm(self):
        ctx = _wc_context(motivation_scores={"height_cm": 178, "weight_kg": 74})
        assert ctx["player_height_cm"] == 178

    def test_context_has_player_weight_kg(self):
        ctx = _wc_context(motivation_scores={"height_cm": 178, "weight_kg": 74})
        assert ctx["player_weight_kg"] == 74

    def test_context_player_height_cm_is_none_when_absent(self):
        ctx = _wc_context(motivation_scores={})
        assert ctx["player_height_cm"] is None

    def test_context_dominant_badge_when_foot_scores_differ(self):
        """right=70, left=30 → right_pct=70% → 'Rl' (right-footed)."""
        ctx = _wc_context(right_foot=70.0, left_foot=30.0)
        assert ctx["dominant_badge"] == "Rl"

    def test_context_dominant_badge_no_data_returns_rl(self):
        """No foot scores → calculate_dominant_badge returns 'rl' (unassessed), never None."""
        ctx = _wc_context(right_foot=None, left_foot=None)
        assert ctx["dominant_badge"] == "rl"
        assert ctx["dominant_badge"] is not None

    # ── Regression: data contract unchanged ──

    def test_player_card_route_uses_calculate_dominant_badge(self, public_player_src):
        """Player Card route must still call calculate_dominant_badge unchanged."""
        assert "calculate_dominant_badge" in public_player_src
        assert "dominant_badge" in public_player_src

    def test_story_template_guards_dominant_badge(self, story_export_src):
        """Story template must guard dominant_badge with {% if %} (and suppress unassessed 'rl')."""
        assert "{% if dominant_badge" in story_export_src
        assert 'dominant_badge != "rl"' in story_export_src

    def test_tiktok_template_guards_player_height_cm(self, tiktok_export_src):
        """TikTok template must guard player_height_cm with {% if %}."""
        assert "{% if player_height_cm %}" in tiktok_export_src


# ── 10. Gallery hub photo upload panel ───────────────────────────────────────

class TestWelcomeCardPhotoUpload:
    """Gallery hub photo upload & delete panel — context, template, JS."""

    # ── Route context ──────────────────────────────────────────────────────────

    def _call_gallery(self, photo_url=None):
        lic = _license()
        lic.player_card_photo_url = photo_url
        db = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(_req(), platform=None, db=db, user=_user()))
        return mock_tmpl.TemplateResponse.call_args.args[1]  # ctx dict

    def test_gallery_context_has_photo_url_key(self):
        ctx = self._call_gallery()
        assert "photo_url" in ctx

    def test_gallery_context_photo_url_is_none_when_no_photo(self):
        ctx = self._call_gallery(photo_url=None)
        assert ctx["photo_url"] is None

    def test_gallery_context_photo_url_matches_license(self):
        url = "/static/uploads/lfa_player_photos/10_orig_1234567890.png"
        ctx = self._call_gallery(photo_url=url)
        assert ctx["photo_url"] == url

    # ── Template static checks ─────────────────────────────────────────────────

    def test_gallery_has_photo_panel(self, gallery_html_src):
        assert "wc-photo-panel" in gallery_html_src

    def test_gallery_has_photo_file_input(self, gallery_html_src):
        assert 'id="wc-photo-input"' in gallery_html_src
        assert 'type="file"' in gallery_html_src

    def test_gallery_upload_endpoint_is_correct(self, gallery_html_src):
        assert "/dashboard/wc-photo'" in gallery_html_src

    def test_gallery_delete_endpoint_is_correct(self, gallery_html_src):
        assert "/dashboard/wc-photo/delete'" in gallery_html_src

    def test_gallery_has_getcsrftoken_function(self, gallery_html_src):
        assert "function getCsrfToken()" in gallery_html_src

    def test_gallery_photo_requests_have_csrf_header(self, gallery_html_src):
        """Both upload and delete fetches must include the X-CSRF-Token header."""
        assert "'X-CSRF-Token': getCsrfToken()" in gallery_html_src

    def test_gallery_success_reloads_iframe(self, gallery_html_src):
        """reloadPreview() must be called on both upload and delete success."""
        assert "function reloadPreview()" in gallery_html_src
        assert "reloadPreview()" in gallery_html_src

    def test_gallery_error_shows_backend_detail(self, gallery_html_src):
        """Error fallback must include data.detail before 'unknown error'."""
        assert "data.detail" in gallery_html_src

    # ── Jinja2 render: thumbnail / placeholder state ───────────────────────────

    def _render_gallery(self, photo_url=None):
        from jinja2 import Environment, FileSystemLoader, Undefined
        from app.services.card_constants import CANVAS_SIZES
        env = Environment(
            loader=FileSystemLoader(str(_GALLERY_TPL_PATH.parents[1])),
            autoescape=True,
            undefined=Undefined,
        )
        canvas_sizes = {pid: {"w": w, "h": h} for pid, (w, h) in CANVAS_SIZES.items()}
        minimal_user = MagicMock()
        minimal_user.nickname = None
        ctx = {
            "request":          MagicMock(),
            "user":             minimal_user,
            "display_name":     "Test Player",
            "platforms":        _WC_GALLERY_PLATFORMS,
            "default_platform": "instagram_square",
            "photo_url":        photo_url,
            "canvas_sizes":     canvas_sizes,
            "use_nickname":     False,
        }
        return env.get_template("public/welcome_card.html").render(**ctx)

    def test_thumbnail_src_set_when_photo_url_present(self):
        url = "/static/uploads/lfa_player_photos/10_orig_1234567890.png"
        html = self._render_gallery(photo_url=url)
        assert f'src="{url}"' in html

    def test_thumbnail_visible_when_photo_url_present(self):
        url = "/static/uploads/lfa_player_photos/10_orig_1234567890.png"
        html = self._render_gallery(photo_url=url)
        # img tag must NOT be hidden (display:none absent from its inline style)
        thumb_start = html.find('id="wc-photo-thumb"')
        assert thumb_start != -1
        tag_end = html.find('>', thumb_start)
        tag_html = html[thumb_start:tag_end]
        assert "display:none" not in tag_html

    def test_placeholder_visible_when_no_photo_url(self):
        html = self._render_gallery(photo_url=None)
        ph_start = html.find('id="wc-photo-placeholder"')
        assert ph_start != -1
        tag_end = html.find('>', ph_start)
        tag_html = html[ph_start:tag_end]
        assert "display:none" not in tag_html

    def test_delete_btn_hidden_when_no_photo_url(self):
        html = self._render_gallery(photo_url=None)
        btn_start = html.find('id="wc-delete-btn"')
        assert btn_start != -1
        tag_end = html.find('>', btn_start)
        tag_html = html[btn_start:tag_end]
        assert "display:none" in tag_html

    def test_delete_btn_visible_when_photo_url_present(self):
        url = "/static/uploads/lfa_player_photos/10_orig_1234567890.png"
        html = self._render_gallery(photo_url=url)
        btn_start = html.find('id="wc-delete-btn"')
        assert btn_start != -1
        tag_end = html.find('>', btn_start)
        tag_html = html[btn_start:tag_end]
        assert "display:none" not in tag_html


# ── 11. profile_page route — lfa_license context key ─────────────────────────

class TestProfilePageLfaLicense:
    """
    profile_page sets lfa_license independent of user.specialization.
    Covers the spec-switch scenario: Welcome Card remains visible after the
    user switches active spec away from LFA_FOOTBALL_PLAYER.
    """

    def _make_db(self, user_licenses):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = user_licenses
        db.query.return_value.filter.return_value.first.return_value = None
        return db

    def _lfa_lic(self, completed=True):
        lic = MagicMock()
        lic.specialization_type = "LFA_FOOTBALL_PLAYER"
        lic.onboarding_completed = completed
        return lic

    def _call_profile(self, user_licenses, active_spec_value=None):
        """Invoke profile_page and return the TemplateResponse context dict."""
        user = _user()
        if active_spec_value is not None:
            spec = MagicMock()
            spec.value = active_spec_value
            user.specialization = spec
        else:
            user.specialization = None
        db = self._make_db(user_licenses)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(profile_page(_req(), db=db, user=user))
        return mock_tmpl.TemplateResponse.call_args.args[1]

    # ── Route context: key presence ───────────────────────────────────────────

    def test_context_includes_lfa_license_key(self):
        ctx = self._call_profile([])
        assert "lfa_license" in ctx

    def test_lfa_license_none_when_no_licenses(self):
        ctx = self._call_profile([])
        assert ctx["lfa_license"] is None

    def test_lfa_license_none_when_incomplete_onboarding(self):
        ctx = self._call_profile([self._lfa_lic(completed=False)])
        assert ctx["lfa_license"] is None

    def test_lfa_license_set_when_completed(self):
        lic = self._lfa_lic(completed=True)
        ctx = self._call_profile([lic])
        assert ctx["lfa_license"] is lic

    # ── Spec-switch scenario ──────────────────────────────────────────────────

    def test_lfa_license_set_when_active_spec_is_gancuju(self):
        """
        Active spec = GANCUJU_PLAYER, but a completed LFA_FOOTBALL_PLAYER license
        also exists → lfa_license must be set so the Welcome Card remains visible.
        """
        lic = self._lfa_lic(completed=True)
        ctx = self._call_profile([lic], active_spec_value="GANCUJU_PLAYER")
        assert ctx["lfa_license"] is lic

    def test_lfa_license_set_when_active_spec_is_lfa_coach(self):
        lic = self._lfa_lic(completed=True)
        ctx = self._call_profile([lic], active_spec_value="LFA_COACH")
        assert ctx["lfa_license"] is lic

    def test_lfa_license_none_when_only_gancuju_license_exists(self):
        """User has GANCUJU license (no LFA_FOOTBALL_PLAYER) → lfa_license is None."""
        gancuju = MagicMock()
        gancuju.specialization_type = "GANCUJU_PLAYER"
        gancuju.onboarding_completed = True
        ctx = self._call_profile([gancuju])
        assert ctx["lfa_license"] is None

    # ── Template: Welcome Card visible via lfa_license (not active_license) ───

    def test_welcome_card_visible_when_active_spec_is_gancuju(self, profile_src):
        """
        Template: Welcome Card section must appear when lfa_license is set,
        even if the user's current active spec is GANCUJU_PLAYER.
        """
        lic = self._lfa_lic(completed=True)
        html = _render_wc_fragment(profile_src, lic)
        assert "/profile/onboarding-card" in html
        assert "View Welcome Card" in html


# ── 12. Square export template — WC quality fixes ────────────────────────────

_LANDSCAPE_EXPORT_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates" / "public" / "export" / "landscape" / "fifa.html"
)


@pytest.fixture(scope="module")
def landscape_export_src():
    return _LANDSCAPE_EXPORT_TPL_PATH.read_text(encoding="utf-8")


class TestSquareExportWCFixes:
    """
    Static source assertions for export/square/fifa.html Welcome Card quality fixes.

    SQ-WC-01: player.age_group bare pattern removed; or "—" fallback present in both grid paths
    SQ-WC-02: ex-mini-grid--wc CSS class defined (2×2 WC variant)
    SQ-WC-03: welcome_card_mode gates which grid variant renders
    SQ-WC-04: AGE and GENDER cells absent from WC mini-grid branch
    SQ-WC-05: WC mini-grid branch contains NAT, GROUP, HEIGHT, WEIGHT cells
    SQ-WC-06: stat strip WC branch renders TIER label and SA value
    SQ-WC-07: stat strip non-WC branch preserves LICENSE / Lv. logic
    SQ-WC-08: no-photo placeholder contains ex-photo-monogram inner element
    """

    def test_sq_wc_01_age_group_bare_pattern_removed(self, square_export_src):
        """Bare {{ player.age_group }} without fallback must not exist in square template."""
        assert "{{ player.age_group }}" not in square_export_src, (
            "Bare {{ player.age_group }} renders empty string when None. "
            "Must use player.age_group or '—' in both grid paths."
        )

    def test_sq_wc_01_age_group_has_or_dash_fallback(self, square_export_src):
        """Both WC and non-WC grid paths must use player.age_group or '—'."""
        assert 'player.age_group or "—"' in square_export_src, (
            "GROUP cell must use player.age_group or '—' fallback in square template."
        )

    def test_sq_wc_02_mini_grid_wc_css_class_defined(self, square_export_src):
        """CSS class ex-mini-grid--wc must be defined for the 2×2 WC grid layout."""
        assert ".ex-mini-grid--wc" in square_export_src, (
            "WC 2×2 grid CSS class ex-mini-grid--wc must be defined in square template."
        )

    def test_sq_wc_03_welcome_card_mode_gates_mini_grid(self, square_export_src):
        """welcome_card_mode Jinja2 conditional must control which grid variant renders."""
        assert "{% if welcome_card_mode %}" in square_export_src, (
            "Mini-grid must be gated on welcome_card_mode to switch between "
            "2×2 WC layout and 3×2 full layout."
        )

    def test_sq_wc_04_age_cell_absent_in_wc_grid_branch(self, square_export_src):
        """AGE cell must not appear inside the WC mini-grid branch."""
        wc_start   = square_export_src.find("{% if welcome_card_mode %}")
        else_start = square_export_src.find("{% else %}", wc_start)
        assert wc_start != -1 and else_start != -1, "welcome_card_mode conditional not found"
        wc_branch = square_export_src[wc_start:else_start]
        assert ">AGE<" not in wc_branch, (
            "AGE cell must be suppressed in WC mini-grid branch — "
            "player_age is not in the Welcome Card context."
        )

    def test_sq_wc_04_gender_cell_absent_in_wc_grid_branch(self, square_export_src):
        """GENDER cell must not appear inside the WC mini-grid branch."""
        wc_start   = square_export_src.find("{% if welcome_card_mode %}")
        else_start = square_export_src.find("{% else %}", wc_start)
        wc_branch  = square_export_src[wc_start:else_start]
        assert ">GENDER<" not in wc_branch, (
            "GENDER cell must be suppressed in WC mini-grid branch — "
            "player_gender is not in the Welcome Card context."
        )

    def test_sq_wc_05_wc_branch_has_nat_group_height_weight(self, square_export_src):
        """WC mini-grid branch must contain exactly NAT, GROUP, HEIGHT, WEIGHT cells."""
        wc_start   = square_export_src.find("{% if welcome_card_mode %}")
        else_start = square_export_src.find("{% else %}", wc_start)
        wc_branch  = square_export_src[wc_start:else_start]
        for label in (">NAT.<", ">GROUP<", ">HEIGHT<", ">WEIGHT<"):
            assert label in wc_branch, (
                f"{label!r} cell must appear in WC mini-grid branch."
            )

    def test_sq_wc_06_stat_strip_tier_label_in_wc_branch(self, square_export_src):
        """Stat strip WC branch must render TIER label instead of LICENSE."""
        stat_start = square_export_src.find("<!-- Stat strip:")
        assert stat_start != -1, "Stat strip HTML comment not found in square template"
        stat_section = square_export_src[stat_start:stat_start + 900]
        assert "TIER" in stat_section, (
            "WC stat strip must render 'TIER' label in welcome_card_mode branch."
        )

    def test_sq_wc_06_stat_strip_sa_value_in_wc_branch(self, square_export_src):
        """Stat strip WC branch must render SA value instead of Lv. N."""
        stat_start   = square_export_src.find("<!-- Stat strip:")
        stat_section = square_export_src[stat_start:stat_start + 900]
        assert ">SA<" in stat_section, (
            "WC stat strip must render '>SA<' value in welcome_card_mode branch."
        )

    def test_sq_wc_07_stat_strip_license_preserved_in_non_wc_branch(self, square_export_src):
        """Non-WC stat strip branch must still render LICENSE / Lv. X logic unchanged."""
        stat_start   = square_export_src.find("<!-- Stat strip:")
        stat_section = square_export_src[stat_start:stat_start + 1200]
        assert "LICENSE" in stat_section, (
            "Non-WC stat strip branch must retain the LICENSE label."
        )
        assert "license_current_level or 1" in stat_section, (
            "Non-WC stat strip branch must retain license_current_level or 1 fallback."
        )

    def test_sq_wc_08_photo_monogram_class_defined_in_css(self, square_export_src):
        """CSS class ex-photo-monogram must be defined in square template."""
        assert ".ex-photo-monogram" in square_export_src, (
            "ex-photo-monogram CSS class must be defined for the no-photo circle badge."
        )

    def test_sq_wc_08_photo_monogram_used_in_placeholder_html(self, square_export_src):
        """ex-photo-monogram inner div must appear inside the ex-photo-placeholder block."""
        ph_idx       = square_export_src.find("ex-photo-placeholder")
        monogram_idx = square_export_src.find("ex-photo-monogram", ph_idx)
        assert ph_idx != -1, "ex-photo-placeholder not found in square template"
        assert monogram_idx != -1, (
            "ex-photo-monogram must appear after ex-photo-placeholder "
            "as the inner element of the no-photo fallback."
        )


class TestLandscapeExportP0Fix:
    """
    P0 hotfix: export/landscape/fifa.html must not render bare 'Lv.' when
    license_current_level is absent from the context (Welcome Card flow).

    LS-P0-01: bare {{ license_current_level }} pattern removed — was broken render
    LS-P0-02: fallback renders 'Lv. —' not 'Lv. ' when value is absent
    LS-P0-03: max-level suffix guarded to prevent 'Lv. — / None' edge case
    """

    def test_ls_p0_01_bare_license_pattern_removed(self, landscape_export_src):
        """Bare Lv. {{ license_current_level }} without fallback must not exist."""
        assert "Lv. {{ license_current_level }}" not in landscape_export_src, (
            "Broken P0: 'Lv. {{ license_current_level }}' renders 'Lv. ' (empty) "
            "when license_current_level is not in the Welcome Card context. "
            "Must use a fallback."
        )

    def test_ls_p0_02_license_has_or_dash_fallback(self, landscape_export_src):
        """License value must fall back to '—' when license_current_level is absent."""
        assert 'license_current_level or "—"' in landscape_export_src, (
            "License row must use license_current_level or '—' to produce "
            "'Lv. —' instead of 'Lv. ' in Welcome Card context."
        )

    def test_ls_p0_03_max_level_suffix_guarded_by_current_level(self, landscape_export_src):
        """Max-level suffix must only render when license_current_level is truthy."""
        # The fix wraps the max-level condition with `license_current_level and ...`
        assert "license_current_level and license_max_level" in landscape_export_src, (
            "Max-level suffix must be guarded: if license_current_level is '—', "
            "the '/ max_level' suffix must not render."
        )


# ── WC-40/41: native_export_mode explicit False (Phase 4C) ────────────────────

class TestWelcomeCardNativeExportMode:
    """
    Phase 4C: _build_welcome_card_context() must explicitly set native_export_mode=False.

    Welcome Card never uses the native screenshot capture path — the key must be
    present and False so FIFA base templates that reference it get a defined value.
    """

    def _ctx(self, export: bool) -> dict:
        return _build_welcome_card_context(
            request=_req(),
            user=_user(),
            license=_license(),
            platform=None,
            export=export,
        )

    def test_wc40_native_export_mode_false_in_gallery_context(self):
        """WC-40: native_export_mode is explicitly False in non-export (gallery) context."""
        ctx = self._ctx(export=False)
        assert "native_export_mode" in ctx, (
            "native_export_mode key must be present in WC context"
        )
        assert ctx["native_export_mode"] is False

    def test_wc41_native_export_mode_false_in_export_context(self):
        """WC-41: native_export_mode is explicitly False when export=True."""
        ctx = self._ctx(export=True)
        assert "native_export_mode" in ctx, (
            "native_export_mode key must be present in WC export context"
        )
        assert ctx["native_export_mode"] is False


# ── 14. Phase 4E — Hero Welcome Card templates ────────────────────────────────
#
# Tests cover:
#   WC-4E-CTX  — context builder: welcome_heading, welcome_subtitle, category_averages
#   WC-4E-RT   — template routing: _select_welcome_card_template()
#   WC-4E-RND  — Jinja2 render: content contract per archetype
#   WC-4E-NEG  — negative: no EVENTS / XP / LICENSE / Football Skills panel title
#   WC-4E-REG  — regression: Player Card export routing unchanged

_WELCOME_TPL_DIR = pathlib.Path(__file__).resolve().parents[4] / "app" / "templates"


def _render_welcome(archetype: str, platform: str) -> str:
    """Render the given Welcome Card archetype with a real Jinja2 environment."""
    from jinja2 import Environment, FileSystemLoader
    from app.utils.country_codes import register_filters as _rf
    env = Environment(
        loader=FileSystemLoader(str(_WELCOME_TPL_DIR)),
        autoescape=True,
    )
    _rf(env)
    ctx = _build_welcome_card_context(_req(), _user(), _license(), platform, False)
    return env.get_template(f"public/export/welcome/{archetype}.html").render(**ctx)


class TestWelcomeCardPhase4E:
    """
    Phase 4E — Hero Welcome Card template suite.

    Context, routing, render, negative, and regression checks.
    """

    # ── WC-4E-CTX: context builder ────────────────────────────────────────────

    def test_ctx01_welcome_heading(self):
        """WC-4E-CTX-01: welcome_heading must be 'WELCOME TO LFA'."""
        ctx = _build_welcome_card_context(_req(), _user(), _license(), None, False)
        assert ctx["welcome_heading"] == "WELCOME TO LFA"

    def test_ctx02_welcome_subtitle(self):
        """WC-4E-CTX-02: welcome_subtitle must be 'Welcome to Lion Football Academy'."""
        ctx = _build_welcome_card_context(_req(), _user(), _license(), None, False)
        assert ctx["welcome_subtitle"] == "Welcome to Lion Football Academy"

    def test_ctx03_category_averages_has_four_items(self):
        """WC-4E-CTX-03: category_averages must contain exactly 4 entries."""
        ctx = _build_welcome_card_context(_req(), _user(), _license(), None, False)
        assert len(ctx["category_averages"]) == 4, (
            f"Expected 4 category_averages, got {len(ctx['category_averages'])}"
        )

    def test_ctx04_category_averages_sa_based(self):
        """WC-4E-CTX-04: category avg values are computed from self_assessment values."""
        from app.skills_config import get_all_skill_keys
        sa_val = 80.0
        skills = {k: {"self_assessment": sa_val, "current_level": 50.0}
                  for k in get_all_skill_keys()}
        lic = _license(football_skills=skills)
        ctx = _build_welcome_card_context(_req(), _user(), lic, None, False)
        for entry in ctx["category_averages"]:
            assert entry["avg"] == 80, (
                f"Category {entry['key']}: expected avg=80 (from SA=80.0), "
                f"got {entry['avg']}"
            )

    def test_ctx05_category_averages_have_required_keys(self):
        """WC-4E-CTX-05: each category_averages entry has key, name, emoji, avg."""
        ctx = _build_welcome_card_context(_req(), _user(), _license(), None, False)
        for entry in ctx["category_averages"]:
            assert "key"   in entry, f"Missing 'key' in {entry}"
            assert "name"  in entry, f"Missing 'name' in {entry}"
            assert "emoji" in entry, f"Missing 'emoji' in {entry}"
            assert "avg"   in entry, f"Missing 'avg' in {entry}"
            assert isinstance(entry["avg"], int), (
                f"avg must be int (rounded), got {type(entry['avg'])}"
            )

    def test_ctx06_category_avg_is_deterministic(self):
        """WC-4E-CTX-06: same skills → same category averages on repeated calls."""
        ctx_a = _build_welcome_card_context(_req(), _user(), _license(), None, False)
        ctx_b = _build_welcome_card_context(_req(), _user(), _license(), None, False)
        assert ctx_a["category_averages"] == ctx_b["category_averages"]

    # ── WC-4E-RT: template routing (Dir C Tier 1) ────────────────────────────
    # Dir C layouts are on disk → Tier 1 always wins; legacy archetypes are
    # never reached for the platforms listed in WC_PLATFORM_LAYOUT.

    def test_rt01_square_routes_to_panel(self):
        """WC-4E-RT-01: instagram_square → welcome/panel.html (Dir C)."""
        from app.api.web_routes.profile import _select_welcome_card_template
        assert _select_welcome_card_template("instagram_square", False) == \
               "public/export/welcome/panel.html"

    def test_rt02_facebook_square_routes_to_full_bleed(self):
        """WC-4E-RT-02: facebook_square → welcome/full_bleed.html (Dir C)."""
        from app.api.web_routes.profile import _select_welcome_card_template
        assert _select_welcome_card_template("facebook_square", False) == \
               "public/export/welcome/full_bleed.html"

    def test_rt03_story_routes_to_panel(self):
        """WC-4E-RT-03: instagram_story → welcome/panel.html (Dir C)."""
        from app.api.web_routes.profile import _select_welcome_card_template
        assert _select_welcome_card_template("instagram_story", False) == \
               "public/export/welcome/panel.html"

    def test_rt04_tiktok_routes_to_cinematic(self):
        """WC-4E-RT-04: tiktok → welcome/cinematic.html (Dir C)."""
        from app.api.web_routes.profile import _select_welcome_card_template
        assert _select_welcome_card_template("tiktok", False) == \
               "public/export/welcome/cinematic.html"

    def test_rt05_portrait_routes_to_panel(self):
        """WC-4E-RT-05: instagram_portrait → welcome/panel.html (Dir C)."""
        from app.api.web_routes.profile import _select_welcome_card_template
        assert _select_welcome_card_template("instagram_portrait", False) == \
               "public/export/welcome/panel.html"

    def test_rt06_landscape_routes_to_split(self):
        """WC-4E-RT-06: facebook_landscape → welcome/split.html (Dir C)."""
        from app.api.web_routes.profile import _select_welcome_card_template
        assert _select_welcome_card_template("facebook_landscape", False) == \
               "public/export/welcome/split.html"

    def test_rt07_banner_routes_to_banner(self):
        """WC-4E-RT-07: banner_custom → welcome/banner.html (Dir C)."""
        from app.api.web_routes.profile import _select_welcome_card_template
        assert _select_welcome_card_template("banner_custom", False) == \
               "public/export/welcome/banner.html"

    def test_rt08_no_platform_returns_preview_fallback(self):
        """WC-4E-RT-08: None platform → FIFA preview fallback (gallery hub)."""
        from app.api.web_routes.profile import _select_welcome_card_template
        assert _select_welcome_card_template(None, False) == \
               "public/player_card_fifa.html"

    # ── WC-4E-RND: render — content contract ──────────────────────────────────

    def test_rnd01_square_has_welcome_heading(self):
        """WC-4E-RND-01: square render contains 'WELCOME TO LFA'."""
        html = _render_welcome("square", "instagram_square")
        assert "WELCOME TO LFA" in html

    def test_rnd02_square_has_subtitle(self):
        """WC-4E-RND-02: square render contains 'Lion Football Academy'."""
        html = _render_welcome("square", "instagram_square")
        assert "Lion Football Academy" in html

    def test_rnd03_square_has_lfa_logo(self):
        """WC-4E-RND-03: square render contains the LFA logo URL."""
        html = _render_welcome("square", "instagram_square")
        assert "logo-wc.png" in html

    def test_rnd04_square_has_four_category_badges(self):
        """WC-4E-RND-04: square render has 4 wc-cat-avg elements."""
        html = _render_welcome("square", "instagram_square")
        assert html.count("wc-cat-avg") >= 4

    def test_rnd05_square_photo_fallback_renders(self):
        """WC-4E-RND-05: square render with no photo renders initials fallback."""
        html = _render_welcome("square", "instagram_square")
        assert "wc-initials" in html or "wc-photo" in html

    def test_rnd06_vertical_has_welcome_heading(self):
        """WC-4E-RND-06: vertical (tiktok) render contains 'WELCOME TO LFA'."""
        html = _render_welcome("vertical", "tiktok")
        assert "WELCOME TO LFA" in html

    def test_rnd07_vertical_has_lfa_logo(self):
        """WC-4E-RND-07: vertical render contains the LFA logo URL."""
        html = _render_welcome("vertical", "tiktok")
        assert "logo-wc.png" in html

    def test_rnd08_vertical_has_four_category_badges(self):
        """WC-4E-RND-08: vertical render has 4 wc-cat-avg elements (2×2 grid)."""
        html = _render_welcome("vertical", "tiktok")
        assert html.count("wc-cat-avg") >= 4

    def test_rnd09_horizontal_has_welcome_heading(self):
        """WC-4E-RND-09: horizontal (landscape) render contains 'WELCOME TO LFA'."""
        html = _render_welcome("horizontal", "facebook_landscape")
        assert "WELCOME TO LFA" in html

    def test_rnd10_horizontal_has_lfa_logo(self):
        """WC-4E-RND-10: horizontal render contains the LFA logo URL."""
        html = _render_welcome("horizontal", "facebook_landscape")
        assert "logo-wc.png" in html

    def test_rnd11_horizontal_has_four_category_badges(self):
        """WC-4E-RND-11: horizontal render has 4 wc-cat-avg elements (4-in-a-row)."""
        html = _render_welcome("horizontal", "facebook_landscape")
        assert html.count("wc-cat-avg") >= 4

    def test_rnd12_horizontal_banner_has_welcome_heading(self):
        """WC-4E-RND-12: horizontal (banner_custom) render contains 'WELCOME TO LFA'."""
        html = _render_welcome("horizontal", "banner_custom")
        assert "WELCOME TO LFA" in html

    # ── WC-4E-NEG: no forbidden content ──────────────────────────────────────

    def _assert_no_forbidden(self, html: str, archetype: str) -> None:
        """Assert none of the forbidden Player Card elements appear in the render."""
        assert "EVENTS" not in html, \
            f"{archetype}: 'EVENTS' must not appear in Welcome Card render"
        assert "✦" not in html, \
            f"{archetype}: '✦' (XP) must not appear in Welcome Card render"
        assert "LICENSE" not in html, \
            f"{archetype}: 'LICENSE' must not appear in Welcome Card render"
        assert "Lv." not in html, \
            f"{archetype}: 'Lv.' (license level) must not appear in Welcome Card render"
        assert "Football Skills —" not in html, \
            f"{archetype}: 'Football Skills —' Player Card title must not appear in Welcome Card"

    def test_neg01_square_no_forbidden_content(self):
        """WC-4E-NEG-01: square render has no EVENTS / XP / LICENSE bleed."""
        self._assert_no_forbidden(_render_welcome("square", "instagram_square"), "square")

    def test_neg02_vertical_no_forbidden_content(self):
        """WC-4E-NEG-02: vertical render has no EVENTS / XP / LICENSE bleed."""
        self._assert_no_forbidden(_render_welcome("vertical", "instagram_story"), "vertical")

    def test_neg03_horizontal_no_forbidden_content(self):
        """WC-4E-NEG-03: horizontal render has no EVENTS / XP / LICENSE bleed."""
        self._assert_no_forbidden(_render_welcome("horizontal", "facebook_landscape"), "horizontal")

    def test_neg04_banner_no_forbidden_content(self):
        """WC-4E-NEG-04: banner render has no EVENTS / XP / LICENSE bleed."""
        self._assert_no_forbidden(_render_welcome("horizontal", "banner_custom"), "banner_custom")

    # ── WC-4E-REG: Player Card routing regression ─────────────────────────────

    def test_reg01_pc_export_route_unchanged(self):
        """WC-4E-REG-01: Player Card export templates still routed via old bucket logic."""
        from app.services.card_constants import EXPORT_FORMAT_BUCKETS
        # Verify the PC bucket mapping is intact — WC routing must not have touched it
        assert EXPORT_FORMAT_BUCKETS["instagram_square"] == "square"
        assert EXPORT_FORMAT_BUCKETS["tiktok"]           == "tiktok"
        assert EXPORT_FORMAT_BUCKETS["banner_custom"]    == "banner"
        assert EXPORT_FORMAT_BUCKETS["facebook_landscape"] == "landscape"

    def test_reg02_existing_wc_api_tests_unchanged(self):
        """WC-4E-REG-02: WC context still has welcome_card_mode=True (no regression)."""
        ctx = _build_welcome_card_context(_req(), _user(), _license(), None, False)
        assert ctx["welcome_card_mode"] is True

    def test_reg03_wc_sponsor_logo_still_none(self):
        """WC-4E-REG-03: sponsor_logo_url still None on WC context."""
        ctx = _build_welcome_card_context(_req(), _user(), _license(), None, False)
        assert ctx["sponsor_logo_url"] is None


# ── 13. Landscape BUG-1/2 render guards + BUG-3 age_group + platform regression ─

_LANDSCAPE_TPL_DIR = (
    pathlib.Path(__file__).resolve().parents[4] / "app" / "templates"
)


def _render_landscape(ctx: dict) -> str:
    """Render public/export/landscape/fifa.html with a real Jinja2 environment."""
    from jinja2 import Environment, FileSystemLoader
    from app.utils.country_codes import register_filters as _rf
    env = Environment(
        loader=FileSystemLoader(str(_LANDSCAPE_TPL_DIR)),
        autoescape=True,
    )
    _rf(env)
    return env.get_template("public/export/landscape/fifa.html").render(**ctx)


def _wc_landscape_ctx() -> dict:
    """WC context for landscape: built via _build_welcome_card_context with DOB=None."""
    user = _user()
    user.date_of_birth = None
    return _build_welcome_card_context(_req(), user, _license(), "facebook_landscape", False)


def _pc_landscape_ctx() -> dict:
    """PC-equivalent landscape context: welcome_card_mode=False + license/XP keys."""
    ctx = _wc_landscape_ctx()
    ctx["welcome_card_mode"]     = False
    ctx["license_current_level"] = 5
    ctx["license_max_level"]     = 10
    ctx["xp_balance"]            = 1234
    return ctx


class TestLandscapeBugFix:
    """
    BUG-1/2/3 regression tests for landscape/fifa.html and _build_welcome_card_context.

    LS-BUG-01: WC landscape render must NOT contain the License row
    LS-BUG-02: WC landscape render must NOT contain the XP row (✦)
    LS-BUG-03: PC landscape render must STILL contain License and XP rows (regression guard)
    LS-BUG-04: WC context age_group = AMATEUR when date_of_birth is None
    LS-BUG-05: WC context age_group = YOUTH for a 10-year-old
    LS-BUG-06: WC context age_group = PRE for a 5-year-old
    LS-BUG-07: WC context age_group = AMATEUR for a 25-year-old
    LS-BUG-08: welcome_card_mode=True present on all non-landscape WC platforms
    """

    # ── LS-BUG-01: License row absent in WC landscape ─────────────────────────

    def test_ls_bug_01_wc_landscape_license_row_absent(self):
        """BUG-1: Welcome Card landscape must not render the 'License' profile row."""
        html = _render_landscape(_wc_landscape_ctx())
        assert ">License<" not in html, (
            "Welcome Card landscape must not render the License row. "
            "Guard condition {% if not welcome_card_mode %} must wrap License div."
        )

    # ── LS-BUG-02: XP row absent in WC landscape ──────────────────────────────

    def test_ls_bug_02_wc_landscape_xp_row_absent(self):
        """BUG-2: Welcome Card landscape must not render the XP / ✦ row."""
        html = _render_landscape(_wc_landscape_ctx())
        assert "✦" not in html, (
            "Welcome Card landscape must not render the XP row (contains ✦). "
            "Guard condition {% if not welcome_card_mode %} must wrap XP div."
        )

    # ── LS-BUG-03: Regression — PC landscape still has License + XP ───────────

    def test_ls_bug_03_pc_landscape_license_and_xp_present(self):
        """Regression: Player Card landscape must still render License and XP rows."""
        html = _render_landscape(_pc_landscape_ctx())
        assert "Lv." in html, (
            "Player Card landscape must still render the License row ('Lv.')."
        )
        assert "✦" in html, (
            "Player Card landscape must still render the XP row ('✦')."
        )

    # ── LS-BUG-04..07: age_group from date_of_birth ───────────────────────────

    def test_ls_bug_04_age_group_amateur_when_no_dob(self):
        """BUG-3: No DOB → age_group defaults to AMATEUR."""
        user = _user()
        user.date_of_birth = None
        ctx = _build_welcome_card_context(_req(), user, _license(), None, False)
        assert ctx["player"].age_group == "AMATEUR"

    def test_ls_bug_05_age_group_youth_for_age_10(self):
        """BUG-3: DOB → age 10 → age_group = YOUTH."""
        from datetime import date
        user = _user()
        today = date.today()
        user.date_of_birth = date(today.year - 10, today.month, today.day)
        ctx = _build_welcome_card_context(_req(), user, _license(), None, False)
        assert ctx["player"].age_group == "YOUTH"

    def test_ls_bug_06_age_group_pre_for_age_5(self):
        """BUG-3: DOB → age 5 → age_group = PRE."""
        from datetime import date
        user = _user()
        today = date.today()
        user.date_of_birth = date(today.year - 5, today.month, today.day)
        ctx = _build_welcome_card_context(_req(), user, _license(), None, False)
        assert ctx["player"].age_group == "PRE"

    def test_ls_bug_07_age_group_amateur_for_age_25(self):
        """BUG-3: DOB → age 25 → age_group = AMATEUR."""
        from datetime import date
        user = _user()
        today = date.today()
        user.date_of_birth = date(today.year - 25, today.month, today.day)
        ctx = _build_welcome_card_context(_req(), user, _license(), None, False)
        assert ctx["player"].age_group == "AMATEUR"

    # ── LS-BUG-08: other WC platforms still have welcome_card_mode=True ───────

    def test_ls_bug_08_square_welcome_card_mode_true(self):
        """Regression: WC context for square still has welcome_card_mode=True."""
        _, ctx = TestWelcomeCardFifaRoute()._call_with_platform("instagram_square")
        assert ctx.get("welcome_card_mode") is True

    def test_ls_bug_08_story_welcome_card_mode_true(self):
        """Regression: WC context for story still has welcome_card_mode=True."""
        _, ctx = TestWelcomeCardFifaRoute()._call_with_platform("instagram_story")
        assert ctx.get("welcome_card_mode") is True

    def test_ls_bug_08_tiktok_welcome_card_mode_true(self):
        """Regression: WC context for tiktok still has welcome_card_mode=True."""
        _, ctx = TestWelcomeCardFifaRoute()._call_with_platform("tiktok")
        assert ctx.get("welcome_card_mode") is True

    def test_ls_bug_08_banner_welcome_card_mode_true(self):
        """Regression: WC context for banner still has welcome_card_mode=True."""
        _, ctx = TestWelcomeCardFifaRoute()._call_with_platform("linkedin_banner")
        assert ctx.get("welcome_card_mode") is True


# ── Nickname toggle ────────────────────────────────────────────────────────────

class TestWelcomeCardNicknameToggle:
    """
    NC-01..NC-08: use_nickname query param resolves player.name in context builder.

    All tests call _build_welcome_card_context() directly to stay isolated from
    route-level concerns (auth, DB, rate limiting).
    """

    def _ctx(self, use_nickname=False, name="John Doe", nickname="JohnnyD"):
        u = _user()
        u.name     = name
        u.nickname = nickname
        return _build_welcome_card_context(
            _req(), u, _license(), "instagram_square", False, use_nickname
        )

    # NC-01
    def test_default_uses_real_name(self):
        """Without use_nickname, player.name equals user.name."""
        ctx = self._ctx(use_nickname=False, name="John Doe", nickname="JohnnyD")
        assert ctx["player"].name == "John Doe"

    # NC-02
    def test_use_nickname_true_shows_nickname(self):
        """use_nickname=True + nickname present → player.name equals nickname."""
        ctx = self._ctx(use_nickname=True, name="John Doe", nickname="JohnnyD")
        assert ctx["player"].name == "JohnnyD"

    # NC-03
    def test_use_nickname_true_falls_back_when_nickname_none(self):
        """use_nickname=True but nickname is None → player.name falls back to real name."""
        u = _user()
        u.name     = "John Doe"
        u.nickname = None
        ctx = _build_welcome_card_context(
            _req(), u, _license(), "instagram_square", False, True
        )
        assert ctx["player"].name == "John Doe"

    # NC-04
    def test_use_nickname_true_falls_back_when_nickname_empty(self):
        """use_nickname=True but nickname is empty string → player.name falls back to real name."""
        u = _user()
        u.name     = "John Doe"
        u.nickname = ""
        ctx = _build_welcome_card_context(
            _req(), u, _license(), "instagram_square", False, True
        )
        assert ctx["player"].name == "John Doe"

    # NC-05
    def test_initials_follow_resolved_name(self):
        """Initials are derived from whichever name wins (nickname or real name)."""
        ctx_real = self._ctx(use_nickname=False, name="John Doe",    nickname="XZ")
        ctx_nick = self._ctx(use_nickname=True,  name="John Doe",    nickname="XZ")
        assert ctx_real["initials"] == "JD"
        assert ctx_nick["initials"] == "X"

    # NC-06
    def test_gallery_context_exposes_use_nickname(self):
        """Gallery hub context must include use_nickname so the template can set toggle state."""
        db = _mock_db(license_return=_license())
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(
                _req(), platform=None, use_nickname=False, db=db, user=_user()
            ))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert "use_nickname" in ctx
        assert ctx["use_nickname"] is False

    # NC-07
    def test_gallery_context_use_nickname_true_when_param_set(self):
        """Gallery hub reflects use_nickname=True when query param is present."""
        db = _mock_db(license_return=_license())
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(
                _req(), platform=None, use_nickname=True, db=db, user=_user()
            ))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx["use_nickname"] is True

    # NC-08
    def test_display_name_in_context_matches_player_name(self):
        """display_name key (used in page title) must equal player.name."""
        ctx = self._ctx(use_nickname=True, name="John Doe", nickname="JohnnyD")
        assert ctx["display_name"] == ctx["player"].name == "JohnnyD"


# ── 9. WC photo separation (Option A) ─────────────────────────────────────────

class TestWCPhotoSeparation:
    """Welcome Card photo fields are fully independent from Player Card fields.

    Fallback chain (per profile.py _build_welcome_card_context):
      photo_url          = wc_photo_url  or player_card_photo_url
      portrait_photo_url = wc_photo_portrait_url or wc_photo_url
                           or card_photo_portrait_url or player_card_photo_url
      landscape_photo_url= wc_photo_landscape_url or wc_photo_url
                           or card_photo_landscape_url or player_card_photo_url
    """

    def _ctx(self, **lic_overrides):
        lic = _license()
        for k, v in lic_overrides.items():
            setattr(lic, k, v)
        return _build_welcome_card_context(
            _req(), _user(), lic, "instagram_square", export=False, use_nickname=False
        )

    # PS-01: WC-specific photo used when set
    def test_wc_photo_url_takes_priority_over_pc_photo(self):
        ctx = self._ctx(wc_photo_url="/wc.png", player_card_photo_url="/pc.png")
        assert ctx["photo_url"] == "/wc.png"

    # PS-02: fallback to PC photo when WC photo not set
    def test_photo_falls_back_to_pc_when_wc_null(self):
        ctx = self._ctx(wc_photo_url=None, player_card_photo_url="/pc.png")
        assert ctx["photo_url"] == "/pc.png"

    # PS-03: both null → photo_url is falsy
    def test_photo_is_none_when_both_null(self):
        ctx = self._ctx(wc_photo_url=None, player_card_photo_url=None)
        assert not ctx["photo_url"]

    # PS-04: WC portrait takes priority over everything
    def test_wc_portrait_takes_priority(self):
        ctx = self._ctx(
            wc_photo_portrait_url="/wc_p.png",
            wc_photo_url="/wc.png",
            card_photo_portrait_url="/pc_p.png",
            player_card_photo_url="/pc.png",
        )
        assert ctx["portrait_photo_url"] == "/wc_p.png"

    # PS-05: WC base photo is second priority for portrait when portrait variant absent
    def test_portrait_falls_back_to_wc_base_when_wc_portrait_null(self):
        ctx = self._ctx(
            wc_photo_portrait_url=None,
            wc_photo_url="/wc.png",
            card_photo_portrait_url="/pc_p.png",
            player_card_photo_url="/pc.png",
        )
        assert ctx["portrait_photo_url"] == "/wc.png"

    # PS-06: portrait falls back to PC portrait when no WC photos at all
    def test_portrait_falls_back_to_pc_portrait_when_no_wc_photos(self):
        ctx = self._ctx(
            wc_photo_portrait_url=None,
            wc_photo_url=None,
            card_photo_portrait_url="/pc_p.png",
            player_card_photo_url="/pc.png",
        )
        assert ctx["portrait_photo_url"] == "/pc_p.png"

    # PS-07: portrait final fallback to PC base when all portrait variants absent
    def test_portrait_final_fallback_to_pc_base(self):
        ctx = self._ctx(
            wc_photo_portrait_url=None,
            wc_photo_url=None,
            card_photo_portrait_url=None,
            player_card_photo_url="/pc.png",
        )
        assert ctx["portrait_photo_url"] == "/pc.png"

    # PS-08: landscape chain mirrors portrait chain
    def test_landscape_falls_back_through_full_chain(self):
        # WC landscape set → used
        ctx = self._ctx(
            wc_photo_landscape_url="/wc_l.png",
            wc_photo_url="/wc.png",
            card_photo_landscape_url="/pc_l.png",
            player_card_photo_url="/pc.png",
        )
        assert ctx["landscape_photo_url"] == "/wc_l.png"

        # WC landscape absent, WC base present → WC base
        ctx = self._ctx(
            wc_photo_landscape_url=None,
            wc_photo_url="/wc.png",
            card_photo_landscape_url="/pc_l.png",
            player_card_photo_url="/pc.png",
        )
        assert ctx["landscape_photo_url"] == "/wc.png"

        # No WC photos → PC landscape
        ctx = self._ctx(
            wc_photo_landscape_url=None,
            wc_photo_url=None,
            card_photo_landscape_url="/pc_l.png",
            player_card_photo_url="/pc.png",
        )
        assert ctx["landscape_photo_url"] == "/pc_l.png"

        # All absent except PC base → PC base
        ctx = self._ctx(
            wc_photo_landscape_url=None,
            wc_photo_url=None,
            card_photo_landscape_url=None,
            player_card_photo_url="/pc.png",
        )
        assert ctx["landscape_photo_url"] == "/pc.png"

    # PS-09: PC photo unchanged when only WC photo is modified
    def test_pc_photo_fields_untouched_by_wc_context(self):
        """WC context does not read from PC fields when WC fields are present.
        The PC context (public_player.py) still uses player_card_photo_url exclusively.
        This test confirms WC context reads wc_photo_url first, not player_card_photo_url."""
        ctx = self._ctx(
            wc_photo_url="/wc_only.png",
            player_card_photo_url="/pc_only.png",
        )
        assert ctx["photo_url"] == "/wc_only.png"
        # PC photo is NOT surfaced in WC context when WC photo exists
        assert ctx["photo_url"] != "/pc_only.png"

    # PS-10: gallery hub photo_url uses WC field with fallback
    def test_gallery_hub_photo_url_uses_wc_photo(self):
        """Gallery hub route passes wc_photo_url (not player_card_photo_url) to template."""
        lic = _license()
        lic.wc_photo_url          = "/wc_gallery.png"
        lic.player_card_photo_url = "/pc_gallery.png"
        db = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(_req(), platform=None, db=db, user=_user()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx["photo_url"] == "/wc_gallery.png"

    # PS-11: gallery hub falls back to PC photo when WC photo absent
    def test_gallery_hub_photo_url_falls_back_to_pc(self):
        lic = _license()
        lic.wc_photo_url          = None
        lic.player_card_photo_url = "/pc_gallery.png"
        db = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(_req(), platform=None, db=db, user=_user()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx["photo_url"] == "/pc_gallery.png"


# ── 10. Onboarding initial photo — dual-write isolation ───────────────────────

class TestInitialPlayerPhoto:
    """POST /dashboard/initial-player-photo writes both PC and WC fields atomically.

    IP-01..IP-06: endpoint contract, field isolation, no partial state guarantee.
    """

    _BASE_DASH = "app.api.web_routes.dashboard"

    def _license(self):
        lic = MagicMock()
        lic.player_card_photo_url = None
        lic.wc_photo_url          = None
        return lic

    def _db(self, lic):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = lic
        return db

    def _upload(self, lic, content_type="image/png"):
        from app.api.web_routes.dashboard import student_upload_initial_player_photo
        from fastapi import UploadFile
        import io
        req  = MagicMock()
        user = MagicMock(); user.id = 42
        db   = self._db(lic)
        file = MagicMock(spec=UploadFile)
        file.content_type = content_type
        file.read = AsyncMock(return_value=b"fake-image-bytes")
        with patch(f"{self._BASE_DASH}.save_initial_player_photo",
                   return_value="/static/uploads/lfa_player_photos/42_orig_9999.png") as mock_save:
            result = _run(student_upload_initial_player_photo(
                req, file=file, db=db, user=user
            ))
        return result, lic, mock_save

    # IP-01: successful upload writes BOTH fields to the same URL
    def test_both_fields_written_on_success(self):
        lic = self._license()
        result, lic, _ = self._upload(lic)
        data = result.body if hasattr(result, "body") else {}
        import json
        d = json.loads(result.body)
        assert d["ok"] is True
        assert lic.player_card_photo_url == "/static/uploads/lfa_player_photos/42_orig_9999.png"
        assert lic.wc_photo_url          == "/static/uploads/lfa_player_photos/42_orig_9999.png"

    # IP-02: both fields get the same URL value (independent DB strings)
    def test_both_fields_hold_identical_url(self):
        lic = self._license()
        _, lic, _ = self._upload(lic)
        assert lic.player_card_photo_url == lic.wc_photo_url

    # IP-03: save_initial_player_photo is called exactly once (one file-save)
    def test_single_file_save(self):
        lic = self._license()
        _, _, mock_save = self._upload(lic)
        mock_save.assert_called_once()

    # IP-04: no license → 404, no fields written
    def test_no_license_returns_404(self):
        from app.api.web_routes.dashboard import student_upload_initial_player_photo
        from fastapi import UploadFile
        req  = MagicMock()
        user = MagicMock(); user.id = 99
        db   = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        file = MagicMock(spec=UploadFile)
        file.content_type = "image/png"
        file.read = AsyncMock(return_value=b"x")
        result = _run(student_upload_initial_player_photo(req, file=file, db=db, user=user))
        import json
        d = json.loads(result.body)
        assert d["ok"] is False
        assert result.status_code == 404

    # IP-05: ValueError from save propagates as 400, no fields written
    def test_bad_file_returns_400_no_fields_written(self):
        from app.api.web_routes.dashboard import student_upload_initial_player_photo
        from fastapi import UploadFile
        lic  = self._license()
        req  = MagicMock()
        user = MagicMock(); user.id = 42
        db   = self._db(lic)
        file = MagicMock(spec=UploadFile)
        file.content_type = "image/png"
        file.read = AsyncMock(return_value=b"bad")
        with patch(f"{self._BASE_DASH}.save_initial_player_photo",
                   side_effect=ValueError("Érvénytelen képfájl")):
            result = _run(student_upload_initial_player_photo(req, file=file, db=db, user=user))
        import json
        d = json.loads(result.body)
        assert d["ok"] is False
        assert result.status_code == 400
        # Neither field was touched
        assert lic.player_card_photo_url is None
        assert lic.wc_photo_url          is None

    # IP-06: isolation — a subsequent PC upload does not change wc_photo_url
    def test_subsequent_pc_upload_does_not_affect_wc_field(self):
        """After initial dual-write, independent PC upload must not touch wc_photo_url."""
        from app.api.web_routes.dashboard import student_upload_player_photo
        from fastapi import UploadFile
        lic = self._license()
        lic.player_card_photo_url = "/initial.png"
        lic.wc_photo_url          = "/initial.png"   # as if initial upload ran

        req  = MagicMock()
        user = MagicMock(); user.id = 42
        db   = self._db(lic)
        file = MagicMock(spec=UploadFile)
        file.content_type = "image/png"
        file.read = AsyncMock(return_value=b"new-pc-photo")
        with patch(f"{self._BASE_DASH}.save_player_photo",
                   return_value="/static/uploads/lfa_player_photos/42_orig_8888.png"):
            _run(student_upload_player_photo(req, file=file, db=db, user=user))

        assert lic.player_card_photo_url == "/static/uploads/lfa_player_photos/42_orig_8888.png"
        assert lic.wc_photo_url          == "/initial.png"   # untouched


# ── Spec-nav header (SN-01..SN-07) ────────────────────────────────────────────

class TestSpecNavHeader:
    """
    SN-01..SN-07: spec_subpage_hdr.html Profile link is spec-aware and _spec_ctx()
    returns spec_profile_url + spec_profile_icon in addition to dashboard vars.
    """

    _HDR = pathlib.Path(__file__).resolve().parents[4] / \
           "app" / "templates" / "includes" / "spec_subpage_hdr.html"

    @property
    def _hdr_src(self):
        return self._HDR.read_text(encoding="utf-8")

    # SN-01: template no longer has a bare hardcoded /profile href
    def test_sn01_profile_link_not_hardcoded(self):
        """Profile anchor must use {{ _purl }} — no literal href='/profile'."""
        assert 'href="/profile"' not in self._hdr_src

    # SN-02: template renders _purl variable in the profile anchor
    def test_sn02_profile_link_uses_purl_variable(self):
        """Profile anchor must reference the {{ _purl }} Jinja2 variable."""
        assert "{{ _purl }}" in self._hdr_src

    # SN-03: template has the spec_profile_url derivation block
    def test_sn03_template_has_spec_profile_url_guard(self):
        """Header template must check spec_profile_url context var before falling back."""
        assert "spec_profile_url" in self._hdr_src

    # SN-04: _spec_ctx returns spec_profile_url for LFA_FOOTBALL_PLAYER
    def test_sn04_spec_ctx_returns_profile_url_for_lfa(self):
        from app.api.web_routes.student_features import _spec_ctx
        user = MagicMock()
        user.specialization = MagicMock()
        user.specialization.value = "LFA_FOOTBALL_PLAYER"
        ctx = _spec_ctx(user)
        assert ctx["spec_profile_url"] == "/profile/lfa-football-player"

    # SN-05: _spec_ctx returns spec_profile_icon for LFA_FOOTBALL_PLAYER
    def test_sn05_spec_ctx_returns_profile_icon_for_lfa(self):
        from app.api.web_routes.student_features import _spec_ctx
        user = MagicMock()
        user.specialization = MagicMock()
        user.specialization.value = "LFA_FOOTBALL_PLAYER"
        ctx = _spec_ctx(user)
        assert ctx["spec_profile_icon"] == "🪪"

    # SN-06: _spec_ctx falls back to /profile + 👤 for unknown spec
    def test_sn06_spec_ctx_profile_fallback_for_unknown_spec(self):
        from app.api.web_routes.student_features import _spec_ctx
        user = MagicMock()
        user.specialization = None
        ctx = _spec_ctx(user)
        assert ctx["spec_profile_url"] == "/profile"
        assert ctx["spec_profile_icon"] == "👤"

    # SN-07: gallery route context includes spec_profile_url
    def test_sn07_gallery_context_has_spec_profile_url(self):
        """Gallery hub context must pass spec_profile_url for spec_subpage_hdr.html."""
        lic = _license()
        db  = _mock_db(license_return=lic)
        with patch(f"{_BASE}.templates") as mock_tmpl:
            mock_tmpl.TemplateResponse.return_value = MagicMock()
            _run(onboarding_welcome_card(_req(), platform=None, db=db, user=_user()))
        _, ctx = mock_tmpl.TemplateResponse.call_args.args
        assert ctx.get("spec_profile_url") == "/profile/lfa-football-player"
        assert ctx.get("spec_profile_icon") == "🪪"
