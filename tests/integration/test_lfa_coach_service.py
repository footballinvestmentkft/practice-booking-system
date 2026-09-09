"""
Unit Tests for LFA Coach Service

Tests the LFA Coach specialization service including:
- Factory pattern registration
- Semester-based enrollment requirements
- 8-level certification progression
- Age validation for coaching
- Session booking logic
"""

import pytest
from datetime import date, datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.dialects import sqlite as sqlite_dialect
from app.database import Base

# Register PostgreSQL type → SQLite fallbacks
_stc = sqlite_dialect.base.SQLiteTypeCompiler
if not hasattr(_stc, 'visit_JSONB'):
    _stc.visit_JSONB = _stc.visit_JSON
if not hasattr(_stc, 'visit_ARRAY'):
    _stc.visit_ARRAY = lambda self, type_, **kw: 'TEXT'
if not hasattr(_stc, 'visit_UUID'):
    _stc.visit_UUID = lambda self, type_, **kw: 'VARCHAR(36)'
if not hasattr(_stc, 'visit_BYTEA'):
    _stc.visit_BYTEA = lambda self, type_, **kw: 'BLOB'
from app.models.user import User, UserRole
from app.models.license import UserLicense
from app.models.semester import Semester, SemesterStatus
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.session import Session as SessionModel
from app.models.specialization import SpecializationType
from app.models.ws1_domain import UserGuardianConsent
from app.services.specs import get_spec_service
from app.services.specs.semester_based.lfa_coach_service import LFACoachService


# ============================================================================
# TEST DATABASE SETUP
# ============================================================================

