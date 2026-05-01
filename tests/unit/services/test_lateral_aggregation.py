"""
Unit tests for laterality-aware skill aggregation (_lateral.py).

Tests: LA-01 through LA-13
Layer: L2 — gameplay aggregation only (no DB, no EMA, no badge logic)
"""

import pytest
from app.services.skill_progression._lateral import (
    aggregate_lateral_components,
    update_lateral_component,
)
from app.services.skill_progression._formulas import MIN_SKILL_VALUE, MAX_SKILL_CAP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry(current_level=65.0, components=None):
    """Minimal football_skills entry dict."""
    e = {"baseline": 60.0, "current_level": current_level, "total_delta": 5.0}
    if components is not None:
        e["lateral_components"] = components
    return e


def _lc(level, count=1, total_delta=1.0, last_delta=1.0):
    """Build a lateral component bucket."""
    return {"level": level, "tournament_count": count,
            "total_delta": total_delta, "last_delta": last_delta}


# ---------------------------------------------------------------------------
# aggregate_lateral_components — all partial-availability cases
# ---------------------------------------------------------------------------

class TestAggregateOnlyOneComponent:

    def test_la01_only_neutral_returns_neutral_level(self):
        """LA-01: only neutral component → result equals neutral.level."""
        entry = _entry(components={"neutral": _lc(64.0)})
        result = aggregate_lateral_components(entry, right_foot_score=50.0, left_foot_score=50.0)
        assert result == 64.0

    def test_la02_only_right_returns_right_level(self):
        """LA-02: only right component → result equals right.level (R weight normalised to 1)."""
        entry = _entry(components={"right": _lc(67.0)})
        result = aggregate_lateral_components(entry, right_foot_score=68.0, left_foot_score=32.0)
        assert result == 67.0

    def test_la03_only_left_returns_left_level(self):
        """LA-03: only left component → result equals left.level."""
        entry = _entry(components={"left": _lc(61.5)})
        result = aggregate_lateral_components(entry, right_foot_score=32.0, left_foot_score=68.0)
        assert result == 61.5


class TestAggregateTwoComponents:

    def test_la04_right_and_neutral_R068(self):
        """LA-04: right + neutral, R=0.68 → (0.68*70 + 1.0*64) / 1.68 ≈ 66.6."""
        entry = _entry(components={"right": _lc(70.0), "neutral": _lc(64.0)})
        result = aggregate_lateral_components(entry, right_foot_score=68.0, left_foot_score=32.0)
        expected = round((0.68 * 70.0 + 1.0 * 64.0) / 1.68, 1)
        assert result == expected

    def test_la05_left_and_neutral_R032(self):
        """LA-05: left + neutral, R=0.32 → (0.32 weight on left? No — L=0.68).
        L = 1 - R = 0.68.  (0.68*68 + 1.0*62) / 1.68 ≈ 64.2"""
        entry = _entry(components={"left": _lc(68.0), "neutral": _lc(62.0)})
        result = aggregate_lateral_components(entry, right_foot_score=32.0, left_foot_score=68.0)
        L = 68.0 / (32.0 + 68.0)          # 0.68
        expected = round((L * 68.0 + 1.0 * 62.0) / (L + 1.0), 1)
        assert result == expected

    def test_la06_right_and_left_only(self):
        """LA-06: right + left (no neutral), balanced player R=0.5 → average of two."""
        entry = _entry(components={"right": _lc(70.0), "left": _lc(60.0)})
        result = aggregate_lateral_components(entry, right_foot_score=50.0, left_foot_score=50.0)
        # R=L=0.5 → (0.5*70 + 0.5*60) / 1.0 = 65.0
        assert result == 65.0


class TestAggregateAllThree:

    def test_la07_all_three_R07(self):
        """LA-07: all three components, R=0.7 → full formula."""
        entry = _entry(components={
            "right":   _lc(70.0),
            "left":    _lc(62.0),
            "neutral": _lc(65.0),
        })
        result = aggregate_lateral_components(entry, right_foot_score=70.0, left_foot_score=30.0)
        R, L = 0.7, 0.3
        expected = round((R * 70.0 + L * 62.0 + 1.0 * 65.0) / (R + L + 1.0), 1)
        assert result == expected

    def test_la08_balanced_player_R05_symmetry(self):
        """LA-08: balanced player (R=L=0.5) with symmetric right/left levels
        → current_level lies between right and left, pulled toward neutral."""
        entry = _entry(components={
            "right":   _lc(70.0),
            "left":    _lc(70.0),
            "neutral": _lc(65.0),
        })
        result = aggregate_lateral_components(entry, right_foot_score=50.0, left_foot_score=50.0)
        # (0.5*70 + 0.5*70 + 1.0*65) / 2.0 = 67.5
        assert result == round((0.5 * 70.0 + 0.5 * 70.0 + 1.0 * 65.0) / 2.0, 1)


