"""
PCG_ — Player Card Gallery Hub tests.

Routes under test:
  GET /players/{uid}/card              (no params)   → gallery hub
  GET /players/{uid}/card?export=1                   → NOT gallery hub (Playwright path)
  GET /players/{uid}/card?platform=X                 → NOT gallery hub (direct platform render)

PCG-01  No-platform, no-export → gallery hub template returned
PCG-02  Gallery context includes player_name, user_id, overall, tier_label, tier_color, initials
PCG-03  Gallery context default_platform is "instagram_portrait"
PCG-04  Gallery context platforms list has 7 entries (CARD_GALLERY_PLATFORM_IDS length)
PCG-05  Gallery context canvas_sizes covers all CANVAS_SIZES keys
PCG-06  Gallery context default_iframe_src contains platform=instagram_portrait&export=1
PCG-07  export=True with no platform bypasses gallery, falls through to card render
PCG-08  platform=instagram_portrait bypasses gallery, falls through to card render
PCG-09  Gallery template source: pcg-frame-wrap iframe present
PCG-10  Gallery template source: pcg-dl-btn download links iterate over platforms
PCG-11  Gallery template source: scaleIframe JS function present
PCG-12  Editor Jinja2 src uses instagram_portrait&export=1 when platform is default/unset
PCG-13  Editor _cardIframeSrc JS uses instagram_portrait&export=1 for default platform
PCG-14  Editor _applyIframeSize JS uses instagram_portrait canvas dimensions for default
"""
from __future__ import annotations

import pathlib
import pytest
from unittest.mock import MagicMock, patch

_BASE = "app.api.web_routes.public_player"

# ── Paths ───────────────────────────────────────────────────────────────────────
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
_GALLERY_TPL = _REPO_ROOT / "app" / "templates" / "public" / "player_card_gallery.html"
_EDITOR_TPL  = _REPO_ROOT / "app" / "templates" / "dashboard_card_editor.html"

# ── Shared mock helpers ─────────────────────────────────────────────────────────

def _make_user(uid: int = 42):
    u = MagicMock()
    u.id            = uid
    u.name          = "Test Player"
    u.email         = "test@lfa.com"
    u.nationality   = "Hungarian"
    u.is_active     = True
    u.date_of_birth = None
    u.nickname      = None
    u.age           = None
    u.gender        = None
    u.current_location = None
    u.country       = None
    u.xp_balance    = 0
    u.created_at    = None
    return u


def _make_license(public_card_platform: str | None = None):
    lic = MagicMock()
    lic.user_id                  = 42
    lic.specialization_type      = "LFA_FOOTBALL_PLAYER"
    lic.is_active                = True
    # onboarding_completed=False so get_skill_profile (lazy local import) is skipped;
    # overall defaults to 50.0 which is sufficient for gallery context tests.
    lic.onboarding_completed     = False
    lic.card_variant             = "fifa"
    lic.published_card_platform  = public_card_platform
    lic.published_card_variant   = "fifa"
    lic.published_card_theme     = "default"
    lic.motivation_scores        = {"position": "striker", "positions": ["striker"]}
    lic.player_card_photo_url    = None
    lic.card_photo_portrait_url  = None
    lic.card_photo_landscape_url = None
    lic.sponsor_logo_url         = None
    lic.card_bg_compact_url      = None
    lic.card_bg_showcase_url     = None
    lic.card_compact_photo_position = "left"
    lic.card_compact_focus_x     = 50
    lic.card_compact_focus_y     = 100
    lic.card_showcase_focus_x    = 50
    lic.card_showcase_focus_y    = 50
    lic.started_at               = None
    lic.average_motivation_score = None
    lic.current_level            = 1
    lic.max_achieved_level       = 1
    lic.right_foot_score         = 70.0
    lic.left_foot_score          = 30.0
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

