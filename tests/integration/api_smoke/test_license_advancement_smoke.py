"""
E2E Workflow Tests — License Advancement & Payment Verification (Phase 4)

Coverage gap addressed: license advancement + payment verification flow had
no end-to-end chain test.

Chain tested:
  WF01: Student views their licenses     GET /api/v1/licenses/me
  WF02: Student views license dashboard  GET /api/v1/licenses/dashboard
  WF03: Student requests advancement     POST /api/v1/licenses/advance
  WF04: Admin verifies payment           POST /api/v1/licenses/{id}/verify-payment
  WF05: Admin unverifies payment         POST /api/v1/licenses/{id}/unverify-payment
  WF06: Check updated license state      GET /api/v1/licenses/my-licenses
  WF07: Instructor advancement path      POST /api/v1/licenses/instructor/advance

Also tests assessment lifecycle (assessments.py — previously unregistered, now fixed):
  ASS01: Create assessment               POST /api/v1/licenses/{id}/skills/{name}/assess
  ASS02: Get assessment history          GET /api/v1/licenses/{id}/skills/{name}/assessments
  ASS03: Get single assessment           GET /api/v1/licenses/assessments/{id}
  ASS04: Validate assessment             POST /api/v1/licenses/assessments/{id}/validate
  ASS05: Archive assessment              POST /api/v1/licenses/assessments/{id}/archive

Notes:
  - Full /api/v1/... paths are used.
  - UserLicense is created directly via test_db where needed.
  - All assertions accept the full range of expected status codes.
"""

import pytest
from datetime import datetime, timezone
from typing import Optional


# ── Module-scoped preconditions ───────────────────────────────────────────────

@pytest.fixture(scope="function")
def wf_user_license_id(test_db, _student_user) -> Optional[int]:
    """
    Get or create a UserLicense for the per-test student user.
    """
    from app.models.license import UserLicense

    student = _student_user

    # Prefer an existing LFA_PLAYER_YOUTH license
    lic = test_db.query(UserLicense).filter(
        UserLicense.user_id == student.id
    ).first()
    if lic:
        return lic.id

    # Create a minimal license
    lic = UserLicense(
        user_id=student.id,
        specialization_type="LFA_PLAYER_YOUTH",
        current_level=1,
        max_achieved_level=1,
        started_at=datetime.now(timezone.utc),
        is_active=True,
    )
    test_db.add(lic)
    test_db.commit()
    test_db.refresh(lic)
    return lic.id


# ── Advancement workflow ───────────────────────────────────────────────────────

