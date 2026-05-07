"""
Team Business Flow Integration Tests — TEAM-10 through TEAM-16 + TEAM-11b

Proves the complete credit+invite team creation flow end-to-end:

  TEAM-10   POST /tournaments/{id}/team/create — captain has enough credits
            → team created, captain is member, credits deducted, CreditTransaction exists

  TEAM-11   POST /tournaments/{id}/team/create — insufficient credits → 402 page
            (form re-rendered with error, no team created, balance unchanged)

  TEAM-11b  Race simulation: two sequential requests from same captain →
            only first succeeds; final balance is correct (no double-spend)

  TEAM-12   POST /teams/{id}/invite (by captain) → TeamInvite PENDING

  TEAM-13   POST /teams/invites/{id}/accept (by invited user)
            → TeamMember PLAYER active, invite.status = ACCEPTED

  TEAM-14   POST /teams/invites/{id}/accept (by WRONG user) → 403

  TEAM-15   POST /teams/{id}/invite (by non-captain) → 403

  TEAM-16   Admin POST /admin/tournaments/{id}/teams/{tid}/members
            → direct member add, no invite required

DONE = pytest -v --tb=short, all TEAM-1X: PASSED
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
from app.models.semester import Semester, SemesterStatus, SemesterCategory  # noqa: F401
from app.models.tournament_configuration import TournamentConfiguration
from app.models.tournament_type import TournamentType
from app.models.credit_transaction import CreditTransaction
from app.models.team import Team, TeamMember, TeamInvite, TeamInviteStatus, TournamentTeamEnrollment
from app.core.security import get_password_hash
from app.models.location import Location, LocationType
from app.models.campus import Campus
from app.models.pitch import Pitch
from app.services.tournament import team_service
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


# ── User fixtures ──────────────────────────────────────────────────────────────

def _make_user(db: Session, role: UserRole = UserRole.STUDENT, *, credit_balance: int = 0) -> User:
    u = User(
        email=f"team-test+{uuid.uuid4().hex[:8]}@lfa.com",
        name=f"Team Test User {uuid.uuid4().hex[:4]}",
        password_hash=get_password_hash("Test1234!"),
        role=role,
        is_active=True,
        onboarding_completed=True,
        credit_balance=credit_balance,
        payment_verified=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_license(db: Session, user: User, *, credit_balance: int = 200) -> UserLicense:
    lic = UserLicense(
        user_id=user.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        current_level=1,
        max_achieved_level=1,
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        is_active=True,
        onboarding_completed=True,
        payment_verified=True,
        credit_balance=credit_balance,
    )
    db.add(lic)
    db.flush()
    return lic


@pytest.fixture
def captain_user(test_db: Session) -> User:
    return _make_user(test_db, credit_balance=0)


@pytest.fixture
def captain_license(test_db: Session, captain_user: User) -> UserLicense:
    return _make_license(test_db, captain_user, credit_balance=200)


@pytest.fixture
def other_user(test_db: Session) -> User:
    return _make_user(test_db, credit_balance=0)


@pytest.fixture
def other_license(test_db: Session, other_user: User) -> UserLicense:
    return _make_license(test_db, other_user, credit_balance=0)


@pytest.fixture
def admin_user(test_db: Session) -> User:
    return _make_user(test_db, role=UserRole.ADMIN)


# ── Tournament fixture (TEAM type, cost=100) ───────────────────────────────────

@pytest.fixture
def team_tournament(test_db: Session) -> Semester:
    tt = TournamentFactory.ensure_tournament_type(test_db, code=f"tt-team-{uuid.uuid4().hex[:6]}")

    t = Semester(
        code=f"TEAM-{uuid.uuid4().hex[:8].upper()}",
        name="Team Test Tournament",
        semester_category=SemesterCategory.TOURNAMENT,
        status=SemesterStatus.ONGOING,
        tournament_status="ENROLLMENT_OPEN",
        age_group="YOUTH",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 8),
        enrollment_cost=0,
        specialization_type="LFA_FOOTBALL_PLAYER",
    )
    test_db.add(t)
    test_db.flush()

    cfg = TournamentConfiguration(
        semester_id=t.id,
        tournament_type_id=tt.id,
        participant_type="TEAM",
        max_players=64,
        parallel_fields=1,
        sessions_generated=False,
        team_enrollment_cost=100,  # 100 credits to create a team
    )
    test_db.add(cfg)
    test_db.flush()

    return t


# ── Client fixtures ─────────────────────────────────────────────────────────────

def _make_client(test_db: Session, user: User) -> TestClient:
    def override_db():
        yield test_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user_web] = lambda: user
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user

    # Bearer header bypasses CSRF middleware (same pattern as test_tournament_lifecycle_e2e.py)
    client = TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})
    return client


@pytest.fixture
def captain_client(test_db: Session, captain_user: User, captain_license: UserLicense):
    """TestClient logged in as captain (must create license before client)."""
    c = _make_client(test_db, captain_user)
    yield c
    app.dependency_overrides.clear()


@pytest.fixture
def other_client(test_db: Session, other_user: User, other_license: UserLicense):
    c = _make_client(test_db, other_user)
    yield c
    app.dependency_overrides.clear()


@pytest.fixture
def admin_client_fixture(test_db: Session, admin_user: User):
    c = _make_client(test_db, admin_user)
    yield c
    app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════════

class TestTeamBusinessFlow:

    def test_team_10_create_team_deducts_credits(
        self,
        captain_client: TestClient,
        captain_user: User,
        captain_license: UserLicense,
        team_tournament: Semester,
        test_db: Session,
    ):
        """TEAM-10: Create team with enough credits → team created, credits deducted."""
        before_balance = captain_license.credit_balance  # 200
        cost = 100

        resp = captain_client.post(
            f"/tournaments/{team_tournament.id}/team/create",
            data={"name": "Dragon FC"},
            follow_redirects=False,
        )

        assert resp.status_code == 303, f"Expected redirect 303, got {resp.status_code}"
        assert "/teams/" in resp.headers["location"]

        test_db.expire_all()

        # Team created
        team = test_db.query(Team).filter(Team.captain_user_id == captain_user.id).first()
        assert team is not None, "Team must exist in DB"
        assert team.name == "Dragon FC"

        # Captain is an active member
        member = test_db.query(TeamMember).filter(
            TeamMember.team_id == team.id,
            TeamMember.user_id == captain_user.id,
            TeamMember.is_active == True,
        ).first()
        assert member is not None, "Captain must be active team member"
        assert member.role == "CAPTAIN"

        # Credit deducted
        lic_after = test_db.query(UserLicense).filter(UserLicense.id == captain_license.id).first()
        assert lic_after.credit_balance == before_balance - cost, (
            f"Expected balance {before_balance - cost}, got {lic_after.credit_balance}"
        )

        # CreditTransaction recorded
        ct = test_db.query(CreditTransaction).filter(
            CreditTransaction.user_license_id == captain_license.id,
            CreditTransaction.amount == -cost,
        ).first()
        assert ct is not None, "CreditTransaction must exist"
        assert ct.balance_after == before_balance - cost

    def test_team_11_insufficient_credits_blocked(
        self,
        captain_user: User,
        captain_license: UserLicense,
        team_tournament: Semester,
        test_db: Session,
    ):
        """TEAM-11: Create team with insufficient credits → 402, no team created, balance unchanged."""
        # Set balance too low
        captain_license.credit_balance = 50  # cost is 100
        test_db.flush()

        def override_db():
            yield test_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user_web] = lambda: captain_user
        app.dependency_overrides[get_current_user] = lambda: captain_user
        app.dependency_overrides[get_current_active_user] = lambda: captain_user

        try:
            client = TestClient(app, raise_server_exceptions=False, headers={"Authorization": "Bearer test-csrf-bypass"})
            resp = client.post(
                f"/tournaments/{team_tournament.id}/team/create",
                data={"name": "Broke FC"},
                follow_redirects=False,
            )

            # Form re-rendered with 402 error
            assert resp.status_code == 402, f"Expected 402, got {resp.status_code}"

            test_db.expire_all()

            # No team created
            team = test_db.query(Team).filter(
                Team.captain_user_id == captain_user.id,
                Team.is_active == True,
            ).first()
            assert team is None, "No team should be created on 402"

            # Balance unchanged
            lic_after = test_db.query(UserLicense).filter(UserLicense.id == captain_license.id).first()
            assert lic_after.credit_balance == 50, "Balance must be unchanged on failure"
        finally:
            app.dependency_overrides.clear()

    def test_team_11b_race_simulation_no_double_spend(
        self,
        captain_user: User,
        captain_license: UserLicense,
        team_tournament: Semester,
        test_db: Session,
    ):
        """TEAM-11b: Sequential race — two create requests, only first succeeds, balance correct."""
        captain_license.credit_balance = 100  # exact cost — second must fail
        test_db.flush()

        def override_db():
            yield test_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user_web] = lambda: captain_user
        app.dependency_overrides[get_current_user] = lambda: captain_user
        app.dependency_overrides[get_current_active_user] = lambda: captain_user

        try:
            client = TestClient(app, raise_server_exceptions=False, headers={"Authorization": "Bearer test-csrf-bypass"})

            # First request — must succeed
            resp1 = client.post(
                f"/tournaments/{team_tournament.id}/team/create",
                data={"name": "Race FC"},
                follow_redirects=False,
            )
            assert resp1.status_code == 303, f"First request must succeed: {resp1.status_code}"

            test_db.expire_all()

            lic_mid = test_db.query(UserLicense).filter(UserLicense.id == captain_license.id).first()
            assert lic_mid.credit_balance == 0, "Balance must be 0 after first request"

            # Second request — must fail (balance = 0)
            resp2 = client.post(
                f"/tournaments/{team_tournament.id}/team/create",
                data={"name": "Race FC 2"},
                follow_redirects=False,
            )
            assert resp2.status_code == 402, f"Second request must fail with 402: {resp2.status_code}"

            test_db.expire_all()

            # Final balance still 0, not negative
            lic_after = test_db.query(UserLicense).filter(UserLicense.id == captain_license.id).first()
            assert lic_after.credit_balance == 0, (
                f"Balance must remain 0 after failed second request; got {lic_after.credit_balance}"
            )

            # Exactly one CreditTransaction
            cts = test_db.query(CreditTransaction).filter(
                CreditTransaction.user_license_id == captain_license.id,
                CreditTransaction.amount < 0,
            ).all()
            assert len(cts) == 1, f"Must have exactly 1 deduction transaction; got {len(cts)}"
        finally:
            app.dependency_overrides.clear()

    def test_team_12_captain_can_invite(
        self,
        captain_client: TestClient,
        captain_user: User,
        captain_license: UserLicense,
        other_user: User,
        team_tournament: Semester,
        test_db: Session,
    ):
        """TEAM-12: Captain invites player → TeamInvite PENDING in DB."""
        # Create team first
        resp = captain_client.post(
            f"/tournaments/{team_tournament.id}/team/create",
            data={"name": "Invite FC"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        test_db.expire_all()

        team = test_db.query(Team).filter(
            Team.captain_user_id == captain_user.id,
            Team.is_active == True,
        ).first()
        assert team is not None

        # Captain invites the other user
        resp2 = captain_client.post(
            f"/teams/{team.id}/invite",
            data={"invited_user_id": other_user.id},
            follow_redirects=False,
        )
        assert resp2.status_code == 303

        test_db.expire_all()

        invite = test_db.query(TeamInvite).filter(
            TeamInvite.team_id == team.id,
            TeamInvite.invited_user_id == other_user.id,
            TeamInvite.status == TeamInviteStatus.PENDING.value,
        ).first()
        assert invite is not None, "TeamInvite PENDING must exist"
        assert invite.invited_by_id == captain_user.id

    def test_team_13_invited_user_accepts(
        self,
        captain_user: User,
        captain_license: UserLicense,
        other_user: User,
        other_license: UserLicense,
        team_tournament: Semester,
        test_db: Session,
    ):
        """TEAM-13: Invited user accepts → TeamMember PLAYER active, invite.status = ACCEPTED.

        Setup via service layer to avoid fixture-collision in app.dependency_overrides.
        HTTP is tested for the 'accept' action only.
        """
        # Setup via service layer
        team = team_service.create_team_with_cost(
            db=test_db,
            name="Accept FC",
            captain_user_id=captain_user.id,
            specialization_type="TEAM",
            tournament_id=team_tournament.id,
        )
        invite = team_service.invite_member(
            db=test_db,
            team_id=team.id,
            invited_user_id=other_user.id,
            invited_by_id=captain_user.id,
        )
        test_db.expire_all()

        assert invite is not None
        assert invite.status == TeamInviteStatus.PENDING.value

        # Test: other_user accepts via HTTP
        def override_db():
            yield test_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user_web] = lambda: other_user
        app.dependency_overrides[get_current_user] = lambda: other_user
        app.dependency_overrides[get_current_active_user] = lambda: other_user

        try:
            c = TestClient(app, raise_server_exceptions=False, headers={"Authorization": "Bearer test-csrf-bypass"})
            resp_accept = c.post(
                f"/teams/invites/{invite.id}/accept",
                follow_redirects=False,
            )
            assert resp_accept.status_code == 303, f"Expected 303, got {resp_accept.status_code}"
        finally:
            app.dependency_overrides.clear()

        test_db.expire_all()

        # Invite status = ACCEPTED
        invite_after = test_db.query(TeamInvite).filter(TeamInvite.id == invite.id).first()
        assert invite_after.status == TeamInviteStatus.ACCEPTED.value
        assert invite_after.responded_at is not None

        # other_user is now an active PLAYER
        member = test_db.query(TeamMember).filter(
            TeamMember.team_id == team.id,
            TeamMember.user_id == other_user.id,
            TeamMember.is_active == True,
        ).first()
        assert member is not None, "Other user must be active team member after acceptance"
        assert member.role == "PLAYER"

    def test_team_14_wrong_user_cannot_accept(
        self,
        captain_user: User,
        captain_license: UserLicense,
        other_user: User,
        other_license: UserLicense,
        team_tournament: Semester,
        test_db: Session,
    ):
        """TEAM-14: Wrong user tries to accept invite → error redirect, invite stays PENDING."""
        third_user = _make_user(test_db, credit_balance=0)
        _make_license(test_db, third_user, credit_balance=0)

        # Setup via service layer
        team = team_service.create_team_with_cost(
            db=test_db,
            name="Wrong FC",
            captain_user_id=captain_user.id,
            specialization_type="TEAM",
            tournament_id=team_tournament.id,
        )
        invite = team_service.invite_member(
            db=test_db,
            team_id=team.id,
            invited_user_id=other_user.id,
            invited_by_id=captain_user.id,
        )
        test_db.expire_all()

        # third_user (NOT the invitee) tries to accept
        def override_db():
            yield test_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user_web] = lambda: third_user
        app.dependency_overrides[get_current_user] = lambda: third_user
        app.dependency_overrides[get_current_active_user] = lambda: third_user

        try:
            c3 = TestClient(app, raise_server_exceptions=False, headers={"Authorization": "Bearer test-csrf-bypass"})
            resp = c3.post(
                f"/teams/invites/{invite.id}/accept",
                follow_redirects=False,
            )
            # Either 403 directly, or redirect with error
            assert resp.status_code in (302, 303, 403), f"Unexpected status {resp.status_code}"
            if resp.status_code in (302, 303):
                assert "error" in resp.headers.get("location", ""), (
                    "Redirect after wrong-user accept must include error param"
                )
        finally:
            app.dependency_overrides.clear()

        test_db.expire_all()

        # Invite still PENDING
        invite_after = test_db.query(TeamInvite).filter(TeamInvite.id == invite.id).first()
        assert invite_after.status == TeamInviteStatus.PENDING.value, (
            "Invite must remain PENDING after wrong-user attempt"
        )

    def test_team_15_non_captain_cannot_invite(
        self,
        captain_user: User,
        captain_license: UserLicense,
        other_user: User,
        other_license: UserLicense,
        team_tournament: Semester,
        test_db: Session,
    ):
        """TEAM-15: Non-captain tries to invite → redirect with error, no invite created."""
        # Setup via service layer
        team = team_service.create_team_with_cost(
            db=test_db,
            name="Guard FC",
            captain_user_id=captain_user.id,
            specialization_type="TEAM",
            tournament_id=team_tournament.id,
        )
        test_db.expire_all()

        # A third user (non-captain, non-member) tries to invite other_user
        third_user = _make_user(test_db, credit_balance=0)
        _make_license(test_db, third_user, credit_balance=0)

        def override_db():
            yield test_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user_web] = lambda: third_user
        app.dependency_overrides[get_current_user] = lambda: third_user
        app.dependency_overrides[get_current_active_user] = lambda: third_user

        try:
            c3 = TestClient(app, raise_server_exceptions=False, headers={"Authorization": "Bearer test-csrf-bypass"})
            resp = c3.post(
                f"/teams/{team.id}/invite",
                data={"invited_user_id": other_user.id},
                follow_redirects=False,
            )
            assert resp.status_code in (302, 303, 403), f"Unexpected status {resp.status_code}"
        finally:
            app.dependency_overrides.clear()

        test_db.expire_all()

        # No invite created
        invite = test_db.query(TeamInvite).filter(
            TeamInvite.team_id == team.id,
            TeamInvite.invited_user_id == other_user.id,
        ).first()
        assert invite is None, "No invite should be created by non-captain"

    def test_team_16_admin_can_add_member_directly(
        self,
        captain_user: User,
        captain_license: UserLicense,
        other_user: User,
        admin_user: User,
        team_tournament: Semester,
        test_db: Session,
    ):
        """TEAM-16: Admin directly adds member bypassing invite flow."""
        # Setup via service layer
        team = team_service.create_team_with_cost(
            db=test_db,
            name="Admin FC",
            captain_user_id=captain_user.id,
            specialization_type="TEAM",
            tournament_id=team_tournament.id,
        )
        test_db.expire_all()

        # Admin adds other_user directly via HTTP
        def override_db():
            yield test_db

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user_web] = lambda: admin_user
        app.dependency_overrides[get_current_user] = lambda: admin_user
        app.dependency_overrides[get_current_active_user] = lambda: admin_user

        try:
            c_admin = TestClient(app, raise_server_exceptions=False, headers={"Authorization": "Bearer test-csrf-bypass"})
            resp = c_admin.post(
                f"/admin/tournaments/{team_tournament.id}/teams/{team.id}/members",
                data={"user_id": other_user.id},
                follow_redirects=False,
            )
            assert resp.status_code == 303, f"Admin add member must redirect, got {resp.status_code}"
        finally:
            app.dependency_overrides.clear()

        test_db.expire_all()

        # other_user is now active member — no invite required
        member = test_db.query(TeamMember).filter(
            TeamMember.team_id == team.id,
            TeamMember.user_id == other_user.id,
            TeamMember.is_active == True,
        ).first()
        assert member is not None, "Admin must add member directly without invite"

        # No TeamInvite was created
        invite = test_db.query(TeamInvite).filter(
            TeamInvite.team_id == team.id,
            TeamInvite.invited_user_id == other_user.id,
        ).first()
        assert invite is None, "Admin direct-add must not create TeamInvite"


# ══════════════════════════════════════════════════════════════════════════════
# TGENV — GenerationValidator TEAM participant_type branch
# ══════════════════════════════════════════════════════════════════════════════

class TestGenerationValidatorTeam:
    """
    TGENV-01: TEAM tournament with enrolled teams → validator counts
              TournamentTeamEnrollment rows (not SemesterEnrollment).
    TGENV-02: TEAM tournament with NO enrolled teams → validator rejects.
    TGENV-03: INDIVIDUAL tournament with enrolled players → existing path unchanged.
    """

    def _team_tournament_in_progress(self, db: Session) -> Semester:
        """Create a TEAM HEAD_TO_HEAD tournament in IN_PROGRESS status."""
        from tests.factories.game_factory import TournamentFactory
        tt = TournamentFactory.ensure_tournament_type(db, code=f"tt-genv-{uuid.uuid4().hex[:6]}")

        uid = uuid.uuid4().hex[:8]
        loc = Location(
            name=f"GENV Location {uid}",
            city=f"GENVCity-{uid}",
            country="HU",
            is_active=True,
            location_type=LocationType.CENTER,
        )
        db.add(loc)
        db.flush()
        camp = Campus(location_id=loc.id, name=f"GENV Campus {uid}", is_active=True)
        db.add(camp)
        db.flush()
        # Session generation requires ≥1 active pitch on the campus (domain invariant)
        db.add(Pitch(campus_id=camp.id, pitch_number=1, name="Pálya A", capacity=22, is_active=True))
        db.flush()

        # Session generation requires instructor assignment (domain invariant: no
        # auto-generated session may have instructor_id=NULL).
        instructor = User(
            email=f"genv-instructor-{uuid.uuid4().hex[:8]}@lfa.com",
            name="GENV Instructor",
            password_hash=get_password_hash("pw"),
            role=UserRole.INSTRUCTOR,
            is_active=True,
        )
        db.add(instructor)
        db.flush()

        t = Semester(
            code=f"GENV-{uuid.uuid4().hex[:8].upper()}",
            name="GenVal TEAM Tournament",
            semester_category=SemesterCategory.TOURNAMENT,
            status=SemesterStatus.ONGOING,
            tournament_status="IN_PROGRESS",
            age_group="YOUTH",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 8),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
            campus_id=camp.id,
            master_instructor_id=instructor.id,
        )
        db.add(t)
        db.flush()

        cfg = TournamentConfiguration(
            semester_id=t.id,
            tournament_type_id=tt.id,
            participant_type="TEAM",
            max_players=64,
            parallel_fields=1,
            sessions_generated=False,
            team_enrollment_cost=0,
        )
        db.add(cfg)
        db.flush()
        return t

    def _make_team_with_member(self, db: Session) -> Team:
        """Create a team with one active member."""
        user = _make_user(db, credit_balance=0)
        team = Team(
            name=f"Team {uuid.uuid4().hex[:6]}",
            specialization_type="LFA_FOOTBALL_PLAYER",
            is_active=True,
            captain_user_id=user.id,
        )
        db.add(team)
        db.flush()
        member = TeamMember(team_id=team.id, user_id=user.id, role="PLAYER", is_active=True)
        db.add(member)
        db.flush()
        return team

    def test_TGENV_01_team_tournament_counts_team_enrollments(self, test_db: Session):
        """Two enrolled teams → validator passes (IN_PROGRESS)."""
        from app.services.tournament.session_generation.validators.generation_validator import GenerationValidator

        t = self._team_tournament_in_progress(test_db)
        t1 = self._make_team_with_member(test_db)
        t2 = self._make_team_with_member(test_db)

        test_db.add(TournamentTeamEnrollment(semester_id=t.id, team_id=t1.id, is_active=True, payment_verified=True))
        test_db.add(TournamentTeamEnrollment(semester_id=t.id, team_id=t2.id, is_active=True, payment_verified=True))
        test_db.flush()

        validator = GenerationValidator(test_db)
        can, reason = validator.can_generate_sessions(t.id)

        assert can is True, f"Expected True, got False: {reason}"

    def test_TGENV_02_team_tournament_no_teams_rejected(self, test_db: Session):
        """Zero enrolled teams → validator rejects with clear error."""
        from app.services.tournament.session_generation.validators.generation_validator import GenerationValidator

        t = self._team_tournament_in_progress(test_db)

        validator = GenerationValidator(test_db)
        can, reason = validator.can_generate_sessions(t.id)

        assert can is False
        assert "teams enrolled" in reason.lower(), f"Expected 'teams enrolled' in error: {reason}"

    def test_TGENV_03_individual_tournament_path_unchanged(self, test_db: Session):
        """INDIVIDUAL tournament: validator still uses SemesterEnrollment (regression guard)."""
        from app.services.tournament.session_generation.validators.generation_validator import GenerationValidator
        from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus

        # Instructor required — instructor guard fires before enrollment count check.
        instructor = User(
            email=f"genv-ind-instr-{uuid.uuid4().hex[:8]}@lfa.com",
            name="GENV IND Instructor",
            password_hash=get_password_hash("pw"),
            role=UserRole.INSTRUCTOR,
            is_active=True,
        )
        test_db.add(instructor)
        test_db.flush()

        t = Semester(
            code=f"GENV-IND-{uuid.uuid4().hex[:8].upper()}",
            name="GenVal IND Tournament",
            semester_category=SemesterCategory.TOURNAMENT,
            status=SemesterStatus.ONGOING,
            tournament_status="IN_PROGRESS",
            age_group="ADULT",
            start_date=date(2026, 4, 1),
            end_date=date(2026, 4, 8),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
            master_instructor_id=instructor.id,
        )
        test_db.add(t)
        test_db.flush()

        cfg = TournamentConfiguration(
            semester_id=t.id,
            participant_type="INDIVIDUAL",
            max_players=64,
            parallel_fields=1,
            sessions_generated=False,
        )
        test_db.add(cfg)
        test_db.flush()

        # No SemesterEnrollment rows → must reject
        validator = GenerationValidator(test_db)
        can, reason = validator.can_generate_sessions(t.id)
        assert can is False
        assert "players enrolled" in reason.lower(), f"Expected 'players enrolled' in error: {reason}"


# ══════════════════════════════════════════════════════════════════════════════
# TGUARD — Enrollment guard for empty teams
# ══════════════════════════════════════════════════════════════════════════════

class TestEnrollmentGuardEmptyTeam:
    """
    TGUARD-01: enroll_existing_team_in_tournament with 0 active members → HTTP 400.
    TGUARD-02: enroll_existing_team_in_tournament with 1+ active members → succeeds.
    """

    def _open_team_tournament(self, db: Session) -> Semester:
        """Create a TEAM tournament in ENROLLMENT_OPEN status."""
        from tests.factories.game_factory import TournamentFactory
        tt = TournamentFactory.ensure_tournament_type(db, code=f"tt-guard-{uuid.uuid4().hex[:6]}")

        t = Semester(
            code=f"GUARD-{uuid.uuid4().hex[:8].upper()}",
            name="Guard Test Tournament",
            semester_category=SemesterCategory.TOURNAMENT,
            status=SemesterStatus.ONGOING,
            tournament_status="ENROLLMENT_OPEN",
            age_group="YOUTH",
            start_date=date(2026, 5, 1),
            end_date=date(2026, 5, 8),
            enrollment_cost=0,
            specialization_type="LFA_FOOTBALL_PLAYER",
        )
        db.add(t)
        db.flush()

        cfg = TournamentConfiguration(
            semester_id=t.id,
            tournament_type_id=tt.id,
            participant_type="TEAM",
            max_players=64,
            parallel_fields=1,
            sessions_generated=False,
            team_enrollment_cost=0,
        )
        db.add(cfg)
        db.flush()
        return t

    def test_TGUARD_01_empty_team_enrollment_rejected(self, test_db: Session):
        """Team with 0 active members cannot enroll → HTTP 400."""
        from fastapi import HTTPException

        captain = _make_user(test_db, credit_balance=500)
        empty_team = Team(
            name=f"Empty FC {uuid.uuid4().hex[:4]}",
            specialization_type="LFA_FOOTBALL_PLAYER",
            is_active=True,
            captain_user_id=captain.id,
        )
        test_db.add(empty_team)
        test_db.flush()
        # Intentionally no TeamMember rows for empty_team

        tournament = self._open_team_tournament(test_db)

        with pytest.raises(HTTPException) as exc_info:
            team_service.enroll_existing_team_in_tournament(
                db=test_db,
                team_id=empty_team.id,
                captain_user_id=captain.id,
                tournament_id=tournament.id,
            )

        assert exc_info.value.status_code == 400
        assert "no active players" in exc_info.value.detail.lower()

    def test_TGUARD_02_team_with_members_can_enroll(self, test_db: Session):
        """Team with at least one active member → enrollment succeeds."""
        captain = _make_user(test_db, credit_balance=500)
        team = Team(
            name=f"Full FC {uuid.uuid4().hex[:4]}",
            specialization_type="LFA_FOOTBALL_PLAYER",
            is_active=True,
            captain_user_id=captain.id,
        )
        test_db.add(team)
        test_db.flush()
        test_db.add(TeamMember(team_id=team.id, user_id=captain.id, role="CAPTAIN", is_active=True))
        test_db.flush()

        tournament = self._open_team_tournament(test_db)

        enrollment = team_service.enroll_existing_team_in_tournament(
            db=test_db,
            team_id=team.id,
            captain_user_id=captain.id,
            tournament_id=tournament.id,
        )

        assert enrollment is not None
        assert enrollment.team_id == team.id
        assert enrollment.semester_id == tournament.id
        assert enrollment.is_active is True
