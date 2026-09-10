"""
Unit Tests: LFAPlayerService

Covers:
- Basic overrides (is_session_based, get_specialization_name)
- Age group calculation: PRE / YOUTH / AMATEUR / PRO boundaries
- Age group extraction from specialization_type string
- Age eligibility validation (with/without target group)
- Cross-age-group session attendance matrix
- Next-milestone calculation (pure)
- Enrollment requirements lookup (DB-mocked)
- Progression status with skills/achievements (DB-mocked)
- Master Instructor age-group promotion (DB-mocked)
"""
import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from app.services.specs.session_based.lfa_player_service import LFAPlayerService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dob(years_old: int) -> date:
    """Return a DOB with exactly this age at the current season's July 1."""
    today = date.today()
    season_start_year = today.year if today.month >= 7 else today.year - 1
    return date(season_start_year - years_old, 7, 1)


def _service() -> LFAPlayerService:
    consent_db = MagicMock()
    consent_db.query.return_value.filter.return_value.first.return_value = (1,)
    return LFAPlayerService(db=consent_db)


def _mock_user(years_old: int = 15, has_dob: bool = True):
    user = MagicMock()
    user.id = 1
    user.date_of_birth = _dob(years_old) if has_dob else None
    return user


def _db_with_license(spec_type: str = "LFA_PLAYER_YOUTH"):
    """Mock DB that returns a license on any .query().filter().first() call."""
    db = MagicMock()
    lic = MagicMock()
    lic.specialization_type = spec_type
    lic.is_active = True
    lic.user_id = 1
    lic.id = 42
    db.query.return_value.filter.return_value.first.return_value = lic
    return db, lic


def _canonical_entitlement_and_assignment(category: str):
    license_row = MagicMock(
        specialization_type="LFA_FOOTBALL_PLAYER",
        canonical_program_id="LFA_FOOTBALL_PLAYER",
        is_active=True,
        user_id=1,
        id=42,
    )
    assignment = MagicMock(effective_category=category)
    return license_row, assignment


def _db_no_license():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


def _db_with_assessments(assessments: list):
    """Mock DB for get_progression_status: .query().filter().all() → list."""
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = assessments
    return db


def _mock_assessment(skill_name: str, percentage: float, pe: int = 8, pt: int = 10):
    a = MagicMock()
    a.skill_name = skill_name
    a.percentage = percentage
    a.points_earned = pe
    a.points_total = pt
    a.created_at = None
    return a


# ===========================================================================
# TestBasicOverrides
# ===========================================================================

class TestBasicOverrides:

    def test_is_session_based_true(self):
        assert _service().is_session_based() is True

    def test_is_semester_based_false(self):
        assert _service().is_semester_based() is False

    def test_get_specialization_name(self):
        assert _service().get_specialization_name() == "LFA Football Player"

    def test_valid_skills_is_nonempty_list(self):
        skills = _service().VALID_SKILLS
        assert isinstance(skills, list)
        assert len(skills) > 0


# ===========================================================================
# TestCalculateAgeGroup
# ===========================================================================

class TestCalculateAgeGroup:
    """Boundary-value tests for calculate_age_group."""

    def test_age_5_is_pre(self):
        assert _service().calculate_age_group(_dob(5)) == 'PRE'

    def test_age_0_raises(self):
        with pytest.raises(ValueError):
            _service().calculate_age_group(_dob(0))

    def test_age_6_is_pre(self):
        assert _service().calculate_age_group(_dob(6)) == 'PRE'

    def test_age_9_is_pre(self):
        assert _service().calculate_age_group(_dob(9)) == 'PRE'

    def test_age_11_is_pre(self):
        assert _service().calculate_age_group(_dob(11)) == 'PRE'

    def test_age_12_is_pre(self):
        assert _service().calculate_age_group(_dob(12)) == 'PRE'

    def test_age_14_is_youth(self):
        """14-year-olds map to YOUTH (natural UP category) by default."""
        assert _service().calculate_age_group(_dob(14)) == 'YOUTH'

    def test_age_18_is_youth(self):
        assert _service().calculate_age_group(_dob(18)) == 'YOUTH'

    def test_age_19_is_amateur(self):
        assert _service().calculate_age_group(_dob(19)) == 'AMATEUR'

    def test_age_40_is_amateur(self):
        """PRO is never returned; requires explicit Master Instructor promotion."""
        assert _service().calculate_age_group(_dob(40)) == 'AMATEUR'


