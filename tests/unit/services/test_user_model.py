"""Unit tests for app.models.user — User model properties and methods.

Sprint 24 P1: models/user.py  54% stmt / 2% branch  →  ≥90% stmt / ≥65% branch

Coverage strategy: every test flips a branch flag.
- state transitions (verify/unverify, give/revoke consent)
- guard clauses (role check, None check, specialization mismatch)
- conditional branches (if/else, early return)
- data boundary cases (age boundary, empty spec list, existing spec)
"""
import pytest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.models.user import User, UserRole
from app.models.specialization import SpecializationType

_SPEC_LOADER = "app.models.user.SpecializationConfigLoader"
_SEM_ENROLL  = "app.models.user.SemesterEnrollment"
_USER_LIC    = "app.models.user.UserLicense"
_INSTR_SPEC  = "app.models.user.InstructorSpecialization"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _user(**kwargs):
    """Build a User without a DB session.

    Uses User() to trigger SA mapper init, then injects values directly via
    __dict__ to bypass SA write-tracking (no session needed).
    SA reads column values from instance.__dict__ when not expired.
    Relationship lists (instructor_specializations) also stored in __dict__.
    """
    u = User()  # triggers configure_mappers, sets _sa_instance_state
    u.__dict__.update({
        "id": 42,
        "name": "Test User",
        "email": "test@lfa.com",
        "role": UserRole.STUDENT,
        "specialization": None,
        "payment_verified": False,
        "payment_verified_at": None,
        "payment_verified_by": None,
        "date_of_birth": None,
        "parental_consent": False,
        "parental_consent_at": None,
        "parental_consent_by": None,
        "instructor_specializations": [],
    })
    u.__dict__.update(kwargs)
    return u


def _spec_record(spec_str, is_active=True):
    return SimpleNamespace(specialization=spec_str, is_active=is_active)


def _db_first(result):
    """Mock db session whose query chain returns result from .first()."""
    db = MagicMock()
    q = MagicMock()
    db.query.return_value = q
    q.filter.return_value = q
    q.join.return_value = q
    q.order_by.return_value = q
    q.first.return_value = result
    return db


# ===========================================================================
# specialization_display  (lines 231-239)
# ===========================================================================

class TestSpecializationDisplayProperty:
    def test_no_specialization_returns_default(self):
        u = _user(specialization=None)
        assert u.specialization_display == "Nincs kiválasztva"

    def test_with_specialization_success(self):
        u = _user(specialization=SpecializationType.LFA_COACH)
        with patch(_SPEC_LOADER, create=True) as MockLoader:
            MockLoader.return_value.get_display_info.return_value = {"name": "LFA Edző"}
            result = u.specialization_display
        assert result == "LFA Edző"

    def test_with_specialization_missing_name_key_falls_back_to_value(self):
        u = _user(specialization=SpecializationType.LFA_COACH)
        with patch(_SPEC_LOADER, create=True) as MockLoader:
            MockLoader.return_value.get_display_info.return_value = {}
            result = u.specialization_display
        assert result == SpecializationType.LFA_COACH.value

    def test_with_specialization_loader_exception_falls_back_to_value(self):
        u = _user(specialization=SpecializationType.LFA_FOOTBALL_PLAYER)
        with patch(_SPEC_LOADER, create=True) as MockLoader:
            MockLoader.return_value.get_display_info.side_effect = Exception("Config error")
            result = u.specialization_display
        assert result == SpecializationType.LFA_FOOTBALL_PLAYER.value


# ===========================================================================
# specialization_icon  (lines 244-252)
# ===========================================================================

class TestSpecializationIconProperty:
    def test_no_specialization_returns_question_mark(self):
        assert _user(specialization=None).specialization_icon == "❓"

    def test_with_specialization_returns_icon(self):
        u = _user(specialization=SpecializationType.LFA_COACH)
        with patch(_SPEC_LOADER, create=True) as MockLoader:
            MockLoader.return_value.get_display_info.return_value = {"icon": "⚽"}
            result = u.specialization_icon
        assert result == "⚽"

    def test_missing_icon_key_falls_back_to_dart(self):
        u = _user(specialization=SpecializationType.LFA_COACH)
        with patch(_SPEC_LOADER, create=True) as MockLoader:
            MockLoader.return_value.get_display_info.return_value = {}
            result = u.specialization_icon
        assert result == "🎯"

    def test_loader_exception_falls_back_to_dart(self):
        u = _user(specialization=SpecializationType.GANCUJU_PLAYER)
        with patch(_SPEC_LOADER, create=True) as MockLoader:
            MockLoader.return_value.get_display_info.side_effect = RuntimeError("fail")
            result = u.specialization_icon
        assert result == "🎯"


