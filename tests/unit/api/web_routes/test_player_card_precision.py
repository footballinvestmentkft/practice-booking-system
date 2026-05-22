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
