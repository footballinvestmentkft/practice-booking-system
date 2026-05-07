"""
Integration Critical Suite — Fixtures with CREATE + CLEANUP pattern

Philosophy:
- CREATE: Explicit user creation via API with unique namespace (INT_TEST_ + timestamp)
- CLEANUP: Explicit deletion via API (DELETE → verify 404)
- State isolation: Each test gets fresh users (scope="function")
- Deterministic: No dependency on pre-existing @lfa-seed.hu pool
"""

import pytest
import requests
import time
from typing import Dict, List


# ============================================================================
# CONFIGURATION
# ============================================================================

# Pre-existing admin credentials (from E2E test user fixture)
ADMIN_EMAIL = "admin@lfa.com"
ADMIN_PASSWORD = "admin123"


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================

def get_admin_token(api_url: str) -> str:
    """Get auth token for pre-existing admin user."""
    response = requests.post(
        f"{api_url}/api/v1/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    assert response.status_code == 200, f"Admin login failed: {response.text}"
    return response.json()["access_token"]


def create_test_user(
    api_url: str,
    admin_token: str,
    role: str,
    timestamp: int,
    index: int = 0
) -> Dict:
    """
    Create a test user via API + license (for students).

    Returns:
        {
            "id": int,
            "email": str,
            "password": str,  # Plain password for login
            "token": str,     # Auth token for API calls
        }
    """
    email = f"int_test_{role.lower()}_{timestamp}_{index}@test.lfa"
    password = f"TestPass_{timestamp}_{index}"

    # Create user via API
    user_data = {
        "name": f"INT_TEST_{role}_{timestamp}_{index}",
        "email": email,
        "password": password,
        "role": role.lower(),  # API expects lowercase role ('student', 'instructor', 'admin')
        "is_active": True,
        "onboarding_completed": True,
        "payment_verified": True,
    }

    # Add date_of_birth for students (required for tournament enrollment)
    if role.upper() == "STUDENT":
        user_data["date_of_birth"] = "2000-01-01"  # PRO age group (24+ years old)

    response = requests.post(
        f"{api_url}/api/v1/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=user_data
    )

    assert response.status_code == 200, f"User creation failed: {response.text}"
    user_data = response.json()
    user_id = user_data["id"]

    # Login to get user token
    login_response = requests.post(
        f"{api_url}/api/v1/auth/login",
        json={"email": email, "password": password}
    )
    assert login_response.status_code == 200, f"User login failed: {login_response.text}"
    user_token = login_response.json()["access_token"]

    # Create LFA_FOOTBALL_PLAYER UserLicense for students (needed for tournament enrollment)
    # Create LFA_COACH UserLicense for instructors (needed for tournament assignment)
    if role.upper() == "STUDENT":
        # Step 1: Create license via payment verification (sets onboarding_completed=False by default)
        spec_response = requests.post(
            f"{api_url}/api/v1/payment-verification/students/{user_id}/add-specialization",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"specialization_type": "LFA_FOOTBALL_PLAYER"}
        )
        if spec_response.status_code not in [200, 201]:
            print(f"⚠️  Specialization creation failed for user {user_id}: {spec_response.status_code} - {spec_response.text}")

        # Step 2: Complete LFA player onboarding via proper endpoint (uses student's own token)
        # This is the canonical code path — same endpoint real students use in the browser.
        from app.skills_config import get_all_skill_keys
        skill_defaults = {k: 75 for k in get_all_skill_keys()}
        onboarding_response = requests.post(
            f"{api_url}/specialization/lfa-player/onboarding-submit",
            headers={"Authorization": f"Bearer {user_token}"},
            json={
                "position": "MIDFIELDER",
                "goals": "improve_performance",
                "motivation": "Integration test user — auto-generated",
                "skills": skill_defaults,
            }
        )
        if onboarding_response.status_code not in [200, 201]:
            print(f"⚠️  Onboarding failed for user {user_id}: {onboarding_response.status_code} - {onboarding_response.text}")
    elif role.upper() == "INSTRUCTOR":
        # Add LFA_COACH license for instructors (needed for tournament assignment)
        # Use direct database creation for simplicity in tests
        from app.database import get_db
        from app.models.license import UserLicense
        from datetime import datetime, timedelta

        db_session = next(get_db())
        try:
            coach_license = UserLicense(
                user_id=user_id,
                specialization_type="LFA_COACH",
                current_level=8,  # Maximum level (Level 8 can teach all age groups)
                max_achieved_level=8,
                started_at=datetime.utcnow(),
                payment_verified=True,
                payment_verified_at=datetime.utcnow(),
                onboarding_completed=True,
                onboarding_completed_at=datetime.utcnow(),
                is_active=True,
                issued_at=datetime.utcnow(),
                expires_at=datetime.utcnow() + timedelta(days=730),  # 2 years
                renewal_cost=0,
                credit_balance=0,
                credit_purchased=0
            )
            db_session.add(coach_license)
            db_session.commit()
        except Exception as e:
            db_session.rollback()
            print(f"⚠️  LFA_COACH license creation failed for instructor {user_id}: {str(e)}")
        finally:
            db_session.close()

    return {
        "id": user_data["id"],
        "email": email,
        "password": password,
        "token": user_token,
    }


