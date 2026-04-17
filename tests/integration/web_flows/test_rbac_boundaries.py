"""
Role-Based Access Control (RBAC) Boundary Tests — RBAC-01..03
=============================================================

Closes the Role × Flow matrix gaps identified in the Phase 5.5 audit:

  RBAC-01  STUDENT cannot access /admin/* (real E2E: inline _admin_guard fires)
  RBAC-02  ADMIN cannot enroll in a semester (programs.py role check)
  RBAC-03  SD team enrollment is reflected in student-visible public event page

Matrix cells closed by these tests:

  | Role           | Forbidden Path | Cross-Role       |
  |----------------|----------------|------------------|
  | STUDENT        | RBAC-01 ✅     | existing ✅      |
  | ADMIN          | RBAC-02 ✅     | existing ✅      |
  | INSTRUCTOR     | G3-11 ✅       | existing ✅      |
  | SPORT_DIRECTOR | SD-05 ✅       | RBAC-03 ✅       |
"""
import uuid
import pytest
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import event

from app.main import app
from app.database import engine, get_db
from app.dependencies import get_current_user_web, get_current_sport_director_user_web
from app.models.campus import Campus
from app.models.instructor_assignment import SportDirectorAssignment
from app.models.location import Location, LocationType
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.team import Team, TeamMember, TournamentTeamEnrollment
from app.models.tournament_enums import TeamMemberRole
from app.models.tournament_configuration import TournamentConfiguration
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from tests.factories.game_factory import TournamentFactory


# ── SAVEPOINT-isolated DB fixture ─────────────────────────────────────────────

@pytest.fixture(scope="function")
def test_db():
    """PostgreSQL session with per-test SAVEPOINT isolation."""
    connection = engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = TestSession()
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

def _make_user(db: Session, role: UserRole = UserRole.STUDENT) -> User:
    u = User(
        email=f"rbac-{uuid.uuid4().hex[:8]}@lfa.com",
        name=f"RBAC {role.value} {uuid.uuid4().hex[:4]}",
        password_hash=get_password_hash("Test1234!"),
        role=role,
        is_active=True,
        onboarding_completed=True,
        credit_balance=0,
        payment_verified=True,
    )
    db.add(u)
    db.flush()
    return u


