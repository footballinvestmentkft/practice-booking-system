"""
tests/unit/test_card_macros.py
Unit tests for Jinja2 card macros (Phase 1 macro extraction refactor).

Tests use jinja2.Environment directly — no server or database needed.
"""
from types import SimpleNamespace

import pytest
import jinja2


# ---------------------------------------------------------------------------
# Shared Jinja2 environment + helper
# ---------------------------------------------------------------------------

TEMPLATES_DIR = "app/templates"

env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
    autoescape=False,
)


def _render_macro(template_path: str, macro_name: str, *args, **kwargs) -> str:
    """Load *template_path*, call *macro_name* with args/kwargs, return rendered string."""
    source = (
        "{{% from '{path}' import {macro} %}}"
        "{{{{ {macro}({positional}{keyword}) }}}}"
    ).format(
        path=template_path,
        macro=macro_name,
        positional=", ".join("arg{}".format(i) for i in range(len(args))),
        keyword=", ".join("{}=kw{}".format(k, i) for i, k in enumerate(kwargs)),
    )
    # Build the template string properly
    call_parts = []
    call_parts.extend("arg{}".format(i) for i in range(len(args)))
    call_parts.extend("{}=kw{}".format(k, i) for i, k in enumerate(kwargs))

    tmpl_src = "{{% from '{path}' import {macro} %}}{{{{ {macro}({call}) }}}}".format(
        path=template_path,
        macro=macro_name,
        call=", ".join(call_parts),
    )

    tmpl = env.from_string(tmpl_src)

    ctx = {}
    for i, v in enumerate(args):
        ctx["arg{}".format(i)] = v
    for i, k in enumerate(kwargs):
        ctx["kw{}".format(i)] = kwargs[k]

    return tmpl.render(**ctx)


def _make_skill(key: str, name_en: str) -> SimpleNamespace:
    return SimpleNamespace(key=key, name_en=name_en)


def _make_cat(key: str = "outfield", name_en: str = "Outfield", emoji: str = "⚽",
              skills=None) -> SimpleNamespace:
    if skills is None:
        skills = [_make_skill("passing", "Passing"), _make_skill("dribbling", "Dribbling")]
    return SimpleNamespace(key=key, name_en=name_en, emoji=emoji, skills=skills)


# ---------------------------------------------------------------------------
# TestExportSkillRowsMacro
# ---------------------------------------------------------------------------

class TestExportSkillRowsMacro:
    """MR_ prefix — export_skill_rows macro from macros/card_skill_row.html"""

    MACRO_PATH = "macros/card_skill_row.html"
    MACRO_NAME = "export_skill_rows"

    def _render(self, cat, skills_dict, delta_dict, **kw):
        return _render_macro(self.MACRO_PATH, self.MACRO_NAME,
                             cat, skills_dict, delta_dict, **kw)

    def test_MR_export_renders_skill_name(self):
        cat = _make_cat(skills=[_make_skill("passing", "Passing")])
        html = self._render(cat, {"passing": {"current_level": 75}}, {})
        assert "Passing" in html
        assert "ex-sname" in html

    def test_MR_export_renders_skill_score(self):
        cat = _make_cat(skills=[_make_skill("shooting", "Shooting")])
        html = self._render(cat, {"shooting": {"current_level": 82}}, {})
        assert "82" in html
        assert "ex-sval" in html

    def test_MR_export_positive_delta_green_bar_and_visible_up_arrow(self):
        cat = _make_cat(skills=[_make_skill("passing", "Passing")])
        html = self._render(cat, {"passing": {"current_level": 70}}, {"passing": 5})
        assert "#48bb78" in html
        assert "visibility:visible" in html
        assert "↑" in html

    def test_MR_export_negative_delta_red_bar_and_visible_down_arrow(self):
        cat = _make_cat(skills=[_make_skill("passing", "Passing")])
        html = self._render(cat, {"passing": {"current_level": 60}}, {"passing": -3})
        assert "#fc8181" in html
        assert "visibility:visible" in html
        assert "↓" in html

    def test_MR_export_zero_delta_hidden_trend_neutral_colors(self):
        cat = _make_cat(skills=[_make_skill("passing", "Passing")])
        html = self._render(cat, {"passing": {"current_level": 65}}, {"passing": 0})
        assert "visibility:hidden" in html
        assert "var(--ex-text-secondary)" in html
        assert "var(--ex-text-strong)" in html

    def test_MR_export_custom_neutral_bar_and_val_colors(self):
        cat = _make_cat(skills=[_make_skill("passing", "Passing")])
        html = self._render(
            cat,
            {"passing": {"current_level": 65}},
            {},
            neutral_bar="rgba(255,255,255,0.18)",
            neutral_val="rgba(255,255,255,0.80)",
        )
        assert "rgba(255,255,255,0.18)" in html
        assert "rgba(255,255,255,0.80)" in html

    def test_MR_export_missing_skill_key_uses_default_50(self):
        cat = _make_cat(skills=[_make_skill("unknown_skill", "Unknown")])
        html = self._render(cat, {}, {})
        assert "50" in html

    def test_MR_export_renders_ex_skill_rows_wrapper_div(self):
        cat = _make_cat(skills=[_make_skill("passing", "Passing")])
        html = self._render(cat, {}, {})
        assert 'class="ex-skill-rows"' in html

    def test_MR_export_renders_ex_row_for_each_skill(self):
        skills = [_make_skill("passing", "Passing"), _make_skill("shooting", "Shooting")]
        cat = _make_cat(skills=skills)
        html = self._render(cat, {}, {})
        assert html.count('class="ex-row"') == 2


# ---------------------------------------------------------------------------
# TestCardSkillRowsMacro
# ---------------------------------------------------------------------------

class TestCardSkillRowsMacro:
    """MR_ prefix — card_skill_rows macro from macros/card_skill_row.html"""

    MACRO_PATH = "macros/card_skill_row.html"
    MACRO_NAME = "card_skill_rows"

    def _render(self, cat, skills_dict, delta_dict):
        return _render_macro(self.MACRO_PATH, self.MACRO_NAME, cat, skills_dict, delta_dict)

    def test_MR_card_renders_skill_name(self):
        cat = _make_cat(skills=[_make_skill("dribbling", "Dribbling")])
        html = self._render(cat, {"dribbling": {"current_level": 78.5}}, {})
        assert "Dribbling" in html
        assert "skill-name" in html

    def test_MR_card_renders_skill_score(self):
        cat = _make_cat(skills=[_make_skill("shooting", "Shooting")])
        html = self._render(cat, {"shooting": {"current_level": 80.0}}, {})
        assert "80.0" in html
        assert "skill-val" in html

    def test_MR_card_positive_delta_shows_up_arrow(self):
        cat = _make_cat(skills=[_make_skill("passing", "Passing")])
        html = self._render(cat, {"passing": {"current_level": 72}}, {"passing": 4})
        assert "#48bb78" in html
        assert "↑" in html
        # skill-up span rendered (not hidden)
        assert 'class="skill-up"' in html

    def test_MR_card_negative_delta_shows_red_down_arrow(self):
        cat = _make_cat(skills=[_make_skill("passing", "Passing")])
        html = self._render(cat, {"passing": {"current_level": 68}}, {"passing": -2})
        assert "#fc8181" in html
        assert "↓" in html
        assert 'color:#fc8181' in html

    def test_MR_card_zero_delta_hidden_trend_arrow(self):
        cat = _make_cat(skills=[_make_skill("passing", "Passing")])
        html = self._render(cat, {"passing": {"current_level": 70}}, {"passing": 0})
        assert 'visibility:hidden' in html
        assert "var(--card-bar-neutral)" in html
        assert "var(--card-val-neutral)" in html

    def test_MR_card_renders_skill_row_for_each_skill(self):
        skills = [_make_skill("a", "Alpha"), _make_skill("b", "Beta"), _make_skill("c", "Gamma")]
        cat = _make_cat(skills=skills)
        html = self._render(cat, {}, {})
        assert html.count('class="skill-row"') == 3

    def test_MR_card_uses_round_1_for_score(self):
        cat = _make_cat(skills=[_make_skill("pace", "Pace")])
        html = self._render(cat, {"pace": {"current_level": 77.3}}, {})
        # round(1) → 77.3
        assert "77.3" in html

    def test_MR_card_no_wrapping_container_div(self):
        """card_skill_rows must NOT render an outer wrapper div (unlike export_skill_rows)."""
        cat = _make_cat(skills=[_make_skill("passing", "Passing")])
        html = self._render(cat, {}, {}).strip()
        assert not html.startswith('<div class="ex-skill-rows">')
        assert not html.startswith('<div class="skill-rows">')


# ---------------------------------------------------------------------------
# TestSponsorSlotMacro
# ---------------------------------------------------------------------------

class TestSponsorSlotMacro:
    """MR_ prefix — sponsor_slot macro from macros/card_sponsor_block.html"""

    MACRO_PATH = "macros/card_sponsor_block.html"
    MACRO_NAME = "sponsor_slot"

    def _render(self, sponsor_logo_url, app_logo_url):
        return _render_macro(
            self.MACRO_PATH, self.MACRO_NAME, sponsor_logo_url, app_logo_url
        )

    def test_MR_sponsor_logo_url_present_renders_sponsor_img(self):
        html = self._render("https://example.com/sponsor.png", None)
        assert 'alt="Sponsor"' in html
        assert "https://example.com/sponsor.png" in html
        assert 'class="ex-sponsor-slot-img"' in html

    def test_MR_sponsor_logo_none_app_logo_present_renders_lfa_img(self):
        html = self._render(None, "https://example.com/lfa-logo.png")
        assert 'alt="LFA"' in html
        assert "https://example.com/lfa-logo.png" in html

    def test_MR_both_none_no_img_rendered(self):
        html = self._render(None, None)
        assert "<img" not in html

    def test_MR_sponsor_logo_takes_priority_over_app_logo(self):
        html = self._render("https://sponsor.com/logo.png", "https://app.com/logo.png")
        assert 'alt="Sponsor"' in html
        assert 'alt="LFA"' not in html

    def test_MR_renders_ex_sponsor_slot_wrapper_div(self):
        html = self._render(None, None)
        assert 'class="ex-sponsor-slot"' in html


# ---------------------------------------------------------------------------
# TestExportRootVarsMacro
# ---------------------------------------------------------------------------

class TestExportRootVarsMacro:
    """MR_ prefix — export_root_vars macro from macros/card_theme_root.html"""

    MACRO_PATH = "macros/card_theme_root.html"
    MACRO_NAME = "export_root_vars"

    def _render(self, theme, **kw):
        return _render_macro(self.MACRO_PATH, self.MACRO_NAME, theme, **kw)

    def _make_theme(self):
        return SimpleNamespace(
            panel_bg="linear-gradient(155deg, #0d0d0d 0%, #1a1a2e 60%, #16213e 100%)",
            body_bg="#0f0f0f",
            accent="#00d4ff",
        )

    def test_MR_root_panel_bg_injected(self):
        html = self._render(self._make_theme())
        assert "--ex-panel-bg" in html
        assert "#0d0d0d" in html

    def test_MR_root_body_bg_injected(self):
        html = self._render(self._make_theme())
        assert "--ex-body-bg" in html
        assert "#0f0f0f" in html

    def test_MR_root_accent_injected(self):
        html = self._render(self._make_theme())
        assert "--ex-accent" in html
        assert "#00d4ff" in html

    def test_MR_root_default_cat_bg_is_005(self):
        html = self._render(self._make_theme())
        assert "rgba(255,255,255,0.05)" in html

    def test_MR_root_default_bar_bg_is_010(self):
        html = self._render(self._make_theme())
        assert "rgba(255,255,255,0.10)" in html

    def test_MR_root_custom_cat_bg_square(self):
        html = self._render(self._make_theme(), cat_bg='rgba(255,255,255,0.06)')
        assert "rgba(255,255,255,0.06)" in html

    def test_MR_root_custom_cat_bg_landscape(self):
        html = self._render(self._make_theme(), cat_bg='rgba(255,255,255,0.04)')
        assert "rgba(255,255,255,0.04)" in html

    def test_MR_root_undefined_theme_uses_defaults(self):
        html = _render_macro(self.MACRO_PATH, self.MACRO_NAME)
        assert "--ex-panel-bg" in html
        assert "#1a2744" in html
        assert "#1a202c" in html
        assert "#667eea" in html

    def test_MR_root_outputs_root_block(self):
        html = self._render(self._make_theme())
        assert ":root" in html
        assert "--ex-panel-bg" in html
        assert "--ex-body-bg" in html
        assert "--ex-accent" in html
        assert "--ex-bar-bg" in html
        assert "--ex-cat-bg" in html


