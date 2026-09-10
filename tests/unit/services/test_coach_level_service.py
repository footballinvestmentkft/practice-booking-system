"""
Unit tests for coach_level_service module

Covers all pure functions (no DB):
- check_coach_level_sufficient: all 4 age groups + boundary values
- get_required_level_for_age_group: known and unknown groups
- get_eligible_age_groups: boundary coach levels (1, 3, 5, 7+)
- get_level_display_name: all 8 levels + out-of-range fallback
- get_age_group_display_name: all 4 groups + unknown fallback

Excluded: get_instructor_coach_level (DB query — integration only).
"""
import pytest

from app.services.coach_level_service import (
    MINIMUM_COACH_LEVELS,
    check_coach_level_sufficient,
    get_required_level_for_age_group,
    get_eligible_age_groups,
    get_level_display_name,
    get_age_group_display_name,
)


# ─── check_coach_level_sufficient ────────────────────────────────────────────

@pytest.mark.unit
class TestCheckCoachLevelSufficient:

    def test_level_1_sufficient_for_pre(self):
        assert check_coach_level_sufficient(1, "PRE") is True

    def test_level_1_not_sufficient_for_youth(self):
        assert check_coach_level_sufficient(1, "YOUTH") is False

    def test_level_3_sufficient_for_youth(self):
        assert check_coach_level_sufficient(3, "YOUTH") is True

    def test_level_3_not_sufficient_for_amateur(self):
        assert check_coach_level_sufficient(3, "AMATEUR") is False

    def test_level_5_sufficient_for_amateur(self):
        assert check_coach_level_sufficient(5, "AMATEUR") is True

    def test_level_5_not_sufficient_for_pro(self):
        assert check_coach_level_sufficient(5, "PRO") is False

    def test_level_7_sufficient_for_pro(self):
        assert check_coach_level_sufficient(7, "PRO") is True

    def test_level_8_sufficient_for_all(self):
        for age_group in ["PRE", "YOUTH", "AMATEUR", "PRO"]:
            assert check_coach_level_sufficient(8, age_group) is True

    def test_unknown_age_group_fails_closed(self):
        assert check_coach_level_sufficient(1, "UNKNOWN_GROUP") is False
        assert check_coach_level_sufficient(0, "UNKNOWN_GROUP") is False

    def test_exact_boundary_values(self):
        """At each boundary: level N-1 fails, level N passes."""
        for age_group, required in MINIMUM_COACH_LEVELS.items():
            assert check_coach_level_sufficient(required - 1, age_group) is False, (
                f"Level {required-1} should not be sufficient for {age_group}"
            )
            assert check_coach_level_sufficient(required, age_group) is True, (
                f"Level {required} should be sufficient for {age_group}"
            )


# ─── get_required_level_for_age_group ────────────────────────────────────────

@pytest.mark.unit
class TestGetRequiredLevelForAgeGroup:

    def test_pre_requires_level_1(self):
        assert get_required_level_for_age_group("PRE") == 1

    def test_youth_requires_level_3(self):
        assert get_required_level_for_age_group("YOUTH") == 3

    def test_amateur_requires_level_5(self):
        assert get_required_level_for_age_group("AMATEUR") == 5

    def test_pro_requires_level_7(self):
        assert get_required_level_for_age_group("PRO") == 7

    def test_unknown_group_defaults_to_1(self):
        assert get_required_level_for_age_group("MASTERS") == 1
        assert get_required_level_for_age_group("") == 1

    def test_all_groups_in_minimum_coach_levels(self):
        for group, level in MINIMUM_COACH_LEVELS.items():
            assert get_required_level_for_age_group(group) == level


# ─── get_eligible_age_groups ──────────────────────────────────────────────────

@pytest.mark.unit
class TestGetEligibleAgeGroups:

    def test_level_0_eligible_for_nothing(self):
        eligible = get_eligible_age_groups(0)
        assert eligible == []

    def test_level_1_eligible_for_pre_only(self):
        eligible = get_eligible_age_groups(1)
        assert "PRE" in eligible
        assert "YOUTH" not in eligible
        assert "AMATEUR" not in eligible
        assert "PRO" not in eligible

    def test_level_3_eligible_for_pre_and_youth(self):
        eligible = get_eligible_age_groups(3)
        assert "PRE" in eligible
        assert "YOUTH" in eligible
        assert "AMATEUR" not in eligible
        assert "PRO" not in eligible

    def test_level_5_eligible_for_pre_youth_amateur(self):
        eligible = get_eligible_age_groups(5)
        assert "PRE" in eligible
        assert "YOUTH" in eligible
        assert "AMATEUR" in eligible
        assert "PRO" not in eligible

    def test_level_7_eligible_for_all(self):
        eligible = get_eligible_age_groups(7)
        assert set(eligible) == {"PRE", "YOUTH", "AMATEUR", "PRO"}

    def test_level_8_eligible_for_all(self):
        eligible = get_eligible_age_groups(8)
        assert set(eligible) == {"PRE", "YOUTH", "AMATEUR", "PRO"}

    def test_monotonic_increase(self):
        """Higher coach level → at least as many eligible groups."""
        for level in range(1, 8):
            lower = set(get_eligible_age_groups(level))
            higher = set(get_eligible_age_groups(level + 1))
            assert lower.issubset(higher), (
                f"Level {level+1} should include all groups eligible at level {level}"
            )


# ─── get_level_display_name ───────────────────────────────────────────────────

@pytest.mark.unit
class TestGetLevelDisplayName:

    def test_level_1_beginner(self):
        assert "Beginner" in get_level_display_name(1)
        assert "1" in get_level_display_name(1)

    def test_level_5_advanced(self):
        assert "Advanced" in get_level_display_name(5)

    def test_level_8_master(self):
        assert "Master" in get_level_display_name(8)
        assert "8" in get_level_display_name(8)

    def test_all_8_levels_return_non_empty_string(self):
        for level in range(1, 9):
            name = get_level_display_name(level)
            assert isinstance(name, str)
            assert len(name) > 0

    def test_unknown_level_returns_fallback(self):
        name = get_level_display_name(99)
        assert "99" in name

    def test_level_0_returns_fallback(self):
        name = get_level_display_name(0)
        assert "0" in name


# ─── get_age_group_display_name ───────────────────────────────────────────────

@pytest.mark.unit
class TestGetAgeGroupDisplayName:

    def test_pre_display_name(self):
        name = get_age_group_display_name("PRE")
        assert "Pre" in name or "pre" in name.lower()

    def test_youth_display_name(self):
        name = get_age_group_display_name("YOUTH")
        assert "Youth" in name or "youth" in name.lower()

    def test_amateur_display_name(self):
        name = get_age_group_display_name("AMATEUR")
        assert "Amateur" in name or "amateur" in name.lower()

    def test_pro_display_name(self):
        name = get_age_group_display_name("PRO")
        assert "Professional" in name or "Pro" in name

    def test_all_known_groups_return_descriptive_names(self):
        for group in ["PRE", "YOUTH", "AMATEUR", "PRO"]:
            name = get_age_group_display_name(group)
            assert name != group, f"Expected descriptive name, not bare code {group!r}"

    def test_unknown_group_returns_code_as_fallback(self):
        assert get_age_group_display_name("MASTERS") == "MASTERS"
        assert get_age_group_display_name("UNKNOWN") == "UNKNOWN"
