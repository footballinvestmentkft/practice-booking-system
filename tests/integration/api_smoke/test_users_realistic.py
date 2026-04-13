"""
Sprint 35 (cont.) — Realistic users endpoint tests.

Complements test_users_smoke.py (auto-generated, broad assertions) by targeting
specific business-logic status codes with real entity IDs.

Routing note: The users router explicitly includes profile.py and
instructor_analytics.py BEFORE crud.py (which has /{user_id}).  The __init__.py
comment confirms this ordering: "Profile endpoints must come before /{user_id}
to avoid path conflicts."  Static paths /me, /search, /credit-balance, /check-nickname
all route correctly — NO shadow issue.

Coverage targets:
  Profile (profile.py):
  - GET  /users/me                  (200 + role/email fields; 401 unauth)
  - PATCH /users/me                 (200 partial update; 400 emergency phone = phone)
  - GET  /users/check-nickname/{n}  (200 + {available: bool})

  CRUD (crud.py):
  - GET  /users/                    (200 admin + list shape; 403 student; 401 unauth)
  - GET  /users/?role=STUDENT       (role filter branch)
  - GET  /users/?search=smoke       (search filter branch)
  - GET  /users/{user_id}           (200 + stats shape; 404 missing)

  Search (search.py):
  - GET  /users/search?q=smoke      (200 + list; 403 student; 422 missing q)

  Credits (credits.py):
  - GET  /users/credit-balance      (200 + {credit_balance, invoice_counts})
  - GET  /users/me/credit-transactions (200 + {transactions, total_count})
  - GET  /users/me/credit-transactions?limit=5 (pagination branch)
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


class TestUsersRealistic:
    """
    Specific-assertion users endpoint tests.
    Read-heavy — only PATCH /users/me changes state (phone number), which is safe
    to mutate since the smoke student is re-created each CI run.
    """

    # ── GET /users/me — current user profile ──────────────────────────────────

    def test_get_me_student_200(
        self, api_client: TestClient, student_token: str, _student_user
    ):
        """GET /me as student → 200 + id, email, role fields."""
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get("/api/v1/users/me", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "id" in data
        assert data["email"] == _student_user.email
        assert data["role"].upper() == "STUDENT"

    def test_get_me_admin_200(
        self, api_client: TestClient, admin_token: str
    ):
        """GET /me as admin → 200 + role == 'admin' (lowercase from API)."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get("/api/v1/users/me", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["role"].upper() == "ADMIN"

    def test_get_me_unauthenticated_401(self, api_client: TestClient):
        """GET /me without token → 401."""
        response = api_client.get("/api/v1/users/me")
        assert response.status_code == 401, response.text

    # ── PATCH /users/me — update own profile ──────────────────────────────────

    def test_update_me_partial_200(
        self, api_client: TestClient, student_token: str
    ):
        """
        PATCH /me with a safe partial field → 200.

        Exercises the partial-update (exclude_unset=True) branch.  Uses a phone
        number update which doesn't conflict with emergency_phone guard.
        """
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.patch(
            "/api/v1/users/me",
            json={"phone": "+36-30-123-0001"},
            headers=headers,
        )
        assert response.status_code == 200, response.text

    def test_update_me_emergency_phone_same_400(
        self, api_client: TestClient, student_token: str
    ):
        """
        PATCH /me where emergency_phone == phone → 400.

        Exercises the cross-field validation branch: the endpoint explicitly
        checks that emergency contact phone differs from the user's own phone.
        """
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.patch(
            "/api/v1/users/me",
            json={"phone": "+36-30-999-0000", "emergency_phone": "+36-30-999-0000"},
            headers=headers,
        )
        assert response.status_code == 400, response.text

    # ── GET /users/check-nickname/{nickname} ──────────────────────────────────

    def test_check_nickname_available_200(
        self, api_client: TestClient, student_token: str
    ):
        """
        GET /check-nickname/{nickname} → 200 + {available: bool, message: str}.

        Uses a clearly non-existent nickname to guarantee the 'available' branch.
        """
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get(
            "/api/v1/users/check-nickname/nonexistent_nick_xz9923", headers=headers
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert "available" in data
        assert data["available"] is True

    def test_check_nickname_unauthenticated_401(self, api_client: TestClient):
        """GET /check-nickname/{n} without token → 401."""
        response = api_client.get("/api/v1/users/check-nickname/anyname")
        assert response.status_code == 401, response.text

    # ── GET /users/ — admin list ──────────────────────────────────────────────

    def test_admin_list_users_200(
        self, api_client: TestClient, admin_token: str, _student_user, _instructor_user
    ):
        """GET / as admin → 200 + {users: list, total: int, page: int, size: int}."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get("/api/v1/users/", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "users" in data
        assert "total" in data
        assert isinstance(data["users"], list)
        assert data["total"] >= 3  # at least admin + student + instructor

    def test_admin_list_users_student_403(
        self, api_client: TestClient, student_token: str
    ):
        """GET / as student → 403 (admin/instructor only)."""
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get("/api/v1/users/", headers=headers)
        assert response.status_code == 403, response.text

    def test_admin_list_users_unauthenticated_401(self, api_client: TestClient):
        """GET / without token → 401."""
        response = api_client.get("/api/v1/users/")
        assert response.status_code == 401, response.text

    def test_admin_list_users_role_filter(
        self, api_client: TestClient, admin_token: str
    ):
        """GET /?role=student → 200 (exercises the role filter branch; role values are lowercase)."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get("/api/v1/users/?role=student", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        if data["users"]:
            assert all(u["role"].lower() == "student" for u in data["users"])

    def test_admin_list_users_search_filter(
        self, api_client: TestClient, admin_token: str
    ):
        """
        GET /?search=example.com → 200 (exercises the search param branch).

        Searches for 'example.com' which matches smoke.admin@example.com,
        smoke.student@example.com, smoke.instructor@example.com — all guaranteed
        present via the admin_token/student_token/instructor_token fixtures.

        Uses 'example.com' instead of 'smoke' to avoid matching @generated.test
        users that accumulate in persistent local DBs across test runs.  The
        UserList response schema accepts @example.com emails (reserved-domain
        check is absent from the admin list model, unlike /users/search).
        """
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get("/api/v1/users/?search=example.com", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] >= 1

    # ── GET /users/{user_id} ──────────────────────────────────────────────────

    def test_admin_get_user_real_id_200(
        self, api_client: TestClient, admin_token: str, test_student_id: int
    ):
        """
        GET /{user_id} with a real student ID → 200 + UserWithStats shape.

        Uses the conftest ``test_student_id`` fixture (smoke.student@example.com).
        """
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get(f"/api/v1/users/{test_student_id}", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["id"] == test_student_id
        assert "email" in data

    def test_admin_get_user_nonexistent_404(
        self, api_client: TestClient, admin_token: str
    ):
        """GET /99999999 → 404."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get("/api/v1/users/99999999", headers=headers)
        assert response.status_code == 404, response.text

    # ── GET /users/search ─────────────────────────────────────────────────────

    def test_search_users_admin_200(
        self, api_client: TestClient, admin_token: str
    ):
        """
        GET /search?q=lfa → 200 + list (may be empty).

        Uses 'lfa' as query (valid email domain, no Pydantic 422 risk).
        Searching '@example.com' users triggers Pydantic 422 in the /search
        response model because example.com is a reserved domain.

        Does NOT assert len >= 1: admin@lfa.com / grandmaster@lfa.com exist only
        when the E2E baseline seeder ran (API Tests CI job), not in the Unit Tests
        job where api_smoke runs after unit tests without E2E seeding.
        The test validates the endpoint branch executes and returns 200 + list.
        """
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get("/api/v1/users/search?q=lfa", headers=headers)
        assert response.status_code == 200, response.text
        results = response.json()
        assert isinstance(results, list)

    def test_search_users_student_403(
        self, api_client: TestClient, student_token: str
    ):
        """GET /search?q=lfa as student → 403 (admin-only endpoint)."""
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get("/api/v1/users/search?q=lfa", headers=headers)
        assert response.status_code == 403, response.text

    def test_search_users_missing_q_422(
        self, api_client: TestClient, admin_token: str
    ):
        """GET /search without ?q → 422 (q is required, min_length=1)."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get("/api/v1/users/search", headers=headers)
        assert response.status_code == 422, response.text

    # ── GET /users/credit-balance ─────────────────────────────────────────────

    def test_get_credit_balance_200(
        self, api_client: TestClient, student_token: str
    ):
        """
        GET /credit-balance → 200 + {credit_balance, credit_purchased,
        credit_used, invoice_counts: {pending, verified, paid, ...}}.
        """
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get("/api/v1/users/credit-balance", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "credit_balance" in data
        assert "invoice_counts" in data

    def test_get_credit_balance_unauthenticated_401(self, api_client: TestClient):
        """GET /credit-balance without token → 401."""
        response = api_client.get("/api/v1/users/credit-balance")
        assert response.status_code == 401, response.text

    # ── GET /users/me/credit-transactions ─────────────────────────────────────

    def test_get_credit_transactions_200(
        self, api_client: TestClient, student_token: str
    ):
        """
        GET /me/credit-transactions → 200 + {transactions: list, total_count,
        credit_balance, showing, limit, offset}.
        """
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get(
            "/api/v1/users/me/credit-transactions", headers=headers
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert "transactions" in data
        assert "total_count" in data
        assert isinstance(data["transactions"], list)

    def test_get_credit_transactions_pagination_branch(
        self, api_client: TestClient, student_token: str
    ):
        """
        GET /me/credit-transactions?limit=5&offset=0 → 200.

        Exercises the pagination parameter branches (limit/offset query params).
        Response shape may omit limit/offset when the user has no transactions.
        """
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get(
            "/api/v1/users/me/credit-transactions?limit=5&offset=0", headers=headers
        )
        assert response.status_code == 200, response.text
        data = response.json()
        # limit/offset may not appear when result set is empty
        if "limit" in data:
            assert data["limit"] == 5
        if "offset" in data:
            assert data["offset"] == 0