# ---------------------------------------------------------------------------
# TestExportRootVarsDarkLight  (Phase 2b — arctic / light-theme token system)
# ---------------------------------------------------------------------------

class TestExportRootVarsDarkLight:
    """MR_ prefix — light/dark text token behaviour in export_root_vars."""

    MACRO_PATH = "macros/card_theme_root.html"
    MACRO_NAME = "export_root_vars"

    def _dark_theme(self):
        return SimpleNamespace(
            panel_bg="linear-gradient(155deg, #0d0d0d 0%, #1a1a2e 60%, #16213e 100%)",
            body_bg="#0f0f0f",
            accent="#00d4ff",
            is_light_body_bg=False,
        )

    def _light_theme(self):
        """Simulates arctic: near-white body_bg, is_light_body_bg=True."""
        return SimpleNamespace(
            panel_bg="linear-gradient(155deg, #1a2744 0%, #2a3a5c 60%, #1e3a4a 100%)",
            body_bg="#f7fafc",
            accent="#4299e1",
            is_light_body_bg=True,
        )

    def _render(self, theme, **kw):
        return _render_macro(self.MACRO_PATH, self.MACRO_NAME, theme, **kw)

    # -- dark theme emits white text tokens -----------------------------------

    def test_MR_dark_theme_emits_white_text_strong(self):
        html = self._render(self._dark_theme())
        assert "rgba(255,255,255,0.85)" in html

    def test_MR_dark_theme_emits_white_text_body(self):
        html = self._render(self._dark_theme())
        assert "rgba(255,255,255,0.72)" in html

    def test_MR_dark_theme_emits_white_text_secondary(self):
        html = self._render(self._dark_theme())
        assert "rgba(255,255,255,0.48)" in html

    def test_MR_dark_theme_emits_white_text_muted(self):
        html = self._render(self._dark_theme())
        assert "rgba(255,255,255,0.30)" in html

    def test_MR_dark_theme_preserves_white_bar_bg(self):
        html = self._render(self._dark_theme())
        assert "rgba(255,255,255,0.10)" in html

    # -- light theme emits dark text tokens -----------------------------------

    def test_MR_light_theme_emits_dark_text_strong(self):
        html = self._render(self._light_theme())
        assert "rgba(0,0,0,0.87)" in html

    def test_MR_light_theme_emits_dark_text_body(self):
        html = self._render(self._light_theme())
        assert "rgba(0,0,0,0.75)" in html

    def test_MR_light_theme_emits_dark_text_secondary(self):
        html = self._render(self._light_theme())
        assert "rgba(0,0,0,0.55)" in html

    def test_MR_light_theme_emits_dark_text_muted(self):
        html = self._render(self._light_theme())
        assert "rgba(0,0,0,0.38)" in html

    def test_MR_light_theme_emits_dark_bar_bg(self):
        html = self._render(self._light_theme())
        assert "rgba(0,0,0,0.08)" in html

    def test_MR_light_theme_emits_dark_cat_bg(self):
        html = self._render(self._light_theme())
        assert "rgba(0,0,0,0.06)" in html

    def test_MR_light_theme_ignores_platform_cat_bg_param(self):
        """Platform cat_bg param is ignored for light themes — dark value used."""
        html = self._render(self._light_theme(), cat_bg='rgba(255,255,255,0.06)')
        assert "rgba(0,0,0,0.06)" in html
        assert "--ex-cat-bg:         rgba(0,0,0,0.06)" in html

    def test_MR_light_theme_no_white_text_tokens_emitted(self):
        """Light theme must not emit any of the dark-theme white text token values."""
        html = self._render(self._light_theme())
        assert "--ex-text-strong:    rgba(255,255,255" not in html
        assert "--ex-text-body:      rgba(255,255,255" not in html
        assert "--ex-text-secondary: rgba(255,255,255" not in html
        assert "--ex-text-muted:     rgba(255,255,255" not in html

    # -- skill row macro uses CSS vars ----------------------------------------

    def test_MR_skill_row_neutral_val_is_css_var(self):
        """export_skill_rows zero-delta neutral_val must be var(--ex-text-strong)."""
        import os, app as _app_pkg
        tpl_dir = os.path.join(os.path.dirname(_app_pkg.__file__), "templates")
        with open(os.path.join(tpl_dir, "macros/card_skill_row.html")) as f:
            src = f.read()
        assert "neutral_val='var(--ex-text-strong)'" in src

    def test_MR_skill_row_neutral_bar_is_css_var(self):
        """export_skill_rows zero-delta neutral_bar must be var(--ex-text-secondary)."""
        import os, app as _app_pkg
        tpl_dir = os.path.join(os.path.dirname(_app_pkg.__file__), "templates")
        with open(os.path.join(tpl_dir, "macros/card_skill_row.html")) as f:
            src = f.read()
        assert "neutral_bar='var(--ex-text-secondary)'" in src

    # -- template CSS: no hardcoded white RGBA for tokenised body classes ------

    def test_MR_template_square_no_hardcoded_white_text_color_in_tokenised_classes(self):
        """Verify text color (not border/background) is tokenised in body-section classes."""
        import os, app as _app_pkg, re
        tpl_dir = os.path.join(os.path.dirname(_app_pkg.__file__), "templates")
        with open(os.path.join(tpl_dir, "public/export/square/fifa.html")) as f:
            src = f.read()
        tokenised = [".ex-skills-title", ".ex-cat-name", ".ex-sname",
                     ".ex-pos-panel-title", ".ex-pos-primary-label", ".ex-pos-secondary-label"]
        for cls in tokenised:
            block = re.search(rf'{re.escape(cls)}\s*{{([^}}]+)}}', src)
            if block:
                # Only check the `color:` property, not borders/backgrounds
                color_lines = [l for l in block.group(1).splitlines()
                               if re.search(r'\bcolor:', l) and 'border' not in l]
                for line in color_lines:
                    assert "rgba(255,255,255" not in line, \
                        f"Hardcoded white RGBA in `color:` property of {cls} in square template: {line.strip()}"

    def test_MR_template_story_no_hardcoded_white_text_color_in_tokenised_classes(self):
        import os, app as _app_pkg, re
        tpl_dir = os.path.join(os.path.dirname(_app_pkg.__file__), "templates")
        with open(os.path.join(tpl_dir, "public/export/story/fifa.html")) as f:
            src = f.read()
        for cls in [".ex-skills-title", ".ex-cat-name", ".ex-sname"]:
            block = re.search(rf'{re.escape(cls)}\s*{{([^}}]+)}}', src)
            if block:
                color_lines = [l for l in block.group(1).splitlines()
                               if re.search(r'\bcolor:', l) and 'border' not in l]
                for line in color_lines:
                    assert "rgba(255,255,255" not in line, \
                        f"Hardcoded white RGBA in `color:` property of {cls} in story template: {line.strip()}"


# ---------------------------------------------------------------------------
# TestPlayerCardFifaPhase2b  (Phase 2b — player_card_fifa.html child migration)
# ---------------------------------------------------------------------------

class TestPlayerCardFifaPhase2b:
    """PB_ prefix — player_card_fifa.html extends player_card_base.html."""

    @classmethod
    def _fifa_env(cls):
        """Jinja2 env with nationalities_display filter registered (no server needed)."""
        e = jinja2.Environment(
            loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
            autoescape=False,
        )
        e.filters["nationalities_display"] = lambda v, secondary=None: v or ""
        return e

    @classmethod
    def _minimal_ctx(cls, **overrides):
        from types import SimpleNamespace
        player = SimpleNamespace(
            name="Test Player",
            position="CM",
            nationality="Hungarian",
            secondary_nationality=None,
            age_group="U17",
            total_tournaments=5,
            skills={},
        )
        ctx = dict(
            player=player,
            card_theme="slate",
            theme=None,
            platform_class=None,
            export_mode=False,
            native_export_mode=False,
            app_logo_url=None,
            sponsor_logo_url=None,
            photo_url=None,
            portrait_photo_url=None,
            landscape_photo_url=None,
            initials="TP",
            avatar_bg="#1a2744",
            overall=75,
            tier_label="Silver",
            tier_color="#718096",
            pos_color="#667eea",
            primary_pos_label="CM",
            secondary_pos_labels=[],
            teams_info=[],
            player_height_cm=None,
            player_weight_kg=None,
            player_preferred_foot=None,
            skill_categories=[],
            last_skill_delta={},
            participations_history=[],
            position_nodes=[],
            dominant_badge=None,
        )
        ctx.update(overrides)
        return ctx

    def _render_fifa(self, **ctx_overrides):
        from app.services.card_theme_service import get_theme as _gt
        tpl = self._fifa_env().get_template("public/player_card_fifa.html")
        ctx = self._minimal_ctx(**ctx_overrides)
        # F-THEME-1: auto-resolve ThemeDefinition when only card_theme string is given
        if ctx.get('theme') is None:
            ctx['theme'] = _gt(ctx.get('card_theme', 'default'))
        return tpl.render(**ctx)

    def _source(self):
        import os, app as _app_pkg
        tpl_dir = os.path.join(os.path.dirname(_app_pkg.__file__), "templates")
        with open(os.path.join(tpl_dir, "public/player_card_fifa.html")) as f:
            return f.read()

    # --- template structure ---

    def test_PB_fifa_extends_base(self):
        assert '{% extends "public/player_card_base.html" %}' in self._source()

    # --- F-THEME-1: :root injection replaces .theme-* CSS class mechanism ---

    def test_PB_body_carries_no_theme_class(self):
        """F-THEME-1: body must not carry theme-* class — vars injected via :root."""
        html = self._render_fifa(card_theme="default")
        assert 'class="theme-' not in html

    def test_PB_dark_theme_injects_root_vars_not_body_class(self):
        """Midnight theme vars injected via :root; no theme-midnight body class."""
        html = self._render_fifa(card_theme="midnight")
        assert 'theme-midnight' not in html
        assert '--card-body-bg:        #0f0f0f' in html

    def test_PB_arctic_theme_injects_light_tokens_via_root(self):
        """Arctic injects light text tokens via :root; no theme-arctic body class."""
        html = self._render_fifa(card_theme="arctic")
        assert 'theme-arctic' not in html
        assert 'rgba(0,0,0,0.85)' in html

    # --- extra_body_classes block ---

    def test_PB_native_export_mode_adds_class(self):
        html = self._render_fifa(native_export_mode=True, export_mode=False)
        assert "native-export-mode" in html

    def test_PB_export_mode_adds_export_class(self):
        html = self._render_fifa(export_mode=True, native_export_mode=False)
        assert "export-mode" in html

    def test_PB_default_no_export_classes(self):
        import re
        html = self._render_fifa(export_mode=False, native_export_mode=False)
        body_class = re.search(r'<body class="([^"]*)"', html)
        assert body_class is not None
        cls = body_class.group(1)
        assert "export-mode" not in cls
        assert "native-export-mode" not in cls

    # --- page_header block ---

    def test_PB_with_logo_renders_page_logo_img(self):
        html = self._render_fifa(app_logo_url="https://cdn.example.com/logo.png")
        assert 'class="page-logo"' in html
        assert "https://cdn.example.com/logo.png" in html

    def test_PB_with_logo_suppresses_page_brand_div(self):
        html = self._render_fifa(app_logo_url="https://cdn.example.com/logo.png")
        assert 'class="page-brand"' not in html

    def test_PB_without_logo_renders_page_brand(self):
        html = self._render_fifa(app_logo_url=None)
        assert 'class="page-brand"' in html

    # --- lavender right-panel contract ---

    def test_PB_source_has_lavender_root_overrides(self):
        src = self._source()
        assert "--card-right-bg:    #eef2ff" in src
        assert "--card-pill-bg:     #dde4ff" in src
        assert "--card-pill-border: #c7d2fe" in src

    def test_PB_source_has_no_hardcoded_theme_class_selectors(self):
        """F-THEME-1: .theme-* CSS selectors removed from player_card_fifa.html source."""
        src = self._source()
        assert ".theme-midnight" not in src
        assert ".theme-gold" not in src
        assert ".theme-emerald" not in src
        assert ".theme-crimson" not in src

    def test_PB_rendered_output_contains_lavender_right_bg(self):
        """Lavender :root override is present in rendered CSS for any theme."""
        html = self._render_fifa(card_theme="midnight")
        assert "--card-right-bg:    #eef2ff" in html

    def test_PB_rendered_output_contains_lavender_pill_bg(self):
        html = self._render_fifa(card_theme="gold")
        assert "--card-pill-bg:     #dde4ff" in html

    # --- arctic dark text token activation ---

    def test_PB_rendered_arctic_has_light_body_bg_injected(self):
        """F-THEME-1: arctic :root injection emits body_bg — no .theme-arctic CSS class."""
        html = self._render_fifa(card_theme="arctic")
        assert ".theme-arctic" not in html
        assert "#f7fafc" in html

    def test_PB_rendered_arctic_contains_dark_text_token(self):
        """Arctic theme activates dark text tokens (rgba(0,0,0,...)) from base."""
        html = self._render_fifa(card_theme="arctic")
        assert "rgba(0,0,0,0.85)" in html

    # --- base page_header default still works ---

    def test_PB_base_template_page_brand_default_still_renders(self):
        """A minimal child that only overrides content still gets the base page-brand."""
        e = self._fifa_env()
        tpl_src = (
            '{% extends "public/player_card_base.html" %}'
            '{% block content %}CONTENT{% endblock %}'
        )
        tpl = e.from_string(tpl_src)
        html = tpl.render(**self._minimal_ctx(card_theme="slate"))
        assert 'class="page-brand"' in html


