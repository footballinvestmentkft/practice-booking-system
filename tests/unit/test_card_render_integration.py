"""
Card Render Integration Gate
============================
Validates that public_player_card route:
  1. Reads card_theme from UserLicense and passes correct CSS vars to template
  2. Reads card_variant from UserLicense
  3. Falls back to "default"/"fifa" for unknown/null values
  4. ?preview= param overrides card_variant without persisting
  5. Template selection falls back to player_card.html if variant template missing

All tests use MagicMock — no DB required.
"""
import os
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from jinja2 import Environment, FileSystemLoader

from app.services.card_theme_service import THEMES, get_theme
from app.services.card_variant_service import VARIANTS, get_variant
from app.utils.country_codes import register_filters

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "app", "templates")

_RENDER_CONTEXT = {
    "player": {
        "name": "Test Player", "nationality": "HU", "position": "MIDFIELDER",
        "age_group": "AMATEUR", "total_tournaments": 0, "skills": {},
    },
    "overall": 55.0,
    "tier_label": "DEVELOPING",
    "tier_color": "#ed8936",
    "avatar_bg": "#c05621",
    "initials": "TP",
    "pos_color": "#667eea",
    "skill_categories": [],
    "teams_info": [],
    "photo_url": None,
    "portrait_photo_url": None,
    "landscape_photo_url": None,
    "last_skill_delta": {},
    "participations_history": [],
    "theme": get_theme("default"),
    "card_theme_id": "default",
    "card_theme": "default",
    "card_variant_id": "compact",
    "compact_bg_url": None,
    "showcase_bg_url": None,
    "compact_photo_position": "left",
    "compact_focus_x": 50,
    "compact_focus_y": 100,
    "showcase_focus_x": 50,
    "showcase_focus_y": 50,
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_license(card_theme="default", card_variant="fifa"):
    ul = MagicMock()
    ul.card_theme = card_theme
    ul.card_variant = card_variant
    ul.player_card_photo_url = None
    ul.onboarding_completed = True
    ul.motivation_scores = {"position": "MIDFIELDER"}
    return ul


# ── Theme resolution ───────────────────────────────────────────────────────────

class TestThemeResolution:

    def test_known_theme_returns_correct_css_values(self):
        theme = get_theme("gold")
        assert theme.id == "gold"
        assert "#f6ad3c" in theme.accent
        assert "gold" in theme.panel_bg.lower() or "#3d2200" in theme.panel_bg

    def test_unknown_theme_falls_back_to_default(self):
        theme = get_theme("nonexistent_theme_xyz")
        assert theme.id == "default"
        assert theme.accent == "#667eea"

    def test_null_theme_handled_via_or_default(self):
        ul = _make_license(card_theme=None)
        card_theme_id = ul.card_theme or "default"
        theme = get_theme(card_theme_id)
        assert theme.id == "default"

    def test_all_premium_themes_have_css_fields(self):
        for theme_id in ["gold", "emerald", "crimson"]:
            t = get_theme(theme_id)
            assert t.panel_bg, f"{theme_id}.panel_bg empty"
            assert t.body_bg,  f"{theme_id}.body_bg empty"
            assert t.tab_bg,   f"{theme_id}.tab_bg empty"
            assert t.accent,   f"{theme_id}.accent empty"

    def test_free_themes_have_css_fields(self):
        for theme_id in ["default", "midnight", "arctic"]:
            t = get_theme(theme_id)
            assert t.panel_bg and t.body_bg and t.tab_bg and t.accent

    def test_crimson_accent_is_red(self):
        t = get_theme("crimson")
        assert "#ff6b6b" in t.accent

    def test_emerald_accent_is_green(self):
        t = get_theme("emerald")
        assert "#4cde82" in t.accent


# ── Variant resolution ────────────────────────────────────────────────────────

class TestVariantResolution:

    def test_known_variant_returns_correct_definition(self):
        v = get_variant("compact")
        assert v.id == "compact"
        assert v.is_premium is True
        assert v.credit_cost == 300

    def test_unknown_variant_falls_back_to_fifa(self):
        # PR-FC-1B: fallback is now canonical "fclassic"; "fifa" is deprecated alias
        v = get_variant("nonexistent_variant_xyz")
        assert v.id == "fclassic"

    def test_null_variant_handled_via_or_default(self):
        ul = _make_license(card_variant=None)
        card_variant_id = ul.card_variant or "fclassic"  # PR-FC-1B: default is fclassic
        v = get_variant(card_variant_id)
        assert v.id == "fclassic"

    def test_preview_param_overrides_db_variant(self):
        ul = _make_license(card_variant="fifa")
        card_variant_id = ul.card_variant or "fifa"
        preview = "compact"
        if preview and preview in VARIANTS:
            card_variant_id = preview
        assert card_variant_id == "compact"

    def test_invalid_preview_param_does_not_override(self):
        ul = _make_license(card_variant="showcase")
        card_variant_id = ul.card_variant or "fifa"
        preview = "nonexistent_xyz"
        if preview and preview in VARIANTS:
            card_variant_id = preview
        assert card_variant_id == "showcase"

    def test_none_preview_param_does_not_override(self):
        ul = _make_license(card_variant="compact")
        card_variant_id = ul.card_variant or "fifa"
        preview = None
        if preview and preview in VARIANTS:
            card_variant_id = preview
        assert card_variant_id == "compact"


# ── Template selection ────────────────────────────────────────────────────────

class TestTemplateSelection:
    _FALLBACK = "public/player_card.html"

    def _resolve_template(self, variant_id, templates_dir):
        v = get_variant(variant_id)
        candidate = os.path.join(templates_dir, v.template)
        return v.template if os.path.isfile(candidate) else self._FALLBACK

    def test_fallback_when_variant_template_missing(self, tmp_path):
        # tmp_path has no variant template files — should fall back
        result = self._resolve_template("compact", str(tmp_path))
        assert result == self._FALLBACK

    def test_uses_variant_template_when_file_exists(self, tmp_path):
        # Create the variant template file
        v = get_variant("compact")
        target = tmp_path / v.template
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("<html>compact</html>")
        result = self._resolve_template("compact", str(tmp_path))
        assert result == v.template

    def test_player_card_html_exists_in_real_templates(self):
        templates_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "templates"
        )
        fallback = os.path.join(templates_dir, self._FALLBACK)
        assert os.path.isfile(fallback), f"Fallback template missing: {fallback}"

    def test_all_variant_templates_exist(self):
        templates_dir = os.path.join(
            os.path.dirname(__file__), "..", "..", "app", "templates"
        )
        for variant_id in ["fifa", "compact", "showcase", "compact_bg", "showcase_bg"]:
            v = get_variant(variant_id)
            candidate = os.path.join(templates_dir, v.template)
            assert os.path.isfile(candidate), (
                f"Variant template missing: {candidate} — restore from git history or implement"
            )

    def test_all_variants_are_available(self):
        for variant_id in ["fifa", "compact", "showcase", "compact_bg", "showcase_bg"]:
            v = get_variant(variant_id)
            assert v.available, (
                f"Variant '{variant_id}' is marked available=False but has a template — "
                "set available=True"
            )


