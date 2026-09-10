"""
Unit tests for LFAInternshipService (semester_based/lfa_internship_service.py)

Covers pure-logic methods (no DB) and DB-mocked paths:
  get_current_level       — numeric → level string mapping, None default, not-found raises
  get_next_level          — each progression step, PRINCIPAL→None, invalid raises
  get_level_info          — known levels, unknown returns default
  get_all_positions       — returns dict copy
  get_position_count      — sums to 30
  validate_position_selection — empty, too many, duplicates, invalid name, valid
  calculate_session_xp    — absent, invalid type, per-semester per-type values
  is_semester_based       — True
  get_specialization_name — "LFA Internship"
  validate_age_eligibility — DOB missing, too young, exactly 18, adult
  can_book_session        — no-license, no-enrollment, unverified-payment, wrong-type, success
  get_progression_status  — semester-1 dict, PRINCIPAL (no next), xp None defaults to 0
"""

import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from app.services.specs.semester_based.lfa_internship_service import LFAInternshipService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _svc(db=None):
    return LFAInternshipService(db=db or MagicMock())


def _db():
    return MagicMock()


def _q(db, first=None, all_=None):
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = first
    q.all.return_value = all_ if all_ is not None else []
    db.query.return_value = q
    return q


# ===========================================================================
# get_current_level
# ===========================================================================

@pytest.mark.unit
class TestGetCurrentLevel:
    def _lic(self, level):
        lic = MagicMock()
        lic.current_level = level
        return lic

    def test_level_1_returns_junior(self):
        db = _db(); _q(db, first=self._lic(1))
        assert _svc(db).get_current_level(1, db) == "INTERN_JUNIOR"

    def test_level_2_returns_junior(self):
        db = _db(); _q(db, first=self._lic(2))
        assert _svc(db).get_current_level(1, db) == "INTERN_JUNIOR"

    def test_level_3_returns_mid_level(self):
        db = _db(); _q(db, first=self._lic(3))
        assert _svc(db).get_current_level(1, db) == "INTERN_MID_LEVEL"

    def test_level_4_returns_mid_level(self):
        db = _db(); _q(db, first=self._lic(4))
        assert _svc(db).get_current_level(1, db) == "INTERN_MID_LEVEL"

    def test_level_5_returns_senior(self):
        db = _db(); _q(db, first=self._lic(5))
        assert _svc(db).get_current_level(1, db) == "INTERN_SENIOR"

    def test_level_6_returns_senior(self):
        db = _db(); _q(db, first=self._lic(6))
        assert _svc(db).get_current_level(1, db) == "INTERN_SENIOR"

    def test_level_7_returns_lead(self):
        db = _db(); _q(db, first=self._lic(7))
        assert _svc(db).get_current_level(1, db) == "INTERN_LEAD"

    def test_level_8_returns_principal(self):
        db = _db(); _q(db, first=self._lic(8))
        assert _svc(db).get_current_level(1, db) == "INTERN_PRINCIPAL"

    def test_level_none_defaults_to_1_returns_junior(self):
        db = _db(); _q(db, first=self._lic(None))
        assert _svc(db).get_current_level(1, db) == "INTERN_JUNIOR"

    def test_license_not_found_raises(self):
        db = _db(); _q(db, first=None)
        with pytest.raises(ValueError, match="not found"):
            _svc(db).get_current_level(99, db)


# ===========================================================================
# get_next_level
# ===========================================================================

@pytest.mark.unit
class TestGetNextLevel:
    def test_junior_to_mid_level(self):
        assert _svc().get_next_level("INTERN_JUNIOR") == "INTERN_MID_LEVEL"

    def test_mid_level_to_senior(self):
        assert _svc().get_next_level("INTERN_MID_LEVEL") == "INTERN_SENIOR"

    def test_senior_to_lead(self):
        assert _svc().get_next_level("INTERN_SENIOR") == "INTERN_LEAD"

    def test_lead_to_principal(self):
        assert _svc().get_next_level("INTERN_LEAD") == "INTERN_PRINCIPAL"

    def test_principal_returns_none(self):
        assert _svc().get_next_level("INTERN_PRINCIPAL") is None

    def test_invalid_level_raises(self):
        with pytest.raises(ValueError, match="Invalid level"):
            _svc().get_next_level("BOGUS_LEVEL")


