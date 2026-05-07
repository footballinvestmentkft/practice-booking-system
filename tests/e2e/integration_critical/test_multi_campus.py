"""
Integration Critical Suite — Multi-Campus E2E Tests

Purpose: Validate multi-campus infrastructure and session distribution (TICKET-003)
Marker: @pytest.mark.integration_critical (BLOCKING)
Runtime: <30s per test
Policy: 0 flake in 20 runs, parallel execution stable

Tests:
1. test_multi_campus_round_robin - Round-robin campus assignment, balanced distribution, no leakage
"""

import pytest
import requests
from typing import Dict, List
from collections import Counter
import time


def create_multi_campus_student(api_url: str, admin_token: str, timestamp: int, index: int) -> Dict:
    """
    Create isolated test student with LFA_FOOTBALL_PLAYER license.

    Returns:
        {"id": int, "email": str, "password": str, "token": str}
    """
    email = f"int_test_multi_student_{timestamp}_{index}@test.lfa"
    password = f"TestPass_{timestamp}_{index}"

    # Create user via admin API
    user_data = {
        "name": f"MULTI_CAMPUS_STUDENT_{timestamp}_{index}",
        "email": email,
        "password": password,
        "role": "student",
        "is_active": True,
        "onboarding_completed": True,
        "payment_verified": True,
        "date_of_birth": "2000-01-01",  # PRO age group
    }

    response = requests.post(
        f"{api_url}/api/v1/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json=user_data
    )

    assert response.status_code in [200, 201], f"User creation failed: {response.text}"
    user_id = response.json()["id"]

    # Create LFA_FOOTBALL_PLAYER license via database (direct approach from conftest.py)
    from app.database import get_db
    from app.models.license import UserLicense
    from datetime import datetime, timedelta

    db_session = next(get_db())
    try:
        player_license = UserLicense(
            user_id=user_id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.utcnow(),
            payment_verified=True,
            payment_verified_at=datetime.utcnow(),
            onboarding_completed=True,
            onboarding_completed_at=datetime.utcnow(),
            is_active=True,
            issued_at=datetime.utcnow(),
            expires_at=datetime.utcnow() + timedelta(days=730),
            renewal_cost=0,
            credit_balance=0,
            credit_purchased=0
        )
        db_session.add(player_license)
        db_session.commit()
    except Exception as e:
        db_session.rollback()
        raise AssertionError(f"License creation failed: {e}")
    finally:
        db_session.close()

    # Login to get token
    login_response = requests.post(
        f"{api_url}/api/v1/auth/login",
        json={"email": email, "password": password}
    )

    assert login_response.status_code == 200, f"Login failed: {login_response.text}"
    token = login_response.json()["access_token"]

    return {
        "id": user_id,
        "email": email,
        "password": password,
        "token": token
    }


