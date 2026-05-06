"""
Integration tests — tournament_attendance.html rendering.

Stage 1 template-fix regression guards:

  ATT-UI-01  instructors_checked_in=0 → INDIVIDUAL check-in button active (not disabled)
  ATT-UI-02  instructors_checked_in=0 → old "Instructor check-in required" warning absent
  ATT-UI-03  informational banner present when 0 instructors checked in
  ATT-UI-04  instructors_checked_in=1 → banner absent, button still active

DONE = pytest tests/integration/web_flows/test_tournament_attendance_ui.py -v
"""
import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db
from app.dependencies import get_current_user_web
from app.models.user import User, UserRole
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_instructor_slot import TournamentInstructorSlot, SlotStatus
from app.models.license import UserLicense
from app.core.security import get_password_hash


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"att-ui-admin+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Attendance UI Admin",
        password_hash=get_password_hash("Admin1234!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_tournament(db: Session) -> Semester:
    sem = Semester(
        code=f"ATT-UI-{uuid.uuid4().hex[:6]}",
        name="Attendance UI Test Tournament",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
        status=SemesterStatus.DRAFT,
        tournament_status="CHECK_IN_OPEN",
        semester_category=SemesterCategory.MINI_SEASON,
        age_group="AMATEUR",
        enrollment_cost=0,
    )
    db.add(sem)
    db.flush()
    db.add(TournamentConfiguration(
        semester_id=sem.id,
        participant_type="INDIVIDUAL",
        sessions_generated=False,
    ))
    db.flush()
    return sem


def _make_player(db: Session, sem: Semester) -> User:
    u = User(
        email=f"att-ui-player+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Attendance UI Player",
        password_hash=get_password_hash("x"),
        role=UserRole.STUDENT,
        is_active=True,
    )
    db.add(u)
    db.flush()
    lic = UserLicense(
        user_id=u.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        is_active=True,
        started_at=datetime.now(timezone.utc),
    )
    db.add(lic)
    db.flush()
    db.add(SemesterEnrollment(
        user_id=u.id,
        semester_id=sem.id,
        user_license_id=lic.id,
        request_status=EnrollmentStatus.APPROVED,
        is_active=True,
    ))
    db.flush()
    return u


def _make_instructor_slot(db: Session, sem: Semester, admin: User, status: SlotStatus) -> TournamentInstructorSlot:
    instructor = User(
        email=f"att-ui-instr+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Attendance UI Instructor",
        password_hash=get_password_hash("x"),
        role=UserRole.INSTRUCTOR,
        is_active=True,
    )
    db.add(instructor)
    db.flush()
    slot = TournamentInstructorSlot(
        semester_id=sem.id,
        instructor_id=instructor.id,
        role="MASTER",
        status=status,
        assigned_by=admin.id,
    )
    db.add(slot)
    db.flush()
    return slot


def _client(db: Session, admin: User) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user_web] = lambda: admin
    return TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})


# ── ATT-UI-01 ─────────────────────────────────────────────────────────────────

class TestCheckinButtonActiveWithoutInstructor:
    """ATT-UI-01: 0 instructors checked in → INDIVIDUAL check-in button rendered
    without disabled attribute (Stage 1 template-fix).
    """

    def test_att_ui_01_button_not_disabled(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_tournament(test_db)
        _make_player(test_db, sem)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/attendance")
            assert resp.status_code == 200
            html = resp.text

            # The btn-checkin must appear
            assert "btn-checkin" in html

            # It must NOT carry a disabled attribute next to indPlayerCheckin
            # Simplest structural check: 'disabled' must not appear anywhere in
            # the action button column context
            assert 'disabled title="Instructor check-in required"' not in html
            assert 'disabled' not in html or _disabled_only_in_unrelated(html)
        finally:
            app.dependency_overrides.clear()


def _disabled_only_in_unrelated(html: str) -> bool:
    """True if the only 'disabled' occurrences are unrelated to check-in buttons
    (e.g. read-only form inputs in other admin sections).  For the attendance
    page there are no other disabled elements, so any 'disabled' is a bug."""
    return False  # conservative: fail if anything disabled


# ── ATT-UI-02 ─────────────────────────────────────────────────────────────────

class TestOldWarningBannerAbsent:
    """ATT-UI-02: Old blocking warning text 'Instructor check-in required' must
    not appear in the rendered page.
    """

    def test_att_ui_02_old_warning_absent(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_tournament(test_db)
        _make_player(test_db, sem)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/attendance")
            assert resp.status_code == 200
            html = resp.text
            assert "Instructor check-in required" not in html
            assert "before player/team check-in is enabled" not in html
        finally:
            app.dependency_overrides.clear()


# ── ATT-UI-03 ─────────────────────────────────────────────────────────────────

class TestInformationalBannerPresent:
    """ATT-UI-03: 0 instructors checked in → informational (blue) banner shown,
    informing the admin that check-in is available without blocking.
    """

    def test_att_ui_03_info_banner_shown(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_tournament(test_db)
        _make_player(test_db, sem)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/attendance")
            assert resp.status_code == 200
            html = resp.text
            # New informational wording
            assert "No instructor checked in yet" in html
            assert "Player check-in is available" in html
        finally:
            app.dependency_overrides.clear()


# ── ATT-UI-04 ─────────────────────────────────────────────────────────────────

class TestBannerAbsentWhenInstructorCheckedIn:
    """ATT-UI-04: 1 instructor CHECKED_IN → informational banner absent,
    button still active (no regression on the checked-in path).
    """

    def test_att_ui_04_banner_absent_instructor_present(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_tournament(test_db)
        _make_player(test_db, sem)
        slot = _make_instructor_slot(test_db, sem, admin, SlotStatus.CHECKED_IN)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/attendance")
            assert resp.status_code == 200
            html = resp.text
            # Informational banner must be absent (instructor IS checked in)
            assert "No instructor checked in yet" not in html
            # Button still present and active (no disabled)
            assert "btn-checkin" in html
            assert 'disabled title="Instructor check-in required"' not in html
        finally:
            app.dependency_overrides.clear()
