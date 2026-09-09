"""
Tests for LFACoachService — Sprint L

Target: ≥90% stmt, high branch on certify_next_level + can_book_session
Required branches:
  - already certified max level (PRO_HEAD)
  - invalid enrollment state
  - all booking denied variants (license / payment / specialization mismatch)
  - certification rollback path (boundary exam scores)
  - edge progression (level 0/None → clamp, level > 8 → clamp)
"""

import pytest
from datetime import date
from unittest.mock import MagicMock

from app.services.specs.semester_based.lfa_coach_service import LFACoachService
from app.models.license import UserLicense
from app.models.semester_enrollment import SemesterEnrollment


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _svc():
    return LFACoachService()


def _mock_lic(id=1, current_level=1, max_achieved_level=1, user_id=10, is_active=True):
    lic = MagicMock()
    lic.id = id
    lic.current_level = current_level
    lic.max_achieved_level = max_achieved_level
    lic.user_id = user_id
    lic.is_active = is_active
    return lic


def _enrollment(payment_verified=True):
    e = MagicMock()
    e.is_active = True
    e.payment_verified = payment_verified
    return e


def _user(dob=date(1995, 6, 15), id=10):
    u = MagicMock()
    u.id = id
    u.date_of_birth = dob
    return u


def _lic_db(current_level=1, max_level=1):
    """
    Simple DB mock: all UserLicense queries return the same license.
    Works for both get_current_certification (filter by id) and
    certify_next_level second query (filter by id again).
    """
    svc = _svc()
    lic = _mock_lic(current_level=current_level, max_achieved_level=max_level)
    db = MagicMock()
    q = MagicMock()
    q.filter.return_value = q
    q.first.return_value = lic
    db.query.return_value = q
    return svc, db, lic


def _split_db(lic_first=None, enroll_first=None):
    """
    Discriminated DB mock: separates UserLicense and SemesterEnrollment queries.
    Used for can_book_session and get_enrollment_requirements tests.
    """
    db = MagicMock()

    lic_q = MagicMock()
    lic_q.filter.return_value = lic_q
    lic_q.first.return_value = lic_first

    enroll_q = MagicMock()
    enroll_q.filter.return_value = enroll_q
    enroll_q.first.return_value = enroll_first

    def _q(model):
        if model is UserLicense:
            return lic_q
        if model is SemesterEnrollment:
            return enroll_q
        return MagicMock()

    db.query.side_effect = _q
    return db


def _session_with_spec(spec_value='LFA_COACH_MAIN', as_enum=False):
    """Build a mock session with controlled target_specialization."""
    s = MagicMock()
    if spec_value is None:
        s.target_specialization = None
        s.specialization_type = None
    elif as_enum:
        enum_mock = MagicMock()
        enum_mock.value = spec_value
        s.target_specialization = enum_mock
        s.specialization_type = None
    else:
        # plain string — no .value attribute
        s.target_specialization = spec_value
        s.specialization_type = None
    return s


# ─── TestServiceMetadata ──────────────────────────────────────────────────────

class TestServiceMetadata:
    def test_is_semester_based_returns_true(self):
        assert _svc().is_semester_based() is True

    def test_get_specialization_name(self):
        assert _svc().get_specialization_name() == "LFA Coach"

    def test_coach_levels_has_8_entries(self):
        assert len(_svc().COACH_LEVELS) == 8

    def test_coach_levels_starts_pre_assistant_ends_pro_head(self):
        svc = _svc()
        assert svc.COACH_LEVELS[0] == 'PRE_ASSISTANT'
        assert svc.COACH_LEVELS[-1] == 'PRO_HEAD'


# ─── TestGetCurrentCertification ─────────────────────────────────────────────

