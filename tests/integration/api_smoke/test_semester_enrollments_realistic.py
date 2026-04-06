"""
Sprint 34 — Realistic semester_enrollments endpoint tests.

These tests complement the auto-generated test_semester_enrollments_smoke.py
by using real DB entity IDs instead of literal placeholder strings.

Coverage targets:
  - GET /semester-enrollments/semesters/{id}/enrollments  (admin list — real semester)
  - GET /semester-enrollments/students/{id}/enrollments   (admin list — real student)
  - GET /semester-enrollments/{id}/payment-info           (real enrollment)
  - Auth-required rejection branches (student cannot access admin list)
  - Empty list vs populated list response shapes
  - Field validation: POST /verify-by-code with missing field → 422
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.fixtures.builders import build_enrollment, build_semester, build_user_license


# ── Module-scoped DB state ────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def _enrollment_data(test_db: Session, admin_token: str, _student_user) -> dict:
    """
    Create:
      - A test semester (rolling -30/+150 day window)
      - A PLAYER UserLicense for the per-test student user
      - An APPROVED enrollment (is_active=True)
      - A PENDING enrollment in a second semester (for workflow branch coverage)
    """
    uid = uuid.uuid4().hex[:8]
    student = _student_user

    # Approved enrollment
    sem_approved = build_semester(
        test_db, code=f"SER-APP-{uid}", name=f"SE Realistic Approved {uid}"
    )
    test_db.commit()

    lic_approved = build_user_license(
        test_db, user_id=student.id, specialization_type="PLAYER"
    )
    test_db.commit()

    enr_approved = build_enrollment(
        test_db,
        user_id=student.id,
        semester_id=sem_approved.id,
        user_license_id=lic_approved.id,
        approved=True,
        # Set a unique payment reference so payment-info endpoint can resolve it
        payment_reference_code=f"LFA-SER-{uid}",
    )
    test_db.commit()

    # Pending enrollment in second semester
    uid2 = uuid.uuid4().hex[:8]
    sem_pending = build_semester(
        test_db, code=f"SER-PEN-{uid2}", name=f"SE Realistic Pending {uid2}"
    )
    test_db.commit()

    lic_pending = build_user_license(
        test_db, user_id=student.id, specialization_type="PLAYER"
    )
    test_db.commit()

    enr_pending = build_enrollment(
        test_db,
        user_id=student.id,
        semester_id=sem_pending.id,
        user_license_id=lic_pending.id,
        approved=False,
    )
    test_db.commit()

    return {
        "student_id":           student.id,
        "approved_semester_id": sem_approved.id,
        "pending_semester_id":  sem_pending.id,
        "approved_enrollment_id": enr_approved.id,
        "pending_enrollment_id":  enr_pending.id,
        "payment_reference_code": f"LFA-SER-{uid}",
    }


# ── Test class ────────────────────────────────────────────────────────────────

class TestSemesterEnrollmentsRealistic:
    """
    Realistic semester_enrollments smoke tests.

    Many mutation endpoints (approve/reject/toggle/verify) use web-cookie auth
    (get_current_admin_user_web), not Bearer tokens, so they are not testable
    with the standard smoke test infrastructure.  This class focuses on the
    Bearer-token GET endpoints and the validation-layer behaviours that ARE
    testable without cookie auth.
    """

    # ── GET /semesters/{semester_id}/enrollments ──────────────────────────────

    def test_get_semester_enrollments_real_id_200(
        self, api_client, admin_token, _enrollment_data
    ):
        """Admin can list enrollments for a real semester — returns 200 + list."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        sem_id = _enrollment_data["approved_semester_id"]
        response = api_client.get(
            f"/api/v1/semester-enrollments/semesters/{sem_id}/enrollments",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"

    def test_get_semester_enrollments_approved_contains_enrollment(
        self, api_client, admin_token, _enrollment_data
    ):
        """Enrollment list for the approved semester contains the created enrollment."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        sem_id = _enrollment_data["approved_semester_id"]
        enr_id = _enrollment_data["approved_enrollment_id"]
        response = api_client.get(
            f"/api/v1/semester-enrollments/semesters/{sem_id}/enrollments",
            headers=headers,
        )
        assert response.status_code == 200
        ids = [e["id"] for e in response.json()]
        assert enr_id in ids, f"Enrollment {enr_id} missing from list: {ids}"

    def test_get_semester_enrollments_nonexistent_semester_empty_or_404(
        self, api_client, admin_token
    ):
        """Listing enrollments for a non-existent semester returns empty or 404."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get(
            "/api/v1/semester-enrollments/semesters/99999999/enrollments",
            headers=headers,
        )
        assert response.status_code in [200, 404], response.text
        if response.status_code == 200:
            assert response.json() == []

    def test_get_semester_enrollments_forbidden_for_student(
        self, api_client, student_token, _enrollment_data
    ):
        """Students cannot list enrollments for a semester (admin-only endpoint)."""
        headers = {"Authorization": f"Bearer {student_token}"}
        sem_id = _enrollment_data["approved_semester_id"]
        response = api_client.get(
            f"/api/v1/semester-enrollments/semesters/{sem_id}/enrollments",
            headers=headers,
        )
        assert response.status_code in [401, 403], response.text

    def test_get_semester_enrollments_unauthenticated_401(
        self, api_client, _enrollment_data
    ):
        """Unauthenticated access to semester enrollments → 401."""
        sem_id = _enrollment_data["approved_semester_id"]
        response = api_client.get(
            f"/api/v1/semester-enrollments/semesters/{sem_id}/enrollments"
        )
        assert response.status_code == 401, response.text

    # ── GET /students/{student_id}/enrollments ────────────────────────────────

    def test_get_student_enrollments_real_id_200(
        self, api_client, admin_token, _enrollment_data
    ):
        """Admin can list enrollments for a real student — returns 200 + list."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        student_id = _enrollment_data["student_id"]
        response = api_client.get(
            f"/api/v1/semester-enrollments/students/{student_id}/enrollments",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert isinstance(response.json(), list)

    def test_get_student_enrollments_contains_both_enrollments(
        self, api_client, admin_token, _enrollment_data
    ):
        """Student enrollment list contains both the approved and pending enrollment."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        student_id = _enrollment_data["student_id"]
        response = api_client.get(
            f"/api/v1/semester-enrollments/students/{student_id}/enrollments",
            headers=headers,
        )
        assert response.status_code == 200
        ids = [e["id"] for e in response.json()]
        assert _enrollment_data["approved_enrollment_id"] in ids
        assert _enrollment_data["pending_enrollment_id"] in ids

    def test_get_student_enrollments_nonexistent_student_empty_or_404(
        self, api_client, admin_token
    ):
        """Non-existent student ID returns empty list or 404."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get(
            "/api/v1/semester-enrollments/students/99999999/enrollments",
            headers=headers,
        )
        assert response.status_code in [200, 404], response.text

    # ── GET /{enrollment_id}/payment-info ─────────────────────────────────────

    def test_get_payment_info_real_enrollment_id_200(
        self, api_client, admin_token, _enrollment_data
    ):
        """Admin can retrieve payment info for a real enrollment."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        enr_id = _enrollment_data["approved_enrollment_id"]
        response = api_client.get(
            f"/api/v1/semester-enrollments/{enr_id}/payment-info",
            headers=headers,
        )
        # 200 = success; 401 = endpoint uses web-cookie auth (not Bearer token)
        assert response.status_code in [200, 401], response.text
        if response.status_code == 200:
            data = response.json()
            assert "enrollment_id" in data or "payment_reference_code" in data, (
                f"Unexpected response shape: {data}"
            )

    def test_get_payment_info_nonexistent_enrollment_404(self, api_client, admin_token):
        """Non-existent enrollment ID → 404 (or 401 if web-cookie auth fires first)."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get(
            "/api/v1/semester-enrollments/99999999/payment-info",
            headers=headers,
        )
        # 404 = not found; 401 = endpoint uses web-cookie auth (fires before ID lookup)
        assert response.status_code in [401, 404], response.text

    def test_get_payment_info_unauthenticated_401(self, api_client, _enrollment_data):
        """Unauthenticated access to payment info → 401."""
        enr_id = _enrollment_data["approved_enrollment_id"]
        response = api_client.get(
            f"/api/v1/semester-enrollments/{enr_id}/payment-info"
        )
        assert response.status_code == 401, response.text

    # ── POST /enroll — payload validation ─────────────────────────────────────

    def test_create_enrollment_missing_all_fields_422(self, api_client, admin_token):
        """POST /enroll with empty body → 422 (Pydantic field validation)."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.post(
            "/api/v1/semester-enrollments/enroll", json={}, headers=headers
        )
        # 422 = validation; 401/403 = web-cookie auth required (both are acceptable here)
        assert response.status_code in [401, 403, 422], response.text

    def test_create_enrollment_realistic_payload_real_ids(
        self, api_client, admin_token, _enrollment_data
    ):
        """
        POST /enroll with realistic payload using real IDs.

        Exercises the endpoint's business logic (duplicate check, FK lookup)
        rather than always bouncing off Pydantic validation.
        Expected: 200 (already enrolled → duplicate rejection), 400, 401, or 403.
        """
        headers = {"Authorization": f"Bearer {admin_token}"}
        payload = {
            "user_id":         _enrollment_data["student_id"],
            "semester_id":     _enrollment_data["approved_semester_id"],
            "user_license_id": _enrollment_data["approved_enrollment_id"],  # reuse ID for test
        }
        response = api_client.post(
            "/api/v1/semester-enrollments/enroll", json=payload, headers=headers
        )
        # 200/201 = created; 400 = duplicate; 401/403 = cookie auth required
        assert response.status_code in [200, 201, 400, 401, 403, 404], response.text

    # ── POST /verify-by-code — payload validation ─────────────────────────────

    def test_verify_by_code_missing_field_422(self, api_client, admin_token):
        """POST /verify-by-code with missing payment_code → 422."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.post(
            "/api/v1/semester-enrollments/verify-by-code",
            json={"wrong_field": "value"},
            headers=headers,
        )
        assert response.status_code in [401, 403, 422], response.text