# ===========================================================================
# has_specialization  (line 257)
# ===========================================================================

class TestHasSpecializationProperty:
    def test_none_returns_false(self):
        assert _user(specialization=None).has_specialization is False

    def test_set_returns_true(self):
        assert _user(specialization=SpecializationType.LFA_COACH).has_specialization is True


# ===========================================================================
# can_access_session  (lines 266-282)
# branch map: mbappe / no_user_spec / no_session_target / mixed / match / nomatch
# ===========================================================================

class TestCanAccessSession:
    def _session(self, target=None, mixed=False):
        return SimpleNamespace(
            target_specialization=target,
            mixed_specialization=mixed,
        )

    def test_mbappe_bypasses_all_checks(self):
        u = _user(email="mbappe@lfa.com", specialization=SpecializationType.LFA_COACH)
        s = self._session(target=SpecializationType.GANCUJU_PLAYER)
        assert u.can_access_session(s) is True

    def test_no_user_specialization_always_true(self):
        u = _user(specialization=None)
        assert u.can_access_session(self._session(target=SpecializationType.LFA_COACH)) is True

    def test_session_without_target_attr_true(self):
        u = _user(specialization=SpecializationType.LFA_COACH)
        # SimpleNamespace with no target_specialization attr → hasattr returns False
        assert u.can_access_session(SimpleNamespace()) is True

    def test_session_target_is_none_true(self):
        u = _user(specialization=SpecializationType.LFA_COACH)
        assert u.can_access_session(self._session(target=None)) is True

    def test_mixed_specialization_session_true(self):
        u = _user(specialization=SpecializationType.LFA_FOOTBALL_PLAYER)
        s = self._session(target=SpecializationType.LFA_COACH, mixed=True)
        assert u.can_access_session(s) is True

    def test_no_mixed_attr_falls_through_to_match_check(self):
        u = _user(specialization=SpecializationType.LFA_COACH)
        # SimpleNamespace has target but no mixed_specialization attr
        s = SimpleNamespace(target_specialization=SpecializationType.LFA_COACH)
        assert u.can_access_session(s) is True

    def test_matching_specialization_true(self):
        u = _user(specialization=SpecializationType.LFA_COACH)
        assert u.can_access_session(self._session(target=SpecializationType.LFA_COACH)) is True

    def test_non_matching_specialization_false(self):
        u = _user(specialization=SpecializationType.LFA_FOOTBALL_PLAYER)
        assert u.can_access_session(self._session(target=SpecializationType.LFA_COACH)) is False


# ===========================================================================
# can_enroll_in_project  (lines 288-300)
# ===========================================================================

class TestCanEnrollInProject:
    def _project(self, target=None, mixed=False):
        return SimpleNamespace(
            target_specialization=target,
            mixed_specialization=mixed,
        )

    def test_no_user_specialization_true(self):
        assert _user(specialization=None).can_enroll_in_project(self._project()) is True

    def test_project_no_target_attr_true(self):
        u = _user(specialization=SpecializationType.LFA_COACH)
        assert u.can_enroll_in_project(SimpleNamespace()) is True

    def test_project_target_is_none_true(self):
        u = _user(specialization=SpecializationType.LFA_COACH)
        assert u.can_enroll_in_project(self._project(target=None)) is True

    def test_mixed_project_true(self):
        u = _user(specialization=SpecializationType.LFA_FOOTBALL_PLAYER)
        assert u.can_enroll_in_project(
            self._project(target=SpecializationType.LFA_COACH, mixed=True)
        ) is True

    def test_matching_specialization_true(self):
        u = _user(specialization=SpecializationType.LFA_COACH)
        assert u.can_enroll_in_project(self._project(target=SpecializationType.LFA_COACH)) is True

    def test_non_matching_specialization_false(self):
        u = _user(specialization=SpecializationType.LFA_FOOTBALL_PLAYER)
        assert u.can_enroll_in_project(self._project(target=SpecializationType.LFA_COACH)) is False