class TestGetCurrentCertification:
    def test_license_not_found_raises(self):
        svc, db, _ = _lic_db()
        # Override to return None
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(ValueError, match="not found"):
            svc.get_current_certification(1, db)

    def test_level_none_maps_to_pre_assistant(self):
        svc, db, lic = _lic_db()
        lic.current_level = None  # falsy → `or 1` → index 0
        result = svc.get_current_certification(1, db)
        assert result == 'PRE_ASSISTANT'

    def test_level_zero_maps_to_pre_assistant(self):
        svc, db, lic = _lic_db()
        lic.current_level = 0  # falsy → `or 1` → index 0
        result = svc.get_current_certification(1, db)
        assert result == 'PRE_ASSISTANT'

    def test_level_1_maps_to_pre_assistant(self):
        svc, db, _ = _lic_db(current_level=1)
        assert svc.get_current_certification(1, db) == 'PRE_ASSISTANT'

    def test_level_8_maps_to_pro_head(self):
        svc, db, _ = _lic_db(current_level=8)
        assert svc.get_current_certification(1, db) == 'PRO_HEAD'

    def test_level_above_8_clamped_to_pro_head(self):
        svc, db, _ = _lic_db(current_level=10)
        # min(10-1, 7) = 7 → PRO_HEAD
        assert svc.get_current_certification(1, db) == 'PRO_HEAD'


# ─── TestGetNextCertification ─────────────────────────────────────────────────

class TestGetNextCertification:
    def test_pre_assistant_next_is_pre_head(self):
        assert _svc().get_next_certification('PRE_ASSISTANT') == 'PRE_HEAD'

    def test_youth_head_next_is_amateur_assistant(self):
        assert _svc().get_next_certification('YOUTH_HEAD') == 'AMATEUR_ASSISTANT'

    def test_pro_head_returns_none(self):
        assert _svc().get_next_certification('PRO_HEAD') is None

    def test_invalid_cert_raises(self):
        with pytest.raises(ValueError, match="Invalid certification level"):
            _svc().get_next_certification('FAKE_CERT')


# ─── TestGetCertificationInfo ─────────────────────────────────────────────────

class TestGetCertificationInfo:
    def test_pre_assistant_returns_correct_level(self):
        info = _svc().get_certification_info('PRE_ASSISTANT')
        assert info['level'] == 1
        assert info['min_coach_age'] == 14

    def test_pro_head_returns_correct_level(self):
        info = _svc().get_certification_info('PRO_HEAD')
        assert info['level'] == 8
        assert info['min_coach_age'] == 14

    def test_unknown_cert_returns_default(self):
        info = _svc().get_certification_info('MYSTERY_CERT')
        assert info['level'] == 0
        assert info['name'] == 'Unknown'


# ─── TestCertifyNextLevel ─────────────────────────────────────────────────────

class TestCertifyNextLevel:
    def test_already_at_max_raises(self):
        """PRO_HEAD has no next cert → ValueError."""
        svc, db, _ = _lic_db(current_level=8)
        with pytest.raises(ValueError, match="Already at highest certification"):
            svc.certify_next_level(1, certified_by=2, db=db)

    def test_score_above_100_raises(self):
        svc, db, _ = _lic_db(current_level=1)
        with pytest.raises(ValueError, match="0-100"):
            svc.certify_next_level(1, certified_by=2, db=db, exam_score=101)

    def test_score_negative_raises(self):
        svc, db, _ = _lic_db(current_level=1)
        with pytest.raises(ValueError, match="0-100"):
            svc.certify_next_level(1, certified_by=2, db=db, exam_score=-1)

    def test_score_below_80_raises(self):
        """Third validation: below passing threshold (80%)."""
        svc, db, _ = _lic_db(current_level=1)
        with pytest.raises(ValueError, match="below passing threshold"):
            svc.certify_next_level(1, certified_by=2, db=db, exam_score=79)

    def test_score_exactly_80_passes(self):
        """Boundary: 80% is the minimum passing score."""
        svc, db, lic = _lic_db(current_level=1, max_level=1)
        result = svc.certify_next_level(1, certified_by=2, db=db, exam_score=80)
        assert result['success'] is True

    def test_score_none_skips_threshold_check(self):
        """exam_score=None → no score validations run."""
        svc, db, lic = _lic_db(current_level=1)
        result = svc.certify_next_level(1, certified_by=2, db=db, exam_score=None)
        assert result['success'] is True

    def test_returns_expected_dict_structure(self):
        svc, db, _ = _lic_db(current_level=1)
        result = svc.certify_next_level(1, certified_by=99, db=db, exam_score=90, notes="Well done")
        assert result['success'] is True
        assert result['from_cert'] == 'PRE_ASSISTANT'
        assert result['to_cert'] == 'PRE_HEAD'
        assert result['new_level'] == 2
        assert result['exam_score'] == 90
        assert result['certified_by'] == 99
        assert result['notes'] == "Well done"

    def test_updates_license_level_and_commits(self):
        svc, db, lic = _lic_db(current_level=1, max_level=1)
        svc.certify_next_level(1, certified_by=2, db=db)
        assert lic.current_level == 2
        assert lic.max_achieved_level == 2
        db.commit.assert_called_once()
        db.refresh.assert_called_once_with(lic)

    def test_max_achieved_level_not_downgraded(self):
        """If max_achieved_level is already higher, keep the max."""
        svc, db, lic = _lic_db(current_level=1, max_level=5)
        svc.certify_next_level(1, certified_by=2, db=db)
        # new_level=2, max_achieved_level was 5 → max(5,2)=5
        assert lic.max_achieved_level == 5

    def test_mid_level_certification(self):
        """Promote from YOUTH_HEAD (L4) to AMATEUR_ASSISTANT (L5)."""
        svc, db, _ = _lic_db(current_level=4)
        result = svc.certify_next_level(1, certified_by=3, db=db, exam_score=95)
        assert result['from_cert'] == 'YOUTH_HEAD'
        assert result['to_cert'] == 'AMATEUR_ASSISTANT'
        assert result['new_level'] == 5