# ===========================================================================
# get_level_info
# ===========================================================================

@pytest.mark.unit
class TestGetLevelInfo:
    def test_junior_info(self):
        info = _svc().get_level_info("INTERN_JUNIOR")
        assert info["name"] == "INTERN JUNIOR"
        assert info["semester"] == 1
        assert info["total_base_xp"] == 1875

    def test_all_known_levels_have_required_keys(self):
        svc = _svc()
        for lvl in ["INTERN_JUNIOR", "INTERN_MID_LEVEL", "INTERN_SENIOR",
                    "INTERN_LEAD", "INTERN_PRINCIPAL"]:
            info = svc.get_level_info(lvl)
            assert "semester" in info
            assert "total_base_xp" in info
            assert "excellence_threshold" in info

    def test_unknown_level_returns_default(self):
        info = _svc().get_level_info("NONEXISTENT")
        assert info["name"] == "Unknown"
        assert info["semester"] == 0
        assert info["total_base_xp"] == 0


# ===========================================================================
# get_all_positions / get_position_count
# ===========================================================================

@pytest.mark.unit
class TestPositions:
    def test_get_all_positions_returns_dict(self):
        positions = _svc().get_all_positions()
        assert isinstance(positions, dict)
        assert "Administrative" in positions
        assert "Commercial" in positions

    def test_get_all_positions_returns_copy(self):
        svc = _svc()
        positions = svc.get_all_positions()
        positions["Fake"] = ["fake_position"]
        assert "Fake" not in svc.INTERNSHIP_POSITIONS  # Original unchanged

    def test_position_count_is_30(self):
        assert _svc().get_position_count() == 30


# ===========================================================================
# validate_position_selection
# ===========================================================================

@pytest.mark.unit
class TestValidatePositionSelection:
    def _all_positions(self):
        all_pos = []
        for dept in LFAInternshipService.INTERNSHIP_POSITIONS.values():
            all_pos.extend(dept)
        return all_pos

    def test_empty_list_invalid(self):
        ok, msg = _svc().validate_position_selection([])
        assert not ok
        assert "1 position" in msg

    def test_too_many_invalid(self):
        ok, msg = _svc().validate_position_selection(self._all_positions()[:8])
        assert not ok
        assert "7 positions" in msg

    def test_duplicate_positions_invalid(self):
        ok, msg = _svc().validate_position_selection(
            ["LFA Sports Director", "LFA Sports Director"]
        )
        assert not ok
        assert "Duplicate" in msg

    def test_invalid_position_name(self):
        ok, msg = _svc().validate_position_selection(["Fake Position"])
        assert not ok
        assert "Invalid position" in msg

    def test_valid_single_position(self):
        ok, msg = _svc().validate_position_selection(["LFA Sports Director"])
        assert ok
        assert "1 position" in msg

    def test_valid_three_positions(self):
        ok, msg = _svc().validate_position_selection([
            "LFA Sports Director",
            "LFA Digital Marketing Manager",
            "LFA Facility Manager",
        ])
        assert ok
        assert "3 position" in msg

    def test_valid_max_seven_positions(self):
        positions = self._all_positions()[:7]
        ok, msg = _svc().validate_position_selection(positions)
        assert ok


# ===========================================================================
# calculate_session_xp
# ===========================================================================