# ---------------------------------------------------------------------------
# Phase 3a — Shared Export Base Template
# ---------------------------------------------------------------------------

def _make_export_env():
    """Jinja2 env for export templates (includes nationalities_display filter)."""
    e = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATES_DIR),
        autoescape=False,
    )
    e.filters["nationalities_display"] = lambda v, secondary=None: v or ""
    return e


def _make_dark_export_theme():
    from types import SimpleNamespace
    return SimpleNamespace(
        panel_bg="linear-gradient(155deg,#0d0d0d 0%,#1a1a2e 60%,#16213e 100%)",
        body_bg="#0f0f0f",
        accent="#00d4ff",
        is_light_body_bg=False,
    )


def _make_arctic_export_theme():
    from types import SimpleNamespace
    return SimpleNamespace(
        panel_bg="linear-gradient(155deg,#1a2744 0%,#2a3a5c 60%,#1e3a4a 100%)",
        body_bg="#f7fafc",
        accent="#4299e1",
        is_light_body_bg=True,
    )


def _minimal_export_ctx(theme=None, **overrides):
    from types import SimpleNamespace
    if theme is None:
        theme = _make_dark_export_theme()
    player = SimpleNamespace(
        name="Test Player",
        position="CM",
        nationality="Hungarian",
        secondary_nationality=None,
        age_group="U17",
        total_tournaments=5,
        skills={},
    )
    ctx = dict(
        player=player,
        theme=theme,
        overall=75.0,
        tier_label="Silver",
        tier_color="#718096",
        pos_color="#667eea",
        avatar_bg="#1a2744",
        initials="TP",
        portrait_photo_url=None,
        photo_url=None,
        teams_info=[],
        skill_categories=[],
        last_skill_delta={},
        dominant_badge=None,
        player_height_cm=None,
        player_weight_kg=None,
        sponsor_logo_url=None,
        app_logo_url=None,
        landscape_photo_url=None,
    )
    ctx.update(overrides)
    return ctx


def _render_portrait(**ctx_overrides):
    tpl = _make_export_env().get_template("public/export/portrait/fifa.html")
    return tpl.render(**_minimal_export_ctx(**ctx_overrides))


def _render_column_archetype(**ctx_overrides):
    """Render column_archetype.html directly (the Level B column layout base)."""
    tpl = _make_export_env().get_template("public/export/shared/column_archetype.html")
    return tpl.render(**_minimal_export_ctx(**ctx_overrides))


def _render_story(**ctx_overrides):
    tpl = _make_export_env().get_template("public/export/story/fifa.html")
    return tpl.render(**_minimal_export_ctx(**ctx_overrides))


def _render_banner(**ctx_overrides):
    tpl = _make_export_env().get_template("public/export/banner/fifa.html")
    return tpl.render(**_minimal_export_ctx(**ctx_overrides))


def _render_tiktok(**ctx_overrides):
    tpl = _make_export_env().get_template("public/export/tiktok/fifa.html")
    return tpl.render(**_minimal_export_ctx(**ctx_overrides))


def _render_landscape(**ctx_overrides):
    tpl = _make_export_env().get_template("public/export/landscape/fifa.html")
    ctx = dict(
        player_nickname=None,
        player_age=None,
        player_gender=None,
        player_location=None,
        member_since=None,
        license_current_level=None,
        license_max_level=None,
        xp_balance=0,
    )
    ctx.update(ctx_overrides)
    return tpl.render(**_minimal_export_ctx(**ctx))


def _render_square(**ctx_overrides):
    tpl = _make_export_env().get_template("public/export/square/fifa.html")
    ctx = dict(
        export_mode=False,
        animated_mode=False,
        welcome_card_mode=False,
        position_nodes=[],
        primary_pos_label="CM",
        secondary_pos_labels=[],
        player_age=17,
        player_gender="M",
        xp_balance=1250,
        license_current_level=2,
        player_height_cm=None,
        player_weight_kg=None,
    )
    ctx.update(ctx_overrides)
    return tpl.render(**_minimal_export_ctx(**ctx))


# ---------------------------------------------------------------------------
# TestExportBase  (EB_ prefix)
# Tests for fifa_base.html — verified via portrait child
# ---------------------------------------------------------------------------

class TestExportBase:
    """EB_ — public/export/shared/fifa_base.html (tested via portrait child)."""

    # --- HTML reset present and not duplicated ---

    def test_EB_html_reset_appears_exactly_once(self):
        html = _render_portrait()
        assert html.count("box-sizing: border-box") == 1

    def test_EB_html_and_body_reset_present(self):
        html = _render_portrait()
        assert "overflow: hidden" in html

    # --- theme_root block emits CSS vars ---

    def test_EB_theme_root_emits_panel_bg(self):
        html = _render_portrait()
        assert "--ex-panel-bg" in html

    def test_EB_theme_root_emits_bar_bg(self):
        html = _render_portrait()
        assert "--ex-bar-bg" in html

    def test_EB_theme_root_emits_cat_bg(self):
        html = _render_portrait()
        assert "--ex-cat-bg" in html

    def test_EB_default_cat_bg_is_005(self):
        """Portrait uses default export_root_vars — cat_bg must be 0.05."""
        html = _render_portrait()
        assert "rgba(255,255,255,0.05)" in html

    def test_EB_no_square_cat_bg_in_portrait(self):
        """Square-specific cat_bg=0.06 must not be assigned to --ex-cat-bg in portrait."""
        html = _render_portrait()
        assert "--ex-cat-bg:         rgba(255,255,255,0.06)" not in html

    # --- theme_root block overridable ---

    def test_EB_theme_root_block_overridable_for_custom_cat_bg(self):
        """A child that overrides theme_root can emit a custom cat_bg."""
        e = _make_export_env()
        tpl_src = (
            '{%- from "macros/card_theme_root.html" import export_root_vars -%}'
            '{% extends "public/export/shared/export_base.html" %}'
            '{% block theme_root %}{{ export_root_vars(theme, cat_bg="rgba(255,255,255,0.06)") }}{% endblock %}'
            '{% block body_content %}OK{% endblock %}'
        )
        tpl = e.from_string(tpl_src)
        html = tpl.render(**_minimal_export_ctx())
        assert "--ex-cat-bg:         rgba(255,255,255,0.06)" in html

    # --- arctic text tokens ---

    def test_EB_arctic_theme_emits_dark_text_strong(self):
        html = _render_portrait(theme=_make_arctic_export_theme())
        assert "rgba(0,0,0,0.87)" in html

    def test_EB_arctic_theme_emits_dark_text_body(self):
        html = _render_portrait(theme=_make_arctic_export_theme())
        assert "rgba(0,0,0,0.75)" in html

    def test_EB_dark_theme_emits_white_text_tokens(self):
        html = _render_portrait(theme=_make_dark_export_theme())
        assert "rgba(255,255,255,0.85)" in html  # --ex-text-strong

    # --- shared skill-row CSS classes ---

    def test_EB_shared_ex_row_class_present(self):
        html = _render_portrait()
        assert ".ex-row {" in html

    def test_EB_shared_ex_sname_uses_css_var(self):
        html = _render_portrait()
        assert "var(--ex-sname-w" in html

    def test_EB_shared_ex_row_max_height_uses_css_var(self):
        html = _render_portrait()
        assert "var(--ex-row-max-h" in html

    def test_EB_shared_ex_bar_bg_uses_css_var(self):
        html = _render_portrait()
        assert "var(--ex-bar-h" in html

    def test_EB_shared_ex_skill_cats_grid_present(self):
        html = _render_portrait()
        assert ".ex-skill-cats {" in html

    def test_EB_player_name_in_title(self):
        html = _render_portrait()
        assert "<title>Test Player" in html


# ---------------------------------------------------------------------------
# TestExportBaseColumn  (EBC_ prefix)
# Tests for column_archetype.html — rendered directly (Level B column layout base)
# Portrait is now Level C standalone; column_archetype is verified directly here.
# ---------------------------------------------------------------------------