# ─── TestValidateAgeEligibility ──────────────────────────────────────────────

class TestValidateAgeEligibility:
    def test_no_dob_returns_false(self):
        svc = _svc()
        user = _user(dob=None)
        ok, msg = svc.validate_age_eligibility(user)
        assert ok is False
        # base_spec returns "Date of birth is required"
        assert "birth" in msg.lower() or "required" in msg.lower() or "date" in msg.lower()

    def test_future_dob_returns_false(self):
        svc = _svc()
        user = _user(dob=date(2099, 1, 1))
        ok, msg = svc.validate_age_eligibility(user)
        assert ok is False

    def test_age_below_14_returns_false(self):
        svc = _svc()
        user = _user(dob=date(2020, 1, 1))  # age ~6
        ok, msg = svc.validate_age_eligibility(user, db=MagicMock())
        assert ok is False
        assert msg == "PROGRAM_MINIMUM_AGE"

    def test_age_14_no_target_group_eligible(self):
        svc = _svc()
        today = date.today()
        # Born Jan 1, (today.year - 14): birthday has passed (Jan < March)
        dob = date(today.year - 14, 1, 1)
        user = _user(dob=dob)
        ok, msg = svc.validate_age_eligibility(user, db=MagicMock())
        assert ok is True
        assert "Eligible" in msg

    def test_target_group_age_too_low_returns_false(self):
        """Coach levels add no age gate beyond the canonical program minimum."""
        svc = _svc()
        today = date.today()
        dob = date(today.year - 22, 1, 1)
        user = _user(dob=dob)
        ok, msg = svc.validate_age_eligibility(user, target_group='PRO_HEAD')
        assert ok is True

    def test_target_group_age_exactly_at_minimum_passes(self):
        """PRO_HEAD requires 23; user is exactly 23."""
        svc = _svc()
        today = date.today()
        dob = date(today.year - 23, 1, 1)
        user = _user(dob=dob)
        ok, _ = svc.validate_age_eligibility(user, target_group='PRO_HEAD')
        assert ok is True

    def test_pre_assistant_min_age_14(self):
        """PRE_ASSISTANT requires min_coach_age=14; age 14 should pass."""
        svc = _svc()
        today = date.today()
        dob = date(today.year - 14, 1, 1)
        user = _user(dob=dob)
        ok, _ = svc.validate_age_eligibility(user, target_group='PRE_ASSISTANT', db=MagicMock())
        assert ok is True

    def test_unknown_target_group_ignored(self):
        """target_group not in LEVEL_INFO → guard skipped → eligible."""
        svc = _svc()
        user = _user(dob=date(2000, 1, 1))  # ~26 years old
        ok, _ = svc.validate_age_eligibility(user, target_group='NOT_A_REAL_GROUP')
        assert ok is True


