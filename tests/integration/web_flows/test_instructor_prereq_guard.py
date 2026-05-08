"""
Instructor Prerequisite Guard — Integration Tests
==================================================
PR: fix(domain): block tournament start without instructor prerequisite

Tests:
  LC-02  CHECK_IN_OPEN → IN_PROGRESS blocked without instructor (DB-backed).
         Verifies: 400 response, tournament status unchanged.
  GEN-01 Auto-generated sessions carry instructor_id when instructor is pre-assigned.
         Verifies: deterministic tournament creation, generation, NULL assertion.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from app.database import engine
from app.main import app
from app.database import get_db
from app.dependencies import get_current_admin_user_hybrid
from app.models.campus import Campus
from app.models.location import Location
from app.models.pitch import Pitch
from app.models.semester import Semester, SemesterCategory, SemesterStatus
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.session import Session as SessionModel
from app.models.license import UserLicense
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_instructor_slot import TournamentInstructorSlot, SlotRole, SlotStatus
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from tests.factories.game_factory import TournamentFactory


# ── SAVEPOINT-isolated DB fixture ─────────────────────────────────────────────

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


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"ipg-admin+{uuid.uuid4().hex[:8]}@lfa.com",
        name="IPG Admin",
        password_hash=get_password_hash("admin123"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_instructor(db: Session) -> User:
    u = User(
        email=f"ipg-instructor+{uuid.uuid4().hex[:8]}@lfa.com",
        name="IPG Instructor",
        password_hash=get_password_hash("admin123"),
        role=UserRole.INSTRUCTOR,
        is_active=True,
    )
    db.add(u)
    db.flush()
    db.add(UserLicense(
        user_id=u.id,
        specialization_type="LFA_COACH",
        current_level=7,
        max_achieved_level=7,
        is_active=True,
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        expires_at=None,
    ))
    db.flush()
    return u


def _make_campus_with_pitch(db: Session) -> Campus:
    """Create a location + campus + one active pitch (domain invariants)."""
    location = Location(
        name=f"IPG Location {uuid.uuid4().hex[:6]}",
        city=f"IPGCity-{uuid.uuid4().hex[:8]}",  # UNIQUE constraint on city
        country="HU",
    )
    db.add(location)
    db.flush()
    campus = Campus(
        name=f"IPG Campus {uuid.uuid4().hex[:6]}",
        location_id=location.id,
        is_active=True,
    )
    db.add(campus)
    db.flush()
    db.add(Pitch(
        campus_id=campus.id,
        pitch_number=1,
        name="Pálya A",
        capacity=22,
        is_active=True,
    ))
    db.flush()
    return campus


def _make_enrollment_closed_tournament(
    db: Session,
    campus: Campus,
    admin: User,
    instructor_id: int | None,
    tt,
) -> Semester:
    """
    Create a tournament directly in ENROLLMENT_CLOSED status with all prerequisites
    satisfied EXCEPT instructor (controlled by instructor_id parameter).

    Used to test the CHECK_IN_OPEN lifecycle guard: the instructor check fires inside
    GenerationValidator during the CHECK_IN_OPEN transition (before sessions can be
    created), so setting status to ENROLLMENT_CLOSED lets us probe that guard without
    any prior session generation.

    Prerequisites met:
    - campus with active pitch ✅
    - tournament_type ✅
    - format/type valid ✅
    Instructor: set only if instructor_id is not None.
    """
    code = f"IPG-{uuid.uuid4().hex[:10].upper()}"
    t = Semester(
        code=code,
        name=f"IPG Test Tournament {code[-6:]}",
        semester_category=SemesterCategory.TOURNAMENT,
        status=SemesterStatus.DRAFT,
        tournament_status="ENROLLMENT_CLOSED",
        age_group="PRO",
        campus_id=campus.id,
        location_id=None,
        start_date=date.today() + timedelta(days=7),
        end_date=date.today() + timedelta(days=8),
        enrollment_cost=0,
        specialization_type="LFA_FOOTBALL_PLAYER",
        master_instructor_id=instructor_id,
    )
    db.add(t)
    db.flush()

    db.add(TournamentConfiguration(
        semester_id=t.id,
        tournament_type_id=tt.id,
        participant_type="INDIVIDUAL",
        number_of_rounds=1,
        max_players=16,
    ))
    db.flush()

    db.commit()
    db.expire_all()
    return db.query(Semester).filter(Semester.id == t.id).first()


def _admin_client(db: Session, admin: User) -> TestClient:
    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin
    return TestClient(app, raise_server_exceptions=False)


# ── LC-02 — CHECK_IN_OPEN blocked without instructor ─────────────────────────


class TestCheckInOpenInstructorGuard:
    """
    LC-02: ENROLLMENT_CLOSED → CHECK_IN_OPEN is blocked (HTTP 400) when no
    instructor is assigned.  The instructor prerequisite is enforced inside
    GenerationValidator.can_generate_sessions(), which is called during the
    CHECK_IN_OPEN transition (sessions are generated there).  All other
    prerequisites are satisfied so the 400 is attributable exclusively to the
    instructor gap.

    Domain invariant: no auto-generated session may have instructor_id=NULL.
    The guard fires before any session is created.
    """

    def test_lc_02_check_in_open_blocked_no_master_instructor_id(self, test_db):
        """
        No master_instructor_id, no TournamentInstructorSlot →
        PATCH /status CHECK_IN_OPEN → 400 with 'instructor' in detail.
        Status remains ENROLLMENT_CLOSED.
        """
        admin = _make_admin(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(
            test_db, code=f"lc02-tt-{uuid.uuid4().hex[:6]}"
        )
        tournament = _make_enrollment_closed_tournament(
            test_db, campus=campus, admin=admin, instructor_id=None, tt=tt
        )
        tournament_id = tournament.id

        client = _admin_client(test_db, admin)
        try:
            response = client.patch(
                f"/api/v1/tournaments/{tournament_id}/status",
                json={"new_status": "CHECK_IN_OPEN", "reason": "test"},
            )

            assert response.status_code == 400, (
                f"Expected 400, got {response.status_code}: {response.text}"
            )
            body = response.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "instructor" in detail.lower(), (
                f"Expected 'instructor' in error message, got: {detail!r} (body={body})"
            )

            # Status must not have changed
            test_db.expire_all()
            t_after = test_db.query(Semester).filter(Semester.id == tournament_id).first()
            assert t_after.tournament_status == "ENROLLMENT_CLOSED", (
                f"Status changed unexpectedly to {t_after.tournament_status!r}"
            )
        finally:
            app.dependency_overrides.clear()

    def test_lc_02b_check_in_open_blocked_absent_slot_only(self, test_db):
        """
        No master_instructor_id + MASTER slot with status=ABSENT →
        slot is not usable, CHECK_IN_OPEN transition must still be blocked.
        """
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(
            test_db, code=f"lc02b-tt-{uuid.uuid4().hex[:6]}"
        )
        tournament = _make_enrollment_closed_tournament(
            test_db, campus=campus, admin=admin, instructor_id=None, tt=tt
        )

        # Add MASTER slot but mark it ABSENT
        test_db.add(TournamentInstructorSlot(
            semester_id=tournament.id,
            instructor_id=instructor.id,
            role=SlotRole.MASTER.value,
            status=SlotStatus.ABSENT.value,
            assigned_by=admin.id,
        ))
        test_db.commit()

        client = _admin_client(test_db, admin)
        try:
            response = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "CHECK_IN_OPEN", "reason": "test"},
            )
            assert response.status_code == 400
            body = response.json()
            detail = body.get("detail") or body.get("error", {}).get("message", "")
            assert "instructor" in detail.lower(), (
                f"Expected 'instructor' in error message, got: {detail!r}"
            )
        finally:
            app.dependency_overrides.clear()

    def test_lc_02c_check_in_open_not_blocked_with_confirmed_master_slot(self, test_db):
        """
        No master_instructor_id but MASTER/CONFIRMED slot → instructor guard passes,
        transition may succeed or fail for another reason (e.g. not enough players),
        but NOT due to instructor.

        This verifies the slot fallback path is wired correctly in GenerationValidator.
        """
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(
            test_db, code=f"lc02c-tt-{uuid.uuid4().hex[:6]}"
        )
        tournament = _make_enrollment_closed_tournament(
            test_db, campus=campus, admin=admin, instructor_id=None, tt=tt
        )

        # Add MASTER slot with CONFIRMED status (non-ABSENT → usable)
        test_db.add(TournamentInstructorSlot(
            semester_id=tournament.id,
            instructor_id=instructor.id,
            role=SlotRole.MASTER.value,
            status=SlotStatus.CONFIRMED.value,
            assigned_by=admin.id,
        ))
        test_db.commit()

        client = _admin_client(test_db, admin)
        try:
            response = client.patch(
                f"/api/v1/tournaments/{tournament.id}/status",
                json={"new_status": "CHECK_IN_OPEN", "reason": "test"},
            )
            # The instructor guard must NOT return 400 with "instructor" message.
            if response.status_code == 400:
                body = response.json()
                detail = body.get("detail") or body.get("error", {}).get("message", "")
                assert "instructor" not in detail.lower(), (
                    f"Instructor guard incorrectly blocked a tournament with a "
                    f"CONFIRMED MASTER slot: {detail!r}"
                )
        finally:
            app.dependency_overrides.clear()


# ── GEN-01 — auto-generated sessions have instructor_id IS NOT NULL ───────────


class TestGeneratedSessionInstructorNotNull:
    """
    GEN-01: Every auto-generated session must have instructor_id set (not NULL).

    Creates a deterministic tournament with all prerequisites:
    - campus + active pitch
    - instructor (master_instructor_id set)
    - HEAD_TO_HEAD / knockout tournament type
    - 4 enrolled + checked-in players
    - status CHECK_IN_OPEN (skipping lifecycle for direct generator test)

    Then calls GenerationValidator + session generator directly and asserts
    that every created session carries instructor_id IS NOT NULL.
    """

    def test_gen_01_sessions_carry_instructor_id(self, test_db):
        admin = _make_admin(test_db)
        instructor = _make_instructor(test_db)
        campus = _make_campus_with_pitch(test_db)
        tt = TournamentFactory.ensure_tournament_type(
            test_db, code="knockout"
        )

        # Create tournament directly in CHECK_IN_OPEN with instructor assigned
        code = f"GEN01-{uuid.uuid4().hex[:8].upper()}"
        t = Semester(
            code=code,
            name=f"GEN-01 Instructor Test {code[-6:]}",
            semester_category=SemesterCategory.TOURNAMENT,
            status=SemesterStatus.DRAFT,
            tournament_status="CHECK_IN_OPEN",
            age_group="PRO",
            campus_id=campus.id,
            start_date=date.today() + timedelta(days=7),
            end_date=date.today() + timedelta(days=8),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
            master_instructor_id=instructor.id,
        )
        test_db.add(t)
        test_db.flush()

        test_db.add(TournamentConfiguration(
            semester_id=t.id,
            tournament_type_id=tt.id,
            participant_type="INDIVIDUAL",
            number_of_rounds=1,
            max_players=16,
        ))
        test_db.flush()

        # Enroll and check-in 4 players
        from datetime import datetime
        for i in range(4):
            player = User(
                email=f"gen01-player-{uuid.uuid4().hex[:6]}@lfa.com",
                name=f"GEN-01 Player {i}",
                password_hash=get_password_hash("pw"),
                role=UserRole.STUDENT,
                is_active=True,
            )
            test_db.add(player)
            test_db.flush()
            license_ = UserLicense(
                user_id=player.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                current_level=1,
                max_achieved_level=1,
                started_at=datetime.utcnow(),
                is_active=True,
            )
            test_db.add(license_)
            test_db.flush()
            test_db.add(SemesterEnrollment(
                user_id=player.id,
                semester_id=t.id,
                user_license_id=license_.id,
                is_active=True,
                request_status=EnrollmentStatus.APPROVED,
                payment_verified=True,
                tournament_checked_in_at=datetime.utcnow(),
            ))
            test_db.flush()

        test_db.commit()
        test_db.expire_all()

        tournament_id = t.id

        # Validate via GenerationValidator first
        from app.services.tournament.session_generation.validators.generation_validator import (
            GenerationValidator,
        )
        validator = GenerationValidator(test_db)
        can_generate, reason = validator.can_generate_sessions(tournament_id)
        assert can_generate, f"Generation validator blocked unexpectedly: {reason}"

        # Run session generator
        from app.services.tournament_session_generator import TournamentSessionGenerator
        generator = TournamentSessionGenerator(test_db)
        success, message, sessions_created = generator.generate_sessions(
            tournament_id=tournament_id,
            parallel_fields=1,
            session_duration_minutes=60,
            break_minutes=15,
            number_of_rounds=1,
            number_of_legs=1,
            track_home_away=False,
        )
        assert success, f"Session generation failed: {message}"
        assert len(sessions_created) > 0, "Expected at least one session to be created"

        # Assert: every auto-generated session has instructor_id IS NOT NULL
        test_db.expire_all()
        sessions = test_db.query(SessionModel).filter(
            SessionModel.semester_id == tournament_id,
            SessionModel.auto_generated == True,
        ).all()

        assert len(sessions) > 0, "No auto-generated sessions found after generation"

        null_instructor_sessions = [
            s.id for s in sessions if s.instructor_id is None
        ]
        assert null_instructor_sessions == [], (
            f"Found {len(null_instructor_sessions)} auto-generated sessions with "
            f"instructor_id=NULL: session ids = {null_instructor_sessions}"
        )
