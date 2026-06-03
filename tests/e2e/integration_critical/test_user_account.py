"""
User Account E2E — Integration Critical (BLOCKING CI gate)

Sprint 39 — validates the password-change security flow.

Tests:
  1. test_password_change_success       — old creds work → change → old creds 401 → new creds 200
  2. test_password_change_wrong_old_rejected — wrong old_password → 400 Bad Request
  3. test_change_password_requires_auth — no Authorization header → 401

Fixture design:
  - function-scoped account_user: fresh user per test (password changes are destructive;
    each test needs a user with a known, unchanged password)
  - Cleanup: delete user via admin API after each test
"""

from __future__ import annotations

import time
import uuid
from typing import Dict

import pytest
import requests

from tests.e2e.integration_critical.conftest import (
    get_admin_token,
    create_test_user,
    delete_test_user,
)

CHANGE_PASSWORD_PATH = "/api/v1/auth/change-password"


# ---------------------------------------------------------------------------
# Auth header helper
# ---------------------------------------------------------------------------

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Function-scoped fixture (fresh user per test — password changes are destructive)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="function")
def account_user(api_url: str) -> Dict:
    """
    CREATE: 1 fresh student with known password for account lifecycle tests.
    CLEANUP: Delete after each test.

    function-scoped because password changes mutate the user — each test
    needs a user with a guaranteed-known current password.
    """
    admin_token = get_admin_token(api_url)
    uid = uuid.uuid4().hex[:16]  # collision-free across parallel workers
    user = create_test_user(api_url, admin_token, "STUDENT", uid, 0)
    yield user
    try:
        fresh_admin_token = get_admin_token(api_url)
        delete_test_user(api_url, fresh_admin_token, user["id"])
    except Exception as e:
        print(f"⚠️  Cleanup warning: Failed to delete account test user {user['id']}: {e}")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUserAccount:

    # -----------------------------------------------------------------------
    # 1. Password change success — full flow
    # -----------------------------------------------------------------------

    def test_password_change_success(self, api_url: str, account_user: Dict):
        """
        Full password-change lifecycle:
          login → change password → old creds fail → new creds succeed
        """
        old_password = account_user["password"]
        new_password = f"NewPass_{int(time.time())}"

        # Verify old credentials work before change
        login_old = requests.post(
            f"{api_url}/api/v1/auth/login",
            json={"email": account_user["email"], "password": old_password}
        )
        assert login_old.status_code == 200, f"Pre-change login failed: {login_old.text}"

        # Change password
        resp = requests.post(
            f"{api_url}{CHANGE_PASSWORD_PATH}",
            headers=_auth(account_user["token"]),
            json={"old_password": old_password, "new_password": new_password}
        )
        assert resp.status_code == 200, f"Password change failed: {resp.text}"

        # Old credentials must now fail
        login_old_after = requests.post(
            f"{api_url}/api/v1/auth/login",
            json={"email": account_user["email"], "password": old_password}
        )
        assert login_old_after.status_code == 401, \
            f"Old password still works after change — security failure: {login_old_after.text}"

        # New credentials must succeed
        login_new = requests.post(
            f"{api_url}/api/v1/auth/login",
            json={"email": account_user["email"], "password": new_password}
        )
        assert login_new.status_code == 200, f"New password login failed: {login_new.text}"
        assert "access_token" in login_new.json()

    # -----------------------------------------------------------------------
    # 2. Wrong old password rejected
    # -----------------------------------------------------------------------

    def test_password_change_wrong_old_rejected(self, api_url: str, account_user: Dict):
        """
        POST /auth/change-password with wrong old_password → 400 Bad Request.
        Password must NOT be changed.
        """
        resp = requests.post(
            f"{api_url}{CHANGE_PASSWORD_PATH}",
            headers=_auth(account_user["token"]),
            json={"old_password": "wrong_password_xyz", "new_password": "NewPass_valid"}
        )
        assert resp.status_code == 400, \
            f"Expected 400 for wrong old password, got {resp.status_code}: {resp.text}"

        # Confirm original password still works
        login_still_works = requests.post(
            f"{api_url}/api/v1/auth/login",
            json={"email": account_user["email"], "password": account_user["password"]}
        )
        assert login_still_works.status_code == 200, \
            f"Original password broken after rejected change attempt: {login_still_works.text}"

    # -----------------------------------------------------------------------
    # 3. Unauthenticated request rejected
    # -----------------------------------------------------------------------

    def test_change_password_requires_auth(self, api_url: str, account_user: Dict):
        """
        POST /auth/change-password without Authorization header → 401 Unauthorized.
        """
        resp = requests.post(
            f"{api_url}{CHANGE_PASSWORD_PATH}",
            json={
                "old_password": account_user["password"],
                "new_password": "AnyNewPass_123"
            }
        )
        assert resp.status_code == 401, \
            f"Expected 401 without auth header, got {resp.status_code}: {resp.text}"