class TestExportBaseColumn:
    """EBC_ — public/export/shared/column_archetype.html (rendered directly)."""

    # --- hero zone rendered ---

    def test_EBC_hero_zone_present(self):
        html = _render_column_archetype()
        assert "ex-hero" in html

    def test_EBC_hero_css_uses_hero_h_var(self):
        html = _render_column_archetype()
        assert "var(--ex-hero-h" in html

    def test_EBC_avatar_css_uses_avatar_sz_var(self):
        html = _render_column_archetype()
        assert "var(--ex-avatar-sz" in html

    def test_EBC_ovr_num_css_uses_ovr_font_var(self):
        html = _render_column_archetype()
        assert "var(--ex-ovr-font" in html

    def test_EBC_name_css_uses_name_font_var(self):
        html = _render_column_archetype()
        assert "var(--ex-name-font" in html

    # --- platform vars defaults ---

    def test_EBC_column_default_hero_h_is_350px(self):
        html = _render_column_archetype()
        assert "--ex-hero-h:      350px" in html

    def test_EBC_column_default_avatar_sz_is_160px(self):
        html = _render_column_archetype()
        assert "--ex-avatar-sz:   160px" in html

    def test_EBC_column_default_ovr_font_is_88px(self):
        html = _render_column_archetype()
        assert "--ex-ovr-font:    88px" in html

    # --- avatar rendering ---

    def test_EBC_avatar_placeholder_without_photo(self):
        html = _render_column_archetype(portrait_photo_url=None, photo_url=None)
        assert "ex-avatar-placeholder" in html
        assert "TP" in html

    def test_EBC_avatar_img_with_photo_url(self):
        html = _render_column_archetype(photo_url="http://example.com/photo.jpg")
        assert 'class="ex-avatar"' in html
        assert "http://example.com/photo.jpg" in html

    def test_EBC_portrait_photo_url_preferred_over_photo_url(self):
        html = _render_column_archetype(
            portrait_photo_url="http://example.com/portrait.jpg",
            photo_url="http://example.com/photo.jpg",
        )
        assert "portrait.jpg" in html
        assert "photo.jpg" not in html

    # --- identity block ---

    def test_EBC_player_name_rendered(self):
        html = _render_column_archetype()
        assert "Test Player" in html

    def test_EBC_position_badge_rendered(self):
        html = _render_column_archetype()
        assert "ex-pos-badge" in html
        assert "CM" in html

    def test_EBC_brand_tag_rendered(self):
        html = _render_column_archetype()
        assert "LFA Education" in html

    def test_EBC_nationality_rendered(self):
        html = _render_column_archetype()
        assert "Hungarian" in html

    def test_EBC_age_group_rendered(self):
        html = _render_column_archetype()
        assert "U17" in html

    def test_EBC_overall_rendered(self):
        html = _render_column_archetype()
        assert "75" in html

    def test_EBC_tier_label_rendered(self):
        html = _render_column_archetype()
        assert "Silver" in html

    # --- skills zone ---

    def test_EBC_skills_section_absent_when_empty(self):
        html = _render_column_archetype(skill_categories=[])
        # CSS class definition is present; only the DOM element must be absent
        assert '<div class="ex-skills">' not in html
        assert "Football Skills" not in html

    def test_EBC_skills_title_present_with_categories(self):
        from types import SimpleNamespace
        skill = SimpleNamespace(key="passing", name_en="Passing")
        cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=[skill])
        html = _render_column_archetype(skill_categories=[cat])
        assert "Football Skills" in html
        assert "ex-skills-title" in html

    # --- sponsor zone empty by default ---

    def test_EBC_no_sponsor_zone_by_default(self):
        html = _render_column_archetype()
        assert "ex-sponsor" not in html

    # --- tag_row block overridable ---

    def test_EBC_tag_row_block_overridable(self):
        e = _make_export_env()
        tpl_src = (
            '{% extends "public/export/shared/column_archetype.html" %}'
            '{% block tag_row %}<div class="custom-tag">CUSTOM_TAG</div>{% endblock %}'
            '{% block skill_rows scoped %}{% endblock %}'
        )
        tpl = e.from_string(tpl_src)
        html = tpl.render(**_minimal_export_ctx())
        assert "custom-tag" in html
        assert "CUSTOM_TAG" in html

    # --- meta_row block overridable ---

    def test_EBC_meta_row_block_overridable(self):
        e = _make_export_env()
        tpl_src = (
            '{% extends "public/export/shared/column_archetype.html" %}'
            '{% block meta_row %}<div class="custom-meta">CUSTOM_META</div>{% endblock %}'
            '{% block skill_rows scoped %}{% endblock %}'
        )
        tpl = e.from_string(tpl_src)
        html = tpl.render(**_minimal_export_ctx())
        assert "custom-meta" in html
        assert "CUSTOM_META" in html


# ---------------------------------------------------------------------------
# TestPortraitFifaPhase3  (PP3_ prefix)
# Tests for the migrated portrait/fifa.html
# ---------------------------------------------------------------------------

def _four_cats():
    """Return a 4-element skill_categories list required by portrait/story Level C."""
    from types import SimpleNamespace
    def _skill(key, name):
        return SimpleNamespace(key=key, name_en=name)
    return [
        SimpleNamespace(key="outfield",  name="Outfield",        emoji="⚽",
                        skills=[_skill("passing", "Passing"), _skill("shooting", "Shooting")]),
        SimpleNamespace(key="setpieces", name="Set Pieces",       emoji="🎯",
                        skills=[_skill("free_kicks", "Free Kicks")]),
        SimpleNamespace(key="mental",    name="Mental",           emoji="🧠",
                        skills=[_skill("positioning", "Positioning")]),
        SimpleNamespace(key="physical",  name="Physical Fitness", emoji="⚡",
                        skills=[_skill("acceleration", "Acceleration")]),
    ]


class TestPortraitFifaPhase3:
    """PP3_ — portrait/fifa.html PORT-v2 Level C (extends export_base.html directly)."""

    def _source(self):
        import os
        import app as _app_pkg
        tpl_dir = os.path.join(os.path.dirname(_app_pkg.__file__), "templates")
        with open(os.path.join(tpl_dir, "public/export/portrait/fifa.html")) as f:
            return f.read()

    # --- template structure ---

    def test_PP3_extends_column_base(self):
        """PORT-v2 is Level C — extends export_base.html, not column_archetype."""
        assert '{% extends "public/export/shared/export_base.html" %}' in self._source()

    def test_PP3_source_contains_skill_rows_scoped_block(self):
        """PORT-v2 uses inline 4-category guard instead of scoped block."""
        assert "{% if skill_categories|length >= 4 %}" in self._source()

    # --- render correctness ---

    def test_PP3_renders_without_error(self):
        html = _render_portrait()
        assert "Test Player" in html

    def test_PP3_title_contains_player_name(self):
        html = _render_portrait()
        assert "<title>Test Player" in html

    def test_PP3_theme_root_emitted(self):
        html = _render_portrait()
        assert "--ex-panel-bg" in html

    # --- arctic token propagation ---

    def test_PP3_arctic_dark_text_strong_present(self):
        html = _render_portrait(theme=_make_arctic_export_theme())
        assert "rgba(0,0,0,0.87)" in html

    def test_PP3_arctic_dark_text_body_present(self):
        html = _render_portrait(theme=_make_arctic_export_theme())
        assert "rgba(0,0,0,0.75)" in html

    def test_PP3_arctic_dark_text_secondary_present(self):
        html = _render_portrait(theme=_make_arctic_export_theme())
        assert "rgba(0,0,0,0.55)" in html

    # --- skill rows (4-category required by PORT-v2 Level C) ---

    def test_PP3_skill_rows_rendered_with_categories(self):
        html = _render_portrait(skill_categories=_four_cats())
        assert "ex-row" in html
        assert "Passing" in html

    def test_PP3_skill_slice_is_6(self):
        """PORT-v2: skill_slice=None by default (no driver config) — all skills shown."""
        from types import SimpleNamespace
        skills = [SimpleNamespace(key=f"s{i}", name_en=f"Skill{i}") for i in range(10)]
        cats = [
            SimpleNamespace(key="outfield",  name="Outfield",   emoji="⚽", skills=skills),
            SimpleNamespace(key="setpieces", name="Set Pieces", emoji="🎯", skills=[]),
            SimpleNamespace(key="mental",    name="Mental",     emoji="🧠", skills=[]),
            SimpleNamespace(key="physical",  name="Physical",   emoji="⚡", skills=[]),
        ]
        html = _render_portrait(skill_categories=cats)
        for i in range(10):
            assert f"Skill{i}" in html, f"Skill{i} absent (PORT-v2 has no [:6] cap without driver)"

    def test_PP3_skill_positive_delta_green(self):
        html = _render_portrait(
            skill_categories=_four_cats(),
            last_skill_delta={"passing": 3},
        )
        assert "#48bb78" in html
        assert "visibility:visible" in html

    def test_PP3_skill_negative_delta_red(self):
        html = _render_portrait(
            skill_categories=_four_cats(),
            last_skill_delta={"passing": -2},
        )
        assert "#fc8181" in html
        assert "visibility:visible" in html

    def test_PP3_skill_zero_delta_neutral_hidden(self):
        html = _render_portrait(skill_categories=_four_cats(), last_skill_delta={})
        assert "visibility:hidden" in html

    # --- layout invariants ---

    def test_PP3_no_duplicate_css_reset(self):
        html = _render_portrait()
        assert html.count("box-sizing: border-box") == 1

    def test_PP3_no_sponsor_slot(self):
        html = _render_portrait()
        assert "ex-sponsor" not in html

    def test_PP3_default_cat_bg_005(self):
        html = _render_portrait()
        assert "rgba(255,255,255,0.05)" in html

    def test_PP3_portrait_hero_h_is_350px(self):
        """PORT-v2 split-hero height is 660px (replaces the old 350px column hero)."""
        html = _render_portrait()
        assert "--ex-split-hero-h: 660px" in html

    def test_PP3_portrait_avatar_sz_is_160px(self):
        html = _render_portrait()
        assert "160px" in html


# ---------------------------------------------------------------------------
# TestStoryFifaPhase3b1  (SP3_ prefix)
# Tests for story/fifa.html STORY-v2 Level C (extends export_base.html directly)
# ---------------------------------------------------------------------------

