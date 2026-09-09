"""
Simple Unit Tests for LFA Coach Service

Tests core functionality without full database setup.
"""

import pytest
from datetime import date
from app.services.specs import get_spec_service
from app.services.specs.semester_based.lfa_coach_service import LFACoachService


# ============================================================================
# FACTORY PATTERN TESTS
# ============================================================================

def test_factory_returns_lfa_coach_service():
    """Test that factory returns LFACoachService for LFA_COACH specialization"""
    service = get_spec_service("LFA_COACH")
    assert isinstance(service, LFACoachService)
    assert service.get_specialization_name() == "LFA Coach"


def test_factory_handles_coach_variants():
    """Test that factory recognizes LFA_COACH variants"""
    variants = ["LFA_COACH", "LFA_COACH_PRE", "LFA_COACH_YOUTH"]

    for variant in variants:
        service = get_spec_service(variant)
        assert isinstance(service, LFACoachService)


# ============================================================================
# SEMESTER-BASED FLAG TESTS
# ============================================================================

def test_is_semester_based():
    """Test that LFA Coach is marked as semester-based"""
    service = LFACoachService()
    assert service.is_semester_based() == True
    assert service.is_session_based() == False


# ============================================================================
# CERTIFICATION LEVEL TESTS
# ============================================================================

def test_all_8_certification_levels():
    """Test that all 8 certification levels are defined"""
    service = LFACoachService()
    assert len(service.COACH_LEVELS) == 8
    assert service.COACH_LEVELS[0] == "PRE_ASSISTANT"
    assert service.COACH_LEVELS[7] == "PRO_HEAD"

    # Each level should have info
    for level in service.COACH_LEVELS:
        info = service.get_certification_info(level)
        assert 'name' in info
        assert 'level' in info
        assert 'min_coach_age' in info
        assert info['level'] >= 1 and info['level'] <= 8


def test_get_next_certification():
    """Test certification progression sequence"""
    service = LFACoachService()

    # PRE_ASSISTANT → PRE_HEAD
    next_cert = service.get_next_certification("PRE_ASSISTANT")
    assert next_cert == "PRE_HEAD"

    # PRE_HEAD → YOUTH_ASSISTANT
    next_cert = service.get_next_certification("PRE_HEAD")
    assert next_cert == "YOUTH_ASSISTANT"

    # YOUTH_ASSISTANT → YOUTH_HEAD
    next_cert = service.get_next_certification("YOUTH_ASSISTANT")
    assert next_cert == "YOUTH_HEAD"

    # YOUTH_HEAD → AMATEUR_ASSISTANT
    next_cert = service.get_next_certification("YOUTH_HEAD")
    assert next_cert == "AMATEUR_ASSISTANT"

    # AMATEUR_ASSISTANT → AMATEUR_HEAD
    next_cert = service.get_next_certification("AMATEUR_ASSISTANT")
    assert next_cert == "AMATEUR_HEAD"

    # AMATEUR_HEAD → PRO_ASSISTANT
    next_cert = service.get_next_certification("AMATEUR_HEAD")
    assert next_cert == "PRO_ASSISTANT"

    # PRO_ASSISTANT → PRO_HEAD
    next_cert = service.get_next_certification("PRO_ASSISTANT")
    assert next_cert == "PRO_HEAD"

    # PRO_HEAD → None (max level)
    next_cert = service.get_next_certification("PRO_HEAD")
    assert next_cert is None


def test_certification_info():
    """Test getting certification display information"""
    service = LFACoachService()

    # Level 1: PRE_ASSISTANT
    info = service.get_certification_info("PRE_ASSISTANT")
    assert info['name'] == "LFA Pre Football Assistant Coach"
    assert info['level'] == 1
    assert info['min_coach_age'] == 14
    assert info['age_group'] == "Pre (5-13 years)"
    assert info['role'] == "Assistant Coach"
    assert info['requirements']['teaching_hours'] == 0  # Entry level
    assert info['requirements']['previous_cert'] is None

    # Level 2: PRE_HEAD
    info = service.get_certification_info("PRE_HEAD")
    assert info['name'] == "LFA Pre Football Head Coach"
    assert info['level'] == 2
    assert info['min_coach_age'] == 14
    assert info['role'] == "Head Coach"
    assert info['requirements']['teaching_hours'] == 50
    assert info['requirements']['previous_cert'] == "PRE_ASSISTANT"

    # Level 8: PRO_HEAD
    info = service.get_certification_info("PRO_HEAD")
    assert info['name'] == "LFA PRO Football Head Coach"
    assert info['level'] == 8
    assert info['min_coach_age'] == 14
    assert info['role'] == "Head Coach"
    assert info['requirements']['teaching_hours'] == 1200
    assert info['requirements']['previous_cert'] == "PRO_ASSISTANT"


