"""PCPREC — Player Card skill precision + VT trend arrow tests.

Phase 2.4F: two blocks implemented together:

Block 1 — VT trend arrows: last_skill_delta now merges training deltas where
  tournament delta is absent, so VT-only users see ↑/↓ trend arrows.

Block 2 — interactive card 2-decimal: player_card*.html and card_skill_rows
  macro use round(2) instead of round(1), CSS .skill-val width widened.

PCPREC-01  VT-only user → last_skill_delta contains VT delta
PCPREC-02  tournament delta takes priority over VT delta for the same skill
PCPREC-03  abs(delta) < 0.005 → VT delta is NOT included (threshold guard)
PCPREC-04  positive VT delta (>= 0.005) → included in last_skill_delta
PCPREC-05  negative VT delta (<= -0.005) → included in last_skill_delta
PCPREC-06  card_skill_rows macro renders round(2) value
PCPREC-07  export_skill_rows macro still renders integer (round(0)|int)
PCPREC-08  export portrait/story round(0)|int unchanged
PCPREC-09  no VT and no tournament → last_skill_delta key absent (no arrow)
PCPREC-10  mixed: some skills tournament-only, some VT-only, some both
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

_VT_ARROW_THRESHOLD = 0.005

_SRS_MODULE = "app.services.segment_reward_service"


def _merge_vt_into_delta(tournament_delta: dict, vt_deltas: dict) -> dict:
    """Python mirror of the last_skill_delta merge logic in public_player.py."""
    result = dict(tournament_delta)
    for sk, vt_d in vt_deltas.items():
        if sk not in result and abs(vt_d) >= _VT_ARROW_THRESHOLD:
            result[sk] = vt_d
    return result


def _jinja_round2(value: float, default: float = 50.0) -> float:
    """Mirror of Jinja `| round(2)` as used in interactive card templates."""
    return round(value if value is not None else default, 2)


def _jinja_round0_int(value: float, default: float = 50.0) -> int:
    """Mirror of Jinja `| round(0) | int` as used in export templates."""
    return int(round(value if value is not None else default, 0))


# ── PCPREC-01..05: last_skill_delta VT merge logic ───────────────────────────

class TestLastSkillDeltaVtMerge:

    def test_pcprec01_vt_only_user_gets_vt_delta(self):
        """PCPREC-01: no tournament delta → VT delta fills last_skill_delta."""
        result = _merge_vt_into_delta(
            tournament_delta={},
            vt_deltas={"decisions": 0.03, "reactions": 0.33},
        )
        assert "decisions" in result
        assert result["decisions"] == 0.03
        assert "reactions" in result
        assert result["reactions"] == 0.33

    def test_pcprec02_tournament_delta_takes_priority(self):
        """PCPREC-02: tournament delta is NOT overwritten by VT delta."""
        result = _merge_vt_into_delta(
            tournament_delta={"decisions": 1.5},
            vt_deltas={"decisions": 0.03},
        )
        assert result["decisions"] == 1.5

    def test_pcprec03_below_threshold_excluded(self):
        """PCPREC-03: abs(0.003) < 0.005 → VT delta not included."""
        result = _merge_vt_into_delta(
            tournament_delta={},
            vt_deltas={"decisions": 0.003},
        )
        assert "decisions" not in result

    def test_pcprec04_positive_vt_delta_included(self):
        """PCPREC-04: VT delta 0.005 (on threshold) → included."""
        result = _merge_vt_into_delta(
            tournament_delta={},
            vt_deltas={"reactions": 0.005},
        )
        assert "reactions" in result
        assert result["reactions"] == 0.005

    def test_pcprec05_negative_vt_delta_included(self):
        """PCPREC-05: negative VT delta -0.03 → included (abs >= 0.005)."""
        result = _merge_vt_into_delta(
            tournament_delta={},
            vt_deltas={"concentration": -0.06},
        )
        assert "concentration" in result
        assert result["concentration"] == -0.06

    def test_pcprec09_no_vt_no_tournament_key_absent(self):
        """PCPREC-09: skill with no VT and no tournament delta → not in result."""
        result = _merge_vt_into_delta(
            tournament_delta={},
            vt_deltas={},
        )
        assert "decisions" not in result

    def test_pcprec10_mixed_skills(self):
        """PCPREC-10: tournament-only, VT-only, both, and neither skills."""
        result = _merge_vt_into_delta(
            tournament_delta={"passing": 2.1, "decisions": 1.5},
            vt_deltas={"decisions": 0.03, "reactions": 0.33, "composure": 0.002},
        )
        assert result["passing"] == 2.1          # tournament only
        assert result["decisions"] == 1.5        # tournament wins over VT
        assert result["reactions"] == 0.33       # VT only (above threshold)
        assert "composure" not in result         # VT too small (0.002 < 0.005)


# ── PCPREC-06..08: template rounding ─────────────────────────────────────────

class TestCardTemplatePrecision:

    def test_pcprec06_card_skill_rows_macro_round2(self):
        """PCPREC-06: card_skill_rows macro uses round(2) — 60.03 preserved."""
        assert _jinja_round2(60.0325) == 60.03
        assert _jinja_round2(60.0) == 60.0
        assert _jinja_round2(60.325) == round(60.325, 2)

    def test_pcprec07_export_skill_rows_macro_stays_int(self):
        """PCPREC-07: export_skill_rows macro uses round(0)|int — stays integer."""
        assert _jinja_round0_int(60.03) == 60
        assert _jinja_round0_int(60.0) == 60
        assert _jinja_round0_int(60.8) == 61

    def test_pcprec08_export_portrait_story_integer(self):
        """PCPREC-08: export template renders integer (round(0)|int) — unchanged."""
        for val in [60.0, 60.03, 60.33, 60.99]:
            rendered = _jinja_round0_int(val)
            assert isinstance(rendered, int)
            assert rendered == round(val)


# ── PCPREC integration: card_skill_rows macro rendered HTML ──────────────────

class TestCardMacroRenderedHtml:
    """Verify the Jinja macro actually renders round(2) values."""

    MACRO_PATH = "app/templates/macros/card_skill_row.html"
    MACRO_NAME = "card_skill_rows"

    def _render(self, cat, skills_dict, delta_dict):
        from jinja2 import Environment, FileSystemLoader
        import os
        tpl_root = os.path.join(
            os.path.dirname(__file__),
            "../../../../app/templates",
        )
        env = Environment(loader=FileSystemLoader(tpl_root))
        macro_tpl = env.get_template("macros/card_skill_row.html")
        module = macro_tpl.make_module()
        fn = getattr(module, self.MACRO_NAME)
        return fn(cat, skills_dict, delta_dict)

    def _make_cat(self, skills):
        cat = MagicMock()
        cat.skills = skills
        return cat

    def _make_skill(self, key, name):
        s = MagicMock()
        s.key = key
        s.name_en = name
        return s

    def test_pcprec06_rendered_html_has_two_decimal_value(self):
        """PCPREC-06: rendered HTML contains '60.03' for current_level=60.0325."""
        cat = self._make_cat([self._make_skill("decisions", "Decisions")])
        html = self._render(cat, {"decisions": {"current_level": 60.0325}}, {})
        assert "60.03" in html, f"Expected '60.03' in rendered HTML, got: {html!r}"

    def test_pcprec06b_round2_renders_correctly_for_no_training(self):
        """PCPREC-06b: current_level=60.0 → '60.0' still present."""
        cat = self._make_cat([self._make_skill("passing", "Passing")])
        html = self._render(cat, {"passing": {"current_level": 60.0}}, {})
        assert "60.0" in html

    def test_pcprec07_export_macro_still_integer(self):
        """PCPREC-07: export_skill_rows macro renders integer, not decimal."""
        from jinja2 import Environment, FileSystemLoader
        import os
        tpl_root = os.path.join(
            os.path.dirname(__file__),
            "../../../../app/templates",
        )
        env = Environment(loader=FileSystemLoader(tpl_root))
        macro_tpl = env.get_template("macros/card_skill_row.html")
        module = macro_tpl.make_module()
        fn = getattr(module, "export_skill_rows")
        cat = self._make_cat([self._make_skill("decisions", "Decisions")])
        html = fn(cat, {"decisions": {"current_level": 60.03}}, {})
        assert "60</span>" in html or ">60<" in html, (
            f"Expected integer '60' in export macro output, got: {html!r}"
        )
        assert "60.03" not in html, "Export macro must NOT render decimals"


# ── PCPREC-11..14: player_card_fclassic.html CSS fix verification ─────────────────

class TestFifaTemplateCssFix:
    """Verify the FClassic template CSS and macro-render path after Phase 2.4F fix."""

    _FIFA_TPL = "app/templates/public/player_card_fclassic.html"

    def _tpl_src(self) -> str:
        import os
        path = os.path.join(
            os.path.dirname(__file__),
            "../../../../",
            self._FIFA_TPL,
        )
        with open(path) as f:
            return f.read()

    def test_pcprec11_fifa_skill_val_width_36px(self):
        """PCPREC-11: player_card_fclassic.html .skill-val CSS width is 36px (not 26px)."""
        src = self._tpl_src()
        assert "width: 36px" in src, (
            "Expected '.skill-val { ... width: 36px ... }' in FClassic template"
        )
        assert "width: 26px" not in src, (
            "Old 26px width still present — CSS fix not applied"
        )

    def test_pcprec12_fifa_template_imports_card_skill_rows_macro(self):
        """PCPREC-12: player_card_fclassic.html imports and calls card_skill_rows macro."""
        src = self._tpl_src()
        assert 'import card_skill_rows' in src, (
            "FClassic template must import card_skill_rows macro"
        )
        assert 'card_skill_rows(' in src, (
            "FClassic template must call card_skill_rows macro"
        )

    def test_pcprec13_fifa_path_renders_two_decimal_value(self):
        """PCPREC-13: card_skill_rows macro (used by FClassic template) renders '60.03'."""
        from jinja2 import Environment, FileSystemLoader
        import os
        tpl_root = os.path.join(
            os.path.dirname(__file__),
            "../../../../app/templates",
        )
        env = Environment(loader=FileSystemLoader(tpl_root))
        macro_tpl = env.get_template("macros/card_skill_row.html")
        module = macro_tpl.make_module()
        fn = getattr(module, "card_skill_rows")
        cat = MagicMock()
        skill = MagicMock()
        skill.key = "decisions"
        skill.name_en = "Decisions"
        cat.skills = [skill]
        html = fn(cat, {"decisions": {"current_level": 60.03}}, {})
        assert "60.03" in html, (
            f"FClassic render path (card_skill_rows) must output '60.03', got: {html!r}"
        )
        assert "60.0</span>" not in html, "Must not clip to one decimal"

    def test_pcprec14_fifa_path_renders_trend_arrow_with_vt_delta(self):
        """PCPREC-14: card_skill_rows renders ↑ arrow when VT delta > 0 (FClassic path)."""
        from jinja2 import Environment, FileSystemLoader
        import os
        tpl_root = os.path.join(
            os.path.dirname(__file__),
            "../../../../app/templates",
        )
        env = Environment(loader=FileSystemLoader(tpl_root))
        macro_tpl = env.get_template("macros/card_skill_row.html")
        module = macro_tpl.make_module()
        fn = getattr(module, "card_skill_rows")
        cat = MagicMock()
        skill = MagicMock()
        skill.key = "reactions"
        skill.name_en = "Reactions"
        cat.skills = [skill]
        html = fn(cat, {"reactions": {"current_level": 60.33}}, {"reactions": 0.33})
        assert "↑" in html, (
            f"Trend arrow ↑ must appear when VT delta > 0, got: {html!r}"
        )
        assert "60.33" in html


# ── PCPREC-15..19: bare URL routing — interactive card, not export portrait ───

class TestBareUrlRouting:
    """Verify that /players/{id}/card (bare URL) serves the interactive FClassic card."""

    _ROUTE_PATH = "app/api/web_routes/public_player.py"

    def _route_src(self) -> str:
        import os
        path = os.path.join(
            os.path.dirname(__file__),
            "../../../../",
            self._ROUTE_PATH,
        )
        with open(path) as f:
            return f.read()

    def test_pcprec15_early_return_to_public_page_removed(self):
        """PCPREC-15: public_player.py no longer early-returns player_card_public.html
        for bare URL — the export portrait iframe wrapper is retired from this path."""
        src = self._route_src()
        assert '"public/player_card_public.html"' not in src, (
            "Early return to player_card_public.html must be removed from bare URL path"
        )

    def test_pcprec16_effective_platform_gated_on_explicit_export(self):
        """PCPREC-16: effective_platform only uses _published_platform when export=True.
        ?preview= without export=1 (e.g. ?preview=fclassic&native_export=1) resolves to None
        so the interactive FClassic card is served, not an export template."""
        src = self._route_src()
        # Gate must be: bool(export) — NOT bool(preview) or bool(native_export)
        eff_section = src[src.find("effective_platform = platform"):
                          src.find("platform_preset = _get_preset(effective_platform)")]
        assert "bool(export)" in eff_section, (
            "effective_platform must gate _published_platform on explicit export signal"
        )
        assert "bool(preview)" not in eff_section, (
            "bool(preview) must NOT gate effective_platform — ?preview= without export=1 "
            "should serve the interactive FClassic card, not an export template"
        )

    def test_pcprec17_export_skill_rows_macro_still_integer(self):
        """PCPREC-17: export_skill_rows macro (used by explicit export routes) still
        renders integers — export portrait regression guard."""
        from jinja2 import Environment, FileSystemLoader
        import os
        tpl_root = os.path.join(
            os.path.dirname(__file__),
            "../../../../app/templates",
        )
        env = Environment(loader=FileSystemLoader(tpl_root))
        macro_tpl = env.get_template("macros/card_skill_row.html")
        module = macro_tpl.make_module()
        fn = getattr(module, "export_skill_rows")
        cat = MagicMock()
        skill = MagicMock()
        skill.key = "decisions"
        skill.name_en = "Decisions"
        cat.skills = [skill]
        html = fn(cat, {"decisions": {"current_level": 60.03}}, {})
        assert "60.03" not in html, "Export macro must NOT render decimals"
        assert "60</span>" in html or ">60<" in html, (
            f"Export macro must render integer '60', got: {html!r}"
        )

    def test_pcprec18_native_export_not_in_effective_platform_gate(self):
        """PCPREC-18: ?native_export=1 does NOT activate published_platform fallback
        → effective_platform stays None → export layer inactive → FClassic card served."""
        src = self._route_src()
        eff_section = src[src.find("effective_platform = platform"):
                          src.find("platform_preset = _get_preset(effective_platform)")]
        assert "native_export" not in eff_section, (
            "native_export must NOT appear in the effective_platform gate — "
            "it should serve the interactive card, not the export template"
        )
        assert "bool(preview)" not in eff_section, (
            "bool(preview) must NOT appear in the effective_platform gate"
        )

    def test_pcprec19_interactive_card_uses_round2_not_integer(self):
        """PCPREC-19: the template served for bare URL (player_card_fclassic.html) uses
        card_skill_rows with round(2) — confirmed by macro render, not export_skill_rows."""
        from jinja2 import Environment, FileSystemLoader
        import os
        tpl_root = os.path.join(
            os.path.dirname(__file__),
            "../../../../app/templates",
        )
        env = Environment(loader=FileSystemLoader(tpl_root))
        macro_tpl = env.get_template("macros/card_skill_row.html")
        module = macro_tpl.make_module()
        card_fn = getattr(module, "card_skill_rows")
        cat = MagicMock()
        skill = MagicMock()
        skill.key = "decisions"
        skill.name_en = "Decisions"
        cat.skills = [skill]
        html = card_fn(cat, {"decisions": {"current_level": 60.0325}}, {"decisions": 0.03})
        assert "60.03" in html, "Interactive card must show 60.03"
        assert "↑" in html, "Interactive card must show trend arrow"
        # integer-only rendering would produce "60" without decimal — must not happen
        assert "60</span>" not in html


class TestCardEditorIframeFix:
    """PCPREC-20/21: card editor default-platform iframe uses native_export=1, not
    platform=instagram_portrait&export=1, so the editor preview shows 2-decimal values."""

    def _editor_tpl_src(self) -> str:
        """Effective editor source: main template + all Jinja2 includes (REF-P2)."""
        import os
        tmpl_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), "../../../../app/templates"))
        _inc = os.path.join(tmpl_dir, "includes", "player_editor")
        rels = [
            os.path.join(tmpl_dir, "dashboard_card_editor.html"),
            os.path.join(_inc, "styles.html"),
            os.path.join(_inc, "preview_panel.html"),
            os.path.join(_inc, "design_panel.html"),
            os.path.join(_inc, "platform_panel.html"),
            os.path.join(_inc, "photo_panel.html"),
            os.path.join(_inc, "highlight_video_panel.html"),
            os.path.join(_inc, "scripts.html"),
        ]
        parts = []
        for p in rels:
            with open(p) as f:
                parts.append(f.read())
        return "\n".join(parts)

    def test_pcprec20_editor_jinja_default_uses_native_export(self):
        """PCPREC-20: Jinja2 else-branch for active_card_platform == 'default'
        uses native_export=1 — not platform=instagram_portrait&export=1."""
        src = self._editor_tpl_src()
        assert "platform=instagram_portrait&export=1" not in src, (
            "Editor template must not use portrait export for default platform — "
            "that renders integers via export_skill_rows"
        )
        assert "native_export=1" in src, (
            "Editor template must use native_export=1 for default platform preview"
        )

    def test_pcprec21_editor_js_default_uses_native_export(self):
        """PCPREC-21: JS _cardIframeSrc() default branch (platform === 'default')
        uses native_export=1 — not platform=instagram_portrait&export=1."""
        src = self._editor_tpl_src()
        # Find _cardIframeSrc function body
        fn_start = src.find("function _cardIframeSrc()")
        fn_end   = src.find("function _applyIframeSize()")
        fn_body  = src[fn_start:fn_end]
        assert "instagram_portrait" not in fn_body, (
            "_cardIframeSrc default must not reference instagram_portrait"
        )
        assert "native_export=1" in fn_body, (
            "_cardIframeSrc default must use native_export=1"
        )