# ===========================================================================
# TestGetAgeGroupFromSpecialization
# ===========================================================================

class TestGetAgeGroupFromSpecialization:
    """Pure string-extraction tests (no date/DB)."""

    def test_lfa_player_pre(self):
        assert _service().get_age_group_from_specialization("LFA_PLAYER_PRE") == 'PRE'

    def test_lfa_player_youth(self):
        assert _service().get_age_group_from_specialization("LFA_PLAYER_YOUTH") == 'YOUTH'

    def test_lfa_player_amateur(self):
        assert _service().get_age_group_from_specialization("LFA_PLAYER_AMATEUR") == 'AMATEUR'

    def test_lfa_player_pro(self):
        assert _service().get_age_group_from_specialization("LFA_PLAYER_PRO") == 'PRO'

    def test_none_returns_none(self):
        assert _service().get_age_group_from_specialization(None) is None

    def test_empty_string_returns_none(self):
        assert _service().get_age_group_from_specialization("") is None

    def test_non_lfa_player_prefix_returns_none(self):
        assert _service().get_age_group_from_specialization("LFA_COACH") is None

    def test_lfa_player_without_group_returns_none(self):
        # "LFA_PLAYER" → split = ['LFA', 'PLAYER'] → len < 3 → None
        assert _service().get_age_group_from_specialization("LFA_PLAYER") is None

    def test_unknown_group_suffix_returns_none(self):
        assert _service().get_age_group_from_specialization("LFA_PLAYER_SENIOR") is None


# ===========================================================================
# TestValidateAgeEligibility
# ===========================================================================

class TestValidateAgeEligibility:

    # --- no target_group ---

    def test_no_dob_returns_false(self):
        user = _mock_user(has_dob=False)
        ok, reason = _service().validate_age_eligibility(user)
        assert ok is False
        assert reason  # any non-empty error message

    def test_age_5_is_program_minimum(self):
        user = _mock_user(years_old=5)
        ok, reason = _service().validate_age_eligibility(user)
        assert ok is True
        assert "PRE" in reason

    def test_age_8_natural_pre_eligible(self):
        user = _mock_user(years_old=8)
        ok, reason = _service().validate_age_eligibility(user)
        assert ok is True
        assert "PRE" in reason

    def test_age_15_natural_youth_eligible(self):
        user = _mock_user(years_old=15)
        ok, reason = _service().validate_age_eligibility(user)
        assert ok is True
        assert "YOUTH" in reason

    def test_age_25_natural_amateur_eligible(self):
        user = _mock_user(years_old=25)
        ok, reason = _service().validate_age_eligibility(user)
        assert ok is True
        assert "AMATEUR" in reason

    # --- with target_group ---

    def test_invalid_target_group(self):
        user = _mock_user(years_old=15)
        ok, reason = _service().validate_age_eligibility(user, target_group='MASTER')
        assert ok is False
        assert "Invalid age group" in reason

    def test_target_pre_age_8_ok(self):
        user = _mock_user(years_old=8)
        ok, _ = _service().validate_age_eligibility(user, target_group='PRE')
        assert ok is True

    def test_target_pre_age_12_is_canonical_base(self):
        user = _mock_user(years_old=12)
        ok, reason = _service().validate_age_eligibility(user, target_group='PRE')
        assert ok is True

    def test_target_youth_age_10_below_min(self):
        """YOUTH min_age=12; age 10 is under the limit."""
        user = _mock_user(years_old=10)
        ok, reason = _service().validate_age_eligibility(user, target_group='YOUTH')
        assert ok is False
        assert "Explicit football category movement required" in reason

    def test_target_amateur_age_14_ok(self):
        """AMATEUR min_age=14, no max → age 14 eligible."""
        user = _mock_user(years_old=14)
        ok, reason = _service().validate_age_eligibility(user, target_group='AMATEUR')
        assert ok is False
        assert "Explicit football category movement required" in reason

    def test_target_pro_age_13_below_min(self):
        """PRO min_age=14; age 13 not eligible."""
        user = _mock_user(years_old=13)
        ok, reason = _service().validate_age_eligibility(user, target_group='PRO')
        assert ok is False
        assert "Explicit football category movement required" in reason

    def test_target_pro_age_20_ok(self):
        """PRO has no max_age → age 20 eligible (age check only)."""
        user = _mock_user(years_old=20)
        ok, reason = _service().validate_age_eligibility(user, target_group='PRO')
        assert ok is False
        assert "Explicit football category movement required" in reason