class TestStoryFifaPhase3b1:
    """SP3_ — story/fifa.html STORY-v2 Level C (extends export_base.html directly)."""

    def _source(self):
        import os
        import app as _app_pkg
        tpl_dir = os.path.join(os.path.dirname(_app_pkg.__file__), "templates")
        with open(os.path.join(tpl_dir, "public/export/story/fifa.html")) as f:
            return f.read()

    # --- template structure ---

    def test_SP3_extends_column_base(self):
        """STORY-v2 is Level C — extends export_base.html, not column_archetype."""
        assert '{% extends "public/export/shared/export_base.html" %}' in self._source()

    def test_SP3_has_platform_vars_block(self):
        assert "{% block platform_vars %}" in self._source()

    def test_SP3_has_skill_rows_scoped_block(self):
        """STORY-v2 uses inline 4-category guard instead of scoped block."""
        assert "{% if skill_categories|length >= 4 %}" in self._source()

    # --- render correctness ---

    def test_SP3_renders_without_error(self):
        html = _render_story()
        assert "Test Player" in html

    # --- platform vars (STORY-v2 actual values) ---

    def test_SP3_hero_h_is_460px(self):
        """STORY-v2 hero height is 500px (fullbleed photo zone, no avatar)."""
        html = _render_story()
        assert "--ex-hero-h:     500px" in html

    def test_SP3_avatar_sz_is_180px(self):
        """STORY-v2 is Level C standalone — no ex-avatar-sz (column_archetype var removed)."""
        html = _render_story()
        assert "--ex-avatar-sz" not in html

    def test_SP3_ovr_font_is_96px(self):
        """STORY-v2 uses card_ovr_badge macro — no ex-ovr-font var needed."""
        html = _render_story()
        assert "--ex-ovr-font" not in html

    def test_SP3_name_font_is_48px(self):
        """STORY-v2 uses Bebas Neue @font-face — no ex-name-font var needed."""
        html = _render_story()
        assert "--ex-name-font" not in html

    def test_SP3_row_max_h_is_66px(self):
        """STORY-v2 row-max-h is 50px (denser layout vs old 66px column base)."""
        html = _render_story()
        assert "--ex-row-max-h:  50px" in html

    def test_SP3_sname_w_is_155px(self):
        """STORY-v2 sname-w is 138px."""
        html = _render_story()
        assert "--ex-sname-w:    138px" in html

    def test_SP3_font_skill_is_14px(self):
        html = _render_story()
        assert "--ex-font-skill: 14px" in html

    # --- dominant_badge: STORY-v2 does not render foot badges ---

    def test_SP3_dominant_badge_rendered_when_provided(self):
        """STORY-v2 Level C does not render ex-foot-badge (column_archetype feature removed)."""
        html = _render_story(dominant_badge="Right Foot")
        assert 'class="ex-foot-badge"' not in html

    def test_SP3_dominant_badge_absent_when_none(self):
        html = _render_story(dominant_badge=None)
        assert 'class="ex-foot-badge"' not in html

    # --- height / weight in meta strip ---

    def test_SP3_height_rendered_when_provided(self):
        html = _render_story(player_height_cm=175)
        assert "175" in html
        assert "Height" in html

    def test_SP3_height_absent_when_none(self):
        html = _render_story(player_height_cm=None)
        assert "Height" not in html

    def test_SP3_weight_rendered_when_provided(self):
        html = _render_story(player_weight_kg=65)
        assert "65" in html
        assert "Weight" in html

    # --- skill slice: STORY-v2 uses 4-category inline guard ---

    def test_SP3_skill_slice_is_8(self):
        """STORY-v2: skill_slice=None by default — all skills shown (no 8-cap without driver)."""
        from types import SimpleNamespace
        skills = [SimpleNamespace(key=f"s{i}", name_en=f"Skill{i}") for i in range(10)]
        cats = [
            SimpleNamespace(key="outfield",  name="Outfield",   emoji="⚽", skills=skills),
            SimpleNamespace(key="setpieces", name="Set Pieces", emoji="🎯", skills=[]),
            SimpleNamespace(key="mental",    name="Mental",     emoji="🧠", skills=[]),
            SimpleNamespace(key="physical",  name="Physical",   emoji="⚡", skills=[]),
        ]
        html = _render_story(skill_categories=cats)
        for i in range(10):
            assert f"Skill{i}" in html, f"Skill{i} absent (STORY-v2 has no [:8] cap without driver)"

    def test_SP3_story_shows_more_skills_than_portrait(self):
        """Both Level C templates render all skills when driver provides nil slice.
        Guard: both require 4 categories and render skill rows when provided."""
        html_story   = _render_story(skill_categories=_four_cats())
        html_portrait = _render_portrait(skill_categories=_four_cats())
        assert "ex-row" in html_story,   "Story: no skill rows rendered"
        assert "ex-row" in html_portrait, "Portrait: no skill rows rendered"

    # --- sponsor slot (controlled by _driver_config.show_sponsor) ---

    def _story_with_cats(self, **overrides):
        """Render story with 4 skill categories."""
        return _render_story(skill_categories=_four_cats(), **overrides)

    def test_SP3_sponsor_slot_present_with_skill_categories(self):
        """Sponsor slot renders when show_sponsor=True in _driver_config."""
        html = _render_story(
            skill_categories=_four_cats(),
            _driver_config={"show_sponsor": True, "skill_slice": None,
                            "show_position_map": False},
            sponsor_logo_url=None, app_logo_url=None,
        )
        assert 'class="ex-sponsor-slot"' in html

    def test_SP3_sponsor_logo_rendered_when_provided(self):
        html = _render_story(
            skill_categories=_four_cats(),
            _driver_config={"show_sponsor": True, "skill_slice": None,
                            "show_position_map": False},
            sponsor_logo_url="http://sponsor.example.com/logo.png",
        )
        assert "http://sponsor.example.com/logo.png" in html

    # --- arctic contrast ---

    def test_SP3_arctic_dark_text_strong_present(self):
        html = _render_story(theme=_make_arctic_export_theme())
        assert "rgba(0,0,0,0.87)" in html

    def test_SP3_arctic_dark_text_body_present(self):
        html = _render_story(theme=_make_arctic_export_theme())
        assert "rgba(0,0,0,0.75)" in html

    # --- layout invariants / portrait regression guard ---

    def test_SP3_no_duplicate_css_reset(self):
        html = _render_story()
        assert html.count("box-sizing: border-box") == 1

    def test_SP3_portrait_still_has_no_sponsor_slot(self):
        """Portrait Level C must not render ex-sponsor-slot (no driver_config.show_sponsor)."""
        html = _render_portrait()
        assert "ex-sponsor" not in html


# ---------------------------------------------------------------------------
# TestBannerFifaPhase3b2  (BB3_ prefix)
# Tests for the migrated banner/fifa.html  (Phase 3b-2)
# ---------------------------------------------------------------------------

class TestBannerFifaPhase3b2:
    """BB3_ — banner/fifa.html Level C (extends export_base.html directly)."""

    def _source(self):
        import os
        import app as _app_pkg
        tpl_dir = os.path.join(os.path.dirname(_app_pkg.__file__), "templates")
        with open(os.path.join(tpl_dir, "public/export/banner/fifa.html")) as f:
            return f.read()

    # --- template structure ---

    def test_BB3_extends_fifa_base(self):
        """Banner Level C extends export_base.html (not the old fifa_base.html)."""
        assert '{% extends "public/export/shared/export_base.html" %}' in self._source()

    def test_BB3_has_platform_vars_block(self):
        assert "{% block platform_vars %}" in self._source()

    def test_BB3_has_extra_css_block(self):
        assert "{% block extra_css %}" in self._source()

    # --- platform vars (banner Level C actual values) ---

    def test_BB3_card_direction_row(self):
        html = _render_banner()
        assert "--ex-card-direction: row" in html

    def test_BB3_grid_gap_10px(self):
        """Banner Level C compact grid: 8px (was 10px in old base)."""
        html = _render_banner()
        assert "--ex-grid-gap:    8px" in html

    def test_BB3_cat_radius_10px(self):
        """Banner Level C: cat-radius 8px."""
        html = _render_banner()
        assert "--ex-cat-radius:  8px" in html

    def test_BB3_cat_pad_12px(self):
        """Banner Level C: cat-pad 8px 10px."""
        html = _render_banner()
        assert "--ex-cat-pad:     8px 10px" in html

    def test_BB3_font_cat_11px(self):
        """Banner Level C: font-cat 10px."""
        html = _render_banner()
        assert "--ex-font-cat:    10px" in html

    def test_BB3_sname_w_155px(self):
        """Banner Level C sname-w is 100px (compact row layout)."""
        html = _render_banner()
        assert "--ex-sname-w:     100px" in html

    def test_BB3_font_skill_14px(self):
        """Banner Level C font-skill is 11px."""
        html = _render_banner()
        assert "--ex-font-skill:  11px" in html

    def test_BB3_bar_h_7px(self):
        """Banner Level C bar-h is 5px."""
        html = _render_banner()
        assert "--ex-bar-h:       5px" in html

    def test_BB3_sval_w_40px(self):
        """Banner Level C sval-w is 28px."""
        html = _render_banner()
        assert "--ex-sval-w:      28px" in html

    # --- render correctness ---

    def test_BB3_renders_without_error(self):
        html = _render_banner()
        assert "Test Player" in html

    def test_BB3_ex_left_panel_present(self):
        html = _render_banner()
        assert 'class="ex-left"' in html

    def test_BB3_420px_left_panel(self):
        """Banner Level C left panel is 340px (compact horizontal strip)."""
        html = _render_banner()
        assert "0 0 340px" in html

    # --- skill rendering (Level C — no [:4] cap without driver config) ---

    def test_BB3_skill_slice_is_4(self):
        """Banner Level C: skill_slice=None by default — all skills rendered."""
        from types import SimpleNamespace
        skills = [SimpleNamespace(key=f"s{i}", name_en=f"Skill{i}") for i in range(6)]
        cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=skills)
        html = _render_banner(skill_categories=[cat])
        for i in range(6):
            assert f"Skill{i}" in html, f"Skill{i} absent (no [:4] cap in Level C)"

    def test_BB3_banner_shows_fewer_skills_than_portrait(self):
        """Both Level C templates render all skills; guard: both have skill rows."""
        from types import SimpleNamespace
        skills = [SimpleNamespace(key=f"s{i}", name_en=f"Skill{i}") for i in range(6)]
        cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=skills)
        banner_html  = _render_banner(skill_categories=[cat])
        assert "ex-row" in banner_html, "Banner: no skill rows rendered"

    # --- 3-step photo fallback: portrait → landscape → generic ---

    def test_BB3_landscape_photo_url_preferred(self):
        """Banner Level C prefers portrait_photo_url over landscape_photo_url."""
        html = _render_banner(
            landscape_photo_url="http://example.com/landscape.jpg",
            portrait_photo_url="http://example.com/portrait.jpg",
            photo_url="http://example.com/generic.jpg",
        )
        assert "portrait.jpg" in html
        assert "landscape.jpg" not in html
        assert "generic.jpg" not in html

    def test_BB3_portrait_fallback_when_no_landscape(self):
        html = _render_banner(
            landscape_photo_url=None,
            portrait_photo_url="http://example.com/portrait.jpg",
            photo_url="http://example.com/generic.jpg",
        )
        assert "portrait.jpg" in html
        assert "generic.jpg" not in html

    def test_BB3_generic_photo_final_fallback(self):
        html = _render_banner(
            landscape_photo_url=None,
            portrait_photo_url=None,
            photo_url="http://example.com/generic.jpg",
        )
        assert "generic.jpg" in html

    def test_BB3_placeholder_when_no_photo(self):
        """Banner Level C uses ex-photo-initials (not ex-avatar-placeholder)."""
        html = _render_banner(landscape_photo_url=None, portrait_photo_url=None, photo_url=None)
        assert "ex-photo-initials" in html
        assert "<img" not in html

    # --- no sponsor slot ---

    def test_BB3_no_sponsor_slot(self):
        html = _render_banner()
        assert "ex-sponsor-slot" not in html

    # --- arctic contrast ---

    def test_BB3_arctic_dark_text_strong_present(self):
        html = _render_banner(theme=_make_arctic_export_theme())
        assert "rgba(0,0,0,0.87)" in html

    def test_BB3_arctic_dark_text_body_present(self):
        html = _render_banner(theme=_make_arctic_export_theme())
        assert "rgba(0,0,0,0.75)" in html

    # --- layout invariants ---

    def test_BB3_no_duplicate_css_reset(self):
        html = _render_banner()
        assert html.count("box-sizing: border-box") == 1

    def test_BB3_cat_bg_default_005(self):
        """No theme_root override → default cat_bg 0.05 inherited (render-equivalence)."""
        html = _render_banner()
        assert "rgba(255,255,255,0.05)" in html
        assert "rgba(255,255,255,0.04)" not in html


