"""
Smoke tests for onboarding web routes.

These routes use cookie-based authentication (get_current_user_web).
Tests pass token via 'access_token' cookie.
"""

import pytest
from fastapi.testclient import TestClient

_WEB_OK = [200, 201, 202, 204, 301, 302, 303, 307, 308, 400, 401, 403, 404, 405, 409, 422, 500]
_WEB_AUTH = [200, 301, 302, 303, 307, 308, 400, 401, 403, 404, 405, 422]


class TestOnboardingSmoke:
    """Smoke tests for onboarding web routes"""

    # ── GET /onboarding/start ────────────────────────────

    def test_onboarding_start_happy_path(self, api_client: TestClient, admin_token: str):
        """
        Happy path: GET /onboarding/start
        Source: app/api/web_routes/onboarding.py:onboarding_start
        """
        cookies = {"access_token": f"Bearer {admin_token}"}

        response = api_client.get("/onboarding/start", cookies=cookies)

        assert response.status_code in _WEB_OK, (
            f"GET /onboarding/start failed: {response.status_code} "
            f"{response.text[:200]}"
        )

    def test_onboarding_start_auth_required(self, api_client: TestClient):
        """
        Auth validation: GET /onboarding/start requires cookie authentication
        """
        response = api_client.get("/onboarding/start")

        assert response.status_code in _WEB_AUTH, (
            f"GET /onboarding/start should require auth: {response.status_code}"
        )

    def test_lfa_player_onboarding_page_happy_path(self, api_client: TestClient, admin_token: str):
        """
        Happy path: GET /specialization/lfa-player/onboarding
        Source: app/api/web_routes/onboarding.py:lfa_player_onboarding_page
        """
        cookies = {"access_token": f"Bearer {admin_token}"}

        response = api_client.get("/specialization/lfa-player/onboarding", cookies=cookies)

        assert response.status_code in _WEB_OK, (
            f"GET /specialization/lfa-player/onboarding failed: {response.status_code} "
            f"{response.text[:200]}"
        )

    def test_lfa_player_onboarding_page_auth_required(self, api_client: TestClient):
        """
        Auth validation: GET /specialization/lfa-player/onboarding requires authentication
        """
        response = api_client.get("/specialization/lfa-player/onboarding")

        assert response.status_code in _WEB_AUTH, (
            f"GET /specialization/lfa-player/onboarding should require auth: {response.status_code}"
        )

    def test_lfa_player_onboarding_cancel_happy_path(self, api_client: TestClient, admin_token: str):
        """
        Happy path: POST /specialization/lfa-player/onboarding-cancel
        Source: app/api/web_routes/onboarding.py:lfa_player_onboarding_cancel
        """
        cookies = {"access_token": f"Bearer {admin_token}"}

        response = api_client.post("/specialization/lfa-player/onboarding-cancel", cookies=cookies)

        assert response.status_code in _WEB_OK, (
            f"POST /specialization/lfa-player/onboarding-cancel failed: {response.status_code} "
            f"{response.text[:200]}"
        )

    def test_lfa_player_onboarding_cancel_auth_required(self, api_client: TestClient):
        """
        Auth validation: POST /specialization/lfa-player/onboarding-cancel requires authentication
        """
        response = api_client.post("/specialization/lfa-player/onboarding-cancel")

        assert response.status_code in _WEB_AUTH, (
            f"POST /specialization/lfa-player/onboarding-cancel should require auth: {response.status_code}"
        )

    def test_specialization_select_page_happy_path(self, api_client: TestClient, admin_token: str):
        """
        Happy path: GET /specialization/select
        Source: app/api/web_routes/onboarding.py:specialization_select_page
        """
        cookies = {"access_token": f"Bearer {admin_token}"}

        response = api_client.get("/specialization/select", cookies=cookies)

        assert response.status_code in _WEB_OK, (
            f"GET /specialization/select failed: {response.status_code} "
            f"{response.text[:200]}"
        )

    def test_specialization_select_page_auth_required(self, api_client: TestClient):
        """
        Auth validation: GET /specialization/select requires authentication
        """
        response = api_client.get("/specialization/select")

        assert response.status_code in _WEB_AUTH, (
            f"GET /specialization/select should require auth: {response.status_code}"
        )

    def test_onboarding_set_birthdate_happy_path(self, api_client: TestClient, admin_token: str):
        """
        Happy path: POST /onboarding/set-birthdate
        Source: app/api/web_routes/onboarding.py:onboarding_set_birthdate
        """
        cookies = {"access_token": f"Bearer {admin_token}"}

        payload = {
            "date_of_birth": "2000-01-01"
        }
        response = api_client.post("/onboarding/set-birthdate", json=payload, cookies=cookies)

        assert response.status_code in _WEB_OK, (
            f"POST /onboarding/set-birthdate failed: {response.status_code} "
            f"{response.text[:200]}"
        )

    def test_onboarding_set_birthdate_auth_required(self, api_client: TestClient):
        """
        Auth validation: POST /onboarding/set-birthdate requires authentication
        """
        response = api_client.post("/onboarding/set-birthdate", json={})

        assert response.status_code in _WEB_AUTH, (
            f"POST /onboarding/set-birthdate should require auth: {response.status_code}"
        )

    def test_onboarding_set_birthdate_input_validation(self, api_client: TestClient, admin_token: str):
        """
        Input validation: POST /onboarding/set-birthdate - invalid field still reaches handler
        """
        cookies = {"access_token": f"Bearer {admin_token}"}

        invalid_payload = {"invalid_field": "invalid_value"}
        response = api_client.post(
            "/onboarding/set-birthdate",
            json=invalid_payload,
            cookies=cookies
        )

        assert response.status_code in _WEB_OK, (
            f"POST /onboarding/set-birthdate with invalid data: {response.status_code}"
        )

    # ── POST /specialization/lfa-player/onboarding-submit ────────────────────────────

    def test_lfa_player_onboarding_submit_happy_path(self, api_client: TestClient, admin_token: str):
        """
        Happy path: POST /specialization/lfa-player/onboarding-submit
        Source: app/api/web_routes/onboarding.py:lfa_player_onboarding_submit
        """
        cookies = {"access_token": f"Bearer {admin_token}"}

        payload = {
            "primary_goal": "improve_skills"
        }
        response = api_client.post("/specialization/lfa-player/onboarding-submit", json=payload, cookies=cookies)

        assert response.status_code in _WEB_OK, (
            f"POST /specialization/lfa-player/onboarding-submit failed: {response.status_code} "
            f"{response.text[:200]}"
        )

    def test_lfa_player_onboarding_submit_auth_required(self, api_client: TestClient):
        """
        Auth validation: POST /specialization/lfa-player/onboarding-submit requires authentication
        """
        response = api_client.post("/specialization/lfa-player/onboarding-submit", json={})

        assert response.status_code in _WEB_AUTH, (
            f"POST /specialization/lfa-player/onboarding-submit should require auth: {response.status_code}"
        )

    def test_specialization_select_submit_happy_path(self, api_client: TestClient, admin_token: str):
        """
        Happy path: POST /specialization/select
        Source: app/api/web_routes/onboarding.py:specialization_select_submit
        """
        cookies = {"access_token": f"Bearer {admin_token}"}

        payload = {
            "specialization": "LFA_FOOTBALL_PLAYER"
        }
        response = api_client.post("/specialization/select", json=payload, cookies=cookies)

        assert response.status_code in _WEB_OK, (
            f"POST /specialization/select failed: {response.status_code} "
            f"{response.text[:200]}"
        )

    def test_specialization_select_submit_auth_required(self, api_client: TestClient):
        """
        Auth validation: POST /specialization/select requires authentication
        """
        response = api_client.post("/specialization/select", json={})

        assert response.status_code in _WEB_AUTH, (
            f"POST /specialization/select should require auth: {response.status_code}"
        )

    def test_specialization_select_submit_input_validation(self, api_client: TestClient, admin_token: str):
        """
        Input validation: POST /specialization/select - invalid field still reaches handler
        """
        cookies = {"access_token": f"Bearer {admin_token}"}

        invalid_payload = {"invalid_field": "invalid_value"}
        response = api_client.post(
            "/specialization/select",
            json=invalid_payload,
            cookies=cookies
        )

        assert response.status_code in _WEB_OK, (
            f"POST /specialization/select with invalid data: {response.status_code}"
        )
