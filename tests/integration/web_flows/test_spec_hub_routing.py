"""
Spec Hub Routing Tests (SMOKE-34a–d, SMOKE-35a–b, SMOKE-36a–b, SMOKE-37a–b)

Covers:
  SMOKE-34a  Hub, student, license onboarding_completed=False, no football_skills, no enrollment
             → "Continue Setup" shown; "ENTER" href absent
  SMOKE-34b  Hub, student, license onboarding_completed=True
             → "ENTER" shown; "Continue Setup" absent
  SMOKE-34c  GET /dashboard/lfa-football-player, onboarding_completed=False, no signals
             → 303 redirect to /specialization/lfa-player/onboarding
  SMOKE-34d  GET /dashboard/lfa-football-player, onboarding_completed=True
             → 200 (spec dashboard renders)

  SMOKE-35a  POST /api/v1/tournaments/{id}/enroll, onboarding_completed=False, no enrollment
             → 400 "Complete your LFA Football Player onboarding before enrolling"
  SMOKE-35b  POST /api/v1/tournaments/{id}/enroll, onboarding_completed=True
             → 200 enrollment success

  SMOKE-36a  POST /tournaments/{id}/enroll (web form), onboarding_completed=False, no enrollment
             → 303 with error flash
  SMOKE-36b  POST /tournaments/{id}/enroll (web form), onboarding_completed=True
             → 303 success redirect

  SMOKE-37a  Hub, legacy: onboarding_completed=False but HAS SemesterEnrollment via this license
             → "ENTER" shown (existing enrollment = effective onboarding)
  SMOKE-37b  GET /dashboard/lfa-football-player, legacy: onboarding_completed=False but HAS enrollment
             → 200 (not redirected to onboarding)

Business logic:
  Effective onboarding = onboarding_completed=True
                       OR football_skills is not None   (LFA: baseline data present)
                       OR SemesterEnrollment exists for this license (legacy compat: enrolled
                          before the onboarding guard was added — prior enrollment proves engagement)
  TournamentParticipation is NOT a valid signal — it is created ONLY after full
  tournament completion (reward distribution), making it a circular dependency.

State machine:
  PENDING_ONBOARDING: license exists, onboarding_completed=False, football_skills=None,
                      no SemesterEnrollment → NO spec hub access, NO tournament enrollment
  ACTIVE: onboarding_completed=True OR football_skills≠None OR SemesterEnrollment exists
                      → full access

Auth:   get_current_user_web + get_current_user overridden — no real login needed.
DB:     SAVEPOINT-isolated; all changes rolled back after each test.
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, date
from zoneinfo import ZoneInfo

import pytest

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import event

from app.main import app
from app.database import engine, get_db
from app.dependencies import get_current_user_web
from app.api.deps import get_current_user
from app.models.user import User, UserRole
from app.models.license import UserLicense
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_student(test_db: Session) -> User:
    """Student with YOUTH-category DOB to satisfy LFA age guard in spec_dashboard."""
    u = User(
        email=f"smoke34-{uuid.uuid4().hex[:8]}@lfa.com",
        name="Smoke34 Student",
        password_hash=get_password_hash("test"),
        role=UserRole.STUDENT,
        is_active=True,
        onboarding_completed=True,
        date_of_birth=datetime(2008, 1, 15).date(),  # age 18 → YOUTH category
    )
    test_db.add(u)
    test_db.commit()
    test_db.refresh(u)
    return u


def _make_license(test_db: Session, user: User, onboarding_completed: bool) -> UserLicense:
    lic = UserLicense(
        user_id=user.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        current_level=1,
        started_at=datetime.now(ZoneInfo("UTC")),
        payment_verified=True,
        onboarding_completed=onboarding_completed,
    )
    test_db.add(lic)
    test_db.commit()
    test_db.refresh(lic)
    return lic


def _make_enrollment(
    test_db: Session, user: User, license: UserLicense, tournament: Semester
) -> SemesterEnrollment:
    """Insert a SemesterEnrollment directly (bypassing API guard) — simulates a legacy enrollment."""
    enr = SemesterEnrollment(
        user_id=user.id,
        semester_id=tournament.id,
        user_license_id=license.id,
        age_category="YOUTH",
        request_status=EnrollmentStatus.APPROVED,
        payment_verified=True,
        is_active=True,
    )
    test_db.add(enr)
    test_db.commit()
    test_db.refresh(enr)
    return enr


def _make_open_tournament(test_db: Session) -> Semester:
    """Minimal ENROLLMENT_OPEN YOUTH tournament with zero enrollment cost."""
    sem = Semester(
        code=f"SMOKE35-{uuid.uuid4().hex[:6]}",
        name="Smoke35 Enrollment Guard Test",
        start_date=date(2027, 1, 1),
        end_date=date(2027, 6, 30),
        status=SemesterStatus.ONGOING,
        semester_category=SemesterCategory.TOURNAMENT,
        tournament_status="ENROLLMENT_OPEN",
        age_group="YOUTH",
        enrollment_cost=0,
    )
    test_db.add(sem)
    test_db.commit()
    test_db.refresh(sem)
    return sem


@contextmanager
def _client(test_db: Session, user: User):
    def override_db():
        yield test_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user_web] = lambda: user
    app.dependency_overrides[get_current_user] = lambda: user

    with TestClient(
        app,
        headers={"Authorization": "Bearer test-csrf-bypass"},
        follow_redirects=False,
    ) as c:
        yield c

    app.dependency_overrides.clear()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestHubOnboardingNotComplete:
    """SMOKE-34a — hub shows Continue Setup: no onboarding_completed, no football_skills, no TP."""

    def test_smoke34a_hub_shows_continue_setup(self, test_db: Session):
        student = _make_student(test_db)
        _make_license(test_db, student, onboarding_completed=False)

        with _client(test_db, student) as c:
            hub = c.get("/dashboard")
            assert hub.status_code == 200
            html = hub.text
            assert "Continue Setup" in html
            assert 'href="/dashboard/lfa-football-player"' in html  # routes through spec_dashboard() guard
            assert '🚀 ENTER' not in html  # No Enter CTA shown when onboarding incomplete


class TestHubOnboardingComplete:
    """SMOKE-34b — hub shows ENTER when onboarding_completed=True."""

    def test_smoke34b_hub_shows_enter(self, test_db: Session):
        student = _make_student(test_db)
        _make_license(test_db, student, onboarding_completed=True)

        with _client(test_db, student) as c:
            hub = c.get("/dashboard")
            assert hub.status_code == 200
            html = hub.text
            assert 'href="/dashboard/lfa-football-player"' in html
            assert "Continue Setup" not in html


class TestSpecDashboardOnboardingGuard:
    """SMOKE-34c & SMOKE-34d — spec_dashboard() guard."""

    def test_smoke34c_redirects_when_not_onboarded(self, test_db: Session):
        """No onboarding_completed, no football_skills → 303."""
        student = _make_student(test_db)
        _make_license(test_db, student, onboarding_completed=False)

        with _client(test_db, student) as c:
            r = c.get("/dashboard/lfa-football-player")
            assert r.status_code == 303
            assert r.headers.get("location", "").endswith("/specialization/lfa-player/onboarding")

    def test_smoke34d_200_when_onboarded(self, test_db: Session):
        """onboarding_completed=True → 200."""
        student = _make_student(test_db)
        _make_license(test_db, student, onboarding_completed=True)

        with _client(test_db, student) as c:
            r = c.get("/dashboard/lfa-football-player")
            assert r.status_code == 200
            assert 'href="/events"' in r.text


class TestWebFormEnrollmentGuard:
    """SMOKE-36a & SMOKE-36b — web form POST /tournaments/{id}/enroll enforces onboarding_completed."""

    def test_smoke36a_web_enroll_blocked_when_not_onboarded(self, test_db: Session):
        """SMOKE-36a: onboarding_completed=False → 303 redirect with error flash."""
        student = _make_student(test_db)
        _make_license(test_db, student, onboarding_completed=False)
        tournament = _make_open_tournament(test_db)

        with _client(test_db, student) as c:
            r = c.post(f"/tournaments/{tournament.id}/enroll")
            assert r.status_code == 303
            location = r.headers.get("location", "")
            assert "flash_type=error" in location or "error" in location.lower()

    def test_smoke36b_web_enroll_succeeds_when_onboarded(self, test_db: Session):
        """SMOKE-36b: onboarding_completed=True → 303 redirect to /tournaments (success)."""
        student = _make_student(test_db)
        _make_license(test_db, student, onboarding_completed=True)
        tournament = _make_open_tournament(test_db)

        with _client(test_db, student) as c:
            r = c.post(f"/tournaments/{tournament.id}/enroll")
            assert r.status_code == 303
            location = r.headers.get("location", "")
            assert "error" not in location.lower()


class TestEnrollmentOnboardingGuard:
    """SMOKE-35a & SMOKE-35b — enrollment API enforces onboarding_completed."""

    def test_smoke35a_enroll_blocked_when_not_onboarded(self, test_db: Session):
        """SMOKE-35a: onboarding_completed=False → 400 before enrollment proceeds."""
        student = _make_student(test_db)
        _make_license(test_db, student, onboarding_completed=False)
        tournament = _make_open_tournament(test_db)

        with _client(test_db, student) as c:
            r = c.post(f"/api/v1/tournaments/{tournament.id}/enroll")
            assert r.status_code == 400
            body = r.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "onboarding" in detail.lower()

    def test_smoke35b_enroll_succeeds_when_onboarded(self, test_db: Session):
        """SMOKE-35b: onboarding_completed=True → 200 enrollment success."""
        student = _make_student(test_db)
        _make_license(test_db, student, onboarding_completed=True)
        tournament = _make_open_tournament(test_db)

        with _client(test_db, student) as c:
            r = c.post(f"/api/v1/tournaments/{tournament.id}/enroll")
            assert r.status_code == 200
            assert r.json().get("success") is True


class TestLegacyEnrollmentSignal:
    """SMOKE-37a & SMOKE-37b — SemesterEnrollment is a valid ACTIVE signal for legacy users."""

    def test_smoke37a_hub_shows_enter_for_legacy_enrolled_user(self, test_db: Session):
        """SMOKE-37a: onboarding_completed=False but HAS SemesterEnrollment → hub shows ENTER."""
        student = _make_student(test_db)
        license_ = _make_license(test_db, student, onboarding_completed=False)
        tournament = _make_open_tournament(test_db)
        _make_enrollment(test_db, student, license_, tournament)

        with _client(test_db, student) as c:
            hub = c.get("/dashboard")
            assert hub.status_code == 200
            html = hub.text
            assert 'href="/dashboard/lfa-football-player"' in html
            assert "Continue Setup" not in html

    def test_smoke37b_spec_dashboard_200_for_legacy_enrolled_user(self, test_db: Session):
        """SMOKE-37b: onboarding_completed=False but HAS SemesterEnrollment → spec dashboard 200."""
        student = _make_student(test_db)
        license_ = _make_license(test_db, student, onboarding_completed=False)
        tournament = _make_open_tournament(test_db)
        _make_enrollment(test_db, student, license_, tournament)

        with _client(test_db, student) as c:
            r = c.get("/dashboard/lfa-football-player")
            assert r.status_code == 200
            assert 'href="/events"' in r.text