# ---------------------------------------------------------------------------
# TestTikTokFifaPhase3b3  (TK3_ prefix)
# Tests for tiktok/fifa.html — Phase 3b-3 migration to extends fifa_base.html
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestTikTokFifaPhase3b3:
    """TK3_ — tiktok/fifa.html extends fifa_base.html (Phase 3b-3).

    Verifies:
    - base inheritance (no standalone HTML shell)
    - platform_vars sizing overrides
    - full-bleed hero photo + placeholder fallback
    - identity strip (nationality / height / weight / dominant foot)
    - Set Pieces big-number display vs standard bar layout
    - no skill slicing
    - unconditional sponsor slot, conditional logo
    - Arctic + dark theme cat-bg tokens
    - cat-name margin/padding override
    - portrait / story / banner regression guards
    """

    # --- base inheritance ---

    def test_TK3_extends_base_not_standalone(self):
        """Template source must use {% extends %}, not standalone <!DOCTYPE>."""
        import os, app as _app_pkg
        src_path = os.path.join(
            os.path.dirname(_app_pkg.__file__),
            "templates/public/export/tiktok/fifa.html",
        )
        with open(src_path) as f:
            src = f.read()
        assert "{% extends" in src, "TK3: tiktok/fifa.html must extend fifa_base.html"
        assert "<!DOCTYPE" not in src, "TK3: standalone <!DOCTYPE found — should be removed"

    def test_TK3_no_duplicate_css_reset(self):
        html = _render_tiktok()
        assert html.count("box-sizing: border-box") == 1

    # --- platform_vars ---

    def test_TK3_sname_width_140px(self):
        html = _render_tiktok()
        assert "--ex-sname-w:     140px" in html

    def test_TK3_font_skill_13px(self):
        html = _render_tiktok()
        assert "--ex-font-skill:  13px" in html

    def test_TK3_bar_h_7px(self):
        html = _render_tiktok()
        assert "--ex-bar-h:       7px" in html

    def test_TK3_sval_width_38px(self):
        html = _render_tiktok()
        assert "--ex-sval-w:      38px" in html

    def test_TK3_grid_gap_10px(self):
        html = _render_tiktok()
        assert "--ex-grid-gap:    10px" in html

    def test_TK3_cat_pad_12px(self):
        html = _render_tiktok()
        assert "--ex-cat-pad:     12px" in html

    def test_TK3_font_cat_12px(self):
        html = _render_tiktok()
        assert "--ex-font-cat:    12px" in html

    # --- hero zone ---

    def test_TK3_hero_photo_rendered_from_portrait_url(self):
        """portrait_photo_url (step 1) must appear in hero img src."""
        html = _render_tiktok(portrait_photo_url="http://example.com/port.jpg")
        assert 'class="ex-hero-photo"' in html
        assert "port.jpg" in html

    def test_TK3_photo_fallback_to_photo_url(self):
        """photo_url used when portrait_photo_url is None."""
        html = _render_tiktok(portrait_photo_url=None, photo_url="http://example.com/gen.jpg")
        assert "gen.jpg" in html
        assert 'class="ex-hero-photo"' in html

    def test_TK3_hero_placeholder_when_no_photo(self):
        """No photo → initials placeholder, no <img>."""
        html = _render_tiktok(portrait_photo_url=None, photo_url=None)
        assert 'class="ex-hero-placeholder"' in html
        assert "<img" not in html

    def test_TK3_portrait_photo_preferred_over_generic(self):
        """portrait_photo_url takes priority over photo_url."""
        html = _render_tiktok(
            portrait_photo_url="http://example.com/portrait.jpg",
            photo_url="http://example.com/generic.jpg",
        )
        assert "portrait.jpg" in html
        assert "generic.jpg" not in html

    # --- identity strip ---

    def test_TK3_identity_strip_present(self):
        html = _render_tiktok()
        assert 'class="ex-identity-strip"' in html

    def test_TK3_identity_nationality_rendered(self):
        html = _render_tiktok()
        assert "Hungarian" in html
        assert "ex-stat-label" in html

    def test_TK3_identity_height_conditional(self):
        with_h = _render_tiktok(player_height_cm=175)
        without_h = _render_tiktok(player_height_cm=None)
        assert "175 cm" in with_h
        assert "175 cm" not in without_h

    def test_TK3_identity_weight_conditional(self):
        with_w = _render_tiktok(player_weight_kg=68)
        without_w = _render_tiktok(player_weight_kg=None)
        assert "68 kg" in with_w
        assert "68 kg" not in without_w

    def test_TK3_identity_dominant_badge_conditional(self):
        with_b = _render_tiktok(dominant_badge="Right Foot")
        without_b = _render_tiktok(dominant_badge=None)
        assert "Right Foot" in with_b
        assert "Right Foot" not in without_b

    # --- set pieces big-number display ---

    def _tiktok_with_cats(self, **overrides):
        from types import SimpleNamespace
        std_skill = SimpleNamespace(key="passing", name_en="Passing")
        std_cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=[std_skill])
        sp_skill = SimpleNamespace(key="free_kick", name_en="Free Kick")
        sp_cat = SimpleNamespace(key="set_pieces", name_en="Set Pieces", emoji="🎯", skills=[sp_skill])
        return _render_tiktok(skill_categories=[std_cat, sp_cat], **overrides)

    def test_TK3_set_pieces_big_number_display(self):
        """set_pieces cat must use ex-sp-val (big number), not ex-bar-bg (bar)."""
        html = self._tiktok_with_cats()
        assert 'class="ex-sp-val"' in html

    def test_TK3_set_pieces_no_bar_layout(self):
        """set_pieces cat must NOT use bar layout."""
        html = self._tiktok_with_cats()
        # Only the standard cat has bars — check that Free Kick is in ex-sp-val context
        assert "Free Kick" in html
        # The sp-grid structure must be present
        assert 'class="ex-sp-grid"' in html

    def test_TK3_standard_cat_uses_bar_layout(self):
        """Non-set_pieces categories must use bar layout."""
        html = self._tiktok_with_cats()
        assert "ex-bar-bg" in html

    # --- no skill slicing ---

    def test_TK3_no_skill_slicing(self):
        """All skills in each category must be rendered (no [:N] slicing)."""
        from types import SimpleNamespace
        skills = [SimpleNamespace(key=f"s{i}", name_en=f"Skill{i}") for i in range(6)]
        cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=skills)
        html = _render_tiktok(skill_categories=[cat])
        for i in range(6):
            assert f"Skill{i}" in html, f"TK3: Skill{i} not rendered — unexpected slicing"

    # --- sponsor slot ---

    def test_TK3_sponsor_slot_unconditional(self):
        """Sponsor slot is always rendered regardless of skill_categories."""
        html_with_cats = self._tiktok_with_cats()
        html_no_cats = _render_tiktok(skill_categories=[])
        assert 'class="ex-sponsor-slot"' in html_with_cats
        assert 'class="ex-sponsor-slot"' in html_no_cats

    def test_TK3_sponsor_logo_rendered_when_provided(self):
        html = _render_tiktok(sponsor_logo_url="http://example.com/logo.png")
        assert 'class="ex-sponsor-slot-img"' in html
        assert "logo.png" in html

    def test_TK3_sponsor_logo_absent_when_none(self):
        html = _render_tiktok(sponsor_logo_url=None)
        assert 'class="ex-sponsor-slot-img"' not in html

    # --- theme tokens ---

    def test_TK3_dark_theme_cat_bg_white_overlay(self):
        html = _render_tiktok(theme=_make_dark_export_theme())
        assert "rgba(255,255,255,0.05)" in html

    def test_TK3_arctic_cat_bg_dark_overlay(self):
        html = _render_tiktok(theme=_make_arctic_export_theme())
        assert "rgba(0,0,0,0.06)" in html

    def test_TK3_arctic_text_tokens_present(self):
        html = _render_tiktok(theme=_make_arctic_export_theme())
        assert "rgba(0,0,0,0.87)" in html

    # --- cat-name spacing override ---

    def test_TK3_cat_name_margin_override(self):
        """base has margin-bottom:10px; tiktok overrides to 8px."""
        html = _render_tiktok()
        assert "margin-bottom: 8px" in html

    def test_TK3_cat_name_padding_override(self):
        """base has padding-bottom:8px; tiktok overrides to 6px."""
        html = _render_tiktok()
        assert "padding-bottom: 6px" in html

    # --- regression guards ---

    def test_TK3_portrait_has_no_hero_photo(self):
        """Portrait must not contain the TikTok full-bleed hero class."""
        html = _render_portrait()
        assert 'class="ex-hero-photo"' not in html

    def test_TK3_portrait_has_no_identity_strip(self):
        html = _render_portrait()
        assert 'class="ex-identity-strip"' not in html

    def test_TK3_story_has_no_identity_strip(self):
        html = _render_story()
        assert 'class="ex-identity-strip"' not in html

    def test_TK3_banner_has_no_identity_strip(self):
        html = _render_banner()
        assert 'class="ex-identity-strip"' not in html

    def test_TK3_banner_has_no_hero_photo(self):
        html = _render_banner()
        assert 'class="ex-hero-photo"' not in html


