"""
PCP_ — Player Card Public Profile tests.

Route semantics — GET /players/{uid}/card (no params):
  • Serves a clean, read-only public profile page (player_card_public.html)
  • No platform picker, no export-format panel, no PNG download buttons
  • Resolves published platform from CardDraft.published_platform (primary),
    then UserLicense.published_card_platform (legacy fallback), then "instagram_portrait"

PCP-01   No platform + no export → public profile template returned
PCP-02   Public profile context includes all required keys
PCP-02b  Public profile context has no export-gallery keys
PCP-03   Both sources None → public_platform falls back to instagram_portrait
PCP-04   CardDraft.published_platform set → public_platform uses it (primary source)
PCP-04b  "default" in either source → treated as invalid, falls back to instagram_portrait
PCP-04c  CardDraft square + license None  → square  [BUG regression guard]
PCP-04d  CardDraft square + license portrait → square (CardDraft wins)
PCP-04e  CardDraft None  + license tiktok → tiktok  (UserLicense fallback)
PCP-04f  CardDraft None  + license None   → instagram_portrait (final fallback)
PCP-05   pub_card_w / pub_card_h match CANVAS_SIZES for public_platform
PCP-06   public_iframe_src contains platform={platform}&export=1
PCP-07   export=True with no platform bypasses public profile (Playwright path)
PCP-08   platform=X bypasses public profile (direct platform render)
PCP-08b  preview=X bypasses public profile (editor draft-variant param)
PCP-09   Public profile template: pcp-card-wrap iframe container present
PCP-10   Public profile template: NO platform picker and NO download buttons
PCP-10b  Public profile template: NO download buttons
PCP-11   Public profile template: scaleCard JS function present
PCP-12   Public profile template: _PUB_CARD_W / _PUB_CARD_H injected
PCP-13   Editor initial Jinja2 iframe src uses instagram_portrait&export=1 for default platform
PCP-14   Editor _cardIframeSrc JS uses instagram_portrait&export=1 for default platform
PCP-15   Editor _applyIframeSize JS uses instagram_portrait canvas dims for default platform
PCP-16   Editor exportCard JS maps 'default' platform to 'instagram_portrait' (WYSIWYG export)
"""
from __future__ import annotations

import pathlib
import pytest
from unittest.mock import MagicMock, patch

_BASE = "app.api.web_routes.public_player"

# Sentinel: distinguishes "caller did not pass draft_published_platform" from passing None.
# When _UNSET, _make_db() seeds draft.published_platform from license_.published_card_platform
# (preserving backward compatibility with tests that don't care about the CardDraft layer).
_UNSET = object()

# ── Paths ───────────────────────────────────────────────────────────────────────
_REPO_ROOT  = pathlib.Path(__file__).resolve().parents[4]
_PUBLIC_TPL = _REPO_ROOT / "app" / "templates" / "public" / "player_card_public.html"
_EDITOR_TPL = _REPO_ROOT / "app" / "templates" / "dashboard_card_editor.html"
_FIFA_TPL   = _REPO_ROOT / "app" / "templates" / "public" / "player_card_fclassic.html"

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


def _make_db(user, license_, draft_published_platform=_UNSET):
    """Build a mock db session.

    draft_published_platform:
      _UNSET (default) → CardDraft.published_platform mirrors license_.published_card_platform
                         (backward-compatible; tests that don't care about CardDraft layer).
      None             → CardDraft.published_platform = None (CardDraft has no published state).
      str              → CardDraft.published_platform = that string.
    """
    from app.models.card_draft import CardDraft as _CardDraft
    db = MagicMock()
    _calls = [0]

    _draft_platform = (
        license_.published_card_platform
        if draft_published_platform is _UNSET
        else draft_published_platform
    )

    def _side(*args):
        _calls[0] += 1
        q = MagicMock()
        first_arg = args[0] if args else None
        if first_arg is _CardDraft:
            draft = MagicMock()
            draft.published_theme    = "default"
            draft.published_variant  = "fifa"
            draft.published_platform = _draft_platform
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


def _call_route(platform=None, export=False, native_export=False, user=None,
                license_=None, draft_published_platform=_UNSET):
    """Call public_player_card and capture the TemplateResponse call args.

    draft_published_platform: forwarded to _make_db — controls CardDraft.published_platform
    independently of license_.published_card_platform.
    native_export: mirrors the ?native_export=1 query param (default platform PNG export path).
    """
    from fastapi import Request as _Request
    from app.api.web_routes.public_player import public_player_card

    user_    = user     or _make_user()
    license_ = license_ or _make_license()
    db       = _make_db(user_, license_, draft_published_platform=draft_published_platform)
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
            native_export=native_export,
            db=db,
        )
    return captured


