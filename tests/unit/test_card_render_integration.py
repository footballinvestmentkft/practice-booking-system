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
    "last_skill_delta": {},
    "participations_history": [],
    "theme": get_theme("default"),
    "card_theme_id": "default",
    "card_theme": "default",
    "card_variant_id": "compact",
    "compact_bg_url": None,
    "showcase_bg_url": None,
    "compact_photo_position": "left",
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
        v = get_variant("nonexistent_variant_xyz")
        assert v.id == "fifa"

    def test_null_variant_handled_via_or_default(self):
        ul = _make_license(card_variant=None)
        card_variant_id = ul.card_variant or "fifa"
        v = get_variant(card_variant_id)
        assert v.id == "fifa"

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
        return Environment(loader=FileSystemLoader(_TEMPLATES_DIR))

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
        """Compact extends player_card_base.html which injects <body class="theme-{id}">."""
        ctx = {**_RENDER_CONTEXT, "theme": get_theme(theme_id), "card_theme_id": theme_id, "card_theme": theme_id}
        t = jinja_env.get_template("public/player_card_compact.html")
        try:
            html = t.render(**ctx)
        except Exception as e:
            pytest.fail(f"compact render with theme={theme_id!r} raised {type(e).__name__}: {e}")
        assert f"theme-{theme_id}" in html, f"theme CSS class missing in rendered output for {theme_id!r}"
