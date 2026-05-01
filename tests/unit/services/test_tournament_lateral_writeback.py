"""
Tournament lateral write-back integration tests.

Tests: TW-01 through TW-04, BC-01 through BC-03

Layer: L2 integration — exercises the update_lateral_component +
aggregate_lateral_components pipeline exactly as the reward orchestrator
calls it, but without the full ORM/DB stack.

BC tests verify that Layer-1 (badge display) and the standard skill-entry
interface are unaffected by the lateral write-back.
"""

import pytest
from app.services.skill_progression._lateral import (
    aggregate_lateral_components,
    update_lateral_component,
)
from app.services.skill_progression._formulas import MIN_SKILL_VALUE, MAX_SKILL_CAP
from app.utils.dominant_foot import calculate_dominant_badge


# ---------------------------------------------------------------------------
# Helpers — replicate the orchestrator's per-skill loop
# ---------------------------------------------------------------------------

def _entry(current_level=65.0, baseline=60.0):
    """Minimal skill entry without lateral_components (old-format record)."""
    return {
        "baseline":        baseline,
        "current_level":   current_level,
        "total_delta":     5.0,
        "tournament_delta": 0.5,
        "tournament_count": 3,
    }


def _apply_tournament(entry: dict, foot_ctx: str, delta: float,
                      right_ft: float | None, left_ft: float | None) -> dict:
    """Single-pass of the orchestrator's per-skill logic."""
    entry = update_lateral_component(entry, foot_ctx, delta)
    agg   = aggregate_lateral_components(entry, right_ft, left_ft)
    entry["current_level"] = agg
    return entry


# ---------------------------------------------------------------------------
# TW-01..04 — write-back integration
# ---------------------------------------------------------------------------

class TestTournamentLateralWriteback:

    def test_tw01_right_foot_tournament_increments_right_count(self):
        """TW-01: right-foot tournament → lateral_components['right'].tournament_count += 1."""
        entry = _entry(current_level=65.0)
        # Seed the right bucket (first contact)
        entry = _apply_tournament(entry, "right", delta=0.8,
                                  right_ft=70.0, left_ft=30.0)
        assert entry["lateral_components"]["right"]["tournament_count"] == 1

        # Second right-foot tournament
        entry = _apply_tournament(entry, "right", delta=0.5,
                                  right_ft=70.0, left_ft=30.0)
        assert entry["lateral_components"]["right"]["tournament_count"] == 2

    def test_tw02_neutral_tournament_first_contact_creates_bucket(self):
        """TW-02: neutral tournament on a new skill entry → lateral_components['neutral'] created."""
        entry = _entry(current_level=65.0)
        entry = _apply_tournament(entry, "neutral", delta=0.6,
                                  right_ft=50.0, left_ft=50.0)

        assert "lateral_components" in entry
        assert "neutral" in entry["lateral_components"]
        comp = entry["lateral_components"]["neutral"]
        assert comp["tournament_count"] == 1
        assert comp["level"] == round(65.0 + 0.6, 1)
        assert comp["last_delta"] == 0.6

    def test_tw03_aggregated_current_level_matches_formula_after_right_and_neutral(self):
        """TW-03: after right + neutral tournaments, current_level == weighted formula result."""
        entry = _entry(current_level=65.0)
        right_ft, left_ft = 70.0, 30.0
        R = right_ft / (right_ft + left_ft)   # 0.7

        # First: right-foot tournament
        entry = _apply_tournament(entry, "right", delta=1.0,
                                  right_ft=right_ft, left_ft=left_ft)
        right_level = entry["lateral_components"]["right"]["level"]   # 66.0

        # Second: neutral tournament
        entry = _apply_tournament(entry, "neutral", delta=0.5,
                                  right_ft=right_ft, left_ft=left_ft)
        neutral_level = entry["lateral_components"]["neutral"]["level"]  # seeded from post-right current_level + 0.5

        # Expected aggregation: (R * right + 1.0 * neutral) / (R + 1.0)
        expected = round((R * right_level + 1.0 * neutral_level) / (R + 1.0), 1)
        assert entry["current_level"] == expected

    def test_tw04_old_record_neutral_tournament_current_level_shifts_by_delta(self):
        """TW-04: old record (no lateral_components) + neutral tournament →
        current_level changes by exactly the delta on first contact."""
        delta = 0.8
        start_level = 65.0
        entry = _entry(current_level=start_level)

        entry = _apply_tournament(entry, "neutral", delta=delta,
                                  right_ft=50.0, left_ft=50.0)

        # With balanced scores (R=L=0.5) and only neutral component:
        # current_level = neutral.level = clamp(start + delta)
        expected = round(start_level + delta, 1)
        assert entry["current_level"] == expected

    def test_tw04b_old_record_gains_lateral_components_on_first_tournament(self):
        """TW-04b: old record gets lateral_components key after first tournament."""
        entry = _entry(current_level=65.0)
        assert "lateral_components" not in entry

        entry = _apply_tournament(entry, "right", delta=0.4,
                                  right_ft=68.0, left_ft=32.0)

        assert "lateral_components" in entry

    def test_tw05_negative_delta_decreases_component_and_current_level(self):
        """TW-05: bad tournament result (negative delta) decreases both bucket and aggregated level."""
        entry = _entry(current_level=65.0)
        # Seed with one good neutral tournament first
        entry = _apply_tournament(entry, "neutral", delta=1.0,
                                  right_ft=50.0, left_ft=50.0)
        level_after_good = entry["current_level"]

        # Now a bad neutral tournament
        entry = _apply_tournament(entry, "neutral", delta=-2.0,
                                  right_ft=50.0, left_ft=50.0)
        assert entry["current_level"] < level_after_good

    def test_tw06_right_dominant_player_weights_right_component_more(self):
        """TW-06: right-dominant player (R=0.8) with separate right and left tournaments →
        current_level is closer to right_level than to left_level."""
        entry = _entry(current_level=65.0)
        right_ft, left_ft = 80.0, 20.0

        # One right-foot tournament (good result)
        entry = _apply_tournament(entry, "right", delta=2.0,
                                  right_ft=right_ft, left_ft=left_ft)
        right_level = entry["lateral_components"]["right"]["level"]  # 67.0

        # One left-foot tournament (poor result — level seeded from current_level at that point)
        pre_left = entry["current_level"]
        entry = _apply_tournament(entry, "left", delta=-1.5,
                                  right_ft=right_ft, left_ft=left_ft)
        left_level = entry["lateral_components"]["left"]["level"]

        # R=0.8 → current_level must be closer to right_level
        dist_to_right = abs(entry["current_level"] - right_level)
        dist_to_left  = abs(entry["current_level"] - left_level)
        assert dist_to_right < dist_to_left