# ── 1. Route behaviour ─────────────────────────────────────────────────────────

class TestPlayerCardPublicRoute:

    def test_pcp01_no_platform_returns_interactive_fifa_card(self):
        """No platform + no export → bare URL serves the interactive FIFA card directly.
        Phase 2.4F: player_card_public.html iframe wrapper retired from this path."""
        ctx = _call_route(platform=None, export=False)
        assert ctx.get("template") == "public/player_card_fclassic.html", (
            "Bare URL must render the interactive FIFA card, not the old iframe wrapper"
        )

    def test_pcp02_interactive_card_context_required_keys(self):
        """Bare URL context must include keys consumed by player_card_fclassic.html."""
        ctx = _call_route()
        for key in ("player", "overall", "tier_label", "tier_color", "initials",
                    "skill_categories", "last_skill_delta", "participations_history",
                    "theme", "card_theme_id", "card_variant_id"):
            assert key in ctx, f"Interactive card context missing required key: {key!r}"

    def test_pcp02b_public_profile_has_no_export_gallery_keys(self):
        """Bare URL context must NOT include platform picker or gallery-specific keys."""
        ctx = _call_route()
        for key in ("platforms", "canvas_sizes", "default_platform", "default_iframe_src"):
            assert key not in ctx, (
                f"Export-gallery key {key!r} must not appear on the bare card URL — "
                "download/export UI belongs exclusively in the card editor"
            )

    def test_pcp03_bare_url_uses_default_platform_preset(self):
        """Bare URL with no published platform → effective_platform is None → 'default' preset.
        The export layer must NOT activate, so the interactive FIFA card is always served."""
        lic = _make_license(public_card_platform=None)
        ctx = _call_route(platform=None, export=False, license_=lic, draft_published_platform=None)
        assert ctx.get("template") == "public/player_card_fclassic.html"
        assert ctx.get("platform_id") == "default"

    def test_pcp04_explicit_export_uses_published_platform(self):
        """export=True with no URL ?platform= → effective_platform picks up published_platform."""
        lic = _make_license(public_card_platform="instagram_story")
        ctx = _call_route(export=True, license_=lic)
        assert ctx.get("platform_id") == "instagram_story"

    def test_pcp04b_bare_url_ignores_published_platform(self):
        """Bare URL must NOT activate the export layer even when published_platform is set.
        The interactive FIFA card must be served regardless of the user's published platform."""
        lic = _make_license(public_card_platform="instagram_portrait")
        ctx = _call_route(platform=None, export=False, license_=lic)
        assert ctx.get("template") == "public/player_card_fclassic.html"

    # ── Priority chain regression tests (PCP-04c/d/e/f) ──────────────────────
    # These four tests prove the correct read priority for explicit export path:
    #   CardDraft.published_platform  >  UserLicense.published_card_platform  >  None
    # The chain now applies to effective_platform when export=True (not bare URL).

    def test_pcp04c_card_draft_platform_used_when_license_has_none(self):
        """CardDraft.published_platform wins for export=True even when UserLicense is None.

        Regression guard: publish_draft() writes to card_drafts.published_platform;
        if the route reads only UserLicense, it always falls back (NULL) and ignores
        what was published.
        """
        lic = _make_license(public_card_platform=None)
        ctx = _call_route(export=True, license_=lic, draft_published_platform="instagram_square")
        assert ctx.get("platform_id") == "instagram_square", (
            "CardDraft.published_platform must be used as the primary source for export path; "
            "UserLicense being NULL must not override it"
        )

    def test_pcp04d_card_draft_wins_over_license_when_both_set(self):
        """CardDraft.published_platform takes precedence over UserLicense on export path."""
        lic = _make_license(public_card_platform="instagram_portrait")
        ctx = _call_route(export=True, license_=lic, draft_published_platform="instagram_square")
        assert ctx.get("platform_id") == "instagram_square", (
            "CardDraft must win over UserLicense even when UserLicense has a valid platform"
        )

    def test_pcp04e_license_fallback_when_draft_platform_is_none(self):
        """UserLicense.published_card_platform is used on export path when CardDraft is None."""
        lic = _make_license(public_card_platform="tiktok")
        ctx = _call_route(export=True, license_=lic, draft_published_platform=None)
        assert ctx.get("platform_id") == "tiktok", (
            "UserLicense.published_card_platform must be the fallback when CardDraft has no published platform"
        )

    def test_pcp04f_default_preset_when_both_sources_are_none_on_export(self):
        """Default preset used for export=True when both CardDraft and UserLicense are None."""
        lic = _make_license(public_card_platform=None)
        ctx = _call_route(export=True, license_=lic, draft_published_platform=None)
        assert ctx.get("platform_id") == "default", (
            "When both CardDraft and UserLicense have no published platform on export path, "
            "effective_platform is None → 'default' preset"
        )

    def test_pcp05_bare_url_context_has_last_skill_delta(self):
        """Bare URL context must include last_skill_delta (VT trend arrows rely on it)."""
        ctx = _call_route()
        assert "last_skill_delta" in ctx, (
            "last_skill_delta must be present in bare URL context for VT trend arrows"
        )

    def test_pcp06_bare_url_context_has_skill_categories(self):
        """Bare URL context must include skill_categories (FIFA card skill panel)."""
        ctx = _call_route()
        assert "skill_categories" in ctx, (
            "skill_categories must be present in bare URL context for the FIFA skill panel"
        )

    def test_pcp07_export_true_serves_export_template(self):
        """export=True must serve an export template (not the interactive FIFA card for bare URL)."""
        ctx = _call_route(platform=None, export=True)
        assert ctx.get("template") != "public/player_card_public.html"

    def test_pcp07b_native_export_serves_interactive_card(self):
        """?native_export=1 must serve the interactive FIFA card without export layer activation.

        The Playwright PNG service uses ?native_export=1 for the 'default' platform path.
        effective_platform must remain None (no published_platform inherited) so the FIFA card
        is rendered in native-export-mode — Playwright then screenshots .card-wrap.
        """
        ctx = _call_route(platform=None, export=False, native_export=True)
        assert ctx.get("template") == "public/player_card_fclassic.html", (
            "?native_export=1 must serve the interactive FIFA card so Playwright can "
            "screenshot the .card-wrap element"
        )
        assert ctx.get("native_export_mode") is True

    def test_pcp08_platform_param_uses_export_layer(self):
        """?platform=X must activate the export layer and route to the matching template."""
        ctx = _call_route(platform="instagram_portrait", export=False)
        assert ctx.get("template") != "public/player_card_public.html"
        assert ctx.get("platform_id") == "instagram_portrait"

    def test_pcp08b_preview_param_serves_variant_card(self):
        """?preview=fifa (draft-variant param) must serve a card template (not the old wrapper)."""
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
            "?preview= param must not serve the old iframe wrapper"
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

    def test_pcp13_initial_iframe_src_uses_native_export_for_default(self):
        """When platform is default/unset, initial iframe src must use native_export=1
        so the editor preview shows the interactive FIFA card with 2-decimal values."""
        assert "native_export=1" in self._html
        assert "platform=instagram_portrait&export=1" not in self._html, (
            "Editor must not use portrait export for the default platform preview — "
            "that renders integers via export_skill_rows"
        )

    def test_pcp14_card_iframe_src_js_uses_native_export_for_default(self):
        """_cardIframeSrc() for default must use native_export=1 — not instagram_portrait&export=1."""
        assert "_currentPlatform === 'default'" in self._html
        idx     = self._html.index("_currentPlatform === 'default'")
        snippet = self._html[idx: idx + 300]
        assert "native_export=1" in snippet, (
            "_cardIframeSrc default branch must use native_export=1"
        )
        assert "instagram_portrait" not in snippet, (
            "_cardIframeSrc default branch must not reference instagram_portrait"
        )

    def test_pcp15_apply_iframe_size_js_uses_default_dims(self):
        """_applyIframeSize() for default must look up 'default' canvas (820×800),
        not instagram_portrait, since the preview now shows the native FIFA card."""
        start = self._html.find("function _applyIframeSize(")
        assert start != -1, "_applyIframeSize function must exist"
        body = self._html[start: start + 600]
        assert "instagram_portrait" not in body, (
            "_applyIframeSize must not hard-code instagram_portrait for the default platform — "
            "use the 'default' key from CANVAS_SIZES (820×800)"
        )

    def test_pcp16_export_card_js_maps_default_to_instagram_portrait(self):
        """exportCard() must map _currentPlatform==='default' to instagram_portrait.

        The preview shows instagram_portrait for the default platform slot (P-1 fix).
        The export must match — otherwise the user sees Portrait but downloads FIFA Classic.
        """
        start = self._html.find("async function exportCard(")
        assert start != -1, "exportCard function must exist"
        body = self._html[start: start + 400]
        assert "_currentPlatform === 'default' ? 'instagram_portrait'" in body, (
            "exportCard must map 'default' platform to 'instagram_portrait' to match the preview"
        )


