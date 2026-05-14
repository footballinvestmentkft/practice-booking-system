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
        tpl = self._fifa_env().get_template("public/player_card_fifa.html")
        return tpl.render(**self._minimal_ctx(**ctx_overrides))

    def _source(self):
        import os, app as _app_pkg
        tpl_dir = os.path.join(os.path.dirname(_app_pkg.__file__), "templates")
        with open(os.path.join(tpl_dir, "public/player_card_fifa.html")) as f:
            return f.read()

    # --- template structure ---

    def test_PB_fifa_extends_base(self):
        assert '{% extends "public/player_card_base.html" %}' in self._source()

    # --- theme body class (added by base, was missing in old standalone FIFA) ---

    def test_PB_slate_body_has_theme_class(self):
        html = self._render_fifa(card_theme="slate")
        assert 'theme-slate' in html

    def test_PB_arctic_body_has_theme_arctic_class(self):
        html = self._render_fifa(card_theme="arctic")
        assert 'theme-arctic' in html

    def test_PB_midnight_body_has_theme_midnight_class(self):
        html = self._render_fifa(card_theme="midnight")
        assert 'theme-midnight' in html

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

    def test_PB_source_has_dark_theme_lavender_reset_for_all_four_themes(self):
        src = self._source()
        assert ".theme-midnight" in src
        assert ".theme-gold" in src
        assert ".theme-emerald" in src
        assert ".theme-crimson" in src

    def test_PB_rendered_output_contains_lavender_right_bg(self):
        """Lavender :root override is present in rendered CSS for any theme."""
        html = self._render_fifa(card_theme="midnight")
        assert "--card-right-bg:    #eef2ff" in html

    def test_PB_rendered_output_contains_lavender_pill_bg(self):
        html = self._render_fifa(card_theme="gold")
        assert "--card-pill-bg:     #dde4ff" in html

    # --- arctic dark text token activation ---

    def test_PB_rendered_arctic_contains_theme_arctic_css_block(self):
        """Base .theme-arctic block is present in rendered output."""
        html = self._render_fifa(card_theme="arctic")
        assert ".theme-arctic" in html

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
    )
    ctx.update(overrides)
    return ctx


def _render_portrait(**ctx_overrides):
    tpl = _make_export_env().get_template("public/export/portrait/fifa.html")
    return tpl.render(**_minimal_export_ctx(**ctx_overrides))


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
        """Square-specific cat_bg=0.06 must not appear in portrait output."""
        html = _render_portrait()
        assert "rgba(255,255,255,0.06)" not in html

    # --- theme_root block overridable ---

    def test_EB_theme_root_block_overridable_for_custom_cat_bg(self):
        """A child that overrides theme_root can emit a custom cat_bg."""
        e = _make_export_env()
        tpl_src = (
            '{%- from "macros/card_theme_root.html" import export_root_vars -%}'
            '{% extends "public/export/shared/fifa_base.html" %}'
            '{% block theme_root %}{{ export_root_vars(theme, cat_bg="rgba(255,255,255,0.06)") }}{% endblock %}'
            '{% block body_content %}OK{% endblock %}'
        )
        tpl = e.from_string(tpl_src)
        html = tpl.render(**_minimal_export_ctx())
        assert "rgba(255,255,255,0.06)" in html

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
# Tests for fifa_base_column.html — verified via portrait child
# ---------------------------------------------------------------------------