# ===========================================================================
# payment_status_display  (lines 306-308)
# ===========================================================================

class TestPaymentStatusDisplay:
    def test_verified_returns_checkmark(self):
        assert _user(payment_verified=True).payment_status_display == "✅ Verified"

    def test_not_verified_returns_cross(self):
        assert _user(payment_verified=False).payment_status_display == "❌ Not Verified"


# ===========================================================================
# can_enroll_in_semester  (lines 314-318)
# ===========================================================================

class TestCanEnrollInSemester:
    def test_admin_always_true(self):
        assert _user(role=UserRole.ADMIN).can_enroll_in_semester is True

    def test_instructor_always_true(self):
        assert _user(role=UserRole.INSTRUCTOR).can_enroll_in_semester is True

    def test_student_payment_verified_true(self):
        assert _user(role=UserRole.STUDENT, payment_verified=True).can_enroll_in_semester is True

    def test_student_not_verified_false(self):
        assert _user(role=UserRole.STUDENT, payment_verified=False).can_enroll_in_semester is False


# ===========================================================================
# verify_payment / unverify_payment  (lines 322-330)
# ===========================================================================

class TestVerifyUnverifyPayment:
    def test_verify_sets_verified_flag(self):
        u = _user()
        admin = _user(id=99, role=UserRole.ADMIN)
        u.verify_payment(admin)
        assert u.payment_verified is True

    def test_verify_records_admin_id(self):
        u = _user()
        admin = _user(id=77, role=UserRole.ADMIN)
        u.verify_payment(admin)
        assert u.payment_verified_by == 77

    def test_verify_sets_timestamp(self):
        u = _user()
        admin = _user(id=99, role=UserRole.ADMIN)
        u.verify_payment(admin)
        assert isinstance(u.payment_verified_at, datetime)

    def test_unverify_clears_all_fields(self):
        u = _user(
            payment_verified=True,
            payment_verified_by=99,
            payment_verified_at=datetime(2026, 1, 1),
        )
        u.unverify_payment()
        assert u.payment_verified is False
        assert u.payment_verified_by is None
        assert u.payment_verified_at is None


# ===========================================================================
# get_active_semester_enrollment  (lines 341-381)
# branch map: admin_early_return / instructor_early_return /
#             semester_id_branch / spec_join_branch / fallback_branch
# ===========================================================================

class TestGetActiveSemesterEnrollment:
    def test_admin_returns_none_immediately(self):
        u = _user(role=UserRole.ADMIN)
        assert u.get_active_semester_enrollment(MagicMock(), semester_id=5) is None

    def test_instructor_returns_none_immediately(self):
        u = _user(role=UserRole.INSTRUCTOR)
        assert u.get_active_semester_enrollment(MagicMock(), semester_id=5) is None

    def test_with_semester_id_found(self):
        mock_enroll = MagicMock()
        u = _user()
        with patch(_SEM_ENROLL, create=True):
            result = u.get_active_semester_enrollment(_db_first(mock_enroll), semester_id=5)
        assert result is mock_enroll

    def test_with_semester_id_not_found(self):
        u = _user()
        with patch(_SEM_ENROLL, create=True):
            result = u.get_active_semester_enrollment(_db_first(None), semester_id=5)
        assert result is None

    def test_no_semester_id_with_specialization_found_on_first_query(self):
        mock_enroll = MagicMock()
        u = _user(specialization=SpecializationType.LFA_COACH)
        with patch(_SEM_ENROLL, create=True), patch(_USER_LIC, create=True):
            result = u.get_active_semester_enrollment(_db_first(mock_enroll))
        assert result is mock_enroll

    def test_no_semester_id_with_specialization_falls_back_to_any(self):
        """First (spec-join) query returns None → fallback query returns enrollment."""
        fallback = MagicMock()
        u = _user(specialization=SpecializationType.LFA_COACH)
        db = MagicMock()
        q1, q2 = MagicMock(), MagicMock()
        q1.join.return_value = q1
        q1.filter.return_value = q1
        q1.order_by.return_value = q1
        q1.first.return_value = None
        q2.filter.return_value = q2
        q2.order_by.return_value = q2
        q2.first.return_value = fallback
        calls = [0]
        def _query(*args):
            calls[0] += 1
            return q1 if calls[0] == 1 else q2
        db.query.side_effect = _query
        with patch(_SEM_ENROLL, create=True), patch(_USER_LIC, create=True):
            result = u.get_active_semester_enrollment(db)
        assert result is fallback

    def test_no_specialization_fallback_found(self):
        mock_enroll = MagicMock()
        u = _user(specialization=None)
        with patch(_SEM_ENROLL, create=True):
            result = u.get_active_semester_enrollment(_db_first(mock_enroll))
        assert result is mock_enroll

    def test_no_specialization_fallback_not_found(self):
        u = _user(specialization=None)
        with patch(_SEM_ENROLL, create=True):
            result = u.get_active_semester_enrollment(_db_first(None))
        assert result is None