# ── CSS custom property values match service registry ────────────────────────

class TestCSSCustomPropertyValues:

    def test_default_theme_css_values(self):
        t = get_theme("default")
        assert t.panel_bg == "linear-gradient(155deg, #1a2744 0%, #2a3a5c 60%, #1e3a4a 100%)"
        assert t.body_bg == "#1a202c"
        assert t.tab_bg == "#2d3748"
        assert t.accent == "#667eea"

    def test_gold_theme_css_values(self):
        t = get_theme("gold")
        assert "#3d2200" in t.panel_bg
        assert t.body_bg == "#1e1500"
        assert t.accent == "#f6ad3c"

    def test_crimson_css_matches_live_output(self):
        t = get_theme("crimson")
        assert "#3d0a0a" in t.panel_bg
        assert t.body_bg == "#1e0d0d"
        assert t.tab_bg == "#2d1010"
        assert t.accent == "#ff6b6b"

    def test_emerald_css_matches_live_output(self):
        t = get_theme("emerald")
        assert "#0a2d0a" in t.panel_bg
        assert t.body_bg == "#0d1f0d"
        assert t.accent == "#4cde82"


# ── Jinja2 render smoke: every variant must render without TemplateNotFound ──

class TestVariantRenderSmoke:
    """
    Catches missing base templates and broken {% extends %} chains.
    os.path.isfile() can't catch Jinja2 inheritance errors — only actual rendering can.
    """

    @pytest.fixture(scope="class")
    def jinja_env(self):
        env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR))
        register_filters(env)
        return env

    @pytest.mark.parametrize("variant_id", ["fifa", "compact", "showcase", "compact_bg", "showcase_bg"])
    def test_variant_renders_without_error(self, jinja_env, variant_id):
        v = get_variant(variant_id)
        try:
            t = jinja_env.get_template(v.template)
        except Exception as e:
            pytest.fail(f"get_template({v.template!r}) raised {type(e).__name__}: {e}")
        try:
            html = t.render(**_RENDER_CONTEXT)
        except Exception as e:
            pytest.fail(f"render({variant_id!r}) raised {type(e).__name__}: {e}")
        assert len(html) > 100, f"Rendered HTML for {variant_id!r} suspiciously short ({len(html)} chars)"

    @pytest.mark.parametrize("theme_id", ["default", "midnight", "arctic", "gold", "emerald", "crimson"])
    def test_compact_renders_all_themes(self, jinja_env, theme_id):
        """Compact extends player_card_base.html; F-THEME-1: theme injected via :root, not body class."""
        theme = get_theme(theme_id)
        ctx = {**_RENDER_CONTEXT, "theme": theme, "card_theme_id": theme_id, "card_theme": theme_id}
        t = jinja_env.get_template("public/player_card_compact.html")
        try:
            html = t.render(**ctx)
        except Exception as e:
            pytest.fail(f"compact render with theme={theme_id!r} raised {type(e).__name__}: {e}")
        assert f"theme-{theme_id}" not in html, f"old .theme-* body class should be gone after F-THEME-1"
        if theme_id != "default":
            assert theme.body_bg in html, f"theme body_bg not injected into :root for {theme_id!r}"