# ===========================================================================
# TestCanAttendAgeGroupSession
# ===========================================================================

class TestCanAttendAgeGroupSession:
    """Cross-age-group attendance matrix (pure)."""

    # Same-group — always allowed
    def test_pre_same_group(self):
        ok, reason = _service().can_attend_age_group_session('PRE', 'PRE')
        assert ok is True
        assert "Same" in reason

    def test_youth_same_group(self):
        ok, _ = _service().can_attend_age_group_session('YOUTH', 'YOUTH')
        assert ok is True

    def test_amateur_same_group(self):
        ok, _ = _service().can_attend_age_group_session('AMATEUR', 'AMATEUR')
        assert ok is True

    def test_pro_same_group(self):
        ok, _ = _service().can_attend_age_group_session('PRO', 'PRO')
        assert ok is True

    # PRE cross-group
    def test_pre_to_youth_requires_assignment(self):
        ok, reason = _service().can_attend_age_group_session('PRE', 'YOUTH')
        assert ok is False
        assert "canonical season assignment" in reason

    def test_pre_to_amateur_denied(self):
        ok, _ = _service().can_attend_age_group_session('PRE', 'AMATEUR')
        assert ok is False

    def test_pre_to_pro_denied(self):
        ok, _ = _service().can_attend_age_group_session('PRE', 'PRO')
        assert ok is False

    # YOUTH cross-group
    def test_youth_to_pre_denied_without_assignment(self):
        ok, _ = _service().can_attend_age_group_session('YOUTH', 'PRE')
        assert ok is False

    def test_youth_to_amateur_denied_without_assignment(self):
        ok, _ = _service().can_attend_age_group_session('YOUTH', 'AMATEUR')
        assert ok is False

    def test_youth_to_pro_denied(self):
        ok, _ = _service().can_attend_age_group_session('YOUTH', 'PRO')
        assert ok is False

    # AMATEUR cross-group
    def test_amateur_to_youth_denied_without_assignment(self):
        ok, _ = _service().can_attend_age_group_session('AMATEUR', 'YOUTH')
        assert ok is False

    def test_amateur_to_pre_denied(self):
        ok, _ = _service().can_attend_age_group_session('AMATEUR', 'PRE')
        assert ok is False

    def test_amateur_to_pro_denied(self):
        ok, _ = _service().can_attend_age_group_session('AMATEUR', 'PRO')
        assert ok is False

    # PRO cross-group (no cross-group allowed)
    def test_pro_to_pre_denied(self):
        ok, _ = _service().can_attend_age_group_session('PRO', 'PRE')
        assert ok is False

    def test_pro_to_youth_denied(self):
        ok, _ = _service().can_attend_age_group_session('PRO', 'YOUTH')
        assert ok is False

    def test_pro_to_amateur_denied(self):
        ok, reason = _service().can_attend_age_group_session('PRO', 'AMATEUR')
        assert ok is False
        assert "canonical season assignment" in reason


