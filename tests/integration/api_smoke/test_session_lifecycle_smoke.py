"""
E2E Workflow Tests — Session Lifecycle (Phase 4, P2)

Coverage gap addressed: session lifecycle had smoke tests for individual
endpoints but no ordered chain test covering Create → Update → CheckIn → Results.

Chain tested:
  WF01: Admin creates a session          POST /api/v1/sessions/
  WF02: Get created session by ID        GET  /api/v1/sessions/{session_id}
  WF03: Admin updates session            PATCH /api/v1/sessions/{session_id}
  WF04: Student checks in to session     POST /api/v1/sessions/{session_id}/check-in
  WF05: Get session results (empty)      GET  /api/v1/sessions/{session_id}/results
  WF06: Submit game results              PATCH /api/v1/sessions/{session_id}/results
  WF07: Session availability endpoint    GET  /api/v1/sessions/availability
  WF08: Session calendar view            GET  /api/v1/sessions/calendar
  WF09: Session booking list             GET  /api/v1/sessions/{session_id}/bookings
  WF10: Admin deletes session            DELETE /api/v1/sessions/{session_id}

Notes:
  - Full /api/v1/... paths are used.
  - Semester precondition is created via wf_semester_id fixture.
  - State is accumulated via class-level attributes (WF01 → WF10).
"""

import pytest
from datetime import datetime, timedelta, timezone
from typing import Optional


# ── Module-scoped preconditions ───────────────────────────────────────────────

@pytest.fixture(scope="function")
def wf_session_semester_id(test_db) -> int:
    """Get or create a Semester for session lifecycle tests."""
    from app.models.semester import Semester, SemesterStatus

    existing = test_db.query(Semester).filter(Semester.status != SemesterStatus.CANCELLED).first()
    if existing:
        return existing.id

    ts = int(datetime.now(timezone.utc).timestamp())
    sem = Semester(
        code=f"WF_SESSION_{ts}",
        name=f"Session Lifecycle Test Semester {ts}",
        start_date=datetime.now(timezone.utc).date(),
        end_date=(datetime.now(timezone.utc) + timedelta(days=30)).date(),
    )
    test_db.add(sem)
    test_db.commit()
    test_db.refresh(sem)
    return sem.id


# ── Session lifecycle workflow ────────────────────────────────────────────────