class TestAggregateEdgeCases:

    def test_la09_no_lateral_components_key_returns_current_level(self):
        """LA-09: no lateral_components key (old record) → current_level unchanged."""
        entry = _entry(current_level=67.3)  # no 'lateral_components' key at all
        result = aggregate_lateral_components(entry, right_foot_score=68.0, left_foot_score=32.0)
        assert result == 67.3

    def test_la10_empty_lateral_components_returns_current_level(self):
        """LA-10: lateral_components={} (explicitly empty) → current_level unchanged."""
        entry = _entry(current_level=65.0, components={})
        result = aggregate_lateral_components(entry, right_foot_score=68.0, left_foot_score=32.0)
        assert result == 65.0

    def test_la11_both_foot_scores_none_defaults_to_balanced(self):
        """LA-11: both foot scores None → R=L=0.5 fallback."""
        entry = _entry(components={"right": _lc(70.0), "left": _lc(60.0)})
        result = aggregate_lateral_components(entry, right_foot_score=None, left_foot_score=None)
        # R=L=0.5 → (0.5*70 + 0.5*60) / 1.0 = 65.0
        assert result == 65.0

    def test_la12_zero_right_score_right_component_excluded(self):
        """LA-12: right_foot_score=0.0 → R=0 → right component weight=0 → excluded."""
        entry = _entry(components={"right": _lc(90.0), "neutral": _lc(64.0)})
        result = aggregate_lateral_components(entry, right_foot_score=0.0, left_foot_score=80.0)
        # R=0 → w_right=0; only neutral has weight (1.0) → result == neutral.level
        assert result == 64.0

    def test_la13_output_capped_at_max_skill_cap(self):
        """LA-13: formula result > 99.0 → clamped to MAX_SKILL_CAP (99.0)."""
        entry = _entry(components={"neutral": _lc(100.0)})
        result = aggregate_lateral_components(entry, right_foot_score=None, left_foot_score=None)
        assert result == MAX_SKILL_CAP

    def test_la14_output_floored_at_min_skill_value(self):
        """LA-14: formula result < 40.0 → clamped to MIN_SKILL_VALUE (40.0)."""
        entry = _entry(components={"neutral": _lc(20.0)})
        result = aggregate_lateral_components(entry, right_foot_score=None, left_foot_score=None)
        assert result == MIN_SKILL_VALUE


# ---------------------------------------------------------------------------
# update_lateral_component
# ---------------------------------------------------------------------------

class TestUpdateLateralComponent:

    def test_new_component_initialised_from_current_level(self):
        """First tournament in a context seeds the component from current_level + delta."""
        entry = _entry(current_level=65.0)  # no lateral_components
        updated = update_lateral_component(entry, "right", delta=0.8)
        assert "lateral_components" in updated
        comp = updated["lateral_components"]["right"]
        assert comp["level"] == round(65.0 + 0.8, 1)
        assert comp["tournament_count"] == 1
        assert comp["last_delta"] == 0.8

    def test_existing_component_accumulated(self):
        """Subsequent tournament in same context accumulates the delta."""
        entry = _entry(current_level=67.8, components={"right": _lc(67.8, count=1)})
        updated = update_lateral_component(entry, "right", delta=0.5)
        comp = updated["lateral_components"]["right"]
        assert comp["level"] == round(67.8 + 0.5, 1)
        assert comp["tournament_count"] == 2

    def test_negative_delta_decreases_component_level(self):
        """Negative EMA delta decreases the component level (bad tournament result)."""
        entry = _entry(current_level=65.0, components={"neutral": _lc(65.0, count=2)})
        updated = update_lateral_component(entry, "neutral", delta=-1.2)
        comp = updated["lateral_components"]["neutral"]
        assert comp["level"] == round(65.0 - 1.2, 1)

    def test_invalid_foot_context_falls_back_to_neutral(self):
        """An unrecognised foot_context string is coerced to 'neutral'."""
        entry = _entry(current_level=65.0)
        updated = update_lateral_component(entry, "invalid_ctx", delta=1.0)
        assert "neutral" in updated["lateral_components"]
        assert "invalid_ctx" not in updated["lateral_components"]

    def test_component_level_capped_by_clamp(self):
        """Component level cannot exceed MAX_SKILL_CAP after delta application."""
        entry = _entry(current_level=98.5, components={"right": _lc(98.5)})
        updated = update_lateral_component(entry, "right", delta=2.0)
        assert updated["lateral_components"]["right"]["level"] == MAX_SKILL_CAP

    def test_component_level_floored_by_clamp(self):
        """Component level cannot go below MIN_SKILL_VALUE after delta application."""
        entry = _entry(current_level=41.0, components={"left": _lc(41.0)})
        updated = update_lateral_component(entry, "left", delta=-5.0)
        assert updated["lateral_components"]["left"]["level"] == MIN_SKILL_VALUE

    def test_other_components_untouched(self):
        """Updating 'right' must not modify 'neutral' or 'left' components."""
        entry = _entry(current_level=65.0, components={
            "neutral": _lc(64.0, count=3),
            "left":    _lc(61.0, count=1),
        })
        updated = update_lateral_component(entry, "right", delta=0.9)
        assert updated["lateral_components"]["neutral"]["level"] == 64.0
        assert updated["lateral_components"]["neutral"]["tournament_count"] == 3
        assert updated["lateral_components"]["left"]["level"] == 61.0