@pytest.fixture(scope="function")
def db_session():
    """Create a fresh in-memory database for each test"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def coach_service():
    """Create LFA Coach service instance"""
    return LFACoachService()


# ============================================================================
# TEST DATA FIXTURES
# ============================================================================

@pytest.fixture
def young_coach_user(db_session):
    """Create a 14-year-old user (minimum age for coaching)"""
    user = User(
        id=1,
        email="young.coach@test.com",
        name="Young Coach",
        password_hash="hashed_test_password",
        date_of_birth=date(2011, 6, 15),  # ~14 years old
        parental_consent=True,
        role=UserRole.STUDENT
    )
    db_session.add(user)
    db_session.commit()
    db_session.add(UserGuardianConsent(user_id=user.id, guardian_name="Test Guardian"))
    db_session.commit()
    return user


@pytest.fixture
def experienced_coach_user(db_session):
    """Create a 25-year-old experienced coach"""
    user = User(
        id=2,
        email="experienced.coach@test.com",
        name="Experienced Coach",
        password_hash="hashed_test_password",
        date_of_birth=date(2000, 3, 10),  # ~25 years old
        role=UserRole.INSTRUCTOR
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def too_young_user(db_session):
    """Create a 12-year-old user (too young for coaching)"""
    user = User(
        id=3,
        email="too.young@test.com",
        name="Too Young",
        password_hash="hashed_test_password",
        date_of_birth=date(2013, 1, 1),  # ~12 years old
        parental_consent=True,
        role=UserRole.STUDENT
    )
    db_session.add(user)
    db_session.commit()
    db_session.add(UserGuardianConsent(user_id=user.id, guardian_name="Test Guardian"))
    db_session.commit()
    return user


@pytest.fixture
def active_semester(db_session):
    """Create an active semester"""
    semester = Semester(
        id=1,
        code="SPRING-2025-COACH",
        name="Spring 2025",
        start_date=date(2025, 1, 15),
        end_date=date(2025, 6, 15),
        status=SemesterStatus.ONGOING
    )
    db_session.add(semester)
    db_session.commit()
    return semester


@pytest.fixture
def coach_license(db_session, young_coach_user):
    """Create active LFA Coach license"""
    license = UserLicense(
        id=1,
        user_id=young_coach_user.id,
        specialization_type=SpecializationType.LFA_COACH.value,  # String column needs .value
        is_active=True,
        current_level=1,  # PRE_ASSISTANT
        max_achieved_level=1,
        started_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc)
    )
    db_session.add(license)
    db_session.commit()
    return license


@pytest.fixture
def semester_enrollment_paid(db_session, young_coach_user, active_semester, coach_license):
    """Create paid semester enrollment"""
    enrollment = SemesterEnrollment(
        id=1,
        user_id=young_coach_user.id,
        semester_id=active_semester.id,
        user_license_id=coach_license.id,
        request_status=EnrollmentStatus.APPROVED,
        payment_verified=True,
        is_active=True,
        enrolled_at=datetime.now(timezone.utc)
    )
    db_session.add(enrollment)
    db_session.commit()
    return enrollment


@pytest.fixture
def coach_session(db_session, active_semester):
    """Create LFA Coach session"""
    session = SessionModel(
        id=1,
        title="Coaching Theory 101",
        target_specialization=SpecializationType.LFA_COACH,
        semester_id=active_semester.id,
        capacity=20,
        date_start=datetime(2025, 2, 1, 10, 0),
        date_end=datetime(2025, 2, 1, 12, 0)
    )
    db_session.add(session)
    db_session.commit()
    return session


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
    # All these should return LFACoachService
    variants = ["LFA_COACH", "LFA_COACH_PRE", "LFA_COACH_YOUTH"]

    for variant in variants:
        service = get_spec_service(variant)
        assert isinstance(service, LFACoachService)


# ============================================================================
# SEMESTER-BASED FLAG TESTS
# ============================================================================

def test_is_semester_based(coach_service):
    """Test that LFA Coach is marked as semester-based"""
    assert coach_service.is_semester_based() == True
    assert coach_service.is_session_based() == False


# ============================================================================
# AGE VALIDATION TESTS
# ============================================================================

def test_age_validation_minimum_age(coach_service, young_coach_user, too_young_user, db_session):
    """Test minimum age requirement (14 years)"""
    # 14 years old - should be eligible
    is_eligible, msg = coach_service.validate_age_eligibility(young_coach_user, db=db_session)
    assert is_eligible == True
    assert "eligible" in msg.lower()

    # 12 years old - too young
    is_eligible, msg = coach_service.validate_age_eligibility(too_young_user, db=db_session)
    assert is_eligible == False
    assert msg == "PROGRAM_MINIMUM_AGE"


def test_age_validation_for_specific_certifications(coach_service, young_coach_user, db_session):
    """Certification levels do not introduce additional age gates."""
    is_eligible, msg = coach_service.validate_age_eligibility(
        young_coach_user, target_group="PRE_ASSISTANT", db=db_session
    )
    assert is_eligible == True

    # The same canonical program-entry rule applies at PRO_HEAD.
    is_eligible, msg = coach_service.validate_age_eligibility(
        young_coach_user, target_group="PRO_HEAD", db=db_session
    )
    assert is_eligible == True
    assert "eligible" in msg.lower()


# ============================================================================
# CERTIFICATION LEVEL TESTS
# ============================================================================

def test_get_current_certification(coach_service, coach_license, db_session):
    """Test getting current certification from license level"""
    # Level 1 → PRE_ASSISTANT
    cert = coach_service.get_current_certification(coach_license.id, db_session)
    assert cert == "PRE_ASSISTANT"

    # Update to level 3 → YOUTH_ASSISTANT
    coach_license.current_level = 3
    db_session.commit()
    cert = coach_service.get_current_certification(coach_license.id, db_session)
    assert cert == "YOUTH_ASSISTANT"

    # Level 8 → PRO_HEAD
    coach_license.current_level = 8
    db_session.commit()
    cert = coach_service.get_current_certification(coach_license.id, db_session)
    assert cert == "PRO_HEAD"


def test_get_next_certification(coach_service):
    """Test certification progression sequence"""
    # PRE_ASSISTANT → PRE_HEAD
    next_cert = coach_service.get_next_certification("PRE_ASSISTANT")
    assert next_cert == "PRE_HEAD"

    # PRE_HEAD → YOUTH_ASSISTANT
    next_cert = coach_service.get_next_certification("PRE_HEAD")
    assert next_cert == "YOUTH_ASSISTANT"

    # PRO_ASSISTANT → PRO_HEAD
    next_cert = coach_service.get_next_certification("PRO_ASSISTANT")
    assert next_cert == "PRO_HEAD"

    # PRO_HEAD → None (max level)
    next_cert = coach_service.get_next_certification("PRO_HEAD")
    assert next_cert is None


def test_certification_info(coach_service):
    """Test getting certification display information"""
    info = coach_service.get_certification_info("PRE_ASSISTANT")
    assert info['name'] == "LFA Pre Football Assistant Coach"
    assert info['level'] == 1
    assert info['min_coach_age'] == 14
    assert info['age_group'] == "Pre (5-13 years)"
    assert info['role'] == "Assistant Coach"

    info = coach_service.get_certification_info("PRO_HEAD")
    assert info['name'] == "LFA PRO Football Head Coach"
    assert info['level'] == 8
    assert info['min_coach_age'] == 14
    assert info['role'] == "Head Coach"


def test_all_8_certification_levels(coach_service):
    """Test that all 8 certification levels are defined"""
    assert len(coach_service.COACH_LEVELS) == 8
    assert coach_service.COACH_LEVELS[0] == "PRE_ASSISTANT"
    assert coach_service.COACH_LEVELS[7] == "PRO_HEAD"

    # Each level should have info
    for level in coach_service.COACH_LEVELS:
        info = coach_service.get_certification_info(level)
        assert 'name' in info
        assert 'level' in info
        assert 'min_coach_age' in info


# ============================================================================
# SESSION BOOKING TESTS
# ============================================================================

def test_can_book_session_with_paid_enrollment(
    coach_service, young_coach_user, coach_session,
    semester_enrollment_paid, db_session
):
    """Test session booking with valid paid enrollment"""
    can_book, reason = coach_service.can_book_session(young_coach_user, coach_session, db_session)
    assert can_book == True
    assert "eligible" in reason.lower()


def test_cannot_book_without_license(coach_service, too_young_user, coach_session, db_session):
    """Test session booking fails without license"""
    can_book, reason = coach_service.can_book_session(too_young_user, coach_session, db_session)
    assert can_book == False
    assert "license" in reason.lower()


def test_cannot_book_without_semester_enrollment(
    coach_service, young_coach_user, coach_session,
    coach_license, db_session
):
    """Test session booking fails without semester enrollment"""
    can_book, reason = coach_service.can_book_session(young_coach_user, coach_session, db_session)
    assert can_book == False
    assert "semester enrollment" in reason.lower()


def test_cannot_book_without_payment_verification(
    coach_service, young_coach_user, coach_session,
    active_semester, coach_license, db_session
):
    """Test session booking fails without payment verification"""
    # Create unpaid enrollment
    enrollment = SemesterEnrollment(
        user_id=young_coach_user.id,
        semester_id=active_semester.id,
        user_license_id=coach_license.id,
        request_status=EnrollmentStatus.APPROVED,
        payment_verified=False,  # NOT PAID
        is_active=True
    )
    db_session.add(enrollment)
    db_session.commit()

    can_book, reason = coach_service.can_book_session(young_coach_user, coach_session, db_session)
    assert can_book == False
    assert "payment" in reason.lower()


# ============================================================================
# ENROLLMENT REQUIREMENTS TESTS
# ============================================================================

def test_get_enrollment_requirements_complete(
    coach_service, young_coach_user, semester_enrollment_paid, db_session
):
    """Test enrollment requirements when all conditions met"""
    reqs = coach_service.get_enrollment_requirements(young_coach_user, db_session)

    assert reqs['can_participate'] == True
    assert len(reqs['missing_requirements']) == 0
    assert reqs['current_status']['has_license'] == True
    assert reqs['current_status']['has_semester_enrollment'] == True
    assert reqs['current_status']['payment_verified'] == True
    assert reqs['current_status']['current_certification'] == "PRE_ASSISTANT"


def test_get_enrollment_requirements_missing_payment(
    coach_service, young_coach_user, coach_license,
    active_semester, db_session
):
    """Test enrollment requirements with missing payment"""
    # Create unpaid enrollment
    enrollment = SemesterEnrollment(
        user_id=young_coach_user.id,
        semester_id=active_semester.id,
        user_license_id=coach_license.id,
        request_status=EnrollmentStatus.APPROVED,
        payment_verified=False,
        is_active=True
    )
    db_session.add(enrollment)
    db_session.commit()

    reqs = coach_service.get_enrollment_requirements(young_coach_user, db_session)

    assert reqs['can_participate'] == False
    assert "Payment verification required" in reqs['missing_requirements']
    assert reqs['current_status']['has_semester_enrollment'] == True
    assert reqs['current_status']['payment_verified'] == False


def test_get_enrollment_requirements_no_enrollment(
    coach_service, young_coach_user, coach_license, db_session
):
    """Test enrollment requirements without semester enrollment"""
    reqs = coach_service.get_enrollment_requirements(young_coach_user, db_session)

    assert reqs['can_participate'] == False
    assert "Semester enrollment required" in reqs['missing_requirements']
    assert reqs['current_status']['has_license'] == True
    assert reqs['current_status']['has_semester_enrollment'] == False


def test_get_enrollment_requirements_too_young(
    coach_service, too_young_user, db_session
):
    """Test enrollment requirements with age restriction"""
    reqs = coach_service.get_enrollment_requirements(too_young_user, db_session)

    assert reqs['can_participate'] == False
    assert any("Age requirement" in req for req in reqs['missing_requirements'])


# ============================================================================
# PROGRESSION STATUS TESTS
# ============================================================================

def test_get_progression_status_level_1(coach_service, coach_license, db_session):
    """Test progression status at level 1 (PRE_ASSISTANT)"""
    status = coach_service.get_progression_status(coach_license, db_session)

    assert status['current_level'] == 1
    assert status['current_certification'] == "PRE_ASSISTANT"
    assert status['next_certification'] == "PRE_HEAD"
    assert status['progress_percentage'] == 12.5  # 1/8 * 100
    assert status['current_cert_info']['name'] == "LFA Pre Football Assistant Coach"
    assert status['next_cert_info']['name'] == "LFA Pre Football Head Coach"


def test_get_progression_status_level_8(coach_service, coach_license, db_session):
    """Test progression status at max level (PRO_HEAD)"""
    coach_license.current_level = 8
    db_session.commit()

    status = coach_service.get_progression_status(coach_license, db_session)

    assert status['current_level'] == 8
    assert status['current_certification'] == "PRO_HEAD"
    assert status['next_certification'] is None
    assert status['progress_percentage'] == 100.0
    assert len(status['achievements']) > 0


def test_get_progression_status_achievements(coach_service, coach_license, db_session):
    """Test achievement unlocking at different levels"""
    # Level 1 - no achievements yet
    status = coach_service.get_progression_status(coach_license, db_session)
    assert len(status['achievements']) == 0

    # Level 2 - Head Coach Qualified
    coach_license.current_level = 2
    db_session.commit()
    status = coach_service.get_progression_status(coach_license, db_session)
    assert len(status['achievements']) == 1

    # Level 8 - all achievements
    coach_license.current_level = 8
    db_session.commit()
    status = coach_service.get_progression_status(coach_license, db_session)
    assert len(status['achievements']) == 4


# ============================================================================
# CERTIFICATION PROMOTION TESTS
# ============================================================================

def test_certify_next_level_success(coach_service, coach_license, db_session):
    """Test successful certification to next level"""
    result = coach_service.certify_next_level(
        user_license_id=coach_license.id,
        certified_by=999,
        db=db_session,
        exam_score=85.0,
        notes="Excellent performance"
    )

    assert result['success'] == True
    assert result['from_cert'] == "PRE_ASSISTANT"
    assert result['to_cert'] == "PRE_HEAD"
    assert result['new_level'] == 2
    assert result['exam_score'] == 85.0

    # Check license was updated
    db_session.refresh(coach_license)
    assert coach_license.current_level == 2
    assert coach_license.max_achieved_level == 2


def test_certify_next_level_max_level_error(coach_service, coach_license, db_session):
    """Test certification fails at max level"""
    coach_license.current_level = 8
    db_session.commit()

    with pytest.raises(ValueError, match="highest certification"):
        coach_service.certify_next_level(
            user_license_id=coach_license.id,
            certified_by=999,
            db=db_session,
            exam_score=90.0
        )


def test_certify_next_level_low_exam_score(coach_service, coach_license, db_session):
    """Test certification fails with exam score below 80%"""
    with pytest.raises(ValueError, match="below passing threshold"):
        coach_service.certify_next_level(
            user_license_id=coach_license.id,
            certified_by=999,
            db=db_session,
            exam_score=75.0  # Below 80%
        )


def test_certify_next_level_invalid_exam_score(coach_service, coach_license, db_session):
    """Test certification fails with invalid exam score"""
    with pytest.raises(ValueError, match="between 0-100"):
        coach_service.certify_next_level(
            user_license_id=coach_license.id,
            certified_by=999,
            db=db_session,
            exam_score=105.0  # Over 100
        )


# ============================================================================
# RUN TESTS
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
