"""
Captain Re-enrollment Integration Tests — RE-01 through RE-06

Proves that an existing team captain can enroll their team into a second
ENROLLMENT_OPEN TEAM tournament without recreating the team.

  RE-01  Captain enrolls team → TournamentTeamEnrollment created, credits deducted
  RE-02  Non-captain member tries to enroll → 403 (redirect with error)
  RE-03  Duplicate enrollment rejected → 400 (redirect with error)
  RE-04  Tournament not ENROLLMENT_OPEN → 400 (redirect with error)
  RE-05  Non-TEAM tournament rejected → 400 (redirect with error)
  RE-06  Insufficient credits → 402 (redirect with error), balance unchanged
"""
import uuid
import pytest
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy import event

from app.main import app
from app.database import engine, get_db
from app.dependencies import get_current_user_web, get_current_user, get_current_active_user
from app.models.user import User, UserRole
from app.models.license import UserLicense
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.tournament_configuration import TournamentConfiguration
from app.models.team import Team, TeamMember, TournamentTeamEnrollment
from app.models.tournament_enums import TeamMemberRole
from app.core.security import get_password_hash
from tests.factories.game_factory import TournamentFactory


# ── SAVEPOINT-isolated DB fixture ──────────────────────────────────────────────

@pytest.fixture(scope="function")
def test_db():
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


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _make_user(db: Session, role: UserRole = UserRole.STUDENT) -> User:
    u = User(
        email=f"re-test+{uuid.uuid4().hex[:8]}@lfa.com",
        name=f"Re-Enroll User {uuid.uuid4().hex[:4]}",
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


def _make_license(db: Session, user: User, *, credit_balance: int = 500) -> UserLicense:
    user.credit_balance = credit_balance
    lic = UserLicense(
        user_id=user.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        current_level=1,
        max_achieved_level=1,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        is_active=True,
        onboarding_completed=True,
        payment_verified=True,
        credit_balance=0,
    )
    db.add(lic)
    db.flush()
    return lic


def _make_team(db: Session, captain: User) -> Team:
    team = Team(
        name=f"Team {uuid.uuid4().hex[:6]}",
        code=f"RE-{uuid.uuid4().hex[:8].upper()}",
        captain_user_id=captain.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        is_active=True,
    )
    db.add(team)
    db.flush()
    # Add captain as member
    db.add(TeamMember(
        team_id=team.id,
        user_id=captain.id,
        role=TeamMemberRole.CAPTAIN.value,
        is_active=True,
    ))
    db.flush()
    return team


def _make_tournament(
    db: Session,
    *,
    status: str = "ENROLLMENT_OPEN",
    participant_type: str = "TEAM",
    cost: int = 50,
) -> Semester:
    tt = TournamentFactory.ensure_tournament_type(db, code=f"tt-re-{uuid.uuid4().hex[:6]}")
    t = Semester(
        code=f"RE-TOURN-{uuid.uuid4().hex[:8].upper()}",
        name=f"Re-Enroll Tournament {uuid.uuid4().hex[:4]}",
        semester_category=SemesterCategory.TOURNAMENT,
        status=SemesterStatus.ONGOING,
        tournament_status=status,
        age_group="YOUTH",
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 8),
        enrollment_cost=0,
        specialization_type="LFA_FOOTBALL_PLAYER",
    )
    db.add(t)
    db.flush()
    cfg = TournamentConfiguration(
        semester_id=t.id,
        tournament_type_id=tt.id,
        participant_type=participant_type,
        max_players=32,
        parallel_fields=1,
        sessions_generated=False,
        team_enrollment_cost=cost,
    )
    db.add(cfg)
    db.flush()
    return t


def _make_client(db: Session, user: User) -> TestClient:
    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user_web] = lambda: user
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    return TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})


