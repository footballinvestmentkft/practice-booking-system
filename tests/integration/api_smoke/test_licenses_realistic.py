"""
Sprint 34 — Realistic license endpoint tests.

These tests complement the auto-generated test_licenses_smoke.py by using
*real* DB entity IDs instead of literal placeholder strings like {license_id}.
Every path starts with /api/v1/ so the _PrefixedClient passes it through
unchanged (no double-prefixing).

Coverage targets:
  - GET  /licenses/progression/{spec}           (public — no auth branch)
  - GET  /licenses/marketing/{spec}             (public — no auth branch)
  - GET  /licenses/my-licenses                  (student owns data branch)
  - GET  /licenses/me                           (alias branch)
  - GET  /licenses/dashboard                    (student dashboard branch)
  - GET  /licenses/admin/sync/desync-issues     (admin + optional filter branches)
  - POST /licenses/admin/sync/all               (dry_run branch)
  - POST /licenses/admin/sync/user/{user_id}    (real user ID branch)
  - GET  /licenses/{id}/football-skills         (real id — not 422)
  - GET  /licenses/user/{id}/football-skills    (list — may be empty)
  - PUT  /licenses/{id}/football-skills         (all-fields branch + missing-field → 422)
  - GET  /licenses/requirements/{spec}/{level}  (real path params)
  - Auth-required rejection branches            (401 for unauthenticated)
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.fixtures.builders import build_user_license


# ── Module-scoped DB state ────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def _license_data(test_db: Session, _student_user, _instructor_user) -> dict:
    """
    Create two UserLicenses for the per-test student user:
      - specialization_type="PLAYER"           (standard)
      - specialization_type="LFA_FOOTBALL_PLAYER" (for football-skills endpoints)
    Returns a dict with user and license IDs.
    """
    student = _student_user
    instructor = _instructor_user

    player_lic = build_user_license(test_db, user_id=student.id, specialization_type="PLAYER")
    test_db.commit()

    lfa_lic = build_user_license(
        test_db, user_id=student.id, specialization_type="LFA_FOOTBALL_PLAYER"
    )
    test_db.commit()

    return {
        "student_id":      student.id,
        "instructor_id":   instructor.id,
        "player_license_id": player_lic.id,
        "lfa_license_id":  lfa_lic.id,
    }


# ── Test class ────────────────────────────────────────────────────────────────

class TestLicensesRealistic:
    """
    Realistic license smoke tests — assert specific status codes using real IDs.

    Contrast with auto-generated tests that send literal '{license_id}' strings
    in the URL (FastAPI parses those as path params → 422, which the broad
    assertion range accepts as a pass).
    """

    # ── Public endpoints (no auth) ────────────────────────────────────────────

    def test_progression_path_player_200(self, api_client, admin_token):
        """Progression path for PLAYER specialization is publicly accessible."""
        response = api_client.get("/api/v1/licenses/progression/PLAYER")
        assert response.status_code == 200, response.text

    def test_progression_path_coach_200(self, api_client, admin_token):
        """Progression path for COACH specialization is publicly accessible."""
        response = api_client.get("/api/v1/licenses/progression/COACH")
        assert response.status_code == 200, response.text

    def test_marketing_content_player_200(self, api_client):
        """Marketing content endpoint is publicly accessible."""
        response = api_client.get("/api/v1/licenses/marketing/PLAYER")
        assert response.status_code == 200, response.text

    # ── Student read paths ────────────────────────────────────────────────────

    def test_my_licenses_student_returns_list(self, api_client, student_token, _license_data):
        """GET /my-licenses returns a list for an authenticated student."""
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get("/api/v1/licenses/my-licenses", headers=headers)
        assert response.status_code == 200, response.text
        assert isinstance(response.json(), list)

    def test_me_alias_matches_my_licenses(self, api_client, student_token, _license_data):
        """GET /me is an alias for /my-licenses and returns the same structure."""
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get("/api/v1/licenses/me", headers=headers)
        assert response.status_code == 200, response.text
        assert isinstance(response.json(), list)

    def test_dashboard_student_200(self, api_client, student_token, _license_data):
        """License dashboard is accessible to authenticated students."""
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get("/api/v1/licenses/dashboard", headers=headers)
        assert response.status_code == 200, response.text

    # ── Admin sync endpoints ──────────────────────────────────────────────────

    def test_admin_desync_issues_returns_list(self, api_client, admin_token):
        """Admin can list desync issues (may be empty list)."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get("/api/v1/licenses/admin/sync/desync-issues", headers=headers)
        assert response.status_code == 200, response.text
        assert isinstance(response.json(), list)

    def test_admin_desync_issues_specialization_filter_200(self, api_client, admin_token):
        """?specialization=PLAYER filter branch returns 200."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get(
            "/api/v1/licenses/admin/sync/desync-issues?specialization=PLAYER",
            headers=headers,
        )
        assert response.status_code == 200, response.text

    def test_admin_desync_issues_forbidden_for_student(self, api_client, student_token):
        """Students are forbidden from admin sync endpoints."""
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get("/api/v1/licenses/admin/sync/desync-issues", headers=headers)
        assert response.status_code == 403, response.text

    def test_admin_sync_all_dry_run_does_not_fail(self, api_client, admin_token):
        """POST /admin/sync/all with dry_run=true exercises the dry-run branch."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        payload = {"direction": "progress_to_license", "dry_run": True}
        response = api_client.post(
            "/api/v1/licenses/admin/sync/all", json=payload, headers=headers
        )
        assert response.status_code in [200, 201, 400], response.text

    def test_admin_sync_user_with_real_student_id(self, api_client, admin_token, _license_data):
        """POST /admin/sync/user/{id} uses a real user ID — exercises user lookup branch."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        student_id = _license_data["student_id"]
        payload = {"specialization": "PLAYER", "direction": "progress_to_license"}
        response = api_client.post(
            f"/api/v1/licenses/admin/sync/user/{student_id}",
            json=payload,
            headers=headers,
        )
        # 200 = synced, 400 = no license to sync yet — both exercise the endpoint logic
        assert response.status_code in [200, 201, 400, 404], response.text

    def test_admin_sync_user_nonexistent_soft_fails(self, api_client, admin_token):
        """
        POST /admin/sync/user/99999999 — non-existent user.
        The endpoint returns 200 with success=false (soft-fail, not 404).
        This exercises the 'no progress found' branch.
        """
        headers = {"Authorization": f"Bearer {admin_token}"}
        payload = {"specialization": "PLAYER", "direction": "progress_to_license"}
        response = api_client.post(
            "/api/v1/licenses/admin/sync/user/99999999",
            json=payload,
            headers=headers,
        )
        # Endpoint returns 200 with success=false for missing data (not 404)
        assert response.status_code in [200, 404], response.text

    # ── Football skills endpoints (real license ID) ───────────────────────────

    def test_get_football_skills_real_license_id_not_422(self, api_client, student_token, _license_data):
        """
        GET /{license_id}/football-skills with a *real* license ID.

        Previous smoke test sent literal '{license_id}' → FastAPI tried to parse
        it as int → 422 (accepted by broad assertion).  This test uses a real ID
        so we can distinguish actual business logic responses (200/400/403) from
        validation failures (422).
        """
        headers = {"Authorization": f"Bearer {student_token}"}
        lic_id = _license_data["lfa_license_id"]
        response = api_client.get(f"/api/v1/licenses/{lic_id}/football-skills", headers=headers)
        # 200 = skills returned; 400 = specialization not LFA_PLAYER type; 403 = perm
        assert response.status_code in [200, 400, 403, 404], (
            f"Expected business logic response, got: {response.status_code} {response.text}"
        )

    def test_get_user_football_skills_list_admin(self, api_client, admin_token, _license_data):
        """GET /user/{student_id}/football-skills — admin can list any user's skills."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        student_id = _license_data["student_id"]
        response = api_client.get(
            f"/api/v1/licenses/user/{student_id}/football-skills", headers=headers
        )
        assert response.status_code == 200, response.text
        assert isinstance(response.json(), list)

    def test_put_football_skills_full_payload_instructor(
        self, api_client, instructor_token, _license_data
    ):
        """PUT /{license_id}/football-skills — instructor sends all 6 required skill scores."""
        headers = {"Authorization": f"Bearer {instructor_token}"}
        lic_id = _license_data["lfa_license_id"]
        payload = {
            "heading":      75,
            "shooting":     80,
            "crossing":     70,
            "passing":      85,
            "dribbling":    78,
            "ball_control": 82,
        }
        response = api_client.put(
            f"/api/v1/licenses/{lic_id}/football-skills", json=payload, headers=headers
        )
        # 200 = updated; 400 = spec type not matching; 403 = perm; NOT 422 (valid payload)
        assert response.status_code in [200, 400, 403, 404], (
            f"Expected real response, got: {response.status_code} {response.text}"
        )

    def test_put_football_skills_incomplete_payload_rejected(
        self, api_client, instructor_token, _license_data
    ):
        """
        PUT /{license_id}/football-skills — incomplete payload rejected.

        The endpoint checks the specialization type before reaching Pydantic
        body validation.  With specialization_type='LFA_FOOTBALL_PLAYER' the
        spec-type guard fires (400) before Pydantic can validate the body (422).
        Either response confirms the endpoint rejects the call correctly.
        """
        headers = {"Authorization": f"Bearer {instructor_token}"}
        lic_id = _license_data["lfa_license_id"]
        response = api_client.put(
            f"/api/v1/licenses/{lic_id}/football-skills",
            json={"heading": 75},  # incomplete payload
            headers=headers,
        )
        # 400 = spec-type guard; 422 = Pydantic validation (if guard passes)
        assert response.status_code in [400, 422], response.text

    # ── Requirements lookup ───────────────────────────────────────────────────

    def test_get_requirements_player_level1(self, api_client, student_token):
        """GET /requirements/PLAYER/1 — exercises level-based requirements lookup."""
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get("/api/v1/licenses/requirements/PLAYER/1", headers=headers)
        assert response.status_code in [200, 404], response.text

    # ── Auth-required rejection ───────────────────────────────────────────────

    def test_my_licenses_unauthenticated_401(self, api_client):
        """GET /my-licenses without token → 401."""
        response = api_client.get("/api/v1/licenses/my-licenses")
        assert response.status_code == 401, response.text

    def test_dashboard_unauthenticated_401(self, api_client):
        """GET /dashboard without token → 401."""
        response = api_client.get("/api/v1/licenses/dashboard")
        assert response.status_code == 401, response.text