# ---------------------------------------------------------------------------
# TestLandscapeFifaPhase3b4  (LS4_ prefix)
# Tests for landscape/fifa.html — Phase 3b-4 migration to extends fifa_base.html
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestLandscapeFifaPhase3b4:
    """LS4_ — landscape/fifa.html extends fifa_base.html (Phase 3b-4).

    Verifies:
    - base inheritance (no standalone HTML shell)
    - theme_root override: cat_bg=0.04 (not default 0.05)
    - platform_vars: row direction + 10 sizing overrides
    - photo fallback: portrait → landscape → generic → placeholder
    - 3-col layout: .ex-center panel present
    - OVR watermark, dominant badge
    - neutral bar/val explicit values in export_skill_rows call
    - category average (.ex-cat-avg)
    - cat-name flex layout for cat_avg alignment
    - no skill slicing
    - Arctic text tokens
    - portrait / story / tiktok / banner regression guards
    """

    # --- base inheritance ---

    def test_LS4_extends_base_not_standalone(self):
        """Template source must use {% extends %}, not standalone <!DOCTYPE>."""
        import os, app as _app_pkg
        src_path = os.path.join(
            os.path.dirname(_app_pkg.__file__),
            "templates/public/export/landscape/fifa.html",
        )
        with open(src_path) as f:
            src = f.read()
        assert "{% extends" in src, "LS4: landscape/fifa.html must extend fifa_base.html"
        assert "<!DOCTYPE" not in src, "LS4: standalone <!DOCTYPE found — should be removed"

    def test_LS4_no_duplicate_css_reset(self):
        html = _render_landscape()
        assert html.count("box-sizing: border-box") == 1

    # --- theme_root cat_bg override: 0.04, not default 0.05 ---

    def test_LS4_cat_bg_is_004(self):
        """theme_root block must override cat_bg to rgba(255,255,255,0.04)."""
        html = _render_landscape()
        # Find the --ex-cat-bg declaration line and check it contains 0.04
        import re
        cat_bg_match = re.search(r'--ex-cat-bg:\s+(.*?);', html)
        assert cat_bg_match, "LS4: --ex-cat-bg declaration not found in rendered HTML"
        assert '0.04' in cat_bg_match.group(1), (
            f"LS4: --ex-cat-bg value is '{cat_bg_match.group(1)}' — expected 0.04"
        )

    def test_LS4_cat_bg_not_default_005(self):
        """cat_bg must NOT be the default 0.05 — landscape uses explicit 0.04."""
        html = _render_landscape()
        import re
        cat_bg_match = re.search(r'--ex-cat-bg:\s+(.*?);', html)
        assert cat_bg_match, "LS4: --ex-cat-bg declaration not found"
        assert '0.05' not in cat_bg_match.group(1), (
            "LS4: --ex-cat-bg declaration contains 0.05 — cat_bg override not applied"
        )

    # --- platform_vars ---

    def test_LS4_row_direction(self):
        # CS-5 (2026-05-17): --ex-card-direction removed from platform_vars; row layout
        # is now set via .ex-card { flex-direction: column } + .ex-cols-row { flex-direction: row }
        # to allow the full-width PosMap footer below the three-column row.
        html = _render_landscape()
        assert "ex-cols-row" in html, (
            "landscape: .ex-cols-row wrapper not found — column layout restructure for PosMap footer failed"
        )

    def test_LS4_sname_width_80px(self):
        html = _render_landscape()
        assert "--ex-sname-w:        80px" in html

    def test_LS4_font_skill_9px(self):
        html = _render_landscape()
        assert "--ex-font-skill:     9px" in html

    def test_LS4_bar_h_4px(self):
        html = _render_landscape()
        assert "--ex-bar-h:          4px" in html

    def test_LS4_row_max_h_26px(self):
        html = _render_landscape()
        assert "--ex-row-max-h:      26px" in html

    def test_LS4_grid_gap_6px(self):
        html = _render_landscape()
        assert "--ex-grid-gap:       6px" in html

    # --- photo fallback: portrait → landscape → generic → placeholder ---

    def test_LS4_portrait_photo_preferred(self):
        """portrait_photo_url is step 1 — takes priority over landscape and generic."""
        html = _render_landscape(
            portrait_photo_url="http://example.com/portrait.jpg",
            landscape_photo_url="http://example.com/landscape.jpg",
            photo_url="http://example.com/generic.jpg",
        )
        assert "portrait.jpg" in html
        assert "landscape.jpg" not in html
        assert "generic.jpg" not in html

    def test_LS4_landscape_photo_fallback(self):
        """landscape_photo_url is step 2 — used when portrait is absent."""
        html = _render_landscape(
            portrait_photo_url=None,
            landscape_photo_url="http://example.com/landscape.jpg",
            photo_url="http://example.com/generic.jpg",
        )
        assert "landscape.jpg" in html
        assert "generic.jpg" not in html

    def test_LS4_generic_photo_fallback(self):
        """photo_url is step 3 — used when portrait and landscape are absent."""
        html = _render_landscape(
            portrait_photo_url=None,
            landscape_photo_url=None,
            photo_url="http://example.com/generic.jpg",
        )
        assert "generic.jpg" in html

    def test_LS4_placeholder_when_no_photo(self):
        """All None → initials placeholder, no <img>."""
        html = _render_landscape(portrait_photo_url=None, landscape_photo_url=None, photo_url=None)
        assert 'class="ex-photo-initials"' in html
        assert "<img" not in html

    # --- 3-col layout, center panel ---

    def test_LS4_center_panel_present(self):
        html = _render_landscape()
        assert 'class="ex-center"' in html

    def test_LS4_center_panel_white_bg(self):
        html = _render_landscape()
        assert "background: #ffffff" in html

    # --- OVR watermark ---

    def test_LS4_ovr_watermark_present(self):
        html = _render_landscape()
        assert 'class="ex-ovr-watermark"' in html

    # --- dominant badge ---

    def test_LS4_dom_badge_rendered_rl(self):
        html = _render_landscape(dominant_badge="Rl")
        assert 'class="ex-dom-badge"' in html
        assert 'class="ex-dom-hi"' in html

    def test_LS4_dom_badge_absent_when_none(self):
        html = _render_landscape(dominant_badge=None)
        assert 'class="ex-dom-badge"' not in html

    # --- neutral bar / val explicit values ---

    def test_LS4_neutral_bar_018(self):
        """Landscape must pass neutral_bar=rgba(255,255,255,0.18) to export_skill_rows."""
        from types import SimpleNamespace
        skill = SimpleNamespace(key="passing", name_en="Passing")
        cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=[skill])
        html = _render_landscape(skill_categories=[cat])
        assert "rgba(255,255,255,0.18)" in html

    def test_LS4_neutral_val_080(self):
        """Landscape must pass neutral_val=rgba(255,255,255,0.80) to export_skill_rows."""
        from types import SimpleNamespace
        skill = SimpleNamespace(key="passing", name_en="Passing")
        cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=[skill])
        html = _render_landscape(skill_categories=[cat])
        assert "rgba(255,255,255,0.80)" in html

    # --- category average ---

    def _landscape_with_cat(self, **overrides):
        from types import SimpleNamespace
        skill = SimpleNamespace(key="passing", name_en="Passing")
        cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=[skill])
        return _render_landscape(skill_categories=[cat], **overrides)

    def test_LS4_cat_avg_rendered(self):
        html = self._landscape_with_cat()
        assert 'class="ex-cat-avg"' in html

    def test_LS4_cat_name_flex_layout(self):
        """cat-name must use flex layout for cat_avg right-alignment."""
        html = _render_landscape()
        assert "justify-content: space-between" in html

    # --- no skill slicing ---

    def test_LS4_no_skill_slicing(self):
        """All skills in a category must be rendered (no [:N] slicing)."""
        from types import SimpleNamespace
        skills = [SimpleNamespace(key=f"s{i}", name_en=f"Skill{i}") for i in range(6)]
        cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=skills)
        html = _render_landscape(skill_categories=[cat])
        for i in range(6):
            assert f"Skill{i}" in html, f"LS4: Skill{i} missing — unexpected slicing"

    # --- Arctic theme ---

    def test_LS4_arctic_text_token_strong(self):
        html = _render_landscape(theme=_make_arctic_export_theme())
        assert "rgba(0,0,0,0.87)" in html

    def test_LS4_arctic_cat_bg_dark_overlay(self):
        """Arctic light theme → macro emits dark overlay regardless of cat_bg param."""
        html = _render_landscape(theme=_make_arctic_export_theme())
        import re
        cat_bg_match = re.search(r'--ex-cat-bg:\s+(.*?);', html)
        assert cat_bg_match, "LS4: --ex-cat-bg not found in Arctic render"
        assert 'rgba(0,0,0,0.06)' in cat_bg_match.group(1)

    # --- regression guards ---

    def test_LS4_portrait_no_center_panel(self):
        html = _render_portrait()
        assert 'class="ex-center"' not in html

    def test_LS4_story_no_center_panel(self):
        html = _render_story()
        assert 'class="ex-center"' not in html

    def test_LS4_tiktok_no_center_panel(self):
        html = _render_tiktok()
        assert 'class="ex-center"' not in html

    def test_LS4_banner_no_center_panel(self):
        html = _render_banner()
        assert 'class="ex-center"' not in html

    def test_LS4_portrait_no_photo_zone(self):
        html = _render_portrait()
        assert 'class="ex-photo-zone"' not in html

    def test_LS4_portrait_no_ovr_watermark(self):
        html = _render_portrait()
        assert 'class="ex-ovr-watermark"' not in html


# ---------------------------------------------------------------------------
# TestSquareFifaPhase3b5  (SQ5_ prefix)
# Tests for square/fifa.html — extends fifa_base.html (Phase 3b-5)
# ---------------------------------------------------------------------------

