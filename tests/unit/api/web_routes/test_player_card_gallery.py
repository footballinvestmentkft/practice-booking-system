"""
PCP_ — Player Card Public Profile tests.

Route semantics — GET /players/{uid}/card (no params):
  • Serves a clean, read-only public profile page (player_card_public.html)
  • No platform picker, no export-format panel, no PNG download buttons
  • Uses player's published_card_platform; falls back to instagram_portrait if unset/invalid

PCP-01  No platform + no export → public profile template returned
PCP-02  Public profile context includes all required keys
PCP-03  published_card_platform=None → public_platform falls back to instagram_portrait
PCP-04  published_card_platform set to valid platform → public_platform uses it
PCP-05  pub_card_w / pub_card_h match CANVAS_SIZES for public_platform
PCP-06  public_iframe_src contains platform={platform}&export=1
PCP-07  export=True with no platform bypasses public profile (Playwright path)
PCP-08  platform=X bypasses public profile (direct platform render)
PCP-08b preview=X bypasses public profile (editor draft-variant param)
PCP-09  Public profile template: pcp-card-wrap iframe container present
PCP-10  Public profile template: NO platform picker and NO download buttons
PCP-11  Public profile template: scaleCard JS function present
PCP-12  Public profile template: _PUB_CARD_W / _PUB_CARD_H injected
PCP-13  Editor initial Jinja2 iframe src uses instagram_portrait&export=1 for default platform
PCP-14  Editor _cardIframeSrc JS uses instagram_portrait&export=1 for default platform
PCP-15  Editor _applyIframeSize JS uses instagram_portrait canvas dims for default platform
"""
from __future__ import annotations

import pathlib
import pytest
from unittest.mock import MagicMock, patch

_BASE = "app.api.web_routes.public_player"

# ── Paths ───────────────────────────────────────────────────────────────────────
_REPO_ROOT  = pathlib.Path(__file__).resolve().parents[4]
_PUBLIC_TPL = _REPO_ROOT / "app" / "templates" / "public" / "player_card_public.html"
_EDITOR_TPL = _REPO_ROOT / "app" / "templates" / "dashboard_card_editor.html"

# ── Shared mock helpers ─────────────────────────────────────────────────────────

def _make_user(uid: int = 42):
    u = MagicMock()
    u.id               = uid
    u.name             = "Test Player"
    u.email            = "test@lfa.com"
    u.nationality      = "Hungarian"
    u.is_active        = True
    u.date_of_birth    = None
    u.nickname         = None
    u.age              = None
    u.gender           = None
    u.current_location = None
    u.country          = None
    u.xp_balance       = 0
    u.created_at       = None
    return u


def _make_license(public_card_platform: str | None = None):
    lic = MagicMock()
    lic.user_id                      = 42
    lic.specialization_type          = "LFA_FOOTBALL_PLAYER"
    lic.is_active                    = True
    # onboarding_completed=False → get_skill_profile (lazy local import) is skipped;
    # overall defaults to 50.0, sufficient for public profile context tests.
    lic.onboarding_completed         = False
    lic.card_variant                 = "fifa"
    lic.published_card_platform      = public_card_platform
    lic.published_card_variant       = "fifa"
    lic.published_card_theme         = "default"
    lic.motivation_scores            = {"position": "striker", "positions": ["striker"]}
    lic.player_card_photo_url        = None
    lic.card_photo_portrait_url      = None
    lic.card_photo_landscape_url     = None
    lic.sponsor_logo_url             = None
    lic.card_bg_compact_url          = None
    lic.card_bg_showcase_url         = None
    lic.card_compact_photo_position  = "left"
    lic.card_compact_focus_x         = 50
    lic.card_compact_focus_y         = 100
    lic.card_showcase_focus_x        = 50
    lic.card_showcase_focus_y        = 50
    lic.started_at                   = None
    lic.average_motivation_score     = None
    lic.current_level                = 1
    lic.max_achieved_level           = 1
    lic.right_foot_score             = 70.0
    lic.left_foot_score              = 30.0
    return lic