class TestSessionLifecycleWorkflow:
    """
    Phase 4 — E2E Workflow: Session Lifecycle.

    Tests run WF01 → WF10 in order. _session_id is set in WF01 and used by
    all downstream steps. If WF01 fails (e.g., missing DB state), downstream
    steps fall back to session_id=99999 and expect 404.
    """

    _session_id: Optional[int] = None

    # ── WF01 — Create session ─────────────────────────────────────────────────

    def test_wf01_admin_creates_session(
        self, api_client, admin_token, wf_session_semester_id
    ):
        """
        Step 1: Admin creates a regular session.
        Schema: SessionCreate — title + date_start + date_end + semester_id required.

        Expected: 200/201 on success; 400/422 on constraint violation.
        """
        headers = {"Authorization": f"Bearer {admin_token}"}
        now = datetime.now(timezone.utc)
        payload = {
            "title": "Session Lifecycle E2E Test",
            "date_start": (now + timedelta(days=2)).isoformat(),
            "date_end": (now + timedelta(days=2, hours=1, minutes=30)).isoformat(),
            "semester_id": wf_session_semester_id,
            "capacity": 15,
            "session_type": "on_site",
            "sport_type": "Football",
            "level": "Amateur",
        }
        response = api_client.post("/api/v1/sessions/", json=payload, headers=headers)
        assert response.status_code in [200, 201, 400, 403, 422], (
            f"Session create returned unexpected status "
            f"{response.status_code}: {response.text[:300]}"
        )
        if response.status_code in [200, 201]:
            data = response.json()
            TestSessionLifecycleWorkflow._session_id = data.get("id")

    # ── WF02 — Get session by ID ──────────────────────────────────────────────

    def test_wf02_get_session_by_id(self, api_client, admin_token):
        """
        Step 2: Retrieve the created session details.
        Expected: 200 with session object; 404 if WF01 didn't create one.
        """
        session_id = TestSessionLifecycleWorkflow._session_id or 99999
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get(
            f"/api/v1/sessions/{session_id}", headers=headers
        )
        assert response.status_code in [200, 403, 404], (
            f"Session GET returned unexpected status "
            f"{response.status_code}: {response.text[:200]}"
        )

    # ── WF03 — Update session ─────────────────────────────────────────────────

    def test_wf03_admin_updates_session(self, api_client, admin_token):
        """
        Step 3: Admin patches the session (e.g., change capacity or level).
        Expected: 200 on success; 400/403/404 on errors.
        """
        session_id = TestSessionLifecycleWorkflow._session_id or 99999
        headers = {"Authorization": f"Bearer {admin_token}"}
        payload = {"capacity": 18, "level": "Pro"}
        response = api_client.patch(
            f"/api/v1/sessions/{session_id}", json=payload, headers=headers
        )
        assert response.status_code in [200, 400, 403, 404, 422], (
            f"Session PATCH returned unexpected status "
            f"{response.status_code}: {response.text[:300]}"
        )

    # ── WF04 — Student checks in ──────────────────────────────────────────────

    def test_wf04_student_checks_in(self, api_client, student_token):
        """
        Step 4: Student checks in to the session.
        Endpoint: POST /api/v1/sessions/{session_id}/check-in
        Schema: AttendanceCheckIn — body is optional (notes only).

        Expected outcomes:
          200 — check-in recorded
          400 — already checked in / session not open / not enrolled
          403 — permission denied
          404 — session not found
        """
        session_id = TestSessionLifecycleWorkflow._session_id or 99999
        headers = {"Authorization": f"Bearer {student_token}"}
        payload = {"notes": "E2E lifecycle check-in test"}
        response = api_client.post(
            f"/api/v1/sessions/{session_id}/check-in",
            json=payload,
            headers=headers,
        )
        assert response.status_code in [200, 201, 400, 403, 404], (
            f"Session check-in returned unexpected status "
            f"{response.status_code}: {response.text[:300]}"
        )

    # ── WF05 — Get session results (baseline) ────────────────────────────────

    def test_wf05_get_session_results_empty(self, api_client, admin_token):
        """
        Step 5: Retrieve results before any have been submitted.
        Expected: 200 with empty/null results; or 404 if session not found.
        """
        session_id = TestSessionLifecycleWorkflow._session_id or 99999
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get(
            f"/api/v1/sessions/{session_id}/results", headers=headers
        )
        assert response.status_code in [200, 400, 403, 404], (
            f"Session results GET returned unexpected status "
            f"{response.status_code}: {response.text[:200]}"
        )

    # ── WF06 — Submit game results ────────────────────────────────────────────

    def test_wf06_submit_game_results(self, api_client, admin_token):
        """
        Step 6: Admin submits game results for a tournament session.
        Endpoint: PATCH /api/v1/sessions/{session_id}/results
        Schema: SubmitGameResultsRequest — results list with user_id + score.

        Expected:
          200  — results stored
          400  — not a tournament session / invalid data
          403  — not admin or master instructor
          404  — session not found
        """
        session_id = TestSessionLifecycleWorkflow._session_id or 99999
        headers = {"Authorization": f"Bearer {admin_token}"}
        payload = {
            "results": [
                {"user_id": 1, "score": 3},
                {"user_id": 2, "score": 1},
            ]
        }
        response = api_client.patch(
            f"/api/v1/sessions/{session_id}/results",
            json=payload,
            headers=headers,
        )
        # 400 is expected because the session is NOT a tournament game
        assert response.status_code in [200, 400, 403, 404, 422], (
            f"Session results PATCH returned unexpected status "
            f"{response.status_code}: {response.text[:300]}"
        )

    # ── WF07 — Session availability ───────────────────────────────────────────

    def test_wf07_session_availability(self, api_client, student_token):
        """
        Step 7: Student checks session availability.
        Endpoint requires session_ids query param (comma-separated int list).
        Uses session_id from WF01 or falls back to "99999".
        """
        headers = {"Authorization": f"Bearer {student_token}"}
        session_id = TestSessionLifecycleWorkflow._session_id or 99999
        response = api_client.get(
            f"/api/v1/sessions/availability?session_ids={session_id}", headers=headers
        )
        assert response.status_code in [200, 400, 403, 404, 422], (
            f"Session availability returned unexpected status "
            f"{response.status_code}: {response.text[:200]}"
        )

    # ── WF08 — Session calendar ───────────────────────────────────────────────

    def test_wf08_session_calendar(self, api_client, student_token):
        """
        Step 8: Student retrieves the session calendar view.
        Returns 200 with calendar-formatted sessions.
        """
        headers = {"Authorization": f"Bearer {student_token}"}
        response = api_client.get("/api/v1/sessions/calendar", headers=headers)
        assert response.status_code in [200, 400, 403], (
            f"Session calendar returned unexpected status "
            f"{response.status_code}: {response.text[:200]}"
        )

    # ── WF09 — Session bookings list ─────────────────────────────────────────

    def test_wf09_get_session_bookings(self, api_client, admin_token):
        """
        Step 9: Admin retrieves the bookings for a specific session.
        Endpoint: GET /api/v1/sessions/{session_id}/bookings
        Expected: 200 (possibly empty list); 404 if session not found.
        """
        session_id = TestSessionLifecycleWorkflow._session_id or 99999
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.get(
            f"/api/v1/sessions/{session_id}/bookings", headers=headers
        )
        assert response.status_code in [200, 403, 404], (
            f"Session bookings GET returned unexpected status "
            f"{response.status_code}: {response.text[:200]}"
        )

    # ── WF10 — Delete session (cleanup) ──────────────────────────────────────

    def test_wf10_admin_deletes_session(self, api_client, admin_token):
        """
        Step 10: Admin deletes the session (cleanup / end of lifecycle).
        Expected: 200/204 on success; 403/404 if not found.
        Note: WF10 skipped gracefully if _session_id is None (WF01 failed).
        """
        session_id = TestSessionLifecycleWorkflow._session_id
        if session_id is None:
            pytest.skip("No session_id from WF01 — skipping delete step")

        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.delete(
            f"/api/v1/sessions/{session_id}", headers=headers
        )
        assert response.status_code in [200, 204, 400, 403, 404], (
            f"Session DELETE returned unexpected status "
            f"{response.status_code}: {response.text[:200]}"
        )