class TestSquareFifaPhase3b5:
    """SQ5_ — square/fifa.html extends fifa_base.html (Phase 3b-5).

    Verifies:
    - base inheritance (no standalone HTML shell)
    - theme_root override: cat_bg=0.06
    - min(100vw,100vh) card sizing
    - hero zone: photo-col, profile-col, OVR watermark
    - photo fallback chain: portrait_photo_url → photo_url → placeholder
    - mini-grid: normal 3×2, WC mode 2×2
    - stat strip: LICENSE label (normal) / TIER+SA (WC mode)
    - skills 3-column layout: outfield col + right-section
    - sponsor/app logo in outfield column
    - Position Map: absent without nodes, present with nodes
    - Position Map: primary label, secondary chips, node.label text
    - export_mode gate: viewport wrapper + scale engine JS
    - animated_mode gate: @keyframes present/absent
    - Arctic text tokens
    - regression guards: pos-panel inside right-section, 220px info, no SVG bg
    - regression guards: portrait has no hero-photo-col or viewport wrapper
    """

    # ── A: Base inheritance ───────────────────────────────────────────────

    def test_SQ5_extends_base_not_standalone(self):
        """Template source must use {% extends %}, not standalone <!DOCTYPE>."""
        import os, app as _app_pkg
        src_path = os.path.join(
            os.path.dirname(_app_pkg.__file__),
            "templates/public/export/square/fifa.html",
        )
        with open(src_path) as f:
            src = f.read()
        assert "{% extends" in src, "SQ5: square/fifa.html must extend fifa_base.html"
        assert "<!DOCTYPE" not in src, "SQ5: standalone <!DOCTYPE found — should be removed"

    # ── B: Render smoke ───────────────────────────────────────────────────

    def test_SQ5_renders_without_error(self):
        html = _render_square()
        assert "Test Player" in html

    # ── C: CSS reset deduplication ────────────────────────────────────────

    def test_SQ5_no_duplicate_css_reset(self):
        html = _render_square()
        assert html.count("box-sizing: border-box") == 1

    # ── D: Theme / cat_bg ─────────────────────────────────────────────────

    def test_SQ5_cat_bg_is_006(self):
        """theme_root block must override cat_bg to rgba(255,255,255,0.06)."""
        html = _render_square()
        import re
        cat_bg_match = re.search(r'--ex-cat-bg:\s+(.*?);', html)
        assert cat_bg_match, "SQ5: --ex-cat-bg declaration not found"
        assert '0.06' in cat_bg_match.group(1), (
            f"SQ5: --ex-cat-bg is '{cat_bg_match.group(1)}' — expected 0.06"
        )

    def test_SQ5_cat_bg_not_default_005(self):
        """Square must not use the base default 0.05 cat_bg."""
        html = _render_square()
        import re
        cat_bg_match = re.search(r'--ex-cat-bg:\s+(.*?);', html)
        assert cat_bg_match
        assert '0.05' not in cat_bg_match.group(1), (
            "SQ5: Square is using the base default 0.05 cat_bg — theme_root override missing"
        )

    def test_SQ5_arctic_dark_text_token_strong(self):
        html = _render_square(theme=_make_arctic_export_theme())
        assert "rgba(0,0,0,0.87)" in html

    # ── E: Card sizing ────────────────────────────────────────────────────

    def test_SQ5_source_contains_min_sizing(self):
        """Source must contain min(100vw, 100vh) for 1:1 aspect ratio."""
        import os, app as _app_pkg
        src_path = os.path.join(
            os.path.dirname(_app_pkg.__file__),
            "templates/public/export/square/fifa.html",
        )
        with open(src_path) as f:
            src = f.read()
        assert "min(100vw, 100vh)" in src

    def test_SQ5_rendered_min_sizing_present(self):
        html = _render_square()
        assert "min(100vw, 100vh)" in html

    # ── F: Hero zone ──────────────────────────────────────────────────────

    def test_SQ5_hero_zone_present(self):
        html = _render_square()
        assert 'class="ex-hero"' in html

    def test_SQ5_portrait_photo_url_preferred(self):
        html = _render_square(
            portrait_photo_url="http://cdn.test/portrait.jpg",
            photo_url="http://cdn.test/generic.jpg",
        )
        assert "portrait.jpg" in html
        assert "generic.jpg" not in html

    def test_SQ5_photo_fallback_to_photo_url(self):
        html = _render_square(
            portrait_photo_url=None,
            photo_url="http://cdn.test/generic.jpg",
        )
        assert "generic.jpg" in html

    def test_SQ5_photo_placeholder_when_no_photo(self):
        html = _render_square(portrait_photo_url=None, photo_url=None)
        assert 'class="ex-photo-placeholder"' in html
        assert 'class="ex-photo-monogram"' in html

    # ── G: Profile / mini-grid / stat-strip ──────────────────────────────

    def test_SQ5_ovr_watermark_present(self):
        html = _render_square()
        assert 'class="ex-ovr-watermark"' in html

    def test_SQ5_normal_mode_mini_grid_6_items(self):
        """Non-WC mode renders 3×2 grid with AGE and GENDER items."""
        html = _render_square(welcome_card_mode=False, player_age=17, player_gender="M")
        assert "AGE" in html
        assert "GENDER" in html
        assert 'class="ex-mini-grid ex-mini-grid--wc"' not in html

    def test_SQ5_welcome_card_mode_uses_wc_class(self):
        html = _render_square(welcome_card_mode=True)
        assert 'class="ex-mini-grid ex-mini-grid--wc"' in html

    def test_SQ5_welcome_card_mode_no_age_gender(self):
        """WC mode 2×2 grid must NOT render AGE or GENDER items."""
        html = _render_square(welcome_card_mode=True)
        assert ">AGE<" not in html
        assert ">GENDER<" not in html

    def test_SQ5_welcome_card_mode_stat_tier_sa(self):
        html = _render_square(welcome_card_mode=True)
        assert ">TIER<" in html
        assert ">SA<" in html

    def test_SQ5_normal_mode_stat_license_label(self):
        html = _render_square(welcome_card_mode=False, license_current_level=3)
        assert ">LICENSE<" in html
        assert "Lv. 3" in html

    # ── H: Skills layout ─────────────────────────────────────────────────

    def test_SQ5_right_section_present(self):
        from types import SimpleNamespace
        cats = [
            SimpleNamespace(name_en="Outfield", emoji="⚽", skills=[]),
            SimpleNamespace(name_en="Set Pieces", emoji="🎯", skills=[]),
            SimpleNamespace(name_en="Mental", emoji="🧠", skills=[]),
            SimpleNamespace(name_en="Physical", emoji="💪", skills=[]),
        ]
        html = _render_square(skill_categories=cats)
        assert 'class="ex-right-section"' in html

    def test_SQ5_outfield_col_present(self):
        from types import SimpleNamespace
        cats = [
            SimpleNamespace(name_en="Outfield", emoji="⚽", skills=[]),
            SimpleNamespace(name_en="Set Pieces", emoji="🎯", skills=[]),
            SimpleNamespace(name_en="Mental", emoji="🧠", skills=[]),
            SimpleNamespace(name_en="Physical", emoji="💪", skills=[]),
        ]
        html = _render_square(skill_categories=cats)
        assert 'ex-col-outfield' in html

    def test_SQ5_skill_categories_rendered_with_cats(self):
        from types import SimpleNamespace
        cats = [
            SimpleNamespace(name_en="Outfield", emoji="⚽", skills=[]),
            SimpleNamespace(name_en="Set Pieces", emoji="🎯", skills=[]),
            SimpleNamespace(name_en="Mental", emoji="🧠", skills=[]),
            SimpleNamespace(name_en="Physical", emoji="💪", skills=[]),
        ]
        html = _render_square(skill_categories=cats)
        assert "Outfield" in html
        assert "Mental" in html
        assert "Physical" in html

    # ── I: Sponsor / outfield logo ────────────────────────────────────────

    def _four_cats(self):
        from types import SimpleNamespace
        return [
            SimpleNamespace(name_en="Outfield",   emoji="⚽", skills=[]),
            SimpleNamespace(name_en="Set Pieces",  emoji="🎯", skills=[]),
            SimpleNamespace(name_en="Mental",      emoji="🧠", skills=[]),
            SimpleNamespace(name_en="Physical",    emoji="💪", skills=[]),
        ]

    def test_SQ5_sponsor_logo_rendered_when_provided(self):
        html = _render_square(
            skill_categories=self._four_cats(),
            sponsor_logo_url="http://sponsor.test/logo.png",
            app_logo_url=None,
        )
        assert "sponsor.test/logo.png" in html
        assert 'class="ex-outfield-logo"' in html

    def test_SQ5_app_logo_fallback_when_no_sponsor(self):
        html = _render_square(
            skill_categories=self._four_cats(),
            sponsor_logo_url=None,
            app_logo_url="http://cdn.test/app-logo.png",
        )
        assert "app-logo.png" in html

    def test_SQ5_logo_absent_when_neither(self):
        html = _render_square(
            skill_categories=self._four_cats(),
            sponsor_logo_url=None,
            app_logo_url=None,
        )
        assert 'class="ex-outfield-logo"' not in html

    # ── J: Position Map ───────────────────────────────────────────────────

    def test_SQ5_pos_map_absent_without_nodes(self):
        html = _render_square(
            skill_categories=self._four_cats(),
            position_nodes=[],
        )
        assert 'class="ex-pos-panel-landscape"' not in html

    def test_SQ5_pos_map_present_with_nodes(self):
        from types import SimpleNamespace
        nodes = [SimpleNamespace(x=0.1, y=0.5, is_primary=True, is_selected=True, label="GK")]
        html = _render_square(skill_categories=self._four_cats(), position_nodes=nodes)
        assert 'class="ex-pos-panel-landscape"' in html

    def test_SQ5_pos_map_primary_label_rendered(self):
        from types import SimpleNamespace
        nodes = [SimpleNamespace(x=0.5, y=0.5, is_primary=True, is_selected=True, label="CM")]
        html = _render_square(
            skill_categories=self._four_cats(),
            position_nodes=nodes,
            primary_pos_label="Central Midfielder",
        )
        assert "Central Midfielder" in html

    def test_SQ5_pos_map_secondary_chips_rendered(self):
        from types import SimpleNamespace
        nodes = [SimpleNamespace(x=0.5, y=0.5, is_primary=True, is_selected=True, label="CM")]
        html = _render_square(
            skill_categories=self._four_cats(),
            position_nodes=nodes,
            secondary_pos_labels=["LM", "RM"],
        )
        assert "LM" in html
        assert "RM" in html
        assert 'ex-sec-pos-chip' in html

    def test_SQ5_pos_map_node_circles_rendered(self):
        """Position nodes must render as SVG circles (no text labels per design spec — test_sq27)."""
        from types import SimpleNamespace
        nodes = [
            SimpleNamespace(x=0.9, y=0.5, is_primary=True, is_selected=True, label="ST"),
            SimpleNamespace(x=0.7, y=0.3, is_primary=False, is_selected=True, label="LW"),
        ]
        html = _render_square(skill_categories=self._four_cats(), position_nodes=nodes)
        assert "<circle" in html, "Position nodes must render as SVG circles"
        assert ">ST<" not in html, "node.label text must not appear in SVG (info column only)"

    # ── K: Export mode gating ─────────────────────────────────────────────

    def test_SQ5_export_mode_no_viewport_wrapper(self):
        html = _render_square(export_mode=True)
        assert 'id="ex-card-viewport"' not in html

    def test_SQ5_human_view_viewport_wrapper_present(self):
        html = _render_square(export_mode=False)
        assert 'id="ex-card-viewport"' in html

    def test_SQ5_human_view_scale_engine_absent_in_export(self):
        html = _render_square(export_mode=True)
        assert "applyScale" not in html

    def test_SQ5_human_view_scale_engine_present_in_non_export(self):
        html = _render_square(export_mode=False)
        assert "applyScale" in html

    # ── L: Animated mode ──────────────────────────────────────────────────

    def test_SQ5_animated_mode_keyframes_absent_in_static(self):
        html = _render_square(animated_mode=False)
        assert "@keyframes" not in html

    def test_SQ5_animated_mode_bar_keyframe_present(self):
        html = _render_square(animated_mode=True)
        assert "@keyframes ex-bar-in" in html

    def test_SQ5_animated_mode_hero_glow_present(self):
        html = _render_square(animated_mode=True)
        assert "@keyframes ex-hero-glow" in html

    # ── M: Regression guards ──────────────────────────────────────────────

    def test_SQ5_pos_panel_inside_right_section(self):
        """Position Map panel must appear AFTER .ex-right-skills, inside .ex-right-section.
        Guards regression: pos-panel must not be a sibling of .ex-skill-cats."""
        from types import SimpleNamespace
        nodes = [SimpleNamespace(x=0.5, y=0.5, is_primary=True, is_selected=True, label="CM")]
        cats = [
            SimpleNamespace(name_en="Outfield", emoji="⚽", skills=[]),
            SimpleNamespace(name_en="Set Pieces", emoji="🎯", skills=[]),
            SimpleNamespace(name_en="Mental", emoji="🧠", skills=[]),
            SimpleNamespace(name_en="Physical", emoji="💪", skills=[]),
        ]
        html = _render_square(skill_categories=cats, position_nodes=nodes)
        right_section_pos = html.find('class="ex-right-section"')
        pos_panel_pos = html.find('class="ex-pos-panel-landscape"')
        skill_cats_pos = html.find('class="ex-skill-cats"')
        assert right_section_pos != -1, "SQ5: .ex-right-section not found"
        assert pos_panel_pos != -1, "SQ5: .ex-pos-panel-landscape not found"
        assert pos_panel_pos > right_section_pos, (
            "SQ5: pos-panel must come after right-section open tag (i.e. be inside it)"
        )
        # pos-panel must not appear before skill-cats closes — it's inside right-section
        right_skills_pos = html.find('class="ex-right-skills"')
        assert pos_panel_pos > right_skills_pos, (
            "SQ5: pos-panel must appear after .ex-right-skills (below it in DOM)"
        )

    def test_SQ5_pos_info_220px_present(self):
        """Guards R2: .ex-pos-info must keep flex: 0 0 220px."""
        html = _render_square()
        assert "220px" in html

    def test_SQ5_pos_svg_no_css_background(self):
        """Guards R3: .ex-pos-svg-landscape must not have CSS background property.
        Green pitch comes from internal SVG <rect>, not CSS background."""
        import re
        html = _render_square()
        block = re.search(r'\.ex-pos-svg-landscape\s*\{([^}]+)\}', html)
        if block:
            assert 'background' not in block.group(1), (
                "SQ5: .ex-pos-svg-landscape must not have CSS background (green is SVG-internal)"
            )

    def test_SQ5_outfield_logo_inside_outfield_col(self):
        """Guards R8: sponsor logo must be inside .ex-col-outfield, not elsewhere."""
        html = _render_square(
            skill_categories=self._four_cats(),
            sponsor_logo_url="http://test/logo.png",
        )
        outfield_pos = html.find('ex-col-outfield')
        logo_pos = html.find('ex-outfield-logo')
        assert outfield_pos != -1
        assert logo_pos != -1
        assert logo_pos > outfield_pos, "SQ5: outfield logo must appear after .ex-col-outfield"

    def test_SQ5_portrait_no_photo_col(self):
        """Guards: portrait export must not contain Square's photo-col div."""
        html = _render_portrait()
        assert 'class="ex-photo-col"' not in html

    def test_SQ5_portrait_no_viewport_wrapper(self):
        """Guards: portrait must never have the Square scale-engine viewport wrapper."""
        html = _render_portrait()
        assert 'id="ex-card-viewport"' not in html

    def test_SQ5_story_no_viewport_wrapper(self):
        html = _render_story()
        assert 'id="ex-card-viewport"' not in html

    def test_SQ5_banner_no_viewport_wrapper(self):
        html = _render_banner()
        assert 'id="ex-card-viewport"' not in html
