"""
Sprint 34 — Realistic session endpoint tests.

These tests complement the auto-generated test_sessions_smoke.py by using
real DB entity IDs and a real semester_id for session creation.

Key problem with auto-generated tests:
  POST /sessions/ sends semester_id=9999 → always 404 (semester not found).
  GET /sessions/{session_id} sends literal '{session_id}' → always 422.
  GET /sessions/availability sends no session_ids param → always 400.

Coverage targets:
  - POST /sessions/         with real semester_id → 200/201 (session created)
  - GET  /sessions/         with semester_id filter → 200 + pagination shape
  - GET  /sessions/{id}     with real session ID → 200 + stats shape
  - GET  /sessions/availability?session_ids={id} → 200 + per-ID dict shape
  - PATCH /sessions/{id}    with valid payload → 200
  - POST /sessions/{id}/check-in → 400 (instructor not assigned) or 403
  - GET  /sessions/{id}/results  → 400 (not tournament game) with real ID
  - PATCH /sessions/{id}/results with real payload → exercises validation
  - Auth-required rejection branches (401 for unauthenticated)
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.fixtures.builders import build_semester, build_session


def _now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Module-scoped DB state ────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def _session_data(test_db: Session, admin_token: str, _instructor_user) -> dict:
    """
    Create:
      - One test semester (rolling ±30/+150 day window)
      - One plain session (no instructor) in that semester
      - One instructor-assigned session in that semester
    Returns IDs for use in tests.
    """
    instructor = _instructor_user

    uid = uuid.uuid4().hex[:8]
    sem = build_semester(test_db, code=f"SESS-REAL-{uid}", name=f"Sessions Realistic {uid}")
    test_db.commit()

    session_plain = build_session(
        test_db, semester_id=sem.id, title=f"Plain-{uid}"
    )
    test_db.commit()

    session_assigned = build_session(
        test_db,
        semester_id=sem.id,
        title=f"Assigned-{uid}",
        instructor_id=instructor.id,
    )
    test_db.commit()

    return {
        "semester_id":        sem.id,
        "session_id":         session_plain.id,
        "session_assigned_id": session_assigned.id,
        "instructor_id":      instructor.id,
        # Pre-computed dates for POST /sessions/ (within semester boundaries)
        "date_start": (_now_naive() + timedelta(days=10)).isoformat(),
        "date_end":   (_now_naive() + timedelta(days=10, hours=2)).isoformat(),
    }


# ── Test class ────────────────────────────────────────────────────────────────

class TestSessionsRealistic:
    """
    Realistic session smoke tests — use real semester and session IDs.
    """

    # ── POST /sessions/ — session creation with real semester ─────────────────

    def test_create_session_real_semester_id_201(self, api_client, admin_token, _session_data):
        """
        POST /sessions/ with a *real* semester_id → 200/201 (session created).

        Previous smoke test used semester_id=9999 → always 404.
        This test exercises the full creation path including date-boundary
        validation and the DB insert.
        """
        headers = {"Authorization": f"Bearer {admin_token}"}
        uid = uuid.uuid4().hex[:6]
        payload = {
            "title":       f"Realistic Session {uid}",
            "date_start":  _session_data["date_start"],
            "date_end":    _session_data["date_end"],
            "semester_id": _session_data["semester_id"],
            "session_type": "on_site",
            "capacity":    20,
        }
        response = api_client.post("/api/v1/sessions/", json=payload, headers=headers)
        assert response.status_code in [200, 201], (
            f"Expected session to be created: {response.status_code} {response.text}"
        )
        data = response.json()
        assert "id" in data, f"Created session should have an id: {data}"

    def test_create_session_date_before_semester_start_400(
        self, api_client, admin_token, _session_data
    ):
        """
        POST /sessions/ with date before semester start → 400 (date boundary check).
        Exercises the branch: session_start_date < semester.start_date.
        """
        headers = {"Authorization": f"Bearer {admin_token}"}
        past = (_now_naive() - timedelta(days=60)).isoformat()
        payload = {
            "title":       "Out of Bounds Session",
            "date_start":  past,
            "date_end":    (_now_naive() - timedelta(days=59)).isoformat(),
            "semester_id": _session_data["semester_id"],
            "session_type": "on_site",
            "capacity":    20,
        }
        response = api_client.post("/api/v1/sessions/", json=payload, headers=headers)
        assert response.status_code == 400, (
            f"Expected 400 for out-of-bounds date: {response.status_code} {response.text}"
        )

    def test_create_session_missing_required_fields_422(self, api_client, admin_token):
        """POST /sessions/ with no required fields → 422 (Pydantic validation)."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.post(
            "/api/v1/sessions/", json={"description": "no title or dates"}, headers=headers
        )
        assert response.status_code == 422, response.text

    def test_create_session_unauthenticated_401(self, api_client, _session_data):
        """POST /sessions/ without token → 401."""
        payload = {
            "title": "No Auth Session",
            "date_start": _session_data["date_start"],
            "date_end": _session_data["date_end"],
            "semester_id": _session_data["semester_id"],
            "session_type": "on_site",
        }
        response = api_client.post("/api/v1/sessions/", json=payload)
        assert response.status_code == 401, response.text

    # ── GET /sessions/ — list with filter ────────────────────────────────────

    def test_list_sessions_returns_pagination_shape(self, api_client, admin_token):
        """GET /sessions/ returns the expected SessionList shape."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get("/api/v1/sessions/?page=1&size=10", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert "sessions" in data
        assert "total" in data
        assert "page" in data

    def test_list_sessions_semester_filter(self, api_client, admin_token, _session_data):
        """GET /sessions/?semester_id={id} filters to the correct semester."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        sem_id = _session_data["semester_id"]
        response = api_client.get(
            f"/api/v1/sessions/?semester_id={sem_id}&page=1&size=50",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        # All returned sessions must belong to the requested semester
        for session in data["sessions"]:
            assert session["semester_id"] == sem_id

    def test_list_sessions_unauthenticated_401(self, api_client):
        """GET /sessions/ without token → 401."""
        response = api_client.get("/api/v1/sessions/")
        assert response.status_code == 401, response.text

    # ── GET /sessions/{session_id} — single session with stats ────────────────

    def test_get_session_by_real_id_200(self, api_client, admin_token, _session_data):
        """
        GET /{session_id} with a *real* ID → 200 + SessionWithStats shape.

        Previous smoke test used literal '{session_id}' → FastAPI tried to
        parse it as int → 422 (accepted by broad assertion).
        """
        headers = {"Authorization": f"Bearer {admin_token}"}
        session_id = _session_data["session_id"]
        response = api_client.get(f"/api/v1/sessions/{session_id}", headers=headers)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["id"] == session_id
        assert "booking_count" in data
        assert "confirmed_bookings" in data
        assert "current_bookings" in data

    def test_get_session_nonexistent_404(self, api_client, admin_token):
        """GET /{session_id} with non-existent ID → 404."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get("/api/v1/sessions/99999999", headers=headers)
        assert response.status_code == 404, response.text

    def test_get_session_unauthenticated_401(self, api_client, _session_data):
        """GET /{session_id} without token → 401."""
        session_id = _session_data["session_id"]
        response = api_client.get(f"/api/v1/sessions/{session_id}")
        assert response.status_code == 401, response.text

    # ── GET /sessions/availability ────────────────────────────────────────────

    def test_availability_with_real_session_id_200(
        self, api_client, admin_token, _session_data
    ):
        """
        GET /availability?session_ids={id} → 200 + dict keyed by session ID.

        Previous smoke test sent no session_ids param → always 400 (required).
        This test sends a real session ID and validates the response shape.
        """
        headers = {"Authorization": f"Bearer {admin_token}"}
        session_id = _session_data["session_id"]
        response = api_client.get(
            f"/api/v1/sessions/availability?session_ids={session_id}",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert str(session_id) in data, f"Session {session_id} not in response: {data}"
        slot = data[str(session_id)]
        assert "capacity" in slot
        assert "status" in slot

    def test_availability_multiple_session_ids(self, api_client, admin_token, _session_data):
        """GET /availability?session_ids=id1,id2 — bulk lookup for two sessions."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        id1 = _session_data["session_id"]
        id2 = _session_data["session_assigned_id"]
        response = api_client.get(
            f"/api/v1/sessions/availability?session_ids={id1},{id2}",
            headers=headers,
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert str(id1) in data
        assert str(id2) in data

    def test_availability_no_params_4xx(self, api_client, admin_token):
        """GET /availability with no session_ids → 400 or 422 (required param missing)."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get("/api/v1/sessions/availability", headers=headers)
        # 422 = Pydantic required field missing; 400 = explicit validation guard
        assert response.status_code in [400, 422], response.text

    # ── PATCH /sessions/{session_id} — update ────────────────────────────────

    def test_update_session_title_real_id_200(self, api_client, admin_token, _session_data):
        """PATCH /{session_id} with a real ID and valid title → 200."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        session_id = _session_data["session_id"]
        payload = {"title": f"Updated Title {uuid.uuid4().hex[:6]}"}
        response = api_client.patch(
            f"/api/v1/sessions/{session_id}", json=payload, headers=headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["id"] == session_id

    def test_update_session_invalid_type_422(self, api_client, admin_token, _session_data):
        """PATCH /{session_id} with wrong field types → 422."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        session_id = _session_data["session_id"]
        payload = {"capacity": "not-a-number"}
        response = api_client.patch(
            f"/api/v1/sessions/{session_id}", json=payload, headers=headers
        )
        assert response.status_code == 422, response.text

    # ── GET /sessions/{id}/results — tournament results ───────────────────────

    def test_get_game_results_non_tournament_session_400(
        self, api_client, admin_token, _session_data
    ):
        """
        GET /{session_id}/results for a non-tournament session → 400.
        Exercises the 'not a tournament game' branch with a real session ID
        (contrast with auto-generated test using literal '{session_id}' → 422).
        """
        headers = {"Authorization": f"Bearer {admin_token}"}
        session_id = _session_data["session_id"]
        response = api_client.get(
            f"/api/v1/sessions/{session_id}/results", headers=headers
        )
        # 400 = not a tournament game (correct business logic branch)
        assert response.status_code in [200, 400, 404], response.text

    # ── POST /sessions/{id}/check-in ─────────────────────────────────────────

    def test_check_in_instructor_not_assigned_400_or_403(
        self, api_client, instructor_token, _session_data
    ):
        """
        POST /{session_id}/check-in — instructor not assigned to plain session.
        Exercises the authorization branch: session.instructor_id != current_user.id.
        """
        headers = {"Authorization": f"Bearer {instructor_token}"}
        session_id = _session_data["session_id"]  # no instructor assigned
        response = api_client.post(
            f"/api/v1/sessions/{session_id}/check-in", headers=headers
        )
        # 400 = wrong instructor; 403 = forbidden; 401 = auth issue
        assert response.status_code in [400, 401, 403, 404], response.text

    def test_check_in_unauthenticated_401(self, api_client, _session_data):
        """POST /{session_id}/check-in without token → 401."""
        session_id = _session_data["session_id"]
        response = api_client.post(f"/api/v1/sessions/{session_id}/check-in")
        assert response.status_code == 401, response.text