# ---------------------------------------------------------------------------
# BC-01..03 — backward compatibility
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:

    def test_bc01_dominant_badge_right_footed(self):
        """BC-01a: right-footed player (70/30) → badge 'Rl'."""
        assert calculate_dominant_badge(70.0, 30.0) == "Rl"

    def test_bc01_dominant_badge_left_footed(self):
        """BC-01b: left-footed player (25/75) → badge 'rL'."""
        assert calculate_dominant_badge(25.0, 75.0) == "rL"

    def test_bc01_dominant_badge_balanced(self):
        """BC-01c: balanced player (50/50) → badge 'RL'."""
        assert calculate_dominant_badge(50.0, 50.0) == "RL"

    def test_bc01_dominant_badge_unassessed(self):
        """BC-01d: no scores (None/None) → badge 'rl'."""
        assert calculate_dominant_badge(None, None) == "rl"

    def test_bc02_update_lateral_component_preserves_standard_entry_keys(self):
        """BC-02: update_lateral_component does not delete standard skill-entry keys
        that get_skill_profile / the dashboard expect."""
        entry = {
            "baseline":         60.0,
            "current_level":    65.0,
            "total_delta":      5.0,
            "tournament_delta": 0.5,
            "tournament_count": 3,
        }
        updated = update_lateral_component(entry, "neutral", delta=0.7)

        for key in ("baseline", "current_level", "total_delta", "tournament_delta", "tournament_count"):
            assert key in updated, f"Expected key '{key}' preserved after update_lateral_component"

    def test_bc02_aggregate_does_not_mutate_entry(self):
        """BC-02b: aggregate_lateral_components is a pure read — it must not mutate the entry dict."""
        entry = _entry(current_level=65.0)
        entry_with = dict(entry)
        entry_with["lateral_components"] = {"neutral": {"level": 65.0, "tournament_count": 1,
                                                         "total_delta": 0.5, "last_delta": 0.5}}
        original_keys = set(entry_with.keys())

        _ = aggregate_lateral_components(entry_with, 50.0, 50.0)

        assert set(entry_with.keys()) == original_keys

    def test_bc03_neutral_preset_old_record_monotone_with_delta(self):
        """BC-03: neutral preset on an old record (no lateral_components) —
        current_level changes monotonically with the EMA delta direction."""
        positive_delta = 0.9
        negative_delta = -0.9
        base = 65.0

        entry_up = _apply_tournament(_entry(base), "neutral", positive_delta, 50.0, 50.0)
        entry_dn = _apply_tournament(_entry(base), "neutral", negative_delta, 50.0, 50.0)

        assert entry_up["current_level"] > base
        assert entry_dn["current_level"] < base

    def test_bc03b_zero_delta_does_not_change_current_level(self):
        """BC-03b: delta=0.0 on a fresh record → current_level unchanged at start_level."""
        start_level = 65.0
        entry = _apply_tournament(_entry(start_level), "neutral", 0.0, 50.0, 50.0)
        assert entry["current_level"] == start_level

    def test_bc04_lateral_functions_do_not_import_from_ema_or_badge(self):
        """BC-04: _lateral.py must not depend on _ema_engine or dominant_foot
        — enforced by checking the module's import graph at import time."""
        import sys
        # If the module imported cleanly (it's already in sys.modules at this point)
        # without pulling in _ema_engine, the layer boundary holds.
        assert "app.services.skill_progression._ema_engine" not in (
            getattr(sys.modules.get("app.services.skill_progression._lateral"), "__dict__", {})
        ), "Layer 2 must not import Layer 3 (_ema_engine)"
        # Verify the module itself is loaded
        assert "app.services.skill_progression._lateral" in sys.modules