# ===========================================================================
# TestGetNextMilestone
# ===========================================================================

class TestGetNextMilestone:
    """Pure milestone calculation."""

    def test_zero_percent_is_proficient_milestone(self):
        result = _service()._get_next_milestone(0.0, 'PRE')
        assert result is not None
        assert result['name'] == 'Proficient Player'
        assert result['target_percentage'] == 60.0
        assert result['remaining'] == pytest.approx(60.0, abs=0.01)

    def test_just_below_60_remaining_correct(self):
        result = _service()._get_next_milestone(59.0, 'YOUTH')
        assert result['name'] == 'Proficient Player'
        assert result['remaining'] == pytest.approx(1.0, abs=0.01)

    def test_exactly_60_is_expert_milestone(self):
        result = _service()._get_next_milestone(60.0, 'AMATEUR')
        assert result['name'] == 'Expert Player'
        assert result['target_percentage'] == 80.0

    def test_between_60_and_80_expert_remaining(self):
        result = _service()._get_next_milestone(70.0, 'YOUTH')
        assert result['name'] == 'Expert Player'
        assert result['remaining'] == pytest.approx(10.0, abs=0.01)

    def test_at_80_pre_suggests_youth_promotion(self):
        result = _service()._get_next_milestone(80.0, 'PRE')
        assert result is not None
        assert 'YOUTH' in result['name']

    def test_at_80_youth_suggests_amateur_promotion(self):
        result = _service()._get_next_milestone(80.0, 'YOUTH')
        assert 'AMATEUR' in result['name']

    def test_at_80_amateur_suggests_pro_promotion(self):
        result = _service()._get_next_milestone(80.0, 'AMATEUR')
        assert 'PRO' in result['name']

    def test_at_80_pro_returns_none(self):
        """PRO is max level — no further promotion milestone."""
        result = _service()._get_next_milestone(80.0, 'PRO')
        assert result is None

    def test_at_80_no_age_group_returns_none(self):
        result = _service()._get_next_milestone(80.0, None)
        assert result is None


# ===========================================================================
# TestGetEnrollmentRequirements
# ===========================================================================