def test_certification_progression_requirements():
    """Test that each level requires previous certification"""
    service = LFACoachService()

    expected_progression = [
        ("PRE_ASSISTANT", None),  # Entry level
        ("PRE_HEAD", "PRE_ASSISTANT"),
        ("YOUTH_ASSISTANT", "PRE_HEAD"),
        ("YOUTH_HEAD", "YOUTH_ASSISTANT"),
        ("AMATEUR_ASSISTANT", "YOUTH_HEAD"),
        ("AMATEUR_HEAD", "AMATEUR_ASSISTANT"),
        ("PRO_ASSISTANT", "AMATEUR_HEAD"),
        ("PRO_HEAD", "PRO_ASSISTANT"),
    ]

    for cert_level, expected_prev in expected_progression:
        info = service.get_certification_info(cert_level)
        assert info['requirements']['previous_cert'] == expected_prev


def test_certification_age_requirements():
    """All levels use the canonical program-entry age gate."""
    service = LFACoachService()

    age_requirements = [
        ("PRE_ASSISTANT", 14),
        ("PRE_HEAD", 14),
        ("YOUTH_ASSISTANT", 14),
        ("YOUTH_HEAD", 14),
        ("AMATEUR_ASSISTANT", 14),
        ("AMATEUR_HEAD", 14),
        ("PRO_ASSISTANT", 14),
        ("PRO_HEAD", 14),
    ]

    for cert_level, expected_age in age_requirements:
        info = service.get_certification_info(cert_level)
        assert info['min_coach_age'] == expected_age


def test_certification_teaching_hours_increase():
    """Test that teaching hours requirement increases"""
    service = LFACoachService()

    teaching_hours = [
        ("PRE_ASSISTANT", 0),
        ("PRE_HEAD", 50),
        ("YOUTH_ASSISTANT", 100),
        ("YOUTH_HEAD", 200),
        ("AMATEUR_ASSISTANT", 300),
        ("AMATEUR_HEAD", 500),
        ("PRO_ASSISTANT", 800),
        ("PRO_HEAD", 1200),
    ]

    for cert_level, expected_hours in teaching_hours:
        info = service.get_certification_info(cert_level)
        assert info['requirements']['teaching_hours'] == expected_hours


def test_minimum_age_constant():
    """Test that minimum age for LFA Coach is 14"""
    service = LFACoachService()
    assert service.MINIMUM_AGE == 14


def test_age_calculation():
    """Test age calculation utility method"""
    service = LFACoachService()

    def _expected_age(dob: date) -> int:
        today = date.today()
        return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

    dob = date(2011, 6, 15)
    assert service.calculate_age(dob) == _expected_age(dob)

    dob = date(2000, 3, 10)
    assert service.calculate_age(dob) == _expected_age(dob)


def test_invalid_certification_level():
    """Test handling of invalid certification level"""
    service = LFACoachService()

    with pytest.raises(ValueError, match="Invalid certification level"):
        service.get_next_certification("INVALID_LEVEL")


def test_unknown_certification_info():
    """Test that unknown certification returns default info"""
    service = LFACoachService()

    info = service.get_certification_info("UNKNOWN_CERT")
    assert info['name'] == "Unknown"
    assert info['icon'] == "❓"
    assert info['level'] == 0


# ============================================================================
# CERTIFICATION MAPPING TESTS
# ============================================================================

def test_assistant_vs_head_coach_roles():
    """Test that Assistant and Head Coach roles are properly distinguished"""
    service = LFACoachService()

    assistant_levels = ["PRE_ASSISTANT", "YOUTH_ASSISTANT", "AMATEUR_ASSISTANT", "PRO_ASSISTANT"]
    head_levels = ["PRE_HEAD", "YOUTH_HEAD", "AMATEUR_HEAD", "PRO_HEAD"]

    for level in assistant_levels:
        info = service.get_certification_info(level)
        assert info['role'] == "Assistant Coach"

    for level in head_levels:
        info = service.get_certification_info(level)
        assert info['role'] == "Head Coach"


def test_age_group_coverage():
    """Test that all 4 age groups are covered (Pre, Youth, Amateur, Pro)"""
    service = LFACoachService()

    age_groups_found = set()
    for cert_level in service.COACH_LEVELS:
        info = service.get_certification_info(cert_level)
        age_group = info['age_group']
        age_groups_found.add(age_group)

    # Should have exactly 4 unique age groups
    assert len(age_groups_found) == 4
    assert "Pre (5-13 years)" in age_groups_found
    assert "Youth (14-18 years)" in age_groups_found
    assert "Amateur (14+ years)" in age_groups_found
    assert "PRO (16+ years)" in age_groups_found


# ============================================================================
# SERVICE CONFIGURATION TESTS
# ============================================================================

def test_all_certifications_require_exam_and_first_aid():
    """Test that all certifications require exam and first aid"""
    service = LFACoachService()

    for cert_level in service.COACH_LEVELS:
        info = service.get_certification_info(cert_level)
        assert info['requirements']['certification_exam'] == True
        assert info['requirements']['first_aid'] == True


def test_student_feedback_requirement():
    """Test that student feedback is required for all except entry level"""
    service = LFACoachService()

    # PRE_ASSISTANT (entry level) doesn't require student feedback
    info = service.get_certification_info("PRE_ASSISTANT")
    assert info['requirements']['student_feedback'] == False

    # All other levels require student feedback
    for cert_level in service.COACH_LEVELS[1:]:
        info = service.get_certification_info(cert_level)
        assert info['requirements']['student_feedback'] == True


# ============================================================================
# RUN TESTS
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