def _db_override(db: Session):
    def _inner():
        yield db
    return _inner


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestRBACBoundaries:

    def test_rbac_01_student_forbidden_from_admin_ui(self, test_db: Session):
        """RBAC-01: STUDENT hitting /admin/users → 403.

        Admin web routes use Depends(get_current_user_web) + inline _admin_guard().
        Overriding get_current_user_web → student causes _admin_guard(student)
        to raise HTTPException(403).  This is a real E2E guard test — the
        guard itself is NOT mocked.

        Closes: STUDENT forbidden-path matrix cell.
        """
        student = _make_user(test_db, role=UserRole.STUDENT)

        app.dependency_overrides[get_db] = _db_override(test_db)
        app.dependency_overrides[get_current_user_web] = lambda: student
        client = TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})

        try:
            resp = client.get("/admin/users")
            assert resp.status_code == 403, (
                f"RBAC-01: expected 403 for student on /admin/users, got {resp.status_code}"
            )
        finally:
            app.dependency_overrides.clear()

    def test_rbac_02_admin_cannot_enroll_in_semester(self, test_db: Session):
        """RBAC-02: ADMIN user POST /semesters/request-enrollment → 'Student+role+required'.

        programs.py:170 checks user.role != UserRole.STUDENT before processing
        enrollment.  G3-11 already covers INSTRUCTOR; this test closes the
        ADMIN forbidden-path matrix cell.

        Asserts:
          - 303 redirect
          - 'Student+role+required' in Location header
        """
        admin = _make_user(test_db, role=UserRole.ADMIN)

        # Semester must be MINI_SEASON category + ONGOING status so that the
        # role check at line 170 is reached (checks happen in order: id lookup
        # → category → status → role).
        sem = Semester(
            code=f"RBAC-SEM-{uuid.uuid4().hex[:8].upper()}",
            name="RBAC Admin-Enroll Test Semester",
            semester_category=SemesterCategory.MINI_SEASON,
            status=SemesterStatus.ONGOING,
            start_date=date(2027, 1, 1),
            end_date=date(2027, 6, 30),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        test_db.add(sem)
        test_db.flush()

        app.dependency_overrides[get_db] = _db_override(test_db)
        app.dependency_overrides[get_current_user_web] = lambda: admin
        client = TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})

        try:
            resp = client.post(
                "/semesters/request-enrollment",
                data={"semester_id": str(sem.id)},
                follow_redirects=False,
            )
            assert resp.status_code == 303, (
                f"RBAC-02: expected 303, got {resp.status_code}"
            )
            assert "Student+role+required" in resp.headers["location"], (
                f"RBAC-02: expected 'Student+role+required' in location, "
                f"got {resp.headers['location']}"
            )
        finally:
            app.dependency_overrides.clear()

    def test_rbac_03_sd_enrollment_reflected_for_student(self, test_db: Session):
        """RBAC-03: SD enrolls team → student-visible public event page shows the team.

        Cross-role state consistency:
          1. SD user POSTs /sport-director/tournaments/{id}/teams/{team_id}/enroll
          2. Student (anonymous) GETs /events/{id} and observes the enrolled team

        GET /events/{id} has no auth dependency — it is the public-facing event
        page.  The enrolled_count and participants list (team names) are rendered
        by the template from the DB state.

        Closes: SPORT_DIRECTOR cross-role matrix cell.
        """
        sd = _make_user(test_db, role=UserRole.SPORT_DIRECTOR)
        captain = _make_user(test_db, role=UserRole.STUDENT)

        # Location + campus + SD assignment
        loc = Location(
            name=f"RBAC Loc {uuid.uuid4().hex[:4]}",
            city=f"RBAC City {uuid.uuid4().hex[:8]}",
            country="HU",
            location_type=LocationType.CENTER,
        )
        test_db.add(loc)
        test_db.flush()

        campus = Campus(
            location_id=loc.id,
            name=f"RBAC Campus {uuid.uuid4().hex[:4]}",
            is_active=True,
        )
        test_db.add(campus)
        test_db.flush()

        test_db.add(SportDirectorAssignment(
            user_id=sd.id,
            location_id=loc.id,
            is_active=True,
        ))
        test_db.flush()

        # Tournament at SD's location (ENROLLMENT_OPEN, TEAM participant_type)
        tt = TournamentFactory.ensure_tournament_type(
            test_db, code=f"tt-rbac-{uuid.uuid4().hex[:6]}"
        )
        tournament = Semester(
            code=f"RBAC-TOURN-{uuid.uuid4().hex[:8].upper()}",
            name=f"RBAC Tournament {uuid.uuid4().hex[:4]}",
            semester_category=SemesterCategory.TOURNAMENT,
            status=SemesterStatus.ONGOING,
            tournament_status="ENROLLMENT_OPEN",
            age_group="YOUTH",
            start_date=date(2026, 7, 1),
            end_date=date(2026, 7, 8),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
            campus_id=campus.id,
        )
        test_db.add(tournament)
        test_db.flush()
        test_db.add(TournamentConfiguration(
            semester_id=tournament.id,
            tournament_type_id=tt.id,
            participant_type="TEAM",
            max_players=32,
            parallel_fields=1,
            sessions_generated=False,
            team_enrollment_cost=0,
        ))
        test_db.flush()

        # Team with captain
        team_name = f"RBAC Team {uuid.uuid4().hex[:6]}"
        team = Team(
            name=team_name,
            code=f"RBAC-{uuid.uuid4().hex[:8].upper()}",
            captain_user_id=captain.id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            is_active=True,
        )
        test_db.add(team)
        test_db.flush()
        test_db.add(TeamMember(
            team_id=team.id,
            user_id=captain.id,
            role=TeamMemberRole.CAPTAIN.value,
            is_active=True,
        ))
        test_db.flush()

        app.dependency_overrides[get_db] = _db_override(test_db)
        app.dependency_overrides[get_current_sport_director_user_web] = lambda: sd
        client = TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})

        try:
            # 1. SD action: enroll the team
            enroll_resp = client.post(
                f"/sport-director/tournaments/{tournament.id}/teams/{team.id}/enroll",
                follow_redirects=False,
            )
            assert enroll_resp.status_code == 303, (
                f"RBAC-03: SD enroll expected 303, got {enroll_resp.status_code}"
            )
            assert "error" not in enroll_resp.headers.get("location", ""), (
                f"RBAC-03: SD enroll returned error: {enroll_resp.headers.get('location')}"
            )

            # 2. Student view: public event page reflects SD's enrollment.
            # GET /events/{id} has no auth dependency — db override is sufficient.
            student_resp = client.get(f"/events/{tournament.id}")
            assert student_resp.status_code == 200, (
                f"RBAC-03: student GET /events/{tournament.id} expected 200, "
                f"got {student_resp.status_code}"
            )
            assert team_name in student_resp.text, (
                f"RBAC-03: team '{team_name}' not visible in public event page "
                f"after SD enrollment — cross-role state consistency broken"
            )
        finally:
            app.dependency_overrides.clear()