class TestPlayerCardGalleryRoute:

    def test_pcg01_no_platform_returns_gallery_template(self):
        """No platform + no export must render the gallery hub template."""
        ctx = _call_route(platform=None, export=False)
        assert ctx.get("template") == "public/player_card_gallery.html"

    def test_pcg02_gallery_context_required_keys(self):
        """Gallery context must include all keys consumed by player_card_gallery.html."""
        ctx = _call_route()
        for key in ("player_name", "user_id", "overall", "tier_label", "tier_color", "initials",
                    "player", "platforms", "canvas_sizes", "default_platform", "default_iframe_src"):
            assert key in ctx, f"Gallery context missing required key: {key!r}"

    def test_pcg03_default_platform_is_instagram_portrait(self):
        ctx = _call_route()
        assert ctx.get("default_platform") == "instagram_portrait"

    def test_pcg04_platforms_list_length(self):
        """platforms list must match CARD_GALLERY_PLATFORM_IDS length."""
        from app.services.card_constants import CARD_GALLERY_PLATFORM_IDS
        ctx = _call_route()
        assert len(ctx["platforms"]) == len(CARD_GALLERY_PLATFORM_IDS)

    def test_pcg05_canvas_sizes_covers_all_canvas_sizes_keys(self):
        from app.services.card_constants import CANVAS_SIZES
        ctx = _call_route()
        assert set(ctx["canvas_sizes"].keys()) == set(CANVAS_SIZES.keys())

    def test_pcg06_default_iframe_src_contains_portrait_export(self):
        ctx = _call_route()
        src = ctx.get("default_iframe_src", "")
        assert "platform=instagram_portrait" in src
        assert "export=1" in src

    def test_pcg07_export_true_bypasses_gallery(self):
        """export=True must skip the gallery hub and render the export template."""
        ctx = _call_route(platform=None, export=True)
        assert ctx.get("template") != "public/player_card_gallery.html"

    def test_pcg08_platform_param_bypasses_gallery(self):
        """?platform=instagram_portrait must skip gallery and render the card directly."""
        ctx = _call_route(platform="instagram_portrait", export=False)
        assert ctx.get("template") != "public/player_card_gallery.html"

    def test_pcg08b_preview_param_bypasses_gallery(self):
        """?preview=fifa (draft variant param) must skip gallery and render card directly."""
        ctx = _call_route(platform=None, export=False)
        # Baseline: no preview → gallery
        assert ctx.get("template") == "public/player_card_gallery.html"

        from app.api.web_routes.public_player import public_player_card
        from fastapi import Request as _Request
        from unittest.mock import MagicMock, patch

        user_   = _make_user()
        license_ = _make_license()
        db      = _make_db(user_, license_)
        request = MagicMock(spec=_Request)
        captured2 = {}

        def _fake2(req, template, context):
            captured2["template"] = template
            captured2.update(context)
            return MagicMock(status_code=200)

        with patch(f"{_BASE}.templates") as mock_tpl:
            mock_tpl.TemplateResponse.side_effect = _fake2
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
        assert captured2.get("template") != "public/player_card_gallery.html", (
            "?preview= param must bypass the gallery hub"
        )


# ── 2. Gallery template source assertions ─────────────────────────────────────

@pytest.fixture(scope="class")
def gallery_src():
    return _GALLERY_TPL.read_text(encoding="utf-8")


class TestPlayerCardGalleryTemplate:

    @pytest.fixture(autouse=True)
    def _src(self, gallery_src):
        self._html = gallery_src

    def test_pcg09_pcg_frame_wrap_present(self):
        """Gallery template must contain the iframe wrap container."""
        assert "pcg-frame-wrap" in self._html

    def test_pcg10_dl_btn_iterates_platforms(self):
        """Gallery template must iterate platforms and render download buttons."""
        assert "{% for p in platforms %}" in self._html
        assert "pcg-dl-btn" in self._html

    def test_pcg11_scale_iframe_js_present(self):
        """Gallery template must include the scaleIframe JS function."""
        assert "scaleIframe" in self._html

    def test_pcg12_canvas_sizes_json_injected(self):
        """Gallery template must server-render CANVAS_SIZES for the JS scaler."""
        assert "canvas_sizes | tojson" in self._html or "canvas_sizes|tojson" in self._html


# ── 3. Card editor default-platform fix (P-1) ─────────────────────────────────

@pytest.fixture(scope="class")
def editor_src():
    return _EDITOR_TPL.read_text(encoding="utf-8")


class TestEditorDefaultPlatformFix:

    @pytest.fixture(autouse=True)
    def _src(self, editor_src):
        self._html = editor_src

    def test_pcg12_initial_iframe_src_uses_portrait_for_default(self):
        """When platform is default/unset, initial iframe src must use instagram_portrait&export=1."""
        # The {% else %} branch of the initial src Jinja2 must no longer render the legacy
        # base URL without platform; it must use instagram_portrait&export=1.
        assert "platform=instagram_portrait&export=1" in self._html

    def test_pcg13_card_iframe_src_js_uses_portrait_for_default(self):
        """_cardIframeSrc() for default must include platform=instagram_portrait&export=1."""
        assert "_currentPlatform === 'default'" in self._html
        idx = self._html.index("_currentPlatform === 'default'")
        # Find the first return statement after this check
        snippet = self._html[idx: idx + 300]
        assert "instagram_portrait" in snippet, (
            "_cardIframeSrc default branch must reference instagram_portrait"
        )
        assert "export=1" in snippet, (
            "_cardIframeSrc default branch must pass export=1"
        )

    def test_pcg14_apply_iframe_size_js_uses_portrait_dims_for_default(self):
        """_applyIframeSize() for default must look up instagram_portrait canvas dimensions."""
        # Find the _applyIframeSize function body
        start = self._html.find("function _applyIframeSize(")
        assert start != -1, "_applyIframeSize function must exist"
        body = self._html[start: start + 600]
        assert "instagram_portrait" in body, (
            "_applyIframeSize default branch must reference instagram_portrait canvas size"
        )