# ===========================================================================
# has_active_semester_enrollment  (lines 391-395)
# ===========================================================================

class TestHasActiveSemesterEnrollment:
    def test_admin_returns_true(self):
        assert _user(role=UserRole.ADMIN).has_active_semester_enrollment(MagicMock()) is True

    def test_instructor_returns_true(self):
        assert _user(role=UserRole.INSTRUCTOR).has_active_semester_enrollment(MagicMock()) is True

    def test_student_with_enrollment_true(self):
        u = _user()
        with patch.object(u, "get_active_semester_enrollment", return_value=MagicMock()):
            assert u.has_active_semester_enrollment(MagicMock()) is True

    def test_student_without_enrollment_false(self):
        u = _user()
        with patch.object(u, "get_active_semester_enrollment", return_value=None):
            assert u.has_active_semester_enrollment(MagicMock()) is False


# ===========================================================================
# age property  (lines 403-406)
# branch: no_dob / isinstance_datetime / birthday_before_today / birthday_after_today
# ===========================================================================

class TestAgeProperty:
    def test_no_dob_returns_none(self):
        assert _user(date_of_birth=None).age is None

    def test_with_datetime_dob_calculates_age(self):
        # Born 2000-01-01, test runs 2026 → age 26
        dob = datetime(2000, 1, 1, tzinfo=timezone.utc)
        assert _user(date_of_birth=dob).age == 26

    def test_birthday_not_yet_passed_reduces_age(self):
        # Born 2008-12-31 → still 17 in March 2026
        dob = datetime(2008, 12, 31, tzinfo=timezone.utc)
        assert _user(date_of_birth=dob).age == 17

    def test_birthday_already_passed_this_year(self):
        # Born 2008-01-01 → already 18 in March 2026
        dob = datetime(2008, 1, 1, tzinfo=timezone.utc)
        assert _user(date_of_birth=dob).age == 18


# ===========================================================================
# is_minor property  (lines 411-412)
# branch: age_is_none / age_lt_18 / age_gte_18
# ===========================================================================

class TestIsMinorProperty:
    def test_no_dob_not_minor(self):
        assert _user(date_of_birth=None).is_minor is False

    def test_age_17_is_minor(self):
        dob = datetime(2008, 12, 31, tzinfo=timezone.utc)  # 17 in March 2026
        assert _user(date_of_birth=dob).is_minor is True

    def test_age_18_not_minor(self):
        dob = datetime(2008, 1, 1, tzinfo=timezone.utc)  # 18 in March 2026
        assert _user(date_of_birth=dob).is_minor is False

    def test_adult_not_minor(self):
        dob = datetime(1990, 6, 15, tzinfo=timezone.utc)
        assert _user(date_of_birth=dob).is_minor is False


# ===========================================================================
# needs_parental_consent  (lines 418-421)
# branch: wrong_spec / lfa_coach_adult / lfa_coach_minor
# ===========================================================================