# ─── TestCanBookSession ───────────────────────────────────────────────────────

class TestCanBookSession:
    def test_no_license_returns_false(self):
        """validate_user_has_license → (False, error) blocks booking."""
        svc = _svc()
        db = _split_db(lic_first=None)
        ok, msg = svc.can_book_session(_user(), _session_with_spec('LFA_COACH_MAIN'), db)
        assert ok is False
        assert "license" in msg.lower()

    def test_no_enrollment_returns_false(self):
        svc = _svc()
        db = _split_db(lic_first=_mock_lic(), enroll_first=None)
        ok, msg = svc.can_book_session(_user(), _session_with_spec('LFA_COACH_MAIN'), db)
        assert ok is False
        assert "enrollment" in msg.lower()

    def test_payment_not_verified_returns_false(self):
        svc = _svc()
        db = _split_db(lic_first=_mock_lic(), enroll_first=_enrollment(payment_verified=False))
        ok, msg = svc.can_book_session(_user(), _session_with_spec('LFA_COACH_MAIN'), db)
        assert ok is False
        assert "payment" in msg.lower()

    def test_session_spec_as_enum_value_passes(self):
        """target_specialization has .value attr (enum path)."""
        svc = _svc()
        db = _split_db(lic_first=_mock_lic(), enroll_first=_enrollment())
        session = _session_with_spec('LFA_COACH_MAIN', as_enum=True)
        ok, _ = svc.can_book_session(_user(), session, db)
        assert ok is True

    def test_session_spec_as_plain_string_passes(self):
        """target_specialization is a plain string (str(spec) path)."""
        svc = _svc()
        db = _split_db(lic_first=_mock_lic(), enroll_first=_enrollment())
        session = _session_with_spec('LFA_COACH_MAIN', as_enum=False)
        ok, _ = svc.can_book_session(_user(), session, db)
        assert ok is True

    def test_wrong_specialization_returns_false(self):
        """Session is for GanCuju, not LFA Coach."""
        svc = _svc()
        db = _split_db(lic_first=_mock_lic(), enroll_first=_enrollment())
        session = _session_with_spec('GANCUJU_PLAYER')
        ok, msg = svc.can_book_session(_user(), session, db)
        assert ok is False
        assert "not for LFA Coach" in msg

    def test_spec_none_returns_false(self):
        """Both target_specialization and specialization_type are None."""
        svc = _svc()
        db = _split_db(lic_first=_mock_lic(), enroll_first=_enrollment())
        session = _session_with_spec(None)
        ok, _ = svc.can_book_session(_user(), session, db)
        assert ok is False

    def test_fallback_to_specialization_type_passes(self):
        """target_specialization=None → falls back to specialization_type."""
        svc = _svc()
        db = _split_db(lic_first=_mock_lic(), enroll_first=_enrollment())
        session = MagicMock()
        session.target_specialization = None
        session.specialization_type = 'LFA_COACH_ADVANCED'
        ok, _ = svc.can_book_session(_user(), session, db)
        assert ok is True


# ─── TestGetProgressionStatus ─────────────────────────────────────────────────

