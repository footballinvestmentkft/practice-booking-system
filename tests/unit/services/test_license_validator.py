"""
Sprint P2 — shared/license_validator.py
========================================
Target: ≥90% stmt, ≥85% branch

Covers all methods of LicenseValidator:
  get_coach_license          — found / not found / raise_if_missing=False
  validate_coach_license     — no age_group / sufficient / insufficient / unknown group
                               optional context fields (email, tournament_id, name)
  get_minimum_level_for_age_group — all 4 known groups + unknown
  check_level_sufficient     — boundary values, all groups, unknown group
"""
import pytest
from unittest.mock import MagicMock
from fastapi import HTTPException

from app.services.shared.license_validator import LicenseValidator


# ── Helpers ───────────────────────────────────────────────────────────────────

def _db(lic=None) -> MagicMock:
    """Mock DB session whose coach-license query returns `lic`."""
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
        [lic] if lic is not None else []
    )
    return db


def _lic(level: int) -> MagicMock:
    lic = MagicMock()
    lic.current_level = level
    lic.canonical_program_id = "LFA_COACH"
    lic.specialization_type = "LFA_COACH"
    return lic


# ── get_coach_license ─────────────────────────────────────────────────────────

class TestGetCoachLicense:
    def test_found_returns_license(self):
        lic = _lic(3)
        result = LicenseValidator.get_coach_license(_db(lic), user_id=42)
        assert result is lic

    def test_not_found_raise_true_raises_403(self):
        with pytest.raises(HTTPException) as exc_info:
            LicenseValidator.get_coach_license(_db(None), user_id=42)
        assert exc_info.value.status_code == 403

    def test_not_found_raise_false_returns_none(self):
        result = LicenseValidator.get_coach_license(_db(None), user_id=42, raise_if_missing=False)
        assert result is None

    def test_error_detail_contains_user_id(self):
        with pytest.raises(HTTPException) as exc_info:
            LicenseValidator.get_coach_license(_db(None), user_id=99)
        assert exc_info.value.detail["user_id"] == 99

    def test_error_detail_error_code_is_license_required(self):
        with pytest.raises(HTTPException) as exc_info:
            LicenseValidator.get_coach_license(_db(None), user_id=42)
        assert exc_info.value.detail["error"] == "license_required"

    def test_found_with_raise_false_still_returns_license(self):
        lic = _lic(5)
        result = LicenseValidator.get_coach_license(_db(lic), user_id=42, raise_if_missing=False)
        assert result is lic


# ── validate_coach_license ────────────────────────────────────────────────────