@pytest.mark.unit
class TestCalculateSessionXp:
    def test_absent_returns_zero(self):
        assert _svc().calculate_session_xp("HYBRID", 1, "ABSENT") == 0

    def test_late_returns_zero(self):
        assert _svc().calculate_session_xp("ONSITE", 1, "LATE") == 0

    def test_invalid_session_type_returns_zero(self):
        assert _svc().calculate_session_xp("INVALID", 1, "PRESENT") == 0

    def test_hybrid_semester_1(self):
        assert _svc().calculate_session_xp("HYBRID", 1, "PRESENT") == 100

    def test_onsite_semester_1(self):
        assert _svc().calculate_session_xp("ONSITE", 1, "PRESENT") == 75

    def test_virtual_semester_1(self):
        assert _svc().calculate_session_xp("VIRTUAL", 1, "PRESENT") == 50

    def test_hybrid_semester_5(self):
        assert _svc().calculate_session_xp("HYBRID", 5, "PRESENT") == 200

    def test_onsite_semester_5(self):
        assert _svc().calculate_session_xp("ONSITE", 5, "PRESENT") == 150

    def test_virtual_semester_5(self):
        assert _svc().calculate_session_xp("VIRTUAL", 5, "PRESENT") == 100

    def test_invalid_semester_defaults_to_semester_1_values(self):
        # semester 99 not in XP_SCALING → defaults to XP_SCALING[1]
        assert _svc().calculate_session_xp("HYBRID", 99, "PRESENT") == 100


# ===========================================================================
# Simple property overrides
# ===========================================================================

@pytest.mark.unit
class TestSimpleOverrides:
    def test_is_semester_based(self):
        assert _svc().is_semester_based() is True

    def test_get_specialization_name(self):
        assert _svc().get_specialization_name() == "LFA Internship"


# ===========================================================================
# validate_age_eligibility
# ===========================================================================

@pytest.mark.unit
class TestValidateAgeEligibility:
    def _user(self, dob=None):
        user = MagicMock()
        user.date_of_birth = dob
        return user

    def test_no_dob_returns_false(self):
        svc = _svc()
        u = self._user()
        ok, msg = svc.validate_age_eligibility(u)
        assert not ok
        assert msg == "DATE_OF_BIRTH_REQUIRED"

    def test_too_young_returns_false(self):
        svc = _svc()
        u = self._user(date.today().replace(year=date.today().year - 17))
        ok, msg = svc.validate_age_eligibility(u)
        assert not ok
        assert msg in {"GUARDIAN_CONSENT_REQUIRED", "PROGRAM_MINIMUM_AGE"}

    def test_exactly_18_is_eligible(self):
        svc = _svc()
        u = self._user(date.today().replace(year=date.today().year - 18))
        ok, msg = svc.validate_age_eligibility(u)
        assert ok
        assert "Eligible" in msg

    def test_adult_eligible(self):
        svc = _svc()
        u = self._user(date.today().replace(year=date.today().year - 30))
        ok, msg = svc.validate_age_eligibility(u)
        assert ok


# ===========================================================================
# can_book_session
# ===========================================================================