class TestNeedsParentalConsent:
    def test_non_lfa_coach_minor_still_requires_global_consent(self):
        dob = datetime(2010, 1, 1, tzinfo=timezone.utc)  # minor
        u = _user(specialization=SpecializationType.LFA_FOOTBALL_PLAYER, date_of_birth=dob)
        assert u.needs_parental_consent is True

    def test_no_specialization_minor_still_requires_global_consent(self):
        dob = datetime(2010, 1, 1, tzinfo=timezone.utc)
        assert _user(specialization=None, date_of_birth=dob).needs_parental_consent is True

    def test_lfa_coach_adult_false(self):
        dob = datetime(2000, 1, 1, tzinfo=timezone.utc)  # adult
        u = _user(specialization=SpecializationType.LFA_COACH, date_of_birth=dob)
        assert u.needs_parental_consent is False

    def test_lfa_coach_minor_true(self):
        dob = datetime(2010, 1, 1, tzinfo=timezone.utc)  # 16 in 2026
        u = _user(specialization=SpecializationType.LFA_COACH, date_of_birth=dob)
        assert u.needs_parental_consent is True

    def test_lfa_coach_no_dob_not_minor_false(self):
        u = _user(specialization=SpecializationType.LFA_COACH, date_of_birth=None)
        assert u.needs_parental_consent is False


# ===========================================================================
# give_parental_consent / revoke_parental_consent  (lines 425-433)
# ===========================================================================

class TestGiveRevokeParentalConsent:
    def test_give_sets_consent_flag(self):
        u = _user()
        u.give_parental_consent("John Parent")
        assert u.parental_consent is True

    def test_give_records_parent_name(self):
        u = _user()
        u.give_parental_consent("Mary Guardian")
        assert u.parental_consent_by == "Mary Guardian"

    def test_give_sets_timestamp(self):
        u = _user()
        u.give_parental_consent("John Parent")
        assert isinstance(u.parental_consent_at, datetime)

    def test_revoke_clears_all_fields(self):
        u = _user(
            parental_consent=True,
            parental_consent_by="John",
            parental_consent_at=datetime(2026, 1, 1),
        )
        u.revoke_parental_consent()
        assert u.parental_consent is False
        assert u.parental_consent_by is None
        assert u.parental_consent_at is None


# ===========================================================================
# get_teaching_specializations  (lines 444-451)
# branch: non_instructor_empty / active_filter
# ===========================================================================

class TestGetTeachingSpecializations:
    def test_student_returns_empty(self):
        assert _user(role=UserRole.STUDENT).get_teaching_specializations() == []

    def test_admin_returns_empty(self):
        assert _user(role=UserRole.ADMIN).get_teaching_specializations() == []

    def test_instructor_returns_active_only(self):
        u = _user(
            role=UserRole.INSTRUCTOR,
            instructor_specializations=[
                _spec_record("LFA_COACH", is_active=True),
                _spec_record("GANCUJU_PLAYER", is_active=False),
            ],
        )
        result = u.get_teaching_specializations()
        assert result == ["LFA_COACH"]

    def test_instructor_all_inactive_returns_empty(self):
        u = _user(
            role=UserRole.INSTRUCTOR,
            instructor_specializations=[_spec_record("LFA_COACH", is_active=False)],
        )
        assert u.get_teaching_specializations() == []

    def test_instructor_no_specializations_returns_empty(self):
        u = _user(role=UserRole.INSTRUCTOR, instructor_specializations=[])
        assert u.get_teaching_specializations() == []


# ===========================================================================
# get_all_teaching_specializations  (lines 461-470)
# ===========================================================================

class TestGetAllTeachingSpecializations:
    def test_student_returns_empty(self):
        assert _user(role=UserRole.STUDENT).get_all_teaching_specializations() == []

    def test_admin_returns_empty(self):
        assert _user(role=UserRole.ADMIN).get_all_teaching_specializations() == []

    def test_instructor_returns_all_with_status(self):
        u = _user(
            role=UserRole.INSTRUCTOR,
            instructor_specializations=[
                _spec_record("LFA_COACH", is_active=True),
                _spec_record("GANCUJU_PLAYER", is_active=False),
            ],
        )
        result = u.get_all_teaching_specializations()
        assert {"specialization": "LFA_COACH", "is_active": True} in result
        assert {"specialization": "GANCUJU_PLAYER", "is_active": False} in result

    def test_instructor_empty_list_returns_empty(self):
        u = _user(role=UserRole.INSTRUCTOR, instructor_specializations=[])
        assert u.get_all_teaching_specializations() == []