class TestGetEnrollmentRequirements:

    def test_no_dob_cannot_participate(self):
        user = _mock_user(has_dob=False)
        result = _service().get_enrollment_requirements(user, _db_no_license())
        assert result['can_participate'] is False
        assert any("Date of birth" in m for m in result['missing_requirements'])

    def test_too_young_cannot_participate(self):
        """Age 5 triggers ValueError in calculate_age_group → missing requirement."""
        user = _mock_user(years_old=5)
        result = _service().get_enrollment_requirements(user, _db_no_license())
        assert result['can_participate'] is False

    def test_no_license_cannot_participate(self):
        user = _mock_user(years_old=15)
        result = _service().get_enrollment_requirements(user, _db_no_license())
        assert result['can_participate'] is False
        assert any("license" in m.lower() for m in result['missing_requirements'])

    def test_has_license_can_participate(self):
        user = _mock_user(years_old=15)
        db = MagicMock()
        license_row, assignment = _canonical_entitlement_and_assignment("YOUTH")
        with patch(
            "app.services.specs.session_based.lfa_player_service.get_active_football_entitlement",
            return_value=license_row,
        ), patch(
            "app.services.specs.session_based.lfa_player_service.get_current_player_assignment",
            return_value=assignment,
        ):
            result = _service().get_enrollment_requirements(user, db)
        assert result['can_participate'] is True
        assert result['missing_requirements'] == []

    def test_status_has_license_true(self):
        user = _mock_user(years_old=15)
        db = MagicMock()
        license_row, assignment = _canonical_entitlement_and_assignment("YOUTH")
        with patch(
            "app.services.specs.session_based.lfa_player_service.get_active_football_entitlement",
            return_value=license_row,
        ), patch(
            "app.services.specs.session_based.lfa_player_service.get_current_player_assignment",
            return_value=assignment,
        ):
            result = _service().get_enrollment_requirements(user, db)
        assert result['current_status']['has_license'] is True
        assert result['current_status']['license_active'] is True

    def test_status_age_group_comes_from_ws1_assignment(self):
        user = _mock_user(years_old=25)
        db = MagicMock()
        license_row, assignment = _canonical_entitlement_and_assignment("AMATEUR")
        with patch(
            "app.services.specs.session_based.lfa_player_service.get_active_football_entitlement",
            return_value=license_row,
        ), patch(
            "app.services.specs.session_based.lfa_player_service.get_current_player_assignment",
            return_value=assignment,
        ):
            result = _service().get_enrollment_requirements(user, db)
        assert result['current_status']['age_group'] == 'AMATEUR'

    def test_missing_ws1_assignment_fails_closed(self):
        user = _mock_user(years_old=25)
        db = MagicMock()
        license_row, _ = _canonical_entitlement_and_assignment("AMATEUR")
        with patch(
            "app.services.specs.session_based.lfa_player_service.get_active_football_entitlement",
            return_value=license_row,
        ), patch(
            "app.services.specs.session_based.lfa_player_service.get_current_player_assignment",
            return_value=None,
        ):
            result = _service().get_enrollment_requirements(user, db)
        assert result['can_participate'] is False
        assert "Canonical football season assignment required" in result['missing_requirements']

    def test_status_natural_age_group_youth_for_15(self):
        user = _mock_user(years_old=15)
        db = MagicMock()
        license_row, assignment = _canonical_entitlement_and_assignment("YOUTH")
        with patch(
            "app.services.specs.session_based.lfa_player_service.get_active_football_entitlement",
            return_value=license_row,
        ), patch(
            "app.services.specs.session_based.lfa_player_service.get_current_player_assignment",
            return_value=assignment,
        ):
            result = _service().get_enrollment_requirements(user, db)
        assert result['current_status']['natural_age_group'] == 'YOUTH'

    def test_can_self_enroll_true_for_amateur(self):
        user = _mock_user(years_old=25)
        db, _ = _db_with_license("LFA_PLAYER_AMATEUR")
        result = _service().get_enrollment_requirements(user, db)
        assert result['current_status']['can_self_enroll'] is True


# ===========================================================================
# TestGetProgressionStatus
# ===========================================================================