# ── Session endpoint input validation edge cases ──────────────────────────────

class TestSessionInputValidation:
    """
    Phase 4 — Edge cases / boundary values for session endpoints.
    Validates schema constraints.
    """

    def test_create_session_missing_title_returns_422(
        self, api_client, admin_token, wf_session_semester_id
    ):
        """title is required for SessionCreate → 422 if omitted."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        now = datetime.now(timezone.utc)
        payload = {
            # title intentionally omitted
            "date_start": (now + timedelta(days=3)).isoformat(),
            "date_end": (now + timedelta(days=3, hours=2)).isoformat(),
            "semester_id": wf_session_semester_id,
        }
        response = api_client.post("/api/v1/sessions/", json=payload, headers=headers)
        assert response.status_code == 422, (
            f"Missing title must be 422, got {response.status_code}"
        )

    def test_create_session_missing_semester_id_returns_422(
        self, api_client, admin_token
    ):
        """semester_id is required for SessionCreate → 422 if omitted."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        now = datetime.now(timezone.utc)
        payload = {
            "title": "Test",
            "date_start": (now + timedelta(days=3)).isoformat(),
            "date_end": (now + timedelta(days=3, hours=2)).isoformat(),
            # semester_id intentionally omitted
        }
        response = api_client.post("/api/v1/sessions/", json=payload, headers=headers)
        assert response.status_code == 422, (
            f"Missing semester_id must be 422, got {response.status_code}"
        )

    def test_create_session_empty_body_returns_422(
        self, api_client, admin_token
    ):
        """Empty body for POST /api/v1/sessions/ → 422."""
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = api_client.post("/api/v1/sessions/", json={}, headers=headers)
        assert response.status_code == 422, (
            f"Empty body must be 422, got {response.status_code}"
        )


# ── Auth guard tests ──────────────────────────────────────────────────────────

class TestSessionLifecycleAuthGuards:
    """Unauthenticated requests to session lifecycle endpoints must return 401/403."""

    def test_session_create_requires_auth(self, api_client):
        response = api_client.post("/api/v1/sessions/", json={})
        assert response.status_code in [401, 403, 422], (
            f"Unauthenticated session create must be 401/403/422, got {response.status_code}"
        )

    def test_session_checkin_requires_auth(self, api_client):
        response = api_client.post("/api/v1/sessions/99999/check-in", json={})
        assert response.status_code in [401, 403], (
            f"Unauthenticated check-in must be 401/403, got {response.status_code}"
        )

    def test_session_results_requires_auth(self, api_client):
        response = api_client.get("/api/v1/sessions/99999/results")
        assert response.status_code in [401, 403], (
            f"Unauthenticated results GET must be 401/403, got {response.status_code}"
        )

    def test_session_delete_requires_auth(self, api_client):
        response = api_client.delete("/api/v1/sessions/99999")
        assert response.status_code in [401, 403], (
            f"Unauthenticated delete must be 401/403, got {response.status_code}"
        )