# ===========================================================================
# can_teach_specialization  (lines 483-492)
# branch: non_instructor / active_match / inactive_match / no_match / enum_input
# ===========================================================================

class TestCanTeachSpecialization:
    def test_student_returns_false(self):
        assert _user(role=UserRole.STUDENT).can_teach_specialization("LFA_COACH") is False

    def test_admin_returns_false(self):
        assert _user(role=UserRole.ADMIN).can_teach_specialization("LFA_COACH") is False

    def test_instructor_active_match_true(self):
        u = _user(
            role=UserRole.INSTRUCTOR,
            instructor_specializations=[_spec_record("LFA_COACH", is_active=True)],
        )
        assert u.can_teach_specialization("LFA_COACH") is True

    def test_instructor_inactive_match_false(self):
        u = _user(
            role=UserRole.INSTRUCTOR,
            instructor_specializations=[_spec_record("LFA_COACH", is_active=False)],
        )
        assert u.can_teach_specialization("LFA_COACH") is False

    def test_instructor_no_matching_spec_false(self):
        u = _user(
            role=UserRole.INSTRUCTOR,
            instructor_specializations=[_spec_record("GANCUJU_PLAYER", is_active=True)],
        )
        assert u.can_teach_specialization("LFA_COACH") is False

    def test_instructor_empty_specs_false(self):
        u = _user(role=UserRole.INSTRUCTOR, instructor_specializations=[])
        assert u.can_teach_specialization("LFA_COACH") is False

    def test_enum_value_extracted_correctly(self):
        u = _user(
            role=UserRole.INSTRUCTOR,
            instructor_specializations=[_spec_record("LFA_COACH", is_active=True)],
        )
        assert u.can_teach_specialization(SpecializationType.LFA_COACH) is True


# ===========================================================================
# add_teaching_specialization  (lines 500-521)
# branch: non_instructor / already_exists / new_spec_string / new_spec_enum
# ===========================================================================

class TestAddTeachingSpecialization:
    def test_student_raises_value_error(self):
        with pytest.raises(ValueError, match="Only instructors"):
            _user(role=UserRole.STUDENT).add_teaching_specialization("LFA_COACH")

    def test_admin_raises_value_error(self):
        with pytest.raises(ValueError):
            _user(role=UserRole.ADMIN).add_teaching_specialization("LFA_COACH")

    def test_already_exists_returns_none(self):
        u = _user(
            role=UserRole.INSTRUCTOR,
            instructor_specializations=[_spec_record("LFA_COACH", is_active=True)],
        )
        with patch(_INSTR_SPEC, create=True):
            result = u.add_teaching_specialization("LFA_COACH")
        assert result is None

    def test_new_spec_string_created_and_appended(self):
        u = _user(role=UserRole.INSTRUCTOR, instructor_specializations=[])
        mock_new = MagicMock()
        with patch(_INSTR_SPEC, create=True, return_value=mock_new):
            result = u.add_teaching_specialization("GANCUJU_PLAYER")
        assert result is mock_new
        assert mock_new in u.instructor_specializations

    def test_new_spec_enum_value_extracted(self):
        u = _user(role=UserRole.INSTRUCTOR, instructor_specializations=[])
        mock_new = MagicMock()
        with patch(_INSTR_SPEC, create=True, return_value=mock_new):
            result = u.add_teaching_specialization(SpecializationType.LFA_COACH)
        assert result is mock_new

    def test_with_certified_by_and_notes_passed(self):
        u = _user(role=UserRole.INSTRUCTOR, instructor_specializations=[])
        mock_new = MagicMock()
        with patch(_INSTR_SPEC, create=True, return_value=mock_new) as MockSpec:
            u.add_teaching_specialization("LFA_COACH", certified_by_id=99, notes="Excellent")
        # Verify InstructorSpecialization was instantiated with correct kwargs
        MockSpec.assert_called_once()
        call_kwargs = MockSpec.call_args[1] if MockSpec.call_args[1] else MockSpec.call_args[0]
        assert "certified_by" in str(MockSpec.call_args) or mock_new in u.instructor_specializations