def _make_db(user, license_):
    from app.models.card_draft import CardDraft as _CardDraft
    db = MagicMock()
    _calls = [0]

    def _side(*args):
        _calls[0] += 1
        q = MagicMock()
        first_arg = args[0] if args else None
        if first_arg is _CardDraft:
            draft = MagicMock()
            draft.published_theme    = "default"
            draft.published_variant  = "fifa"
            draft.published_platform = license_.published_card_platform
            q.filter.return_value.first.return_value = draft
        elif _calls[0] == 1:
            q.filter.return_value.first.return_value = user
        elif _calls[0] == 2:
            q.filter.return_value.first.return_value = license_
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
            q.join.return_value.filter.return_value.order_by.return_value.all.return_value = []
            q.filter.return_value.all.return_value = []
        return q

    db.query.side_effect = _side
    return db


def _call_route(platform=None, export=False, user=None, license_=None):
    """Call public_player_card and capture the TemplateResponse call args."""
    from fastapi import Request as _Request
    from app.api.web_routes.public_player import public_player_card

    user_    = user     or _make_user()
    license_ = license_ or _make_license()
    db       = _make_db(user_, license_)
    request  = MagicMock(spec=_Request)

    captured = {}

    def _fake_response(req, template, context):
        captured["template"] = template
        captured.update(context)
        return MagicMock(status_code=200)

    with patch(f"{_BASE}.templates") as mock_tpl:
        mock_tpl.TemplateResponse.side_effect = _fake_response
        public_player_card(
            request=request,
            user_id=user_.id,
            preview=None,
            platform=platform,
            theme=None,
            export=export,
            animated=False,
            native_export=False,
            db=db,
        )
    return captured


# ── 1. Route behaviour ─────────────────────────────────────────────────────────

