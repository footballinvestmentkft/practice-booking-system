"""
Unit tests for app/services/teaching_permission_service.py

Mock-based: no DB fixture needed.
Covers: get_teaching_permissions, can_teach_independently,
        requires_supervision, get_age_group_for_user, _get_position_details.

RULES:
  - Odd LFA_COACH levels (1,3,5,7)  → ASSISTANT (supervision required)
  - Even LFA_COACH levels (2,4,6,8) → HEAD (independent)
  - Player licenses                  → no teaching permissions
  - Any other specialization         → no teaching permissions
  - No specialization                → warning, no permissions
"""
import pytest
from unittest.mock import MagicMock, patch
from app.services.teaching_permission_service import TeachingPermissionService
from app.models.specialization import SpecializationType


# ── helpers ──────────────────────────────────────────────────────────────────

def _mock_user(specialization=SpecializationType.LFA_COACH, user_id=42):
    u = MagicMock()
    u.id = user_id
    u.specialization = specialization
    return u


def _mock_db_with_license(level: int):
    """Return a db mock that returns a UserLicense with given level."""
    lic = MagicMock()
    lic.current_level = level
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = lic
    return db


def _mock_db_no_license():
    """Return a db mock that returns no active license."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


# ── get_teaching_permissions — no specialization ──────────────────────────────

class TestNoSpecialization:

    def test_returns_warning_when_no_specialization(self):
        user = _mock_user(specialization=None)
        db = _mock_db_no_license()
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert result["can_teach_independently"] is False
        assert result["can_teach_with_supervision"] is False
        assert any("specialization" in w.lower() for w in result["warnings"])

    def test_license_type_is_none_when_no_specialization(self):
        user = _mock_user(specialization=None)
        result = TeachingPermissionService.get_teaching_permissions(user, _mock_db_no_license())
        assert result["license_type"] is None


# ── get_teaching_permissions — player licenses ────────────────────────────────

class TestPlayerLicenses:

    @pytest.mark.parametrize("spec", [
        SpecializationType.LFA_FOOTBALL_PLAYER.value,
        SpecializationType.GANCUJU_PLAYER.value,
    ])
    def test_player_license_no_teaching_permissions(self, spec):
        user = _mock_user(specialization=spec)
        db = _mock_db_with_license(level=3)
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert result["can_teach_independently"] is False
        assert result["can_teach_with_supervision"] is False

    @pytest.mark.parametrize("spec", [
        SpecializationType.LFA_FOOTBALL_PLAYER.value,
        SpecializationType.GANCUJU_PLAYER.value,
    ])
    def test_player_license_warning_present(self, spec):
        user = _mock_user(specialization=spec)
        db = _mock_db_with_license(level=3)
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert any("player" in w.lower() for w in result["warnings"])


# ── get_teaching_permissions — non-coach specialization ──────────────────────

class TestNonCoachSpecialization:

    @pytest.mark.parametrize("spec", [
        "SOME_OTHER_SPEC",
        "LFA_FOOTBALL_PLAYER",   # string form (not enum)
    ])
    def test_non_lfa_coach_no_permissions(self, spec):
        user = _mock_user(specialization=spec)
        db = _mock_db_with_license(level=4)
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert result["can_teach_independently"] is False
        assert result["can_teach_with_supervision"] is False

    def test_non_lfa_coach_warning_present(self):
        user = _mock_user(specialization="GANCUJU_COACH")
        db = _mock_db_with_license(level=2)
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert len(result["warnings"]) > 0


# ── get_teaching_permissions — LFA_COACH head levels (even) ──────────────────

class TestLfaCoachHeadLevels:

    @pytest.mark.parametrize("level", [2, 4, 6, 8])
    def test_head_coach_can_teach_independently(self, level):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_with_license(level)
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert result["can_teach_independently"] is True

    @pytest.mark.parametrize("level", [2, 4, 6, 8])
    def test_head_coach_can_also_teach_with_supervision(self, level):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_with_license(level)
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert result["can_teach_with_supervision"] is True

    @pytest.mark.parametrize("level", [2, 4, 6, 8])
    def test_head_coach_no_warnings(self, level):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_with_license(level)
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert result["warnings"] == []


# ── get_teaching_permissions — LFA_COACH assistant levels (odd) ───────────────

class TestLfaCoachAssistantLevels:

    @pytest.mark.parametrize("level", [1, 3, 5, 7])
    def test_assistant_cannot_teach_independently(self, level):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_with_license(level)
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert result["can_teach_independently"] is False

    @pytest.mark.parametrize("level", [1, 3, 5, 7])
    def test_assistant_can_teach_with_supervision(self, level):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_with_license(level)
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert result["can_teach_with_supervision"] is True

    @pytest.mark.parametrize("level", [1, 3, 5, 7])
    def test_assistant_has_supervision_warning(self, level):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_with_license(level)
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert any("supervision" in w.lower() for w in result["warnings"])


# ── get_teaching_permissions — invalid level ──────────────────────────────────

class TestInvalidLevel:

    @pytest.mark.parametrize("level", [0, 9, 10, 100])
    def test_invalid_level_no_permissions(self, level):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_with_license(level)
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert result["can_teach_independently"] is False
        assert result["can_teach_with_supervision"] is False

    def test_invalid_level_warning_present(self):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_with_license(9)
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert any("invalid" in w.lower() for w in result["warnings"])


# ── get_teaching_permissions — no license ────────────────────────────────────

class TestNoLicense:

    def test_lfa_coach_no_license_is_denied(self):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_no_license()
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert result["current_level"] is None
        assert result["can_teach_with_supervision"] is False
        assert result["can_teach_independently"] is False
        assert any("license" in warning.lower() for warning in result["warnings"])


# ── position details ──────────────────────────────────────────────────────────

class TestGetPositionDetails:

    @pytest.mark.parametrize("level,expected_title,expected_age", [
        (1, "Pre Assistant Coach",     "PRE_FOOTBALL"),
        (2, "Pre Head Coach",          "PRE_FOOTBALL"),
        (3, "Youth Assistant Coach",   "YOUTH_FOOTBALL"),
        (4, "Youth Head Coach",        "YOUTH_FOOTBALL"),
        (5, "Amateur Assistant Coach", "AMATEUR_FOOTBALL"),
        (6, "Amateur Head Coach",      "AMATEUR_FOOTBALL"),
        (7, "Pro Assistant Coach",     "PRO_FOOTBALL"),
        (8, "Pro Head Coach",          "PRO_FOOTBALL"),
    ])
    def test_all_levels_position_details(self, level, expected_title, expected_age):
        details = TeachingPermissionService._get_position_details(level)
        assert details["title"] == expected_title
        assert details["age_group"] == expected_age

    def test_unknown_level_returns_unknown(self):
        details = TeachingPermissionService._get_position_details(99)
        assert details["title"] == "Unknown"
        assert details["age_group"] is None

    def test_position_title_in_result(self):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_with_license(4)
        result = TeachingPermissionService.get_teaching_permissions(user, db)
        assert result["position_title"] == "Youth Head Coach"
        assert result["age_group"] == "YOUTH_FOOTBALL"


# ── convenience methods ───────────────────────────────────────────────────────

class TestConvenienceMethods:

    def test_can_teach_independently_head_coach(self):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_with_license(2)
        assert TeachingPermissionService.can_teach_independently(user, db) is True

    def test_can_teach_independently_assistant_false(self):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_with_license(1)
        assert TeachingPermissionService.can_teach_independently(user, db) is False

    def test_requires_supervision_assistant(self):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_with_license(3)
        assert TeachingPermissionService.requires_supervision(user, db) is True

    def test_requires_supervision_head_coach_false(self):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_with_license(4)
        assert TeachingPermissionService.requires_supervision(user, db) is False

    def test_requires_supervision_player_false(self):
        user = _mock_user(specialization="LFA_FOOTBALL_PLAYER")
        db = _mock_db_with_license(4)
        # Player has no teaching permissions at all → requires_supervision = False
        assert TeachingPermissionService.requires_supervision(user, db) is False

    def test_get_age_group_for_head_coach_level_6(self):
        user = _mock_user(specialization="LFA_COACH")
        db = _mock_db_with_license(6)
        age_group = TeachingPermissionService.get_age_group_for_user(user, db)
        assert age_group == "AMATEUR_FOOTBALL"

    def test_get_age_group_for_player_is_none(self):
        user = _mock_user(specialization="LFA_FOOTBALL_PLAYER")
        db = _mock_db_with_license(2)
        age_group = TeachingPermissionService.get_age_group_for_user(user, db)
        assert age_group is None