# ── 4. Card Design tab — information architecture (CE-01..CE-04) ──────────────

class TestCardDesignTabStructure:
    """CE-01..CE-04 — Design tab must present Card Layout and Color Theme as two
    distinct, clearly labelled sections.  IDs and handlers must be unchanged.
    """

    @pytest.fixture(autouse=True)
    def _src(self, editor_src):
        self._html = editor_src

    def test_ce01_card_layout_section_title_present(self):
        """Design tab must contain a 'Card Layout' section title (card_variants)."""
        assert "Card Layout" in self._html, (
            "The card_variants picker section must be labelled 'Card Layout'"
        )

    def test_ce02_color_theme_section_title_present(self):
        """Design tab must contain a 'Color Theme' section title (card_themes)."""
        assert "Color Theme" in self._html, (
            "The card_themes picker section must be labelled 'Color Theme'"
        )

    def test_ce03_old_combined_section_title_absent(self):
        """The old merged 'Card Design' section-title must no longer appear as a
        ce-section-title — it was replaced by the two distinct section headings.
        """
        assert 'class="ce-section-title">Card Design<' not in self._html, (
            "Merged 'Card Design' section-title must be replaced by 'Card Layout' "
            "and 'Color Theme' headings"
        )

    def test_ce04_picker_ids_preserved(self):
        """Both picker element IDs must survive the restructure."""
        assert 'id="variant-picker"' in self._html, (
            "variant-picker ID must be preserved"
        )
        assert 'id="theme-picker"' in self._html, (
            "theme-picker ID must be preserved"
        )

    def test_ce05_card_layout_above_color_theme(self):
        """'Card Layout' section must appear before 'Color Theme' in DOM order."""
        layout_pos = self._html.find("Card Layout")
        theme_pos  = self._html.find("Color Theme")
        assert layout_pos != -1, "Card Layout section must exist"
        assert theme_pos  != -1, "Color Theme section must exist"
        assert layout_pos < theme_pos, (
            "Card Layout section must precede Color Theme section in DOM order"
        )