def delete_test_user(api_url: str, admin_token: str, user_id: int) -> None:
    """
    Delete a test user and verify cleanup.

    Cleanup validation:
    1. DELETE /api/v1/users/{id} → 200 (soft delete by deactivation)
    2. GET /api/v1/users/{id} → verify is_active=False
    """
    # Delete user
    delete_response = requests.delete(
        f"{api_url}/api/v1/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Note: API returns 200 with message, not 204 (soft delete)
    assert delete_response.status_code == 200, \
        f"User deletion failed: {delete_response.text}"

    # Verify user is deactivated (soft delete)
    get_response = requests.get(
        f"{api_url}/api/v1/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    if get_response.status_code == 200:
        user = get_response.json()
        assert user["is_active"] is False, \
            f"User {user_id} not deactivated after deletion"


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture(scope="session")
def api_url() -> str:
    """API base URL (session-scoped)."""
    return "http://localhost:8000"


@pytest.fixture(scope="session")
def base_url() -> str:
    """Frontend base URL (session-scoped)."""
    return "http://localhost:8501"


@pytest.fixture(scope="function")
def admin_token(api_url: str) -> str:
    """
    Admin auth token (function-scoped).

    Uses pre-existing admin@lfa.com account.
    No CREATE/CLEANUP needed (permanent admin account).
    """
    return get_admin_token(api_url)


@pytest.fixture(scope="function")
def test_students(api_url: str, admin_token: str) -> List[Dict]:
    """
    CREATE + CLEANUP: 4 test students for tournament enrollment.

    Note: Knockout tournaments require power of 2 players (minimum 4).

    Each student has:
    - id: int
    - email: str
    - password: str
    - token: str (auth token)

    Cleanup: Deletes all students after test completes.
    """
    timestamp = int(time.time() * 1000)
    students = []

    # CREATE: 4 students (knockout minimum)
    for i in range(4):
        student = create_test_user(api_url, admin_token, "STUDENT", timestamp, i)
        students.append(student)

    yield students

    # CLEANUP: Delete all students
    for student in students:
        try:
            delete_test_user(api_url, admin_token, student["id"])
        except Exception as e:
            print(f"⚠️  Cleanup warning: Failed to delete student {student['id']}: {e}")


@pytest.fixture(scope="function")
def test_instructor(api_url: str, admin_token: str) -> Dict:
    """
    CREATE + CLEANUP: Test instructor for tournament assignment.

    Returns:
    - id: int
    - email: str
    - password: str
    - token: str (auth token)

    Cleanup: Deletes instructor after test completes.
    """
    timestamp = int(time.time() * 1000)
    instructor = create_test_user(api_url, admin_token, "INSTRUCTOR", timestamp)

    yield instructor

    # CLEANUP: Delete instructor
    try:
        delete_test_user(api_url, admin_token, instructor["id"])
    except Exception as e:
        print(f"⚠️  Cleanup warning: Failed to delete instructor {instructor['id']}: {e}")


def _ensure_campus_has_pitches(campus_id: int) -> None:
    """Ensure a campus has ≥1 active pitch (domain invariant for session generation)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models.pitch import Pitch
    from app.config import settings

    engine = create_engine(settings.DATABASE_URL)
    _Session = sessionmaker(bind=engine)
    db = _Session()
    try:
        count = db.query(Pitch).filter(Pitch.campus_id == campus_id, Pitch.is_active == True).count()
        if count == 0:
            # Session generation requires ≥1 active pitch on the campus (domain invariant)
            for pitch_num, pitch_name in enumerate(["Pálya A", "Pálya B"], start=1):
                db.add(Pitch(campus_id=campus_id, pitch_number=pitch_num, name=pitch_name, capacity=22, is_active=True))
            db.commit()
            print(f"   ↳ Seeded 2 pitches on campus ID={campus_id} (domain invariant)")
    finally:
        db.close()


@pytest.fixture(scope="function")
def test_campus_ids(api_url: str, admin_token: str) -> List[int]:
    """
    Get or create campus IDs for tournament session generation.

    Creates test location + campus if none exist (CREATE pattern).
    Cleanup: Not needed (test infrastructure persists across test runs).

    Returns:
        List of campus IDs (e.g., [1])
    """
    # Query existing campuses
    response = requests.get(
        f"{api_url}/api/v1/campuses",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    if response.status_code == 200:
        campuses = response.json()
        if campuses and len(campuses) > 0:
            # Use first available active campus
            campus_id = campuses[0]["id"]
            print(f"ℹ️  Using existing campus: ID={campus_id}")
            _ensure_campus_has_pitches(campus_id)
            return [campus_id]

    # No campuses found - create test location + campus
    print("ℹ️  No campuses found - creating test location + campus")

    # 1. Create test location
    location_response = requests.post(
        f"{api_url}/api/v1/admin/locations",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "INT_TEST_Location",
            "city": "Test City",
            "address": "123 Test Street",
            "country": "HU",
            "is_active": True,
        }
    )

    if location_response.status_code == 201:
        location_id = location_response.json()["id"]
    elif location_response.status_code == 200:
        location_id = location_response.json()["id"]
    else:
        # Fallback: query for any existing location
        locations_response = requests.get(
            f"{api_url}/api/v1/admin/locations",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        if locations_response.status_code == 200 and locations_response.json():
            location_id = locations_response.json()[0]["id"]
        else:
            raise AssertionError(f"Cannot create/find location: {location_response.text}")

    # 2. Create or reuse test campus
    campus_response = requests.post(
        f"{api_url}/api/v1/admin/locations/{location_id}/campuses",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "INT_TEST_Campus",
            "venue": "Test Venue",
            "address": "456 Campus Ave",
            "is_active": True,
        }
    )

    if campus_response.status_code in [200, 201]:
        campus_id = campus_response.json()["id"]
        print(f"✅ Created test campus: ID={campus_id}")
        _ensure_campus_has_pitches(campus_id)
        return [campus_id]
    elif campus_response.status_code == 400 and "already exists" in campus_response.text:
        # Campus already exists - query it by location
        query_response = requests.get(
            f"{api_url}/api/v1/admin/locations/{location_id}/campuses",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        if query_response.status_code == 200:
            campuses = query_response.json()
            for campus in campuses:
                if campus["name"] == "INT_TEST_Campus":
                    campus_id = campus["id"]
                    print(f"ℹ️  Reusing existing campus: ID={campus_id}")
                    _ensure_campus_has_pitches(campus_id)
                    return [campus_id]

    raise AssertionError(f"Cannot create/find campus: {campus_response.text}")