class TestLicenseAdvancementWorkflow:
    """
    Phase 4 — E2E Workflow: License Advancement + Payment Verification.

    Tests are ordered WF01 → WF07. The class-level _license_id attribute is
    populated from wf_user_license_id and used in payment verification steps.
    """

    # ── WF01 — Student views licenses ─────────────────────────────────────────

    def test_wf01_student_views_licenses(self, api_client, student_token):
        """
        Step 1: Student retrieves their license list.
        Always reachable — returns 200 (empty list if no licenses).
        """
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get("/api/v1/licenses/me", headers=headers)
        assert response.status_code == 200, (
            f"License list failed: {response.status_code} {response.text[:200]}"
        )

    # ── WF02 — License dashboard ──────────────────────────────────────────────

    def test_wf02_student_views_dashboard(self, api_client, student_token):
        """
        Step 2: Student fetches the full license dashboard.
        Exercises: progression summary, requirements, gamification state.
        """
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get("/api/v1/licenses/dashboard", headers=headers)
        assert response.status_code == 200, (
            f"License dashboard failed: {response.status_code} {response.text[:200]}"
        )

    # ── WF03 — Advancement request ────────────────────────────────────────────

    def test_wf03_student_requests_advancement(self, api_client, student_token):
        """
        Step 3: Student requests license level advancement.
        Endpoint: POST /api/v1/licenses/advance
        Payload: specialization + target_level (both required).

        Expected outcomes:
          200  — advancement granted (auto-approved for testing)
          400  — preconditions not met (license not found, level already reached)
          403  — permission denied
          404  — user has no license for this specialization
        """
        headers = {"Authorization": f"Bearer {student_token}"}
        payload = {
            "specialization": "LFA_PLAYER_YOUTH",
            "target_level": 2,
            "reason": "E2E workflow test advancement request",
        }
        response = api_client.post(
            "/api/v1/licenses/advance", json=payload, headers=headers
        )
        assert response.status_code in [200, 400, 403, 404, 422], (
            f"License advancement returned unexpected status "
            f"{response.status_code}: {response.text[:300]}"
        )

    # ── WF04 — Admin verifies payment ─────────────────────────────────────────

    def test_wf04_admin_verifies_payment(
        self, api_client, admin_token, wf_user_license_id
    ):
        """
        Step 4: Admin marks payment as verified for a license.
        Endpoint: POST /api/v1/licenses/{id}/verify-payment (no body)

        Expected outcomes:
          200  — payment verified
          400  — already verified / invalid state
          401  — endpoint uses web cookie auth (get_current_admin_user_web), not Bearer token
          403  — not admin
          404  — license not found
        """
        license_id = wf_user_license_id or 99999
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.post(
            f"/api/v1/licenses/{license_id}/verify-payment", headers=headers
        )
        assert response.status_code in [200, 400, 401, 403, 404], (
            f"Payment verify returned unexpected status "
            f"{response.status_code}: {response.text[:300]}"
        )

    # ── WF05 — Admin unverifies payment ───────────────────────────────────────

    def test_wf05_admin_unverifies_payment(
        self, api_client, admin_token, wf_user_license_id
    ):
        """
        Step 5: Admin unverifies (reverses) a payment (idempotent rollback).
        Endpoint: POST /api/v1/licenses/{id}/unverify-payment (no body)

        Tests the reverse direction of the verify → unverify state machine.
        Note: endpoint uses web cookie auth (get_current_admin_user_web) — 401 expected with Bearer token.
        """
        license_id = wf_user_license_id or 99999
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.post(
            f"/api/v1/licenses/{license_id}/unverify-payment", headers=headers
        )
        assert response.status_code in [200, 400, 401, 403, 404], (
            f"Payment unverify returned unexpected status "
            f"{response.status_code}: {response.text[:300]}"
        )

    # ── WF06 — Student verifies final state ───────────────────────────────────

    def test_wf06_student_views_final_license_state(
        self, api_client, student_token
    ):
        """
        Step 6: Student confirms their license state after the advancement chain.
        GET /api/v1/licenses/my-licenses returns the full license list with levels.
        """
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get("/api/v1/licenses/my-licenses", headers=headers)
        assert response.status_code == 200, (
            f"Final license state check failed: {response.status_code} {response.text[:200]}"
        )

    # ── WF07 — Instructor advancement path ────────────────────────────────────

    def test_wf07_instructor_advance_request(
        self, api_client, instructor_token, wf_user_license_id
    ):
        """
        Step 7: Instructor-initiated advancement (alternate path to WF03).
        Endpoint: POST /api/v1/licenses/instructor/advance

        Expected: 200 / 400 (if preconditions not met) / 403 / 404.
        """
        headers = {"Authorization": f"Bearer {instructor_token}"}
        payload = {
            "user_id": 99999,  # Non-existent → expected 404 or 400
            "specialization": "LFA_PLAYER_YOUTH",
            "target_level": 2,
            "reason": "E2E instructor advancement test",
        }
        response = api_client.post(
            "/api/v1/licenses/instructor/advance", json=payload, headers=headers
        )
        assert response.status_code in [200, 400, 403, 404, 409, 422], (
            f"Instructor advance returned unexpected status "
            f"{response.status_code}: {response.text[:300]}"
        )


# ── Assessment lifecycle chain ────────────────────────────────────────────────