# ══════════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestCaptainReenroll:

    def test_re_01_captain_enrolls_team_credits_deducted(self, test_db: Session):
        """RE-01: Captain with enough credits enrolls existing team → enrollment created, credits deducted."""
        captain = _make_user(test_db)
        lic = _make_license(test_db, captain, credit_balance=200)
        team = _make_team(test_db, captain)
        tournament = _make_tournament(test_db, cost=50)
        client = _make_client(test_db, captain)

        try:
            resp = client.post(
                f"/tournaments/{tournament.id}/teams/{team.id}/enroll",
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert f"/teams/{team.id}" in resp.headers["location"]
            assert "error" not in resp.headers["location"]

            # Verify enrollment created
            enrollment = test_db.query(TournamentTeamEnrollment).filter(
                TournamentTeamEnrollment.semester_id == tournament.id,
                TournamentTeamEnrollment.team_id == team.id,
                TournamentTeamEnrollment.is_active == True,
            ).first()
            assert enrollment is not None

            # Verify credit deducted
            test_db.refresh(captain)
            assert captain.credit_balance == 150  # 200 - 50
        finally:
            app.dependency_overrides.clear()

    def test_re_02_non_captain_cannot_enroll(self, test_db: Session):
        """RE-02: Non-captain team member cannot enroll the team → 303 redirect with error."""
        captain = _make_user(test_db)
        member = _make_user(test_db)
        _make_license(test_db, member, credit_balance=500)
        team = _make_team(test_db, captain)
        # Add member to team
        test_db.add(TeamMember(
            team_id=team.id,
            user_id=member.id,
            role=TeamMemberRole.PLAYER.value,
            is_active=True,
        ))
        test_db.flush()
        tournament = _make_tournament(test_db, cost=0)
        client = _make_client(test_db, member)

        try:
            resp = client.post(
                f"/tournaments/{tournament.id}/teams/{team.id}/enroll",
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "error" in resp.headers["location"]

            # No enrollment created
            enrollment = test_db.query(TournamentTeamEnrollment).filter(
                TournamentTeamEnrollment.semester_id == tournament.id,
                TournamentTeamEnrollment.team_id == team.id,
            ).first()
            assert enrollment is None
        finally:
            app.dependency_overrides.clear()

    def test_re_03_duplicate_enrollment_rejected(self, test_db: Session):
        """RE-03: Team already enrolled → 303 redirect with error, no duplicate row."""
        captain = _make_user(test_db)
        _make_license(test_db, captain, credit_balance=500)
        team = _make_team(test_db, captain)
        tournament = _make_tournament(test_db, cost=0)
        # Pre-enroll the team
        test_db.add(TournamentTeamEnrollment(
            semester_id=tournament.id,
            team_id=team.id,
            is_active=True,
            payment_verified=True,
        ))
        test_db.flush()
        client = _make_client(test_db, captain)

        try:
            resp = client.post(
                f"/tournaments/{tournament.id}/teams/{team.id}/enroll",
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "error" in resp.headers["location"]

            # Still only one enrollment
            count = test_db.query(TournamentTeamEnrollment).filter(
                TournamentTeamEnrollment.semester_id == tournament.id,
                TournamentTeamEnrollment.team_id == team.id,
                TournamentTeamEnrollment.is_active == True,
            ).count()
            assert count == 1
        finally:
            app.dependency_overrides.clear()

    def test_re_04_tournament_not_enrollment_open(self, test_db: Session):
        """RE-04: Tournament is DRAFT → 303 redirect with error."""
        captain = _make_user(test_db)
        _make_license(test_db, captain, credit_balance=500)
        team = _make_team(test_db, captain)
        tournament = _make_tournament(test_db, status="DRAFT", cost=0)
        client = _make_client(test_db, captain)

        try:
            resp = client.post(
                f"/tournaments/{tournament.id}/teams/{team.id}/enroll",
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "error" in resp.headers["location"]
        finally:
            app.dependency_overrides.clear()

    def test_re_05_non_team_tournament_rejected(self, test_db: Session):
        """RE-05: INDIVIDUAL tournament → 303 redirect with error."""
        captain = _make_user(test_db)
        _make_license(test_db, captain, credit_balance=500)
        team = _make_team(test_db, captain)
        tournament = _make_tournament(test_db, participant_type="INDIVIDUAL", cost=0)
        client = _make_client(test_db, captain)

        try:
            resp = client.post(
                f"/tournaments/{tournament.id}/teams/{team.id}/enroll",
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "error" in resp.headers["location"]
        finally:
            app.dependency_overrides.clear()

    def test_re_06_insufficient_credits(self, test_db: Session):
        """RE-06: Cost=200 but captain has only 50 credits → 303 redirect with error, balance unchanged."""
        captain = _make_user(test_db)
        lic = _make_license(test_db, captain, credit_balance=50)
        team = _make_team(test_db, captain)
        tournament = _make_tournament(test_db, cost=200)
        client = _make_client(test_db, captain)

        try:
            resp = client.post(
                f"/tournaments/{tournament.id}/teams/{team.id}/enroll",
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "error" in resp.headers["location"]

            # Balance unchanged
            test_db.refresh(lic)
            test_db.refresh(captain)
            assert captain.credit_balance == 50

            # No enrollment created
            enrollment = test_db.query(TournamentTeamEnrollment).filter(
                TournamentTeamEnrollment.semester_id == tournament.id,
                TournamentTeamEnrollment.team_id == team.id,
            ).first()
            assert enrollment is None
        finally:
            app.dependency_overrides.clear()