class TestPlayerCardPublicRoute:

    def test_pcp01_no_platform_returns_public_profile_template(self):
        """No platform + no export must render the public profile template."""
        ctx = _call_route(platform=None, export=False)
        assert ctx.get("template") == "public/player_card_public.html"

    def test_pcp02_public_profile_context_required_keys(self):
        """Public profile context must include all keys consumed by player_card_public.html."""
        ctx = _call_route()
        for key in ("player_name", "user_id", "overall", "tier_label", "tier_color",
                    "initials", "player", "public_platform", "public_iframe_src",
                    "pub_card_w", "pub_card_h"):
            assert key in ctx, f"Public profile context missing required key: {key!r}"

    def test_pcp02b_public_profile_has_no_export_gallery_keys(self):
        """Public profile context must NOT include platform picker or gallery-specific keys."""
        ctx = _call_route()
        for key in ("platforms", "canvas_sizes", "default_platform", "default_iframe_src"):
            assert key not in ctx, (
                f"Export-gallery key {key!r} must not appear on public profile — "
                "download/export UI belongs exclusively in the card editor"
            )

    def test_pcp03_fallback_to_instagram_portrait_when_platform_unset(self):
        """published_card_platform=None must resolve public_platform to instagram_portrait."""
        lic = _make_license(public_card_platform=None)
        ctx = _call_route(license_=lic)
        assert ctx.get("public_platform") == "instagram_portrait"

    def test_pcp04_published_platform_used_when_set(self):
        """A valid published_card_platform must be used as public_platform."""
        lic = _make_license(public_card_platform="instagram_story")
        ctx = _call_route(license_=lic)
        assert ctx.get("public_platform") == "instagram_story"

    def test_pcp04b_invalid_published_platform_falls_back(self):
        """An invalid/unrecognised published_card_platform must fall back to instagram_portrait."""
        lic = _make_license(public_card_platform="default")
        ctx = _call_route(license_=lic)
        assert ctx.get("public_platform") == "instagram_portrait"

    def test_pcp05_pub_card_dims_match_canvas_sizes(self):
        """pub_card_w / pub_card_h must match CANVAS_SIZES for the resolved public_platform."""
        from app.services.card_constants import CANVAS_SIZES
        ctx = _call_route()
        pid = ctx.get("public_platform")
        expected_w, expected_h = CANVAS_SIZES[pid]
        assert ctx.get("pub_card_w") == expected_w
        assert ctx.get("pub_card_h") == expected_h

    def test_pcp06_public_iframe_src_contains_platform_and_export(self):
        """public_iframe_src must encode platform and export=1."""
        ctx = _call_route()
        src = ctx.get("public_iframe_src", "")
        pid = ctx.get("public_platform")
        assert f"platform={pid}" in src
        assert "export=1" in src

    def test_pcp07_export_true_bypasses_public_profile(self):
        """export=True must skip the public profile and render the export template."""
        ctx = _call_route(platform=None, export=True)
        assert ctx.get("template") != "public/player_card_public.html"

    def test_pcp08_platform_param_bypasses_public_profile(self):
        """?platform=X must skip public profile and render the card directly."""
        ctx = _call_route(platform="instagram_portrait", export=False)
        assert ctx.get("template") != "public/player_card_public.html"

    def test_pcp08b_preview_param_bypasses_public_profile(self):
        """?preview=fifa (draft-variant param) must skip public profile."""
        from app.api.web_routes.public_player import public_player_card
        from fastapi import Request as _Request

        user_    = _make_user()
        license_ = _make_license()
        db       = _make_db(user_, license_)
        request  = MagicMock(spec=_Request)
        captured = {}

        def _fake(req, template, context):
            captured["template"] = template
            captured.update(context)
            return MagicMock(status_code=200)

        with patch(f"{_BASE}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.side_effect = _fake
            public_player_card(
                request=request,
                user_id=user_.id,
                preview="fifa",
                platform=None,
                theme=None,
                export=False,
                animated=False,
                native_export=False,
                db=db,
            )
        assert captured.get("template") != "public/player_card_public.html", (
            "?preview= param must bypass the public profile"
        )


# ── 2. Public profile template source assertions ───────────────────────────────

@pytest.fixture(scope="class")
def public_tpl_src():
    return _PUBLIC_TPL.read_text(encoding="utf-8")


class TestPlayerCardPublicTemplate:

    @pytest.fixture(autouse=True)
    def _src(self, public_tpl_src):
        self._html = public_tpl_src

    def test_pcp09_pcp_card_wrap_present(self):
        """Public profile template must contain the card iframe wrap container."""
        assert "pcp-card-wrap" in self._html

    def test_pcp10_no_platform_picker(self):
        """Public profile template must NOT contain a platform picker loop."""
        assert "{% for p in platforms %}" not in self._html, (
            "Platform picker loop must not appear on the public profile page"
        )

    def test_pcp10b_no_download_buttons(self):
        """Public profile template must NOT contain download buttons."""
        assert "pcg-dl-btn" not in self._html, (
            "Download buttons (pcg-dl-btn) must not appear on the public profile page"
        )

    def test_pcp11_scale_card_js_present(self):
        """Public profile template must include the scaleCard JS function for iframe scaling."""
        assert "scaleCard" in self._html

    def test_pcp12_pub_card_dims_injected(self):
        """Public profile template must inject pub_card_w / pub_card_h for the JS scaler."""
        assert "_PUB_CARD_W" in self._html
        assert "_PUB_CARD_H" in self._html


# ── 3. Card editor default-platform fix (P-1) — unchanged from prior suite ────

@pytest.fixture(scope="class")
def editor_src():
    return _EDITOR_TPL.read_text(encoding="utf-8")


class TestEditorDefaultPlatformFix:

    @pytest.fixture(autouse=True)
    def _src(self, editor_src):
        self._html = editor_src

    def test_pcp13_initial_iframe_src_uses_portrait_for_default(self):
        """When platform is default/unset, initial iframe src must use instagram_portrait&export=1."""
        assert "platform=instagram_portrait&export=1" in self._html

    def test_pcp14_card_iframe_src_js_uses_portrait_for_default(self):
        """_cardIframeSrc() for default must include platform=instagram_portrait&export=1."""
        assert "_currentPlatform === 'default'" in self._html
        idx     = self._html.index("_currentPlatform === 'default'")
        snippet = self._html[idx: idx + 300]
        assert "instagram_portrait" in snippet, (
            "_cardIframeSrc default branch must reference instagram_portrait"
        )
        assert "export=1" in snippet, (
            "_cardIframeSrc default branch must pass export=1"
        )

    def test_pcp15_apply_iframe_size_js_uses_portrait_dims_for_default(self):
        """_applyIframeSize() for default must look up instagram_portrait canvas dimensions."""
        start = self._html.find("function _applyIframeSize(")
        assert start != -1, "_applyIframeSize function must exist"
        body = self._html[start: start + 600]
        assert "instagram_portrait" in body, (
            "_applyIframeSize default branch must reference instagram_portrait canvas size"
        )