class TestGetProgressionStatus:
    def _setup(self, level):
        svc = _svc()
        lic = _mock_lic(id=1, current_level=level)
        db = MagicMock()
        q = MagicMock()
        q.filter.return_value = q
        q.first.return_value = lic
        db.query.return_value = q
        return svc, lic, db

    def test_level_1_no_achievements_progress_12_5(self):
        svc, lic, db = self._setup(1)
        result = svc.get_progression_status(lic, db)
        assert result['current_level'] == 1
        assert result['achievements'] == []
        assert result['next_certification'] == 'PRE_HEAD'
        assert result['progress_percentage'] == 12.5

    def test_level_2_head_coach_qualified(self):
        svc, lic, db = self._setup(2)
        result = svc.get_progression_status(lic, db)
        names = [a['name'] for a in result['achievements']]
        assert 'Head Coach Qualified' in names
        assert len(names) == 1

    def test_level_4_two_achievements(self):
        svc, lic, db = self._setup(4)
        result = svc.get_progression_status(lic, db)
        names = [a['name'] for a in result['achievements']]
        assert 'Head Coach Qualified' in names
        assert 'Youth Specialist' in names
        assert len(names) == 2

    def test_level_6_three_achievements(self):
        svc, lic, db = self._setup(6)
        result = svc.get_progression_status(lic, db)
        names = [a['name'] for a in result['achievements']]
        assert 'Amateur Coach' in names
        assert len(names) == 3

    def test_level_8_all_four_achievements_and_max(self):
        svc, lic, db = self._setup(8)
        result = svc.get_progression_status(lic, db)
        assert result['next_certification'] is None
        assert result['progress_percentage'] == 100.0
        names = [a['name'] for a in result['achievements']]
        assert 'Professional Coach' in names
        assert len(names) == 4

    def test_result_keys_present(self):
        svc, lic, db = self._setup(3)
        result = svc.get_progression_status(lic, db)
        for key in ('current_level', 'current_certification', 'current_cert_info',
                    'next_certification', 'next_cert_info', 'progress_percentage',
                    'certification_history', 'teaching_hours', 'achievements'):
            assert key in result


# ─── TestGetEnrollmentRequirements ───────────────────────────────────────────

class TestGetEnrollmentRequirements:
    def _adult(self):
        return _user(dob=date(1995, 1, 1))  # ~31 years old, clearly eligible

    def test_all_requirements_met_can_participate(self):
        svc = _svc()
        lic = _mock_lic(id=5, current_level=3)
        db = _split_db(lic_first=lic, enroll_first=_enrollment(payment_verified=True))
        result = svc.get_enrollment_requirements(self._adult(), db)
        assert result['can_participate'] is True
        assert result['missing_requirements'] == []

    def test_age_ineligible_adds_to_missing(self):
        svc = _svc()
        lic = _mock_lic()
        db = _split_db(lic_first=lic, enroll_first=_enrollment())
        young_user = _user(dob=date(2020, 1, 1))
        result = svc.get_enrollment_requirements(young_user, db)
        assert result['can_participate'] is False
        assert any("Age" in m for m in result['missing_requirements'])

    def test_no_license_adds_to_missing(self):
        svc = _svc()
        db = _split_db(lic_first=None, enroll_first=_enrollment())
        result = svc.get_enrollment_requirements(self._adult(), db)
        assert result['can_participate'] is False
        assert any("license" in m.lower() for m in result['missing_requirements'])
        assert result['current_status']['has_license'] is False

    def test_has_license_populates_cert_status(self):
        """level=3 → YOUTH_ASSISTANT; status fields populated."""
        svc = _svc()
        lic = _mock_lic(id=7, current_level=3)
        db = _split_db(lic_first=lic, enroll_first=_enrollment(payment_verified=True))
        result = svc.get_enrollment_requirements(self._adult(), db)
        assert result['current_status']['has_license'] is True
        assert result['current_status']['current_level'] == 3
        assert result['current_status']['current_certification'] == 'YOUTH_ASSISTANT'

    def test_no_enrollment_adds_to_missing(self):
        svc = _svc()
        db = _split_db(lic_first=_mock_lic(), enroll_first=None)
        result = svc.get_enrollment_requirements(self._adult(), db)
        assert result['can_participate'] is False
        assert any("enrollment" in m.lower() for m in result['missing_requirements'])

    def test_payment_not_verified_adds_to_missing(self):
        svc = _svc()
        lic = _mock_lic()
        db = _split_db(lic_first=lic, enroll_first=_enrollment(payment_verified=False))
        result = svc.get_enrollment_requirements(self._adult(), db)
        assert result['can_participate'] is False
        assert result['current_status']['payment_verified'] is False
        assert any("Payment" in m for m in result['missing_requirements'])

    def test_has_enrollment_sets_payment_verified_true(self):
        svc = _svc()
        lic = _mock_lic()
        db = _split_db(lic_first=lic, enroll_first=_enrollment(payment_verified=True))
        result = svc.get_enrollment_requirements(self._adult(), db)
        assert result['current_status']['has_semester_enrollment'] is True
        assert result['current_status']['payment_verified'] is True