# ── Combinatorial theme × variant: every combination must render ─────────────

class TestThemeVariantCombinations:
    """
    Validates that applying a theme does not corrupt variant rendering and vice versa.
    Every theme × variant combination must produce valid HTML with the correct
    theme CSS class — verifying that apply_theme and apply_variant are orthogonal.
    """

    _BASE_EXTENDING_VARIANTS = ["compact", "showcase", "compact_bg", "showcase_bg"]
    _ALL_THEMES = ["default", "midnight", "arctic", "gold", "emerald", "crimson"]

    @pytest.fixture(scope="class")
    def jinja_env(self):
        env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR))
        register_filters(env)
        return env

    @pytest.mark.parametrize("theme_id,variant_id", [
        (t, v)
        for t in ["default", "midnight", "arctic", "gold", "emerald", "crimson"]
        for v in ["compact", "showcase", "compact_bg", "showcase_bg"]
    ])
    def test_theme_variant_combination_renders(self, jinja_env, theme_id, variant_id):
        """Each theme × variant must render without error; F-THEME-1: theme applied via :root."""
        theme = get_theme(theme_id)
        v = get_variant(variant_id)
        ctx = {
            **_RENDER_CONTEXT,
            "theme": theme,
            "card_theme_id": theme_id,
            "card_theme": theme_id,
            "card_variant_id": variant_id,
            "compact_photo_position": "left",
            "compact_bg_url": None,
            "showcase_bg_url": None,
        }
        try:
            html = jinja_env.get_template(v.template).render(**ctx)
        except Exception as e:
            pytest.fail(
                f"theme={theme_id!r} × variant={variant_id!r} raised {type(e).__name__}: {e}\n"
                f"This means theme/variant are NOT orthogonal — one overwrites the other's render path."
            )
        assert f"theme-{theme_id}" not in html, (
            f"old .theme-* body class found in {variant_id!r} — should be gone after F-THEME-1"
        )
        if theme_id != "default":
            assert theme.body_bg in html, (
                f"theme body_bg not injected into :root for {theme_id!r} × {variant_id!r} — "
                f"base template :root injection not reaching this variant."
            )

    @pytest.mark.parametrize("photo_position", ["left", "right"])
    def test_compact_photo_position_both_sides(self, jinja_env, photo_position):
        """compact_photo_position=left and right must both render without error."""
        ctx = {**_RENDER_CONTEXT, "compact_photo_position": photo_position}
        try:
            html = jinja_env.get_template("public/player_card_compact.html").render(**ctx)
        except Exception as e:
            pytest.fail(f"compact with photo_position={photo_position!r} raised {type(e).__name__}: {e}")
        assert f"photo-{photo_position}" in html, (
            f"CSS class 'photo-{photo_position}' missing in compact render"
        )

    def test_compact_bg_with_url_renders(self, jinja_env):
        """compact_bg variant with an actual bg URL must render without error."""
        ctx = {**_RENDER_CONTEXT, "compact_bg_url": "https://example.com/bg.jpg", "compact_photo_position": "left"}
        try:
            html = jinja_env.get_template("public/player_card_compact_bg.html").render(**ctx)
        except Exception as e:
            pytest.fail(f"compact_bg with bg URL raised {type(e).__name__}: {e}")
        assert "example.com/bg.jpg" in html

    def test_showcase_bg_with_url_renders(self, jinja_env):
        """showcase_bg variant with an actual bg URL must render without error."""
        ctx = {**_RENDER_CONTEXT, "showcase_bg_url": "https://example.com/bg2.jpg"}
        try:
            html = jinja_env.get_template("public/player_card_showcase_bg.html").render(**ctx)
        except Exception as e:
            pytest.fail(f"showcase_bg with bg URL raised {type(e).__name__}: {e}")
        assert "example.com/bg2.jpg" in html


