"""
Unit tests for app/utils/age_requirements.py

Coverage target: 6% → 100%
No DB, no fixtures — pure function calls.
"""

import pytest
from app.utils.age_requirements import (
    get_available_specializations,
    validate_specialization_for_age,
)


class TestGetAvailableSpecializations:
    """Tests for get_available_specializations(age)."""

    def test_none_age_returns_empty(self):
        result = get_available_specializations(None)
        assert result == []

    def test_age_4_returns_empty(self):
        # Below minimum age for any spec (GANCUJU/LFA_FOOTBALL_PLAYER need 5+)
        result = get_available_specializations(4)
        assert result == []

    def test_age_5_has_gancuju_and_lfa_player(self):
        result = get_available_specializations(5)
        types = [s["type"] for s in result]
        assert "GANCUJU_PLAYER" in types
        assert "LFA_FOOTBALL_PLAYER" in types
        # Under 18 → no INTERNSHIP; under 14 → no LFA_COACH
        assert "INTERNSHIP" not in types
        assert "LFA_COACH" not in types

    def test_age_10_pre_level_lfa_player(self):
        result = get_available_specializations(10)
        lfa = next(s for s in result if s["type"] == "LFA_FOOTBALL_PLAYER")
        assert "Pre Level" in lfa["age_requirement"]
        assert "5-13" in lfa["age_requirement"]

    def test_age_14_adds_coach(self):
        result = get_available_specializations(14)
        types = [s["type"] for s in result]
        assert "LFA_COACH" in types
        assert "INTERNSHIP" not in types
        lfa = next(s for s in result if s["type"] == "LFA_FOOTBALL_PLAYER")
        assert "Youth Level" in lfa["age_requirement"]

    def test_age_16_youth_level_lfa_player(self):
        result = get_available_specializations(16)
        lfa = next(s for s in result if s["type"] == "LFA_FOOTBALL_PLAYER")
        assert "Youth Level" in lfa["age_requirement"]
        assert "14-18" in lfa["age_requirement"]

    def test_age_18_adds_internship(self):
        result = get_available_specializations(18)
        types = [s["type"] for s in result]
        assert "INTERNSHIP" in types
        assert "LFA_COACH" in types
        assert "GANCUJU_PLAYER" in types
        assert "LFA_FOOTBALL_PLAYER" in types

    def test_age_18_is_boundary_for_internship(self):
        # Exactly 18 → INTERNSHIP included (age >= 18)
        result_17 = get_available_specializations(17)
        result_18 = get_available_specializations(18)
        types_17 = [s["type"] for s in result_17]
        types_18 = [s["type"] for s in result_18]
        assert "INTERNSHIP" not in types_17
        assert "INTERNSHIP" in types_18

    def test_age_25_adult_level_lfa_player(self):
        result = get_available_specializations(25)
        lfa = next(s for s in result if s["type"] == "LFA_FOOTBALL_PLAYER")
        assert "Amateur" in lfa["age_requirement"] or "Pro" in lfa["age_requirement"]

    def test_result_has_required_fields(self):
        result = get_available_specializations(20)
        for spec in result:
            assert "type" in spec
            assert "name" in spec
            assert "icon" in spec
            assert "color" in spec
            assert "description" in spec
            assert "age_requirement" in spec

    def test_age_5_is_boundary_for_gancuju(self):
        result_4 = get_available_specializations(4)
        result_5 = get_available_specializations(5)
        types_4 = [s["type"] for s in result_4]
        types_5 = [s["type"] for s in result_5]
        assert "GANCUJU_PLAYER" not in types_4
        assert "GANCUJU_PLAYER" in types_5

    def test_age_14_is_boundary_for_coach(self):
        result_13 = get_available_specializations(13)
        result_14 = get_available_specializations(14)
        types_13 = [s["type"] for s in result_13]
        types_14 = [s["type"] for s in result_14]
        assert "LFA_COACH" not in types_13
        assert "LFA_COACH" in types_14


class TestValidateSpecializationForAge:
    """Tests for validate_specialization_for_age(spec_type, age)."""

    def test_none_age_returns_false(self):
        assert validate_specialization_for_age("INTERNSHIP", None) is False

    def test_unknown_spec_returns_false(self):
        assert validate_specialization_for_age("UNKNOWN_SPEC", 25) is False

    def test_internship_requires_18(self):
        assert validate_specialization_for_age("INTERNSHIP", 17) is False
        assert validate_specialization_for_age("INTERNSHIP", 18) is True
        assert validate_specialization_for_age("INTERNSHIP", 30) is True

    def test_gancuju_requires_5(self):
        assert validate_specialization_for_age("GANCUJU_PLAYER", 4) is False
        assert validate_specialization_for_age("GANCUJU_PLAYER", 5) is True

    def test_lfa_football_player_requires_5(self):
        assert validate_specialization_for_age("LFA_FOOTBALL_PLAYER", 4) is False
        assert validate_specialization_for_age("LFA_FOOTBALL_PLAYER", 5) is True

    def test_ambiguous_lfa_player_id_fails_closed(self):
        # LFA_PLAYER is not owner-approved as a deterministic legacy alias.
        assert validate_specialization_for_age("LFA_PLAYER", 4) is False
        assert validate_specialization_for_age("LFA_PLAYER", 5) is False

    def test_lfa_coach_requires_14(self):
        assert validate_specialization_for_age("LFA_COACH", 13) is False
        assert validate_specialization_for_age("LFA_COACH", 14) is True

    def test_empty_string_spec_returns_false(self):
        assert validate_specialization_for_age("", 25) is False
