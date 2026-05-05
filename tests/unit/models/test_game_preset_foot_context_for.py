"""
GamePreset.foot_context_for() — unit tests.

FC-SKILL-01  Per-skill override present and valid → override returned
FC-SKILL-02  Skill missing from override dict → preset-level default returned
FC-SKILL-03  No skill_foot_contexts key at all → preset-level default returned
             (covers all existing lat_* presets — backward compat guarantee)

No DB, no fixtures — pure Python object construction.
"""

import pytest
from app.models.game_preset import GamePreset


def _preset(foot_context: str, skill_foot_contexts: dict | None = None) -> GamePreset:
    """Build an in-memory GamePreset with the given foot context config."""
    sc: dict = {
        "skills_tested": ["crossing", "finishing", "passing"],
        "skill_weights": {"crossing": 0.4, "finishing": 0.4, "passing": 0.2},
        "skill_impact_on_matches": True,
        "foot_context": foot_context,
    }
    if skill_foot_contexts is not None:
        sc["skill_foot_contexts"] = skill_foot_contexts

    return GamePreset(
        code="test_preset",
        name="Test Preset",
        is_active=True,
        game_config={
            "version": "1.0",
            "format_config": {},
            "skill_config": sc,
            "simulation_config": {},
            "metadata": {"game_category": "FOOTBALL", "difficulty_level": None, "min_players": 2},
        },
    )


# ── FC-SKILL-01 ───────────────────────────────────────────────────────────────

class TestFcSkill01PerSkillOverrideReturned:
    """FC-SKILL-01: override present and valid → that value returned."""

    def test_crossing_right_override(self):
        gp = _preset("neutral", {"crossing": "right", "finishing": "left"})
        assert gp.foot_context_for("crossing") == "right"

    def test_finishing_left_override(self):
        gp = _preset("neutral", {"crossing": "right", "finishing": "left"})
        assert gp.foot_context_for("finishing") == "left"

    def test_override_beats_preset_default(self):
        # Preset says "right", but crossing has an explicit "left" override.
        gp = _preset("right", {"crossing": "left"})
        assert gp.foot_context_for("crossing") == "left"


# ── FC-SKILL-02 ───────────────────────────────────────────────────────────────

class TestFcSkill02FallbackToPresetDefault:
    """FC-SKILL-02: skill absent from override dict → preset-level default."""

    def test_passing_falls_back_when_not_in_override_dict(self):
        # crossing and finishing have overrides; passing does not.
        gp = _preset("neutral", {"crossing": "right", "finishing": "left"})
        assert gp.foot_context_for("passing") == "neutral"

    def test_unknown_skill_falls_back_to_preset_default(self):
        gp = _preset("right", {"crossing": "right"})
        assert gp.foot_context_for("heading") == "right"

    def test_invalid_override_value_falls_back_to_preset_default(self):
        # Corrupt / unexpected value in JSONB → falls back to preset default.
        gp = _preset("right", {"crossing": "BOTH_FEET"})
        assert gp.foot_context_for("crossing") == "right"


# ── FC-SKILL-03 ───────────────────────────────────────────────────────────────

class TestFcSkill03NoOverrideDictAtAll:
    """FC-SKILL-03: skill_foot_contexts key absent → preset-level default.

    This is the backward-compat guarantee for ALL existing presets, including
    lat_passing_right, lat_passing_left, lat_passing_neutral.
    Any call to foot_context_for() on a legacy preset must return the same
    value as foot_context — identical to pre-B1 behavior.
    """

    def test_lat_right_preset_returns_right_for_every_skill(self):
        gp = _preset("right")   # no skill_foot_contexts key
        assert gp.foot_context_for("passing")  == "right"
        assert gp.foot_context_for("crossing") == "right"
        assert gp.foot_context_for("finishing") == "right"

    def test_lat_left_preset_returns_left_for_every_skill(self):
        gp = _preset("left")
        assert gp.foot_context_for("passing")  == "left"
        assert gp.foot_context_for("dribbling") == "left"

    def test_neutral_preset_returns_neutral_for_every_skill(self):
        gp = _preset("neutral")
        assert gp.foot_context_for("passing")  == "neutral"
        assert gp.foot_context_for("aerial")   == "neutral"

    def test_foot_context_for_equals_foot_context_on_legacy_preset(self):
        for ctx in ("right", "left", "neutral"):
            gp = _preset(ctx)
            for skill in ("passing", "crossing", "finishing", "heading"):
                assert gp.foot_context_for(skill) == gp.foot_context, (
                    f"foot_context_for({skill!r}) != foot_context for preset ctx={ctx!r}"
                )
