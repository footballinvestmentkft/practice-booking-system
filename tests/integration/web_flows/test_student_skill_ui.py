"""
Student Skill UI Smoke Tests (SMOKE-32a–32e)

Covers:
  SMOKE-32a  GET /skills  (student)       → 200 + "Skill Progression" in HTML
  SMOKE-32b  GET /skills/data (student)   → 200 + JSON with expected keys
  SMOKE-32c  GET /skills  (admin)         → 303 redirect to /dashboard
  SMOKE-32d  GET /skills/data (admin)     → 403
  SMOKE-32e  GET /progress (student)      → 200 + "Skill Snapshot" in HTML

Auth:   get_current_user_web overridden — no real login needed.
CSRF:   Authorization: Bearer bypass header.
DB:     SAVEPOINT-isolated; all changes rolled back after each test.
"""

import uuid
import json
import pytest

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import event

from app.main import app
from app.database import engine, get_db
from app.dependencies import get_current_user_web
from app.models.user import User, UserRole
from app.core.security import get_password_hash


# ── SAVEPOINT-isolated DB ─────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def test_db():
    connection = engine.connect()
    transaction = connection.begin()
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = TestSessionLocal()
    connection.begin_nested()

    @event.listens_for(session, "after_transaction_end")
    def restart_savepoint(session, txn):
        if txn.nested and not txn._parent.nested:
            session.begin_nested()

    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def student_user(test_db: Session) -> User:
    u = User(
        email=f"smoke32-student+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Smoke32 Student",
        password_hash=get_password_hash("student123"),
        role=UserRole.STUDENT,
        is_active=True,
        onboarding_completed=True,  # required by require_student_onboarding guard
    )
    test_db.add(u)
    test_db.commit()
    test_db.refresh(u)
    return u


@pytest.fixture(scope="function")
def admin_user(test_db: Session) -> User:
    u = User(
        email=f"smoke32-admin+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Smoke32 Admin",
        password_hash=get_password_hash("admin123"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    test_db.add(u)
    test_db.commit()
    test_db.refresh(u)
    return u


@pytest.fixture(scope="function")
def student_client(test_db: Session, student_user: User) -> TestClient:
    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_web] = lambda: student_user

    with TestClient(
        app,
        headers={"Authorization": "Bearer test-csrf-bypass"},
        follow_redirects=False,
    ) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture(scope="function")
def admin_client(test_db: Session, admin_user: User) -> TestClient:
    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user_web] = lambda: admin_user

    with TestClient(
        app,
        headers={"Authorization": "Bearer test-csrf-bypass"},
        follow_redirects=False,
    ) as c:
        yield c

    app.dependency_overrides.clear()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestSkillPageStudentAccess:
    """SMOKE-32a, SMOKE-32b — student can access /skills and /skills/data."""

    def test_smoke32a_skills_page_student_200(self, student_client: TestClient):
        """SMOKE-32a: GET /skills returns 200 and contains 'Skill Progression'."""
        resp = student_client.get("/skills")
        assert resp.status_code == 200
        assert "Skill Progression" in resp.text

    def test_smoke32b_skills_data_student_json(self, student_client: TestClient):
        """SMOKE-32b: GET /skills/data returns 200 JSON with expected keys."""
        resp = student_client.get("/skills/data")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        data = resp.json()
        assert "skills" in data
        assert "average_level" in data
        assert "active_preset_skills" in data
        assert "total_tournaments" in data
        # skills should be a dict (may be empty for new student with no LFA license)
        assert isinstance(data["skills"], dict)
        assert isinstance(data["active_preset_skills"], list)


class TestSkillPageAccessControl:
    """SMOKE-32c, SMOKE-32d — admin cannot access student skill endpoints."""

    def test_smoke32c_skills_page_admin_redirects(self, admin_client: TestClient):
        """SMOKE-32c: GET /skills for admin → 303 redirect."""
        resp = admin_client.get("/skills")
        assert resp.status_code == 303

    def test_smoke32d_skills_data_admin_403(self, admin_client: TestClient):
        """SMOKE-32d: GET /skills/data for admin → 403."""
        resp = admin_client.get("/skills/data")
        assert resp.status_code == 403


class TestProgressSkillWidget:
    """SMOKE-32e — /progress page contains the skill snapshot widget."""

    def test_smoke32e_progress_contains_skill_snapshot(self, student_client: TestClient):
        """SMOKE-32e: GET /progress → 200 and 'Skill Snapshot' widget present."""
        resp = student_client.get("/progress")
        assert resp.status_code == 200
        assert "Skill Snapshot" in resp.text
        # The "View all 44 skills" link should be present
        assert "/skills" in resp.text


class TestSkillHistoryPage:
    """SMOKE-38a–38d — /skills/history page and data endpoint."""

    def test_smoke38a_history_page_student_200(self, student_client: TestClient):
        """SMOKE-38a: GET /skills/history returns 200 with chart canvas."""
        resp = student_client.get("/skills/history")
        assert resp.status_code == 200
        assert "Skill Event History" in resp.text
        assert "skill-chart" in resp.text

    def test_smoke38b_history_data_student_json(self, student_client: TestClient):
        """SMOKE-38b: GET /skills/history/data?skill=passing returns valid JSON."""
        resp = student_client.get("/skills/history/data?skill=passing")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        data = resp.json()
        assert data["skill"] == "passing"
        assert "baseline" in data
        assert "current_level" in data
        assert "total_delta" in data
        assert "timeline" in data
        assert "skill_display_name" in data
        assert isinstance(data["timeline"], list)

    def test_smoke38c_history_data_invalid_skill_404(self, student_client: TestClient):
        """SMOKE-38c: GET /skills/history/data with unknown skill → 404."""
        resp = student_client.get("/skills/history/data?skill=INVALID_SKILL_XYZ_999")
        assert resp.status_code == 404

    def test_smoke38d_history_page_admin_redirects(self, admin_client: TestClient):
        """SMOKE-38d: Admin → GET /skills/history → 303 redirect to /dashboard."""
        resp = admin_client.get("/skills/history")
        assert resp.status_code == 303
        assert resp.headers.get("location") == "/dashboard"