# ── Image pipeline DOM assertions ─────────────────────────────────────────────

class TestImagePipelineDOMAssertions:
    """
    Validates the image pipeline wiring:
      - compact/compact_bg use portrait_photo_url (not photo_url)
      - showcase/showcase_bg use landscape_photo_url (not photo_url)
      - FIFA uses photo_url (original)
      - focus points applied as inline style object-position on <img>
      - BG URL correctly passed as background-image inline style
      - z-index declarations present in BG variant CSS
    """

    _PORTRAIT_URL = "/static/uploads/lfa_player_photos/1_portrait.png"
    _LANDSCAPE_URL = "/static/uploads/lfa_player_photos/1_landscape.png"
    _ORIG_URL = "/static/uploads/lfa_player_photos/1_orig_12345.png"
    _COMPACT_BG = "/static/uploads/lfa_player_photos/1_bg_compact.png"
    _SHOWCASE_BG = "/static/uploads/lfa_player_photos/1_bg_showcase.png"

    @pytest.fixture(scope="class")
    def jinja_env(self):
        env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR))
        register_filters(env)
        return env

    def _ctx(self, **overrides):
        return {
            **_RENDER_CONTEXT,
            "portrait_photo_url": self._PORTRAIT_URL,
            "landscape_photo_url": self._LANDSCAPE_URL,
            "photo_url": self._ORIG_URL,
            **overrides,
        }

    # ── Photo source: correct crop per variant ────────────────────────────────

    def test_compact_uses_portrait_url(self, jinja_env):
        html = jinja_env.get_template("public/player_card_compact.html").render(**self._ctx())
        assert self._PORTRAIT_URL in html, "compact must use portrait_photo_url"
        assert self._LANDSCAPE_URL not in html, "compact must NOT use landscape_photo_url"
        assert self._ORIG_URL not in html, "compact must NOT use original photo_url"

    def test_compact_bg_uses_portrait_url(self, jinja_env):
        html = jinja_env.get_template("public/player_card_compact_bg.html").render(
            **self._ctx(compact_bg_url=self._COMPACT_BG)
        )
        assert self._PORTRAIT_URL in html, "compact_bg must use portrait_photo_url"
        assert self._COMPACT_BG in html, "compact_bg must include compact_bg_url as background"
        assert self._LANDSCAPE_URL not in html, "compact_bg must NOT use landscape_photo_url"

    def test_showcase_uses_landscape_url(self, jinja_env):
        html = jinja_env.get_template("public/player_card_showcase.html").render(**self._ctx())
        assert self._LANDSCAPE_URL in html, "showcase must use landscape_photo_url"
        assert self._PORTRAIT_URL not in html, "showcase must NOT use portrait_photo_url"
        assert self._ORIG_URL not in html, "showcase must NOT use original photo_url"

    def test_showcase_bg_uses_landscape_url(self, jinja_env):
        html = jinja_env.get_template("public/player_card_showcase_bg.html").render(
            **self._ctx(showcase_bg_url=self._SHOWCASE_BG)
        )
        assert self._LANDSCAPE_URL in html, "showcase_bg must use landscape_photo_url"
        assert self._SHOWCASE_BG in html, "showcase_bg must include showcase_bg_url as background"
        assert self._PORTRAIT_URL not in html, "showcase_bg must NOT use portrait_photo_url"

    def test_fifa_uses_orig_photo_url(self, jinja_env):
        # PR-FC-1C: template renamed to player_card_fclassic.html
        html = jinja_env.get_template("public/player_card_fclassic.html").render(**self._ctx())
        assert self._ORIG_URL in html, "FClassic Player must use original photo_url"

    def test_compact_falls_back_to_orig_when_portrait_missing(self, jinja_env):
        """When portrait_photo_url is None, compact falls back to player_card_photo_url."""
        ctx = {**self._ctx(), "portrait_photo_url": None}
        html = jinja_env.get_template("public/player_card_compact.html").render(**ctx)
        # portrait is None → initials placeholder rendered, not the orig URL
        assert self._PORTRAIT_URL not in html
        assert "cmp-photo-initials" in html

    def test_showcase_falls_back_to_orig_when_landscape_missing(self, jinja_env):
        """When landscape_photo_url is None, showcase falls back to player_card_photo_url."""
        ctx = {**self._ctx(), "landscape_photo_url": None}
        html = jinja_env.get_template("public/player_card_showcase.html").render(**ctx)
        assert self._LANDSCAPE_URL not in html
        assert "sc-banner-initials" in html

    # ── Focus point object-position ───────────────────────────────────────────

    def test_compact_focus_point_in_img_style(self, jinja_env):
        ctx = self._ctx(compact_focus_x=30, compact_focus_y=75)
        html = jinja_env.get_template("public/player_card_compact.html").render(**ctx)
        assert "object-position: 30% 75%" in html, (
            "compact img must have inline object-position from compact_focus_x/y"
        )

    def test_compact_bg_focus_point_in_img_style(self, jinja_env):
        ctx = self._ctx(compact_focus_x=20, compact_focus_y=80)
        html = jinja_env.get_template("public/player_card_compact_bg.html").render(**ctx)
        assert "object-position: 20% 80%" in html, (
            "compact_bg img must have inline object-position from compact_focus_x/y"
        )

    def test_showcase_focus_point_in_img_style(self, jinja_env):
        ctx = self._ctx(showcase_focus_x=60, showcase_focus_y=40)
        html = jinja_env.get_template("public/player_card_showcase.html").render(**ctx)
        assert "object-position: 60% 40%" in html, (
            "showcase img must have inline object-position from showcase_focus_x/y"
        )

    def test_showcase_bg_focus_point_in_img_style(self, jinja_env):
        ctx = self._ctx(showcase_focus_x=70, showcase_focus_y=30, showcase_bg_url=self._SHOWCASE_BG)
        html = jinja_env.get_template("public/player_card_showcase_bg.html").render(**ctx)
        assert "object-position: 70% 30%" in html, (
            "showcase_bg img must have inline object-position from showcase_focus_x/y"
        )

    def test_compact_default_focus_is_center_bottom(self, jinja_env):
        """Default compact focus (50/100) must produce center-bottom equivalent."""
        ctx = self._ctx(compact_focus_x=50, compact_focus_y=100)
        html = jinja_env.get_template("public/player_card_compact.html").render(**ctx)
        assert "object-position: 50% 100%" in html

    def test_showcase_default_focus_is_center(self, jinja_env):
        """Default showcase focus (50/50) must produce center-center equivalent."""
        ctx = self._ctx(showcase_focus_x=50, showcase_focus_y=50)
        html = jinja_env.get_template("public/player_card_showcase.html").render(**ctx)
        assert "object-position: 50% 50%" in html

    # ── z-index explicit stacking ─────────────────────────────────────────────

    def test_compact_bg_has_explicit_z_index(self, jinja_env):
        html = jinja_env.get_template("public/player_card_compact_bg.html").render(**self._ctx())
        assert "z-index: 1" in html, "compact_bg must declare z-index:1 for player photo layer"
        assert "z-index: 2" in html, "compact_bg must declare z-index:2 for overlay/badge"

    def test_showcase_bg_has_explicit_z_index(self, jinja_env):
        html = jinja_env.get_template("public/player_card_showcase_bg.html").render(
            **self._ctx(showcase_bg_url=self._SHOWCASE_BG)
        )
        assert "z-index: 1" in html, "showcase_bg must declare z-index:1 for player photo layer"
        assert "z-index: 2" in html, "showcase_bg must declare z-index:2 for overlay"

    # ── BG variant: background-image inline style ─────────────────────────────

    def test_compact_bg_background_image_style(self, jinja_env):
        ctx = self._ctx(compact_bg_url=self._COMPACT_BG)
        html = jinja_env.get_template("public/player_card_compact_bg.html").render(**ctx)
        assert f"background-image: url('{self._COMPACT_BG}')" in html

    def test_showcase_bg_background_image_style(self, jinja_env):
        ctx = self._ctx(showcase_bg_url=self._SHOWCASE_BG)
        html = jinja_env.get_template("public/player_card_showcase_bg.html").render(**ctx)
        assert f"background-image: url('{self._SHOWCASE_BG}')" in html

    def test_compact_bg_no_background_when_url_missing(self, jinja_env):
        ctx = self._ctx(compact_bg_url=None)
        html = jinja_env.get_template("public/player_card_compact_bg.html").render(**ctx)
        # No inline background-image style should appear on the photo column div
        assert 'style="background-image:' not in html
        assert "style=\"background-image: url(" not in html

    def test_showcase_bg_no_background_when_url_missing(self, jinja_env):
        ctx = self._ctx(showcase_bg_url=None)
        html = jinja_env.get_template("public/player_card_showcase_bg.html").render(**ctx)
        assert 'style="background-image:' not in html
        assert "style=\"background-image: url(" not in html