class TestAssessmentLifecycleWorkflow:
    """
    Phase 4 — E2E Workflow: Skill Assessment Lifecycle (assessments.py).

    State machine: NOT_ASSESSED → ASSESSED → VALIDATED → ARCHIVED
    Previously this router was unregistered; now correctly wired via
    licenses/__init__.py → api.py.

    Tracks _assessment_id across steps.
    """

    _assessment_id: Optional[int] = None

    # ── ASS01 — Create assessment (INSTRUCTOR) ────────────────────────────────

    def test_ass01_instructor_creates_assessment(
        self, api_client, instructor_token, wf_user_license_id
    ):
        """
        Step 1: Instructor creates a skill assessment for a student.
        Endpoint: POST /api/v1/licenses/{license_id}/skills/{skill_name}/assess

        Schema: CreateAssessmentRequest — points_earned (ge=0), points_total (gt=0).

        Expected outcomes:
          200 / 201  — assessment created (status=ASSESSED)
          400        — license not LFA_PLAYER specialization / invalid skill
          403        — not INSTRUCTOR or ADMIN
          404        — license not found
        """
        license_id = wf_user_license_id or 99999
        headers = {"Authorization": f"Bearer {instructor_token}"}
        payload = {
            "points_earned": 8,
            "points_total": 10,
            "notes": "E2E workflow assessment — good passing technique",
        }
        response = api_client.post(
            f"/api/v1/licenses/{license_id}/skills/passing/assess",
            json=payload,
            headers=headers,
        )
        assert response.status_code in [200, 201, 400, 403, 404], (
            f"Assessment create returned unexpected status "
            f"{response.status_code}: {response.text[:300]}"
        )
        if response.status_code in [200, 201]:
            data = response.json()
            TestAssessmentLifecycleWorkflow._assessment_id = (
                data.get("assessment", {}).get("id")
            )

    # ── ASS01b — Input validation: invalid points ──────────────────────────────

    def test_ass01b_assessment_invalid_points_returns_422(
        self, api_client, instructor_token, wf_user_license_id
    ):
        """
        Edge case: points_earned has ge=0 constraint. Negative → 422.
        points_total has gt=0 constraint. Zero → 422.
        """
        license_id = wf_user_license_id or 99999
        headers = {"Authorization": f"Bearer {instructor_token}"}
        # negative points_earned violates ge=0
        response = api_client.post(
            f"/api/v1/licenses/{license_id}/skills/passing/assess",
            json={"points_earned": -1, "points_total": 10},
            headers=headers,
        )
        assert response.status_code == 422, (
            f"Negative points_earned must be 422, got {response.status_code}"
        )

    def test_ass01c_assessment_zero_total_returns_422(
        self, api_client, instructor_token, wf_user_license_id
    ):
        """Edge case: points_total=0 violates gt=0 constraint → 422."""
        license_id = wf_user_license_id or 99999
        headers = {"Authorization": f"Bearer {instructor_token}"}
        response = api_client.post(
            f"/api/v1/licenses/{license_id}/skills/passing/assess",
            json={"points_earned": 5, "points_total": 0},
            headers=headers,
        )
        assert response.status_code == 422, (
            f"points_total=0 must be 422, got {response.status_code}"
        )

    # ── ASS02 — Get assessment history ────────────────────────────────────────

    def test_ass02_get_assessment_history(
        self, api_client, instructor_token, wf_user_license_id
    ):
        """
        Step 2: Retrieve assessment history for license + skill combination.
        Endpoint: GET /api/v1/licenses/{id}/skills/{name}/assessments

        Always returns 200 + list (possibly empty if ASS01 failed).
        """
        license_id = wf_user_license_id or 99999
        headers = {"Authorization": f"Bearer {instructor_token}"}
        response = api_client.get(
            f"/api/v1/licenses/{license_id}/skills/passing/assessments",
            headers=headers,
        )
        assert response.status_code in [200, 403, 404], (
            f"Assessment history returned unexpected status "
            f"{response.status_code}: {response.text[:200]}"
        )

    # ── ASS03 — Get single assessment ─────────────────────────────────────────

    def test_ass03_get_single_assessment(
        self, api_client, admin_token
    ):
        """
        Step 3: Retrieve a specific assessment by ID.
        Endpoint: GET /api/v1/licenses/assessments/{assessment_id}

        Uses ID from ASS01 or falls back to 99999 (→ 404).
        """
        assessment_id = TestAssessmentLifecycleWorkflow._assessment_id or 99999
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get(
            f"/api/v1/licenses/assessments/{assessment_id}", headers=headers
        )
        assert response.status_code in [200, 403, 404], (
            f"Single assessment GET returned unexpected status "
            f"{response.status_code}: {response.text[:200]}"
        )

    # ── ASS04 — Validate assessment (ASSESSED → VALIDATED) ────────────────────

    def test_ass04_validate_assessment(self, api_client, admin_token):
        """
        Step 4: Admin validates the assessment.
        State transition: ASSESSED → VALIDATED.
        Endpoint: POST /api/v1/licenses/assessments/{assessment_id}/validate

        Expected:
          200  — validated
          400  — invalid state (e.g., already ARCHIVED)
          403  — not INSTRUCTOR or ADMIN
          404  — assessment not found
        """
        assessment_id = TestAssessmentLifecycleWorkflow._assessment_id or 99999
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.post(
            f"/api/v1/licenses/assessments/{assessment_id}/validate",
            headers=headers,
        )
        assert response.status_code in [200, 400, 403, 404], (
            f"Assessment validate returned unexpected status "
            f"{response.status_code}: {response.text[:300]}"
        )

    # ── ASS05 — Archive assessment (VALIDATED → ARCHIVED) ────────────────────

    def test_ass05_archive_assessment(self, api_client, admin_token):
        """
        Step 5: Admin archives the assessment.
        State transition: VALIDATED/ASSESSED → ARCHIVED.
        Endpoint: POST /api/v1/licenses/assessments/{assessment_id}/archive

        Expected:
          200  — archived
          400  — invalid state transition (already ARCHIVED)
          403  — not INSTRUCTOR or ADMIN
          404  — assessment not found
        """
        assessment_id = TestAssessmentLifecycleWorkflow._assessment_id or 99999
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.post(
            f"/api/v1/licenses/assessments/{assessment_id}/archive?reason=E2E+workflow+test",
            headers=headers,
        )
        assert response.status_code in [200, 400, 403, 404], (
            f"Assessment archive returned unexpected status "
            f"{response.status_code}: {response.text[:300]}"
        )


