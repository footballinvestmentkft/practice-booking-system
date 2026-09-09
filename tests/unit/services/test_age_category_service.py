"""
Unit tests for age_category_service module

Covers all pure functions:
- calculate_age_at_season_start: season lock rule, birthday-before/after July 1
- get_automatic_age_category: PRE/YOUTH/None decision thresholds + edge values
- get_current_season_year: July/January boundary (date.today patched)
- can_override_age_category: under-14 locked, 14+ overridable
- validate_age_category_override: invalid category, under-14 guard, valid overrides

DB-free: zero DB calls in this module.
"""
import pytest
from datetime import date
from unittest.mock import patch

from app.services.age_category_service import (
    calculate_age_at_season_start,
    get_automatic_age_category,
    get_current_season_year,
    can_override_age_category,
    validate_age_category_override,
)


# ─── calculate_age_at_season_start ────────────────────────────────────────────

@pytest.mark.unit
class TestCalculateAgeAtSeasonStart:

    def test_birthday_after_july1_not_yet_turned(self):
        # Born Dec 6 2007 → on July 1 2025 still 17
        assert calculate_age_at_season_start(date(2007, 12, 6), 2025) == 17

    def test_birthday_before_july1_already_turned(self):
        # Born March 15 2007 → on July 1 2025 already 18
        assert calculate_age_at_season_start(date(2007, 3, 15), 2025) == 18

    def test_birthday_exactly_on_july1(self):
        # Born July 1 2010 → on July 1 2025 exactly 15
        assert calculate_age_at_season_start(date(2010, 7, 1), 2025) == 15

    def test_young_child_age_5(self):
        assert calculate_age_at_season_start(date(2020, 8, 1), 2025) == 4
        assert calculate_age_at_season_start(date(2020, 1, 1), 2025) == 5

    def test_older_player_over_18(self):
        # Born 2000 Jan → on July 1 2025 = 25
        assert calculate_age_at_season_start(date(2000, 1, 15), 2025) == 25

    def test_age_increases_by_one_across_seasons(self):
        dob = date(2010, 9, 1)  # September birthday → not turned yet by July 1
        age_2025 = calculate_age_at_season_start(dob, 2025)
        age_2026 = calculate_age_at_season_start(dob, 2026)
        assert age_2026 == age_2025 + 1

    def test_june_birthday_before_july1_already_turned(self):
        # Born June 30 2007 → on July 1 2025 already 18
        assert calculate_age_at_season_start(date(2007, 6, 30), 2025) == 18


# ─── get_automatic_age_category ───────────────────────────────────────────────

@pytest.mark.unit
class TestGetAutomaticAgeCategory:

    # PRE band: 5-13
    def test_age_5_is_pre(self):
        assert get_automatic_age_category(5) == "PRE"

    def test_age_10_is_pre(self):
        assert get_automatic_age_category(10) == "PRE"

    def test_age_13_is_pre(self):
        assert get_automatic_age_category(13) == "PRE"

    # YOUTH band: 14-18
    def test_age_14_is_youth(self):
        assert get_automatic_age_category(14) == "YOUTH"

    def test_age_17_is_youth(self):
        assert get_automatic_age_category(17) == "YOUTH"

    def test_age_18_is_youth(self):
        assert get_automatic_age_category(18) == "YOUTH"

    # No automatic assignment below the minimum; 19+ has AMATEUR base.
    def test_age_19_is_amateur(self):
        assert get_automatic_age_category(19) == "AMATEUR"

    def test_age_25_is_amateur(self):
        assert get_automatic_age_category(25) == "AMATEUR"

    def test_age_4_is_none(self):
        assert get_automatic_age_category(4) is None

    def test_age_0_is_none(self):
        assert get_automatic_age_category(0) is None

    # Boundary checks
    def test_boundary_13_to_14(self):
        assert get_automatic_age_category(13) == "PRE"
        assert get_automatic_age_category(14) == "YOUTH"

    def test_boundary_18_to_19(self):
        assert get_automatic_age_category(18) == "YOUTH"
        assert get_automatic_age_category(19) == "AMATEUR"


# ─── get_current_season_year ──────────────────────────────────────────────────

@pytest.mark.unit
class TestGetCurrentSeasonYear:

    def test_december_returns_current_year(self):
        with patch("app.services.age_category_service.date") as mock_date:
            mock_date.today.return_value = date(2025, 12, 28)
            assert get_current_season_year() == 2025

    def test_july_returns_current_year(self):
        with patch("app.services.age_category_service.date") as mock_date:
            mock_date.today.return_value = date(2026, 7, 10)
            assert get_current_season_year() == 2026

    def test_january_returns_previous_year(self):
        with patch("app.services.age_category_service.date") as mock_date:
            mock_date.today.return_value = date(2026, 1, 15)
            assert get_current_season_year() == 2025

    def test_june_returns_previous_year(self):
        with patch("app.services.age_category_service.date") as mock_date:
            mock_date.today.return_value = date(2026, 6, 30)
            assert get_current_season_year() == 2025

    def test_july_1_boundary(self):
        with patch("app.services.age_category_service.date") as mock_date:
            mock_date.today.return_value = date(2026, 7, 1)
            assert get_current_season_year() == 2026


# ─── can_override_age_category ───────────────────────────────────────────────

@pytest.mark.unit
class TestCanOverrideAgeCategory:

    def test_program_age_can_enter_explicit_movement_command(self):
        assert can_override_age_category(5) is True
        assert can_override_age_category(10) is True
        assert can_override_age_category(13) is True

    def test_exactly_14_can_override(self):
        assert can_override_age_category(14) is True

    def test_youth_range_can_override(self):
        assert can_override_age_category(16) is True
        assert can_override_age_category(18) is True

    def test_adult_can_override(self):
        assert can_override_age_category(21) is True
        assert can_override_age_category(30) is True


# ─── validate_age_category_override ──────────────────────────────────────────

@pytest.mark.unit
class TestValidateAgeCategoryOverride:

    def test_invalid_category_name_rejected(self):
        ok, err = validate_age_category_override(16, "ELITE")
        assert ok is False
        assert "Invalid" in err

    def test_empty_category_rejected(self):
        ok, err = validate_age_category_override(16, "")
        assert ok is False

    def test_under_14_cannot_leave_pre(self):
        ok, err = validate_age_category_override(10, "AMATEUR")
        assert ok is False
        assert err == "TARGET_MINIMUM_AGE_14"

    def test_under_14_can_move_up_to_youth(self):
        ok, err = validate_age_category_override(12, "YOUTH")
        assert ok is True

    def test_under_14_cannot_go_to_pro(self):
        ok, err = validate_age_category_override(13, "PRO")
        assert ok is False

    def test_under_14_staying_in_pre_is_valid(self):
        ok, err = validate_age_category_override(10, "PRE")
        assert ok is True
        assert err is None

    def test_14_can_go_to_amateur(self):
        ok, err = validate_age_category_override(14, "AMATEUR")
        assert ok is True
        assert err is None

    def test_14_can_go_to_pro(self):
        ok, err = validate_age_category_override(14, "PRO")
        assert ok is True

    def test_17_can_go_to_amateur(self):
        ok, err = validate_age_category_override(17, "AMATEUR")
        assert ok is True

    def test_21_can_go_to_pro(self):
        ok, err = validate_age_category_override(21, "PRO")
        assert ok is True

    def test_adult_cannot_move_below_amateur_base(self):
        for cat in ["PRE", "YOUTH"]:
            ok, err = validate_age_category_override(20, cat)
            assert ok is False, f"Expected denial below base for {cat!r}"