class TestValidateCoachLicense:
    def test_no_age_group_returns_license(self):
        lic = _lic(5)
        result = LicenseValidator.validate_coach_license(_db(lic), user_id=42)
        assert result is lic

    def test_no_age_group_none_explicit_returns_license(self):
        lic = _lic(1)
        result = LicenseValidator.validate_coach_license(_db(lic), user_id=42, age_group=None)
        assert result is lic

    def test_known_age_group_exact_level_passes(self):
        """Level exactly at minimum is sufficient."""
        lic = _lic(4)  # YOUTH Head = 4
        result = LicenseValidator.validate_coach_license(_db(lic), user_id=42, age_group="YOUTH")
        assert result is lic

    def test_known_age_group_above_minimum_passes(self):
        lic = _lic(8)  # PRO Head inherits AMATEUR Head scope
        result = LicenseValidator.validate_coach_license(_db(lic), user_id=42, age_group="AMATEUR")
        assert result is lic

    def test_known_age_group_below_minimum_raises_403(self):
        lic = _lic(2)  # YOUTH min = 3, level 2 is insufficient
        with pytest.raises(HTTPException) as exc_info:
            LicenseValidator.validate_coach_license(_db(lic), user_id=42, age_group="YOUTH")
        assert exc_info.value.status_code == 403

    def test_pro_age_group_level_6_insufficient(self):
        lic = _lic(6)  # PRO min = 7
        with pytest.raises(HTTPException) as exc_info:
            LicenseValidator.validate_coach_license(_db(lic), user_id=42, age_group="PRO")
        assert exc_info.value.status_code == 403

    def test_pre_age_group_level_2_head_passes(self):
        lic = _lic(2)  # PRE Head = 2
        result = LicenseValidator.validate_coach_license(_db(lic), user_id=42, age_group="PRE")
        assert result is lic

    def test_unknown_age_group_fails_closed(self):
        """Unknown age group is never an implicit authorization."""
        lic = _lic(1)
        with pytest.raises(HTTPException) as exc_info:
            LicenseValidator.validate_coach_license(
                _db(lic), user_id=42, age_group="UNKNOWN_GROUP"
            )
        assert exc_info.value.detail["error"] == "invalid_age_group"

    def test_missing_license_raises_403_regardless_of_age_group(self):
        with pytest.raises(HTTPException) as exc_info:
            LicenseValidator.validate_coach_license(_db(None), user_id=42, age_group="PRE")
        assert exc_info.value.status_code == 403

    def test_error_detail_contains_required_and_current_level(self):
        lic = _lic(1)  # PRO requires 7
        with pytest.raises(HTTPException) as exc_info:
            LicenseValidator.validate_coach_license(_db(lic), user_id=42, age_group="PRO")
        detail = exc_info.value.detail
        assert detail["required_coach_level"] == 8
        assert detail["current_coach_level"] == 1

    def test_error_detail_error_code(self):
        lic = _lic(1)
        with pytest.raises(HTTPException) as exc_info:
            LicenseValidator.validate_coach_license(_db(lic), user_id=42, age_group="PRO")
        assert exc_info.value.detail["error"] == "insufficient_coach_level"

    def test_optional_user_email_included_in_error(self):
        lic = _lic(1)
        with pytest.raises(HTTPException) as exc_info:
            LicenseValidator.validate_coach_license(
                _db(lic), user_id=42, age_group="PRO", user_email="coach@lfa.com"
            )
        assert exc_info.value.detail["user_email"] == "coach@lfa.com"

    def test_optional_tournament_id_included_in_error(self):
        lic = _lic(1)
        with pytest.raises(HTTPException) as exc_info:
            LicenseValidator.validate_coach_license(
                _db(lic), user_id=42, age_group="PRO", tournament_id=77
            )
        assert exc_info.value.detail["tournament_id"] == 77

    def test_optional_tournament_name_included_in_error(self):
        lic = _lic(1)
        with pytest.raises(HTTPException) as exc_info:
            LicenseValidator.validate_coach_license(
                _db(lic), user_id=42, age_group="PRO", tournament_name="Grand Final"
            )
        assert exc_info.value.detail["tournament_name"] == "Grand Final"

    def test_no_optional_context_keys_absent_from_error(self):
        """When no optional fields given, they must NOT appear in the error dict."""
        lic = _lic(1)
        with pytest.raises(HTTPException) as exc_info:
            LicenseValidator.validate_coach_license(_db(lic), user_id=42, age_group="PRO")
        detail = exc_info.value.detail
        assert "user_email" not in detail
        assert "tournament_id" not in detail
        assert "tournament_name" not in detail

    def test_all_optional_fields_together(self):
        """All three optional context fields simultaneously present in error."""
        lic = _lic(1)
        with pytest.raises(HTTPException) as exc_info:
            LicenseValidator.validate_coach_license(
                _db(lic), user_id=42, age_group="PRO",
                user_email="x@lfa.com", tournament_id=5, tournament_name="Cup"
            )
        detail = exc_info.value.detail
        assert detail["user_email"] == "x@lfa.com"
        assert detail["tournament_id"] == 5
        assert detail["tournament_name"] == "Cup"


# ── get_minimum_level_for_age_group ──────────────────────────────────────────

class TestGetMinimumLevelForAgeGroup:
    def test_pre_returns_1(self):
        assert LicenseValidator.get_minimum_level_for_age_group("PRE") == 1

    def test_youth_returns_3(self):
        assert LicenseValidator.get_minimum_level_for_age_group("YOUTH") == 3

    def test_amateur_returns_5(self):
        assert LicenseValidator.get_minimum_level_for_age_group("AMATEUR") == 5

    def test_pro_returns_7(self):
        assert LicenseValidator.get_minimum_level_for_age_group("PRO") == 7

    def test_unknown_returns_none(self):
        assert LicenseValidator.get_minimum_level_for_age_group("INVALID") is None

    def test_case_sensitive_lower_returns_none(self):
        """Keys are uppercase — lowercase lookup returns None."""
        assert LicenseValidator.get_minimum_level_for_age_group("pre") is None


# ── check_level_sufficient ────────────────────────────────────────────────────

class TestCheckLevelSufficient:
    def test_unknown_age_group_returns_false(self):
        """Unknown group fails closed."""
        assert LicenseValidator.check_level_sufficient(1, "INVALID_GROUP") is False

    def test_exact_minimum_is_sufficient(self):
        assert LicenseValidator.check_level_sufficient(5, "AMATEUR") is True

    def test_above_minimum_is_sufficient(self):
        assert LicenseValidator.check_level_sufficient(8, "AMATEUR") is True

    def test_below_minimum_not_sufficient(self):
        assert LicenseValidator.check_level_sufficient(4, "AMATEUR") is False

    def test_all_groups_at_exact_boundary(self):
        assert LicenseValidator.check_level_sufficient(1, "PRE") is True
        assert LicenseValidator.check_level_sufficient(3, "YOUTH") is True
        assert LicenseValidator.check_level_sufficient(5, "AMATEUR") is True
        assert LicenseValidator.check_level_sufficient(7, "PRO") is True

    def test_all_groups_one_below_boundary(self):
        assert LicenseValidator.check_level_sufficient(0, "PRE") is False
        assert LicenseValidator.check_level_sufficient(2, "YOUTH") is False
        assert LicenseValidator.check_level_sufficient(4, "AMATEUR") is False
        assert LicenseValidator.check_level_sufficient(6, "PRO") is False