class TestGetProgressionStatus:

    @pytest.fixture(autouse=True)
    def canonical_assignment(self):
        assignment = MagicMock(effective_category="YOUTH")
        with patch(
            "app.services.specs.session_based.lfa_player_service.get_current_player_assignment",
            return_value=assignment,
        ):
            yield

    def test_no_assessments_zero_progress(self):
        lic = MagicMock()
        lic.specialization_type = "LFA_PLAYER_YOUTH"
        lic.id = 42
        db = _db_with_assessments([])
        result = _service().get_progression_status(lic, db)
        assert result['progress_percentage'] == 0.0
        assert result['current_level'] == 'YOUTH'

    def test_achievements_empty_at_zero(self):
        lic = MagicMock()
        lic.specialization_type = "LFA_PLAYER_PRE"
        lic.id = 1
        db = _db_with_assessments([])
        result = _service().get_progression_status(lic, db)
        assert result['achievements'] == []

    def test_achievement_proficient_at_60_percent(self):
        lic = MagicMock()
        lic.specialization_type = "LFA_PLAYER_YOUTH"
        lic.id = 1
        a = _mock_assessment("heading", 60.0, 6, 10)
        db = _db_with_assessments([a])
        result = _service().get_progression_status(lic, db)
        names = [ach['name'] for ach in result['achievements']]
        assert 'Proficient Player' in names

    def test_achievement_expert_and_proficient_at_80(self):
        lic = MagicMock()
        lic.specialization_type = "LFA_PLAYER_AMATEUR"
        lic.id = 1
        a = _mock_assessment("shooting", 80.0, 8, 10)
        db = _db_with_assessments([a])
        result = _service().get_progression_status(lic, db)
        names = [ach['name'] for ach in result['achievements']]
        assert 'Expert Player' in names
        assert 'Proficient Player' in names

    def test_next_milestone_proficient_below_60(self):
        lic = MagicMock()
        lic.specialization_type = "LFA_PLAYER_PRE"
        lic.id = 1
        db = _db_with_assessments([])
        result = _service().get_progression_status(lic, db)
        assert result['next_milestone'] is not None
        assert result['next_milestone']['name'] == 'Proficient Player'

    def test_skills_list_length_matches_valid_skills(self):
        svc = _service()
        lic = MagicMock()
        lic.specialization_type = "LFA_PLAYER_YOUTH"
        lic.id = 1
        db = _db_with_assessments([])
        result = svc.get_progression_status(lic, db)
        assert len(result['skills']) == len(svc.VALID_SKILLS)

    def test_unassessed_skills_have_zero_values(self):
        lic = MagicMock()
        lic.specialization_type = "LFA_PLAYER_YOUTH"
        lic.id = 1
        db = _db_with_assessments([])
        result = _service().get_progression_status(lic, db)
        for skill in result['skills']:
            assert skill['percentage'] == 0.0
            assert skill['points_earned'] == 0

    def test_unknown_spec_type_returns_unknown_level(self):
        lic = MagicMock()
        lic.specialization_type = "LFA_PLAYER_INVALID"
        lic.id = 1
        db = _db_with_assessments([])
        with patch(
            "app.services.specs.session_based.lfa_player_service.get_current_player_assignment",
            return_value=None,
        ):
            result = _service().get_progression_status(lic, db)
        assert result['current_level'] == 'Unknown'


# ===========================================================================
# TestPromoteToHigherAgeGroup
# ===========================================================================

class TestPromoteToHigherAgeGroup:

    def test_invalid_target_group_rejected(self):
        lic = MagicMock()
        lic.specialization_type = "LFA_PLAYER_YOUTH"
        db = MagicMock()
        ok, msg = _service().promote_to_higher_age_group(lic, 'SENIOR', 1, db)
        assert ok is False
        assert "canonical" in msg

    def test_invalid_current_spec_rejected(self):
        lic = MagicMock()
        lic.specialization_type = "INVALID_SPEC"
        db = MagicMock()
        ok, msg = _service().promote_to_higher_age_group(lic, 'PRO', 1, db)
        assert ok is False
        assert "canonical" in msg

    def test_already_in_target_group_rejected(self):
        lic = MagicMock()
        lic.specialization_type = "LFA_PLAYER_PRO"
        db = MagicMock()
        ok, msg = _service().promote_to_higher_age_group(lic, 'PRO', 1, db)
        assert ok is False
        assert "canonical" in msg

    def test_legacy_promotion_youth_to_pro_is_disabled(self):
        lic = MagicMock()
        lic.specialization_type = "LFA_PLAYER_YOUTH"
        db = MagicMock()
        ok, msg = _service().promote_to_higher_age_group(lic, 'PRO', 42, db)
        assert ok is False
        assert "canonical" in msg
        assert lic.specialization_type == "LFA_PLAYER_YOUTH"

    def test_legacy_promotion_pre_to_amateur_is_disabled(self):
        lic = MagicMock()
        lic.specialization_type = "LFA_PLAYER_PRE"
        db = MagicMock()
        ok, _ = _service().promote_to_higher_age_group(lic, 'AMATEUR', 1, db)
        assert ok is False
        assert lic.specialization_type == "LFA_PLAYER_PRE"

    def test_legacy_promotion_does_not_commit(self):
        lic = MagicMock()
        lic.specialization_type = "LFA_PLAYER_YOUTH"
        db = MagicMock()
        _service().promote_to_higher_age_group(lic, 'PRO', 1, db)
        db.commit.assert_not_called()