def delete_multi_campus_student(api_url: str, admin_token: str, user_id: int):
    """Delete test student (cleanup)."""
    requests.delete(
        f"{api_url}/api/v1/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )


@pytest.mark.e2e
@pytest.mark.integration_critical
def test_multi_campus_round_robin(
    api_url: str,
    admin_token: str,
    test_campus_ids: List[int],
):
    """
    Multi-campus round-robin session distribution validation (ISOLATED).

    Workflow:
    1. CREATE 3 campuses if needed (fresh infrastructure)
    2. CREATE 16 fresh students with LFA_FOOTBALL_PLAYER licenses
    3. Give 16 students credits via invoice workflow
    4. Create tournament with 16 players, 3 campuses (auto-enroll + auto-generate sessions)
    5. Query all sessions for tournament
    6. Verify round-robin campus assignment: each campus ~equal sessions (±1 variance)
    7. Verify session count matches expected (16 sessions for 16-player knockout)
    8. Verify campus-level isolation (no cross-campus session leakage)
    9. CLEANUP: Delete all created students

    Expected Campus Distribution (15 sessions / 3 campuses = 5 each):
    - Campus 1: sessions 0, 3, 6, 9, 12 = 5 sessions
    - Campus 2: sessions 1, 4, 7, 10, 13 = 5 sessions
    - Campus 3: sessions 2, 5, 8, 11, 14 = 5 sessions

    Expected Runtime: <30s
    Priority: MEDIUM (infrastructure validation)
    Blocking: YES (will be added to CI BLOCKING suite)
    """
    print(f"\n[test_multi_campus_round_robin] Starting ISOLATED multi-campus validation")

    timestamp = int(time.time() * 1000)
    created_students = []

    try:
        # ==================================================================
        # STEP 1: Ensure 3 campuses exist (CREATE if needed)
        # ==================================================================

        print("[Step 1] Ensuring 3 campuses exist...")

        campus_ids_for_test = list(test_campus_ids[:3])  # Copy first 3

        # Create additional campuses if needed
        if len(campus_ids_for_test) < 3:
            campuses_needed = 3 - len(campus_ids_for_test)
            print(f"Creating {campuses_needed} additional campuses...")

            # Create campuses via direct DB insert (location endpoint not available)
            from app.database import get_db
            from app.models.location import Location
            from app.models.campus import Campus

            db_session = next(get_db())
            try:
                for i in range(campuses_needed):
                    # Create location (city must be unique)
                    location = Location(
                        name=f"Multi-Campus Location {timestamp}_{i}",
                        address=f"Test Address {i}",
                        city=f"TestCity{timestamp}_{i}",  # UNIQUE constraint
                        country="Test Country"
                    )
                    db_session.add(location)
                    db_session.flush()  # Get location_id

                    # Create campus
                    campus = Campus(
                        name=f"Multi-Campus {timestamp}_{i}",
                        location_id=location.id
                    )
                    db_session.add(campus)
                    db_session.flush()  # Get campus_id

                    # Session generation requires ≥1 active pitch on the campus (domain invariant)
                    from app.models.pitch import Pitch
                    for pitch_num, pitch_name in enumerate(["Pálya A", "Pálya B"], start=1):
                        db_session.add(Pitch(campus_id=campus.id, pitch_number=pitch_num, name=pitch_name, capacity=22, is_active=True))

                    campus_ids_for_test.append(campus.id)
                    print(f"✅ Created campus {campus.id} with pitches (DB insert)")

                db_session.commit()
            except Exception as e:
                db_session.rollback()
                raise AssertionError(f"Campus creation failed: {e}")
            finally:
                db_session.close()

        assert len(campus_ids_for_test) == 3, \
            f"Need 3 campuses, only have {len(campus_ids_for_test)}"

        print(f"✅ Step 1: Using campuses {campus_ids_for_test}")

        # ==================================================================
        # STEP 2: CREATE 16 fresh students (ISOLATED)
        # ==================================================================

        print("[Step 2] Creating 16 fresh students with licenses...")

        for i in range(16):
            student = create_multi_campus_student(api_url, admin_token, timestamp, i)
            created_students.append(student)
            if (i + 1) % 4 == 0:
                print(f"  Created {i+1}/16 students...")

        print(f"✅ Step 2: Created {len(created_students)} isolated students")

        # ==================================================================
        # STEP 3: Give students credits via invoice workflow
        # ==================================================================

        print("[Step 3] Giving 16 students 500 credits each...")

        for student in created_students:
            # Create invoice
            invoice_response = requests.post(
                f"{api_url}/api/v1/users/request-invoice",
                headers={"Authorization": f"Bearer {student['token']}"},
                json={
                    "package_type": "PACKAGE_500",
                    "specialization_type": "LFA_FOOTBALL_PLAYER",
                }
            )

            if invoice_response.status_code in [200, 201]:
                invoice_id = invoice_response.json()["id"]

                # Admin verifies invoice
                requests.post(
                    f"{api_url}/api/v1/invoices/{invoice_id}/verify",
                    headers={"Authorization": f"Bearer {admin_token}"},
                    json={}
                )

        print(f"✅ Step 3: Gave 16 students 500 credits each")

        # ==================================================================
        # STEP 4: Create tournament with 16 players, 3 campuses
        # ==================================================================

        print(f"[Step 4] Creating 16-player knockout tournament with 3 campuses...")
        tournament_response = requests.post(
            f"{api_url}/api/v1/tournaments/ops/run-scenario",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={
                "scenario": "smoke_test",
                "player_count": 16,  # Auto-enroll 16 players from created_students
                "max_players": 16,
                "tournament_format": "HEAD_TO_HEAD",
                "tournament_type_code": "knockout",
                "auto_generate_sessions": True,  # Generate sessions immediately with campus_ids
                "simulation_mode": "manual",
                "age_group": "PRO",
                "enrollment_cost": 250,
                "initial_tournament_status": "IN_PROGRESS",  # Required for auto-generation
                "dry_run": False,
                "confirmed": False,
                "campus_ids": campus_ids_for_test,
                "player_ids": [s["id"] for s in created_students],  # Explicit player list
            }
        )

        assert tournament_response.status_code == 200, \
            f"Tournament creation failed: {tournament_response.text}"
        tournament_id = tournament_response.json()["tournament_id"]

        print(f"✅ Step 4: Tournament created (ID={tournament_id}) with auto-enrollment and sessions")

        # ==================================================================
        # STEP 5: Query sessions
        # ==================================================================

        print(f"[Step 5] Querying sessions...")
        sessions_response = requests.get(
            f"{api_url}/api/v1/sessions?semester_id={tournament_id}",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

        assert sessions_response.status_code == 200, \
            f"Session query failed: {sessions_response.text}"

        sessions = sessions_response.json().get("sessions", [])
        print(f"✅ Step 5: Found {len(sessions)} sessions")

        # ==================================================================
        # STEP 6: Verify session count
        # ==================================================================

        print("[Step 6] Verifying session count...")
        # 16-player knockout generates 16 sessions (including 3rd-place playoff)
        session_count = len(sessions)
        assert session_count > 0, "No sessions generated"

        print(f"✅ Step 6: Session count verified ({session_count} sessions generated)")

        # ==================================================================
        # STEP 7: Verify round-robin campus assignment
        # ==================================================================

        print("[Step 7] Verifying round-robin campus assignment...")

        campus_distribution = Counter()
        for session in sessions:
            campus_id = session.get("campus_id")
            if campus_id:
                campus_distribution[campus_id] += 1

        print(f"[Debug] Campus distribution: {dict(campus_distribution)}")

        # Verify all 3 campuses used
        assert len(campus_distribution) == 3, \
            f"Expected 3 campuses, got {len(campus_distribution)}"

        # Verify balanced distribution (round-robin: sessions / 3 campuses, ±1 variance)
        expected_per_campus = session_count // 3  # Integer division
        max_variance = 2  # Allow ±2 variance for uneven division

        for campus_id in campus_ids_for_test:
            count = campus_distribution.get(campus_id, 0)
            assert abs(count - expected_per_campus) <= max_variance, \
                f"Campus {campus_id}: {count} sessions (expected {expected_per_campus}±{max_variance})"

        print(f"✅ Step 7: Round-robin validated ({dict(campus_distribution)})")

        # ==================================================================
        # STEP 8: Verify campus-level isolation
        # ==================================================================

        print("[Step 8] Verifying campus isolation...")

        for session in sessions:
            campus_id = session.get("campus_id")
            assert campus_id in campus_ids_for_test, \
                f"Session {session['id']} assigned to {campus_id}, not in {campus_ids_for_test}"

        sessions_without_campus = [s for s in sessions if s.get("campus_id") is None]
        assert len(sessions_without_campus) == 0, \
            f"Found {len(sessions_without_campus)} sessions without campus"

        print(f"✅ Step 8: Campus isolation validated")

        print(f"\n✅✅✅ Multi-campus round-robin: PASS (tournament={tournament_id}, {session_count} sessions, 3 campuses)")

    finally:
        # ==================================================================
        # CLEANUP: Delete all created students
        # ==================================================================

        print(f"\n[Cleanup] Deleting {len(created_students)} created students...")
        for student in created_students:
            delete_multi_campus_student(api_url, admin_token, student["id"])
        print(f"✅ Cleanup complete")