class TestExportBaseColumn:
    """EBC_ — public/export/shared/fifa_base_column.html (tested via portrait child)."""

    # --- hero zone rendered ---

    def test_EBC_hero_zone_present(self):
        html = _render_portrait()
        assert "ex-hero" in html

    def test_EBC_hero_css_uses_hero_h_var(self):
        html = _render_portrait()
        assert "var(--ex-hero-h" in html

    def test_EBC_avatar_css_uses_avatar_sz_var(self):
        html = _render_portrait()
        assert "var(--ex-avatar-sz" in html

    def test_EBC_ovr_num_css_uses_ovr_font_var(self):
        html = _render_portrait()
        assert "var(--ex-ovr-font" in html

    def test_EBC_name_css_uses_name_font_var(self):
        html = _render_portrait()
        assert "var(--ex-name-font" in html

    # --- platform vars defaults match portrait ---

    def test_EBC_column_default_hero_h_is_350px(self):
        html = _render_portrait()
        assert "--ex-hero-h:      350px" in html

    def test_EBC_column_default_avatar_sz_is_160px(self):
        html = _render_portrait()
        assert "--ex-avatar-sz:   160px" in html

    def test_EBC_column_default_ovr_font_is_88px(self):
        html = _render_portrait()
        assert "--ex-ovr-font:    88px" in html

    # --- avatar rendering ---

    def test_EBC_avatar_placeholder_without_photo(self):
        html = _render_portrait(portrait_photo_url=None, photo_url=None)
        assert "ex-avatar-placeholder" in html
        assert "TP" in html

    def test_EBC_avatar_img_with_photo_url(self):
        html = _render_portrait(photo_url="http://example.com/photo.jpg")
        assert 'class="ex-avatar"' in html
        assert "http://example.com/photo.jpg" in html

    def test_EBC_portrait_photo_url_preferred_over_photo_url(self):
        html = _render_portrait(
            portrait_photo_url="http://example.com/portrait.jpg",
            photo_url="http://example.com/photo.jpg",
        )
        assert "portrait.jpg" in html
        assert "photo.jpg" not in html

    # --- identity block ---

    def test_EBC_player_name_rendered(self):
        html = _render_portrait()
        assert "Test Player" in html

    def test_EBC_position_badge_rendered(self):
        html = _render_portrait()
        assert "ex-pos-badge" in html
        assert "CM" in html

    def test_EBC_brand_tag_rendered(self):
        html = _render_portrait()
        assert "LFA Education" in html

    def test_EBC_nationality_rendered(self):
        html = _render_portrait()
        assert "Hungarian" in html

    def test_EBC_age_group_rendered(self):
        html = _render_portrait()
        assert "U17" in html

    def test_EBC_overall_rendered(self):
        html = _render_portrait()
        assert "75" in html

    def test_EBC_tier_label_rendered(self):
        html = _render_portrait()
        assert "Silver" in html

    # --- skills zone ---

    def test_EBC_skills_section_absent_when_empty(self):
        html = _render_portrait(skill_categories=[])
        # CSS class definition is present; only the DOM element must be absent
        assert '<div class="ex-skills">' not in html
        assert "Football Skills" not in html

    def test_EBC_skills_title_present_with_categories(self):
        from types import SimpleNamespace
        skill = SimpleNamespace(key="passing", name_en="Passing")
        cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=[skill])
        html = _render_portrait(skill_categories=[cat])
        assert "Football Skills" in html
        assert "ex-skills-title" in html

    # --- sponsor zone empty by default ---

    def test_EBC_no_sponsor_zone_by_default(self):
        html = _render_portrait()
        assert "ex-sponsor" not in html

    # --- tag_row block overridable ---

    def test_EBC_tag_row_block_overridable(self):
        e = _make_export_env()
        tpl_src = (
            '{% extends "public/export/shared/fifa_base_column.html" %}'
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
            '{% extends "public/export/shared/fifa_base_column.html" %}'
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

class TestPortraitFifaPhase3:
    """PP3_ — public/export/portrait/fifa.html extends fifa_base_column.html."""

    def _source(self):
        import os
        import app as _app_pkg
        tpl_dir = os.path.join(os.path.dirname(_app_pkg.__file__), "templates")
        with open(os.path.join(tpl_dir, "public/export/portrait/fifa.html")) as f:
            return f.read()

    # --- template structure ---

    def test_PP3_extends_column_base(self):
        assert '{% extends "public/export/shared/fifa_base_column.html" %}' in self._source()

    def test_PP3_source_contains_skill_rows_scoped_block(self):
        src = self._source()
        assert "{% block skill_rows scoped %}" in src

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

    # --- skill rows ---

    def test_PP3_skill_rows_rendered_with_categories(self):
        from types import SimpleNamespace
        skill = SimpleNamespace(key="passing", name_en="Passing")
        cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=[skill])
        html = _render_portrait(skill_categories=[cat])
        assert "ex-row" in html
        assert "Passing" in html

    def test_PP3_skill_slice_is_6(self):
        """Portrait renders at most 6 skills per category."""
        from types import SimpleNamespace
        skills = [SimpleNamespace(key=f"s{i}", name_en=f"Skill{i}") for i in range(10)]
        cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=skills)
        html = _render_portrait(skill_categories=[cat])
        for i in range(6):
            assert f"Skill{i}" in html
        for i in range(6, 10):
            assert f"Skill{i}" not in html

    def test_PP3_skill_positive_delta_green(self):
        from types import SimpleNamespace
        skill = SimpleNamespace(key="passing", name_en="Passing")
        cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=[skill])
        html = _render_portrait(
            skill_categories=[cat],
            last_skill_delta={"passing": 3},
        )
        assert "#48bb78" in html
        assert "visibility:visible" in html

    def test_PP3_skill_negative_delta_red(self):
        from types import SimpleNamespace
        skill = SimpleNamespace(key="passing", name_en="Passing")
        cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=[skill])
        html = _render_portrait(
            skill_categories=[cat],
            last_skill_delta={"passing": -2},
        )
        assert "#fc8181" in html
        assert "visibility:visible" in html

    def test_PP3_skill_zero_delta_neutral_hidden(self):
        from types import SimpleNamespace
        skill = SimpleNamespace(key="passing", name_en="Passing")
        cat = SimpleNamespace(key="outfield", name_en="Outfield", emoji="⚽", skills=[skill])
        html = _render_portrait(skill_categories=[cat], last_skill_delta={})
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
        html = _render_portrait()
        assert "350px" in html

    def test_PP3_portrait_avatar_sz_is_160px(self):
        html = _render_portrait()
        assert "160px" in html
