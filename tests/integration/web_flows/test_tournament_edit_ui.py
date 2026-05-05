"""
Integration tests — tournament edit page age group field rendering.

  EDIT-UI-01  PROMOTION_EVENT (multi-age) → id="basic-age-group-readonly" present,
              id="basic-age-group" absent
  EDIT-UI-02  PROMOTION_EVENT (single-age) → id="basic-age-group-readonly" present,
              id="basic-age-group" absent
  EDIT-UI-03  Non-promotion event → id="basic-age-group" present

DONE = pytest tests/integration/web_flows/test_tournament_edit_ui.py -v
"""
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db
from app.dependencies import get_current_user_web
from app.models.user import User, UserRole
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.core.security import get_password_hash


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"edit-ui-admin+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Edit UI Admin",
        password_hash=get_password_hash("Admin1234!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_promotion_semester(db: Session, age_groups: list) -> Semester:
    sem = Semester(
        code=f"PROMO-UI-{uuid.uuid4().hex[:6]}",
        name="UI Test Promo Event",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
        status=SemesterStatus.DRAFT,
        tournament_status="DRAFT",
        semester_category=SemesterCategory.PROMOTION_EVENT,
        age_group=age_groups[0] if len(age_groups) == 1 else None,
        age_groups=age_groups,
        enrollment_cost=0,
    )
    db.add(sem)
    db.flush()
    return sem


def _make_mini_season_semester(db: Session) -> Semester:
    sem = Semester(
        code=f"MINI-UI-{uuid.uuid4().hex[:6]}",
        name="UI Test Mini Season",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
        status=SemesterStatus.DRAFT,
        tournament_status="DRAFT",
        semester_category=SemesterCategory.MINI_SEASON,
        age_group="AMATEUR",
        enrollment_cost=0,
    )
    db.add(sem)
    db.flush()
    return sem


def _client(db: Session, admin: User) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user_web] = lambda: admin
    return TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})


# ── EDIT-UI-01 ────────────────────────────────────────────────────────────────

class TestPromotionEventMultiAge:
    """EDIT-UI-01: multi-age PROMOTION_EVENT → readonly div, no select."""

    def test_edit_ui_01_readonly_present_select_absent(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_promotion_semester(test_db, age_groups=["PRE", "YOUTH"])
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            html = resp.text
            assert 'id="basic-age-group-readonly"' in html
            assert 'id="basic-age-group"' not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-02 ────────────────────────────────────────────────────────────────

class TestPromotionEventSingleAge:
    """EDIT-UI-02: single-age PROMOTION_EVENT → readonly div, no select."""

    def test_edit_ui_02_readonly_present_select_absent(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_promotion_semester(test_db, age_groups=["PRE"])
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            html = resp.text
            assert 'id="basic-age-group-readonly"' in html
            assert 'id="basic-age-group"' not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-03 ────────────────────────────────────────────────────────────────

class TestNonPromotionEventSelect:
    """EDIT-UI-03: non-promotion event → age group <select> rendered normally."""

    def test_edit_ui_03_select_present(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            assert 'id="basic-age-group"' in resp.text
            assert 'id="basic-age-group-readonly"' not in resp.text
        finally:
            app.dependency_overrides.clear()