@pytest.mark.unit
class TestCanBookSession:
    def _user(self, uid=1):
        u = MagicMock(); u.id = uid
        return u

    def _session_mock(self, spec_type="INTERNSHIP_ONSITE"):
        s = MagicMock(); s.specialization_type = spec_type
        return s

    def test_no_license_returns_false(self):
        db = _db()
        svc = _svc(db)
        with patch.object(svc, "validate_user_has_license", return_value=(False, "No license")):
            ok, msg = svc.can_book_session(self._user(), self._session_mock(), db)
        assert not ok
        assert "No license" in msg

    def test_no_enrollment_returns_false(self):
        db = _db()
        svc = _svc(db)
        _q(db, first=None)  # No enrollment found
        with patch.object(svc, "validate_user_has_license", return_value=(True, "")):
            ok, msg = svc.can_book_session(self._user(), self._session_mock(), db)
        assert not ok
        assert "enrollment" in msg.lower()

    def test_payment_not_verified_returns_false(self):
        db = _db()
        svc = _svc(db)
        enrollment = MagicMock(); enrollment.payment_verified = False
        _q(db, first=enrollment)
        with patch.object(svc, "validate_user_has_license", return_value=(True, "")):
            ok, msg = svc.can_book_session(self._user(), self._session_mock(), db)
        assert not ok
        assert "Payment" in msg

    def test_wrong_session_type_returns_false(self):
        db = _db()
        svc = _svc(db)
        enrollment = MagicMock(); enrollment.payment_verified = True
        _q(db, first=enrollment)
        session = self._session_mock(spec_type="COACH_ONSITE")
        with patch.object(svc, "validate_user_has_license", return_value=(True, "")):
            ok, msg = svc.can_book_session(self._user(), session, db)
        assert not ok
        assert "COACH_ONSITE" in msg

    def test_session_type_none_returns_false(self):
        db = _db()
        svc = _svc(db)
        enrollment = MagicMock(); enrollment.payment_verified = True
        _q(db, first=enrollment)
        session = self._session_mock(spec_type=None)
        with patch.object(svc, "validate_user_has_license", return_value=(True, "")):
            ok, msg = svc.can_book_session(self._user(), session, db)
        assert not ok

    def test_success_internship_session(self):
        db = _db()
        svc = _svc(db)
        enrollment = MagicMock(); enrollment.payment_verified = True
        _q(db, first=enrollment)
        with patch.object(svc, "validate_user_has_license", return_value=(True, "")):
            ok, msg = svc.can_book_session(
                self._user(), self._session_mock("INTERNSHIP_HYBRID"), db
            )
        assert ok
        assert "Eligible" in msg


# ===========================================================================
# get_progression_status
# ===========================================================================

@pytest.mark.unit
class TestGetProgressionStatus:
    def _lic(self, level=1, xp=500):
        lic = MagicMock()
        lic.current_level = level
        lic.current_xp = xp
        return lic

    def test_semester_1_structure(self):
        db = _db()
        svc = _svc(db)
        with patch.object(svc, "get_current_level", return_value="INTERN_JUNIOR"):
            result = svc.get_progression_status(self._lic(1, 500), db)
        assert result["current_level"] == "INTERN_JUNIOR"
        assert result["semester"] == 1
        assert result["current_xp"] == 500
        assert result["next_level"] == "INTERN_MID_LEVEL"
        assert result["progress_percentage"] == 20.0
        assert "excellence" in result["xp_thresholds"]
        assert len(result["achievements"]) >= 1

    def test_principal_level_no_next_100_pct(self):
        db = _db()
        svc = _svc(db)
        with patch.object(svc, "get_current_level", return_value="INTERN_PRINCIPAL"):
            result = svc.get_progression_status(self._lic(8, 3900), db)
        assert result["next_level"] is None
        assert result["next_level_info"] is None
        assert result["semester"] == 5
        assert result["progress_percentage"] == 100.0
        assert len(result["achievements"]) == 5

    def test_xp_none_defaults_to_zero(self):
        db = _db()
        svc = _svc(db)
        with patch.object(svc, "get_current_level", return_value="INTERN_MID_LEVEL"):
            result = svc.get_progression_status(self._lic(3, None), db)
        assert result["current_xp"] == 0

    def test_xp_thresholds_calculated(self):
        db = _db()
        svc = _svc(db)
        with patch.object(svc, "get_current_level", return_value="INTERN_JUNIOR"):
            result = svc.get_progression_status(self._lic(1, 0), db)
        thresholds = result["xp_thresholds"]
        # 1875 * 92% = 1725 excellence
        assert thresholds["excellence"] == int(1875 * 92 / 100)
        assert thresholds["standard"] == int(1875 * 74 / 100)
        assert thresholds["conditional"] == int(1875 * 70 / 100)