# ── 5. Card Layout tile upgrade + platform grid fix (CE-06..CE-10) ────────────

class TestLayoutTileAndPlatformGridFix:
    """CE-06..CE-10 — Layout tiles must use the design-tile system;
    platform grid must use minmax(0) to prevent overflow clipping.
    """

    @pytest.fixture(autouse=True)
    def _src(self, editor_src):
        self._html = editor_src

    def test_ce06_layout_tile_css_class_present(self):
        """CSS must define .ce-layout-tile rules for per-variant art."""
        assert "ce-layout-tile" in self._html, (
            "ce-layout-tile CSS class must be defined for the Card Layout tile system"
        )

    def test_ce07_variant_picker_uses_design_tile_structure(self):
        """#variant-picker must use ce-design-tile structure, not pill buttons."""
        picker_start = self._html.find('id="variant-picker"')
        assert picker_start != -1, "variant-picker must exist"
        # The picker container must be a design-grid, not variant-strip
        ctx = self._html[picker_start - 80: picker_start + 20]
        assert "ce-design-grid" in ctx, (
            "variant-picker must be wrapped in a ce-design-grid container"
        )

    def test_ce08_layout_tile_buttons_have_design_tile_class(self):
        """Layout tile buttons must carry ce-design-tile class for visual system."""
        assert 'ce-design-tile ce-layout-tile' in self._html, (
            "Layout tile buttons must have both ce-design-tile and ce-layout-tile classes"
        )

    def test_ce09_platform_grid_uses_minmax(self):
        """Platform grid must use minmax(0, 1fr) to prevent column overflow clipping."""
        assert "minmax(0, 1fr)" in self._html, (
            "ce-platform-grid must use repeat(N, minmax(0, 1fr)) to suppress auto min-width"
        )

    def test_ce10_platform_tile_has_min_width_zero(self):
        """Platform tile must have min-width: 0 to allow grid shrinking."""
        platform_tile_start = self._html.find(".ce-platform-tile {")
        assert platform_tile_start != -1
        block = self._html[platform_tile_start: platform_tile_start + 500]
        assert "min-width: 0" in block, (
            ".ce-platform-tile must set min-width: 0 to prevent grid overflow"
        )

    def test_ce11_platform_dims_has_overflow_hidden(self):
        """Platform dims must have overflow: hidden + text-overflow: ellipsis to clip long text."""
        dims_start = self._html.find(".ce-platform-tile-dims {")
        assert dims_start != -1
        block = self._html[dims_start: dims_start + 250]
        assert "text-overflow: ellipsis" in block, (
            ".ce-platform-tile-dims must have text-overflow: ellipsis to prevent dims text overflow"
        )

    def test_ce12_variant_js_selectors_use_data_attribute(self):
        """setCardVariant and previewVariant must target tiles via data-variant attribute."""
        assert "#variant-picker [data-variant]" in self._html, (
            "JS variant selectors must use #variant-picker [data-variant] attribute targeting"
        )

    def test_ce13_per_variant_art_defined_for_key_designs(self):
        """CSS must define art rules for the main card design families."""
        for variant in ("fifa", "compact", "pulse", "showcase"):
            assert f'[data-variant="{variant}"]' in self._html, (
                f"Per-variant art CSS rule must exist for '{variant}'"
            )


