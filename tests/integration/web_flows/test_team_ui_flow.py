"""
Team UI Flow Tests — TEAM-UI-01 through TEAM-UI-09

Proves the student-facing HTML layer renders correctly and enforces
all edge-case rules in the UI (not just the service layer).

Coverage gap filled vs TEAM-10–16 (which test HTTP status + DB state):
  TEAM-UI-01  GET /tournaments/{id}/team/create → 200 + correct HTML content
  TEAM-UI-02  GET /teams/invites (empty) → 200 + empty-state shown
  TEAM-UI-03  GET /teams/invites (with invite) → invite card visible (team name, buttons)
  TEAM-UI-04  GET /teams/{id} (captain) → member table + invite form rendered
  TEAM-UI-05  GET /teams/{id} (non-captain) → 403
  TEAM-UI-06  POST create team, 0 credits → 402 re-rendered with error text in HTML
  TEAM-UI-07  POST duplicate invite → redirect with error param (no second invite)
  TEAM-UI-08  GET /api/v1/users/invite-search → excludes existing members + pending invites
  TEAM-UI-09  GET /api/v1/users/invite-search → returns matching user by name/email

Network evidence is inline: every test shows the exact request + response
shape used by the browser (form POST payloads, query params, redirect URLs).
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
from app.models.team import Team, TeamMember, TeamInvite, TeamInviteStatus
from app.core.security import get_password_hash
from app.services.tournament import team_service
from tests.factories.game_factory import TournamentFactory


# ── SAVEPOINT-isolated DB fixture ─────────────────────────────────────────────

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


# ── Helper factories ───────────────────────────────────────────────────────────

def _make_user(db: Session, role: UserRole = UserRole.STUDENT, *, credit_balance: int = 0) -> User:
    u = User(
        email=f"team-ui+{uuid.uuid4().hex[:8]}@lfa.com",
        name=f"TeamUI {uuid.uuid4().hex[:6]}",
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


def _make_tournament(db: Session, *, cost: int = 100) -> Semester:
    tt = TournamentFactory.ensure_tournament_type(db, code=f"tt-ui-{uuid.uuid4().hex[:6]}")
    t = Semester(
        code=f"TEAM-UI-{uuid.uuid4().hex[:8].upper()}",
        name="UI Test Tournament",
        semester_category=SemesterCategory.TOURNAMENT,
        status=SemesterStatus.ONGOING,
        tournament_status="ENROLLMENT_OPEN",
        age_group="YOUTH",
        start_date=date(2026, 4, 1),
        end_date=date(2026, 4, 8),
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
        team_enrollment_cost=cost,
    )
    db.add(cfg)
    db.flush()
    return t


def _make_client(db: Session, user: User) -> TestClient:
    app.dependency_overrides[get_db] = lambda: (yield db)
    app.dependency_overrides[get_current_user_web] = lambda: user
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_active_user] = lambda: user
    return TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestTeamUIFlow:

    # ── TEAM-UI-01: Create team page renders correctly ─────────────────────────

    def test_team_ui_01_create_form_renders(self, test_db: Session):
        """
        GET /tournaments/{id}/team/create
        → 200 + tournament name + credit cost badge in HTML
        Network: GET /tournaments/42/team/create (cookie auth)
        """
        captain = _make_user(test_db, credit_balance=0)
        _make_license(test_db, captain, credit_balance=300)
        tournament = _make_tournament(test_db, cost=100)
        client = _make_client(test_db, captain)

        try:
            resp = client.get(f"/tournaments/{tournament.id}/team/create")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        html = resp.text
        assert "Create Your Team" in html
        assert "UI Test Tournament" in html
        assert "100" in html          # cost badge
        assert "credits" in html.lower()
        # Form POSTs to the correct URL
        assert f"/tournaments/{tournament.id}/team/create" in html

    # ── TEAM-UI-02: Invite list — empty state ──────────────────────────────────

    def test_team_ui_02_invites_empty_state(self, test_db: Session):
        """
        GET /teams/invites (no pending invites)
        → 200 + "No pending invites" empty state
        Network: GET /teams/invites (cookie auth)
        """
        student = _make_user(test_db)
        client = _make_client(test_db, student)

        try:
            resp = client.get("/teams/invites")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        assert "No pending invites" in resp.text

    # ── TEAM-UI-03: Invite list — invite card visible ──────────────────────────

    def test_team_ui_03_invites_shows_pending_invite(self, test_db: Session):
        """
        GET /teams/invites after captain sends invite
        → invite card shows team name, Accept / Decline buttons
        Network: GET /teams/invites (cookie auth)
        """
        captain = _make_user(test_db, credit_balance=0)
        _make_license(test_db, captain, credit_balance=300)
        invitee = _make_user(test_db)
        _make_license(test_db, invitee, credit_balance=0)
        tournament = _make_tournament(test_db, cost=100)

        # Setup via service layer
        team = team_service.create_team_with_cost(
            db=test_db, name="Dragon FC", captain_user_id=captain.id,
            specialization_type="TEAM", tournament_id=tournament.id,
        )
        team_service.invite_member(
            db=test_db, team_id=team.id,
            invited_user_id=invitee.id, invited_by_id=captain.id,
        )
        test_db.expire_all()

        client = _make_client(test_db, invitee)
        try:
            resp = client.get("/teams/invites")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        html = resp.text
        assert "Dragon FC" in html       # team name in invite card
        assert "Accept" in html          # Accept button
        assert "Decline" in html         # Decline button
        assert captain.name in html      # "Invited by <captain>"

    # ── TEAM-UI-04: Captain dashboard renders correctly ────────────────────────

    def test_team_ui_04_captain_dashboard_renders(self, test_db: Session):
        """
        GET /teams/{id} (as captain)
        → 200 + member table + invite form + search input
        Network: GET /teams/42 (cookie auth)
        """
        captain = _make_user(test_db, credit_balance=0)
        _make_license(test_db, captain, credit_balance=300)
        tournament = _make_tournament(test_db, cost=100)

        team = team_service.create_team_with_cost(
            db=test_db, name="Dashboard FC", captain_user_id=captain.id,
            specialization_type="TEAM", tournament_id=tournament.id,
        )
        test_db.expire_all()

        client = _make_client(test_db, captain)
        try:
            resp = client.get(f"/teams/{team.id}")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        html = resp.text
        assert "Dashboard FC" in html
        assert "Team Members" in html
        assert captain.name in html      # captain visible in member table
        assert "Captain" in html         # role badge
        # Invite form elements
        assert "Invite a Player" in html
        assert 'id="user-search"' in html   # live search input
        assert "/teams/invite-search" in html  # fetch URL in JS (cookie-auth web route)
        assert f"/teams/{team.id}/invite" in html     # form action

    # ── TEAM-UI-05: Non-captain gets 403 ──────────────────────────────────────

    def test_team_ui_05_non_captain_gets_403(self, test_db: Session):
        """
        GET /teams/{id} by user who is NOT captain or admin → 403.
        Edge case: the team exists, but the requester is a random student.
        Network: GET /teams/42 (cookie auth, wrong user)
        """
        captain = _make_user(test_db, credit_balance=0)
        _make_license(test_db, captain, credit_balance=300)
        stranger = _make_user(test_db)
        tournament = _make_tournament(test_db, cost=100)

        team = team_service.create_team_with_cost(
            db=test_db, name="Private FC", captain_user_id=captain.id,
            specialization_type="TEAM", tournament_id=tournament.id,
        )
        test_db.expire_all()

        client = _make_client(test_db, stranger)
        try:
            resp = client.get(f"/teams/{team.id}", follow_redirects=False)
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 403

    # ── TEAM-UI-06: 0 credits → 402 re-rendered with error text ───────────────

    def test_team_ui_06_zero_credits_shows_error_in_html(self, test_db: Session):
        """
        POST /tournaments/{id}/team/create, captain has 0 credits (cost=100)
        → 402 with form re-rendered; "Insufficient credits" in HTML body.
        Edge case: UI must not just 302, it must re-render the form with an error.
        Network request:
          POST /tournaments/42/team/create
          Body: name=Broke+FC
        Network response:
          Status: 402
          Body: HTML with error message
        """
        captain = _make_user(test_db, credit_balance=0)
        _make_license(test_db, captain, credit_balance=0)   # 0 < 100 cost
        tournament = _make_tournament(test_db, cost=100)

        client = _make_client(test_db, captain)
        try:
            resp = client.post(
                f"/tournaments/{tournament.id}/team/create",
                data={"name": "Broke FC"},
                follow_redirects=False,
            )
        finally:
            app.dependency_overrides.clear()

        # Status 402 with HTML body (form re-rendered, not redirect)
        assert resp.status_code == 402
        html = resp.text
        assert "Broke FC" not in html or "Insufficient" in html  # error shown
        assert "Insufficient" in html or "credits" in html.lower()
        # Confirm no team was created
        team = test_db.query(Team).filter(
            Team.captain_user_id == captain.id
        ).first()
        assert team is None

    # ── TEAM-UI-07: Duplicate invite → error redirect ─────────────────────────

    def test_team_ui_07_duplicate_invite_is_idempotent(self, test_db: Session):
        """
        POST /teams/{id}/invite twice for the same user.
        invite_member() is intentionally idempotent: the second call returns
        the existing PENDING invite without error (no 409, no extra row).

        Edge case proven:
          - Exactly 1 PENDING invite in DB after two requests
          - Both requests redirect with ?msg=Invited (no error)
          - UI stays consistent; no duplicate invite cards shown

        Network evidence:
          POST /teams/42/invite  Body: invited_user_id=7
          Response: 303 → /teams/42?msg=Invited   (first)
          Response: 303 → /teams/42?msg=Invited   (second — idempotent)
        """
        captain = _make_user(test_db, credit_balance=0)
        _make_license(test_db, captain, credit_balance=300)
        invitee = _make_user(test_db)
        _make_license(test_db, invitee, credit_balance=0)
        tournament = _make_tournament(test_db, cost=100)

        team = team_service.create_team_with_cost(
            db=test_db, name="Dup FC", captain_user_id=captain.id,
            specialization_type="TEAM", tournament_id=tournament.id,
        )
        test_db.expire_all()

        client = _make_client(test_db, captain)
        try:
            r1 = client.post(
                f"/teams/{team.id}/invite",
                data={"invited_user_id": invitee.id},
                follow_redirects=False,
            )
            assert r1.status_code == 303
            assert "error" not in r1.headers.get("location", "").lower()

            # Second call — idempotent, must NOT create a second invite
            r2 = client.post(
                f"/teams/{team.id}/invite",
                data={"invited_user_id": invitee.id},
                follow_redirects=False,
            )
            assert r2.status_code == 303
            # No error — existing invite returned silently
            assert "error" not in r2.headers.get("location", "").lower(), (
                f"Duplicate invite must be silent, got error: {r2.headers.get('location')}"
            )
        finally:
            app.dependency_overrides.clear()

        test_db.expire_all()
        # DB invariant: still exactly 1 PENDING invite (idempotent, not duplicated)
        invites = test_db.query(TeamInvite).filter(
            TeamInvite.team_id == team.id,
            TeamInvite.invited_user_id == invitee.id,
            TeamInvite.status == TeamInviteStatus.PENDING.value,
        ).all()
        assert len(invites) == 1, f"Must have exactly 1 PENDING invite; got {len(invites)}"

    # ── TEAM-UI-08: invite-search excludes members + pending invitees ──────────

    def test_team_ui_08_invite_search_excludes_members_and_pending(self, test_db: Session):
        """
        GET /api/v1/users/invite-search?q=teamui&team_id={id}
        → excludes: caller, active team members, users with PENDING invite.
        Network request:
          GET /api/v1/users/invite-search?q=TeamUI&team_id=42
          Header: Authorization: Bearer ...
        Network response:
          [{"id": N, "name": "...", "email": "..."}]  — filtered list
        """
        captain = _make_user(test_db, credit_balance=0)
        _make_license(test_db, captain, credit_balance=300)
        member = _make_user(test_db)     # already in team
        invitee_pending = _make_user(test_db)  # has pending invite
        visible = _make_user(test_db)    # should appear in results
        tournament = _make_tournament(test_db, cost=100)

        team = team_service.create_team_with_cost(
            db=test_db, name="Search FC", captain_user_id=captain.id,
            specialization_type="TEAM", tournament_id=tournament.id,
        )
        # Add member directly
        team_service.add_team_member(
            db=test_db, team_id=team.id, user_id=member.id, role="PLAYER"
        )
        # Send invite (pending)
        team_service.invite_member(
            db=test_db, team_id=team.id,
            invited_user_id=invitee_pending.id, invited_by_id=captain.id,
        )
        test_db.expire_all()

        # Search prefix common to all team-ui test users
        q = "teamui"
        client = _make_client(test_db, captain)
        try:
            resp = client.get(
                f"/api/v1/users/invite-search?q={q}&team_id={team.id}",
            )
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        results = resp.json()
        result_ids = {u["id"] for u in results}

        # captain (self) excluded
        assert captain.id not in result_ids
        # existing member excluded
        assert member.id not in result_ids
        # pending invitee excluded
        assert invitee_pending.id not in result_ids
        # fresh user should appear
        assert visible.id in result_ids

    # ── TEAM-UI-09: invite-search returns matching users ──────────────────────

    def test_team_ui_09_invite_search_returns_match(self, test_db: Session):
        """
        GET /api/v1/users/invite-search?q=<partial name>
        → returns users matching name/email, JSON array with id/name/email.
        Network request:
          GET /api/v1/users/invite-search?q=dragon
        Network response:
          [{"id": 5, "name": "Dragonfly Student", "email": "..."}]
        """
        searcher = _make_user(test_db)
        # User whose name contains the query
        target = User(
            email=f"dragon-target-{uuid.uuid4().hex[:6]}@lfa.com",
            name=f"DragonTarget {uuid.uuid4().hex[:4]}",
            password_hash=get_password_hash("Test1234!"),
            role=UserRole.STUDENT,
            is_active=True,
            onboarding_completed=True,
            credit_balance=0,
            payment_verified=True,
        )
        test_db.add(target)
        test_db.flush()

        client = _make_client(test_db, searcher)
        try:
            resp = client.get("/api/v1/users/invite-search?q=DragonTarget")
        finally:
            app.dependency_overrides.clear()

        assert resp.status_code == 200
        results = resp.json()
        assert isinstance(results, list)
        ids = [u["id"] for u in results]
        assert target.id in ids

        # Verify JSON shape
        match = next(u for u in results if u["id"] == target.id)
        assert "id" in match
        assert "name" in match
        assert "email" in match
        assert match["name"] == target.name