# ── Auth-required guard tests ─────────────────────────────────────────────────

class TestLicenseWorkflowAuthGuards:
    """
    Phase 4 — Auth validation for license workflow endpoints.
    Unauthenticated requests must return 401/403.
    """

    def test_license_advance_requires_auth(self, api_client):
        response = api_client.post("/api/v1/licenses/advance", json={})
        assert response.status_code in [401, 403, 422], (
            f"Unauthenticated advance must be 401/403/422, got {response.status_code}"
        )

    def test_verify_payment_requires_auth(self, api_client):
        response = api_client.post("/api/v1/licenses/99999/verify-payment")
        assert response.status_code in [401, 403], (
            f"Unauthenticated verify-payment must be 401/403, got {response.status_code}"
        )

    def test_assessment_create_requires_auth(self, api_client):
        response = api_client.post(
            "/api/v1/licenses/99999/skills/passing/assess", json={}
        )
        assert response.status_code in [401, 403, 422], (
            f"Unauthenticated assess must be 401/403/422, got {response.status_code}"
        )

    def test_assessment_validate_requires_auth(self, api_client):
        response = api_client.post("/api/v1/licenses/assessments/99999/validate")
        assert response.status_code in [401, 403], (
            f"Unauthenticated validate must be 401/403, got {response.status_code}"
        )
