"""
API Tests for Tournament Enrollment Protection

Tests enrollment validation rules:
1. Status Protection: Players can ONLY enroll when status = READY_FOR_ENROLLMENT
2. Role Protection: Only STUDENT role can enroll
3. License Protection: Must have LFA_FOOTBALL_PLAYER license
4. Credit Protection: Must have sufficient credits

Coverage:
- Negative test: Enrollment blocked when status = INSTRUCTOR_ASSIGNED
- Negative test: Enrollment blocked when status = SEEKING_INSTRUCTOR
- Positive test: Enrollment succeeds when status = READY_FOR_ENROLLMENT
- Negative test: Non-student cannot enroll
- Negative test: Student without LFA_FOOTBALL_PLAYER license cannot enroll
- Negative test: Student with insufficient credits cannot enroll
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from datetime import date, datetime, timedelta

from app.models.user import User, UserRole
from app.models.semester import Semester, SemesterStatus
from app.models.license import UserLicense
from app.models.specialization import SpecializationType
from app.models.session import Session as SessionModel, SessionType, EventCategory
from app.core.security import get_password_hash
from app.core.auth import create_access_token


# =============================================================================
# TEST FIXTURES
# =============================================================================

@pytest.fixture
def student_with_license(test_db: Session) -> User:
    """Create a student user with LFA_FOOTBALL_PLAYER license and credits"""
    user = User(
        email="player.licensed@test.com",
        name="Licensed Player",
        password_hash=get_password_hash("testpass123"),
        role=UserRole.STUDENT,
        is_active=True,
        date_of_birth=date(2005, 6, 15),  # 18+ years old (AMATEUR/PRO eligible)
        credit_balance=500.0,  # Sufficient credits
        credit_purchased=500.0
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    # Create LFA_FOOTBALL_PLAYER license
    license = UserLicense(
        user_id=user.id,
        specialization_type=SpecializationType.LFA_FOOTBALL_PLAYER.value,
        current_level=1,
        max_achieved_level=1,
        started_at=datetime.utcnow(),
        is_active=True,
        onboarding_completed=True
    )
    test_db.add(license)
    test_db.commit()

    return user


@pytest.fixture
def student_without_license(test_db: Session) -> User:
    """Create a student user WITHOUT LFA_FOOTBALL_PLAYER license"""
    user = User(
        email="player.unlicensed@test.com",
        name="Unlicensed Player",
        password_hash=get_password_hash("testpass123"),
        role=UserRole.STUDENT,
        is_active=True,
        date_of_birth=date(2005, 6, 15),
        credit_balance=500.0,
        credit_purchased=500.0
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)
    return user


@pytest.fixture
def student_no_credits(test_db: Session) -> User:
    """Create a student user with license but NO credits"""
    user = User(
        email="player.nocredits@test.com",
        name="Broke Player",
        password_hash=get_password_hash("testpass123"),
        role=UserRole.STUDENT,
        is_active=True,
        date_of_birth=date(2005, 6, 15),
        credit_balance=0.0,  # NO CREDITS
        credit_purchased=0.0
    )
    test_db.add(user)
    test_db.commit()
    test_db.refresh(user)

    # Create LFA_FOOTBALL_PLAYER license
    license = UserLicense(
        user_id=user.id,
        specialization_type=SpecializationType.LFA_FOOTBALL_PLAYER.value,
        current_level=1,
        max_achieved_level=1,
        started_at=datetime.utcnow(),
        is_active=True,
        onboarding_completed=True
    )
    test_db.add(license)
    test_db.commit()

    return user


@pytest.fixture
def tournament_seeking_instructor(test_db: Session) -> Semester:
    """Tournament in SEEKING_INSTRUCTOR status (enrollment BLOCKED)"""
    tournament_date = date.today() + timedelta(days=7)
    semester = Semester(
        code=f"TOURN-SEEKING-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        name="Tournament Seeking Instructor",
        start_date=tournament_date,
        end_date=tournament_date,
        status=SemesterStatus.SEEKING_INSTRUCTOR,  # ❌ BLOCKED
        tournament_status="SEEKING_INSTRUCTOR",
        master_instructor_id=None,
        specialization_type=SpecializationType.LFA_PLAYER_AMATEUR.value,
        age_group="AMATEUR",
        enrollment_cost=100.0
    )
    test_db.add(semester)
    test_db.commit()
    test_db.refresh(semester)

    # Create first session (required for enrollment deadline check)
    first_session = SessionModel(
        title="Tournament Game 1",
        description="First game",
        date_start=datetime.combine(tournament_date, datetime.min.time()) + timedelta(hours=10),
        date_end=datetime.combine(tournament_date, datetime.min.time()) + timedelta(hours=11, minutes=30),
        session_type=SessionType.on_site,
        capacity=20,
        instructor_id=None,
        semester_id=semester.id,
        credit_cost=1,
        event_category=EventCategory.MATCH,
        game_type="Round 1"
    )
    test_db.add(first_session)
    test_db.commit()

    return semester


@pytest.fixture
def tournament_instructor_assigned(test_db: Session, instructor_user: User) -> Semester:
    """Tournament in INSTRUCTOR_ASSIGNED status (enrollment BLOCKED)"""
    tournament_date = date.today() + timedelta(days=7)
    semester = Semester(
        code=f"TOURN-ASSIGNED-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        name="Tournament Instructor Assigned",
        start_date=tournament_date,
        end_date=tournament_date,
        status=SemesterStatus.INSTRUCTOR_ASSIGNED,  # ❌ BLOCKED (admin hasn't opened enrollment yet)
        tournament_status="INSTRUCTOR_ASSIGNED",
        master_instructor_id=instructor_user.id,
        specialization_type=SpecializationType.LFA_PLAYER_AMATEUR.value,
        age_group="AMATEUR",
        enrollment_cost=100.0
    )
    test_db.add(semester)
    test_db.commit()
    test_db.refresh(semester)

    # Create first session
    first_session = SessionModel(
        title="Tournament Game 1",
        description="First game",
        date_start=datetime.combine(tournament_date, datetime.min.time()) + timedelta(hours=10),
        date_end=datetime.combine(tournament_date, datetime.min.time()) + timedelta(hours=11, minutes=30),
        session_type=SessionType.on_site,
        capacity=20,
        instructor_id=instructor_user.id,
        semester_id=semester.id,
        credit_cost=1,
        event_category=EventCategory.MATCH,
        game_type="Round 1"
    )
    test_db.add(first_session)
    test_db.commit()

    return semester


@pytest.fixture
def tournament_ready_for_enrollment(test_db: Session, instructor_user: User) -> Semester:
    """Tournament in READY_FOR_ENROLLMENT status (enrollment ALLOWED)"""
    tournament_date = date.today() + timedelta(days=7)
    semester = Semester(
        code=f"TOURN-READY-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        name="Tournament Ready for Enrollment",
        start_date=tournament_date,
        end_date=tournament_date,
        status=SemesterStatus.READY_FOR_ENROLLMENT,
        tournament_status="ENROLLMENT_OPEN",  # ✅ ALLOWED — endpoint checks tournament_status
        master_instructor_id=instructor_user.id,
        specialization_type=SpecializationType.LFA_PLAYER_AMATEUR.value,
        age_group="AMATEUR",
        enrollment_cost=100.0
    )
    test_db.add(semester)
    test_db.commit()
    test_db.refresh(semester)

    # Create first session
    first_session = SessionModel(
        title="Tournament Game 1",
        description="First game",
        date_start=datetime.combine(tournament_date, datetime.min.time()) + timedelta(hours=10),
        date_end=datetime.combine(tournament_date, datetime.min.time()) + timedelta(hours=11, minutes=30),
        session_type=SessionType.on_site,
        capacity=20,
        instructor_id=instructor_user.id,
        semester_id=semester.id,
        credit_cost=1,
        event_category=EventCategory.MATCH,
        game_type="Round 1"
    )
    test_db.add(first_session)
    test_db.commit()

    return semester


@pytest.fixture
def student_token_with_license(student_with_license: User) -> str:
    """Generate JWT token for licensed student"""
    return create_access_token(data={"sub": student_with_license.email})


@pytest.fixture
def student_token_without_license(student_without_license: User) -> str:
    """Generate JWT token for unlicensed student"""
    return create_access_token(data={"sub": student_without_license.email})


@pytest.fixture
def student_token_no_credits(student_no_credits: User) -> str:
    """Generate JWT token for student with no credits"""
    return create_access_token(data={"sub": student_no_credits.email})


# =============================================================================
# TEST GROUP 1: STATUS PROTECTION
# =============================================================================

def test_01_enrollment_blocked_seeking_instructor(
    client: TestClient,
    tournament_seeking_instructor: Semester,
    student_token_with_license: str
):
    """
    Test E1.1: Enrollment blocked when tournament status = SEEKING_INSTRUCTOR
    Expected: 400 BAD_REQUEST with error message
    """
    response = client.post(
        f"/api/v1/tournaments/{tournament_seeking_instructor.id}/enroll",
        headers={"Authorization": f"Bearer {student_token_with_license}"}
    )

    assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
    data = response.json()
    error_message = data.get("error", {}).get("message", data.get("detail", ""))
    assert "not accepting enrollments" in error_message.lower()
    assert "SEEKING_INSTRUCTOR" in error_message


def test_02_enrollment_blocked_instructor_assigned(
    client: TestClient,
    tournament_instructor_assigned: Semester,
    student_token_with_license: str
):
    """
    Test E1.2: Enrollment blocked when tournament status = INSTRUCTOR_ASSIGNED
    This is the CRITICAL test - admin must open enrollment first!
    Expected: 400 BAD_REQUEST with error message
    """
    response = client.post(
        f"/api/v1/tournaments/{tournament_instructor_assigned.id}/enroll",
        headers={"Authorization": f"Bearer {student_token_with_license}"}
    )

    assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
    data = response.json()
    error_message = data.get("error", {}).get("message", data.get("detail", ""))
    assert "not accepting enrollments" in error_message.lower()
    assert "INSTRUCTOR_ASSIGNED" in error_message


def test_03_enrollment_allowed_ready_for_enrollment(
    client: TestClient,
    tournament_ready_for_enrollment: Semester,
    student_token_with_license: str,
    test_db: Session
):
    """
    Test E1.3: Enrollment succeeds when tournament status = READY_FOR_ENROLLMENT
    Expected: 201 CREATED with enrollment details
    """
    response = client.post(
        f"/api/v1/tournaments/{tournament_ready_for_enrollment.id}/enroll",
        headers={"Authorization": f"Bearer {student_token_with_license}"}
    )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()

    # Verify response structure
    assert data.get("success") is True
    assert "enrollment" in data
    assert "tournament" in data
    assert "credits_remaining" in data
    assert data["tournament"]["id"] == tournament_ready_for_enrollment.id
    assert data["enrollment"]["request_status"] == "approved"  # Auto-approved
    assert data["credits_remaining"] == 400  # 500 - 100 enrollment_cost


# =============================================================================
# TEST GROUP 2: ROLE PROTECTION
# =============================================================================

def test_04_enrollment_blocked_non_student(
    client: TestClient,
    tournament_ready_for_enrollment: Semester,
    instructor_token: str
):
    """
    Test E2.1: Non-student (INSTRUCTOR) cannot enroll
    Expected: 403 FORBIDDEN
    """
    response = client.post(
        f"/api/v1/tournaments/{tournament_ready_for_enrollment.id}/enroll",
        headers={"Authorization": f"Bearer {instructor_token}"}
    )

    assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"
    data = response.json()
    error_message = data.get("error", {}).get("message", data.get("detail", ""))
    assert "only students" in error_message.lower()


def test_05_enrollment_blocked_admin(
    client: TestClient,
    tournament_ready_for_enrollment: Semester,
    admin_token: str
):
    """
    Test E2.2: Admin cannot enroll
    Expected: 403 FORBIDDEN
    """
    response = client.post(
        f"/api/v1/tournaments/{tournament_ready_for_enrollment.id}/enroll",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 403, f"Expected 403, got {response.status_code}: {response.text}"
    data = response.json()
    error_message = data.get("error", {}).get("message", data.get("detail", ""))
    assert "only students" in error_message.lower()


# =============================================================================
# TEST GROUP 3: LICENSE PROTECTION
# =============================================================================

def test_06_enrollment_blocked_no_license(
    client: TestClient,
    tournament_ready_for_enrollment: Semester,
    student_token_without_license: str
):
    """
    Test E3.1: Student without LFA_FOOTBALL_PLAYER license cannot enroll
    Expected: 400 BAD_REQUEST
    """
    response = client.post(
        f"/api/v1/tournaments/{tournament_ready_for_enrollment.id}/enroll",
        headers={"Authorization": f"Bearer {student_token_without_license}"}
    )

    assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
    data = response.json()
    error_message = data.get("error", {}).get("message", data.get("detail", ""))
    assert "license not found" in error_message.lower()


# =============================================================================
# TEST GROUP 4: CREDIT PROTECTION
# =============================================================================

def test_07_enrollment_blocked_insufficient_credits(
    client: TestClient,
    tournament_ready_for_enrollment: Semester,
    student_token_no_credits: str
):
    """
    Test E4.1: Student with insufficient credits cannot enroll
    Expected: 400 BAD_REQUEST
    """
    response = client.post(
        f"/api/v1/tournaments/{tournament_ready_for_enrollment.id}/enroll",
        headers={"Authorization": f"Bearer {student_token_no_credits}"}
    )

    assert response.status_code == 400, f"Expected 400, got {response.status_code}: {response.text}"
    data = response.json()
    error_message = data.get("error", {}).get("message", data.get("detail", ""))
    assert "insufficient" in error_message.lower() or "credit" in error_message.lower()


# =============================================================================
# TEST GROUP 5: EDGE CASES
# =============================================================================

def test_08_enrollment_blocked_nonexistent_tournament(
    client: TestClient,
    student_token_with_license: str
):
    """
    Test E5.1: Enrollment fails for non-existent tournament
    Expected: 404 NOT_FOUND
    """
    response = client.post(
        "/api/v1/tournaments/999999/enroll",
        headers={"Authorization": f"Bearer {student_token_with_license}"}
    )

    assert response.status_code == 404, f"Expected 404, got {response.status_code}: {response.text}"
    data = response.json()
    error_message = data.get("error", {}).get("message", data.get("detail", ""))
    assert "not found" in error_message.lower()


def test_09_enrollment_blocked_duplicate(
    client: TestClient,
    tournament_ready_for_enrollment: Semester,
    student_token_with_license: str
):
    """
    Test E5.2: Student cannot enroll twice in same tournament
    Expected: First enrollment succeeds (200), second enrollment fails (400)
    """
    # First enrollment - should succeed
    response1 = client.post(
        f"/api/v1/tournaments/{tournament_ready_for_enrollment.id}/enroll",
        headers={"Authorization": f"Bearer {student_token_with_license}"}
    )
    assert response1.status_code == 200, f"First enrollment failed: {response1.text}"

    # Second enrollment - should fail
    response2 = client.post(
        f"/api/v1/tournaments/{tournament_ready_for_enrollment.id}/enroll",
        headers={"Authorization": f"Bearer {student_token_with_license}"}
    )
    assert response2.status_code == 400, f"Expected 400 for duplicate enrollment, got {response2.status_code}: {response2.text}"
    data = response2.json()
    error_message = data.get("error", {}).get("message", data.get("detail", ""))
    assert "already enrolled" in error_message.lower()