# ── 6. FIFA Classic header/body alignment (CE-14..CE-18) ────────────────────

class TestFifaHeaderBodyAlignment:
    """CE-14..CE-21 — Photo column right edge must align with Outfield left edge.

    Design intent: --photo-col-w CSS custom property is set on .card-wrap (default 170px).
    Both .fclassic-header and .card-body reference var(--photo-col-w) so they are always
    in sync. In export-mode, body.export-mode .card-wrap overrides --photo-col-w to
    var(--ex-photo-w) so the alignment is preserved at any viewport / render context.
    Spacer is hidden only for landscape/banner (side-by-side layouts) and on mobile
    (1-col stack where the concept doesn't apply).
    """

    @pytest.fixture(autouse=True)
    def _src(self):
        self._html = _FIFA_TPL.read_text(encoding="utf-8")

    def test_ce14_card_body_grid_uses_photo_col_w_token(self):
        """card-body grid must reference --photo-col-w token, not a hardcoded pixel value."""
        assert "var(--photo-col-w) 3fr 2fr" in self._html, (
            ".card-body must use grid-template-columns: var(--photo-col-w) 3fr 2fr"
        )

    def test_ce15_photo_spacer_div_present_in_card_body(self):
        """An empty card-body-photo-spacer div must be the first child of .card-body."""
        assert "card-body-photo-spacer" in self._html, (
            "card-body-photo-spacer div must exist in player_card_fclassic.html"
        )

    def test_ce16_skills_panel_has_no_left_padding(self):
        """skills-panel must have padding-left: 0 so Outfield starts at the spacer boundary."""
        assert "padding: 1rem 1rem 1rem 0" in self._html, (
            ".skills-panel must have padding-left:0 (spacer provides left visual margin)"
        )

    def test_ce17_export_mode_syncs_photo_col_w_not_hides_spacer(self):
        """export-mode must override --photo-col-w on .card-wrap (NOT hide the spacer).

        Hiding the spacer in export-mode would break alignment because export-mode is the
        context the editor's iframe runs in. The correct fix is to synchronise the token.
        """
        assert "export-mode .card-wrap" in self._html and "--photo-col-w: var(--ex-photo-w)" in self._html, (
            "body.export-mode .card-wrap { --photo-col-w: var(--ex-photo-w) } must be present"
        )

    def test_ce18_spacer_hidden_on_mobile(self):
        """At max-width:560px the spacer must be hidden (grid collapses to 1-col)."""
        mobile_block_start = self._html.index("@media (max-width: 560px)")
        mobile_block = self._html[mobile_block_start: mobile_block_start + 600]
        assert "card-body-photo-spacer" in mobile_block, (
            ".card-body-photo-spacer must be hidden inside the max-width:560px media block"
        )

    def test_ce19_photo_col_w_default_defined_on_card_wrap(self):
        """--photo-col-w: 170px must be defined on .card-wrap as the default value."""
        assert "--photo-col-w: 170px" in self._html, (
            ".card-wrap must define --photo-col-w: 170px as the non-export default"
        )

    def test_ce20_header_uses_photo_col_w_token(self):
        """fclassic-header grid must reference var(--photo-col-w), not a hardcoded width."""
        assert "var(--photo-col-w) 1fr" in self._html, (
            ".fclassic-header must use grid-template-columns: var(--photo-col-w) 1fr "
            "so header and body spacer are always in sync"
        )

    def test_ce21_landscape_banner_hides_spacer(self):
        """Landscape and banner export layouts are side-by-side; spacer must be hidden."""
        for platform in ("platform-facebook-landscape", "platform-og", "platform-banner-custom"):
            assert f"{platform} .card-body-photo-spacer" in self._html, (
                f"body.export-mode.{platform} must hide .card-body-photo-spacer "
                f"(header and body are side-by-side in this layout)"
            )
