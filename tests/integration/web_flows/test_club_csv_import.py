"""
Club CSV Import Integration Tests — CLUB-01 through CLUB-08

  CLUB-01  POST /admin/clubs/create → club created, code auto-generated
  CLUB-02  CSV import 5 rows → 5 users + 2 teams + 1 import log
  CLUB-03  CSV import idempotency → second import: rows_updated, 0 created
  CLUB-04  CSV row missing email → skip row, error recorded in log
  CLUB-05  CSV duplicate email → user name/position UPDATE (no duplicate user)
  CLUB-06  initial_credits > 0 → CreditTransaction created
  CLUB-07  initial_credits idempotency → second import does NOT create duplicate tx
  CLUB-08  Promotion wizard → N promotion events created (PROMOTION_EVENT), teams enrolled per age_group

DONE = pytest tests/integration/web_flows/test_club_csv_import.py -v
"""
import uuid
import pytest
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import engine, get_db
from app.dependencies import get_current_user_web
from app.models.user import User, UserRole
from app.models.club import Club, CsvImportLog
from app.models.team import Team, TeamMember, TournamentTeamEnrollment
from app.models.credit_transaction import CreditTransaction
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.core.security import get_password_hash
from app.services import csv_import_service
from app.services.club_service import create_club


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"admin-club+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Club Admin",
        password_hash=get_password_hash("Admin1234!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_club(db: Session, admin: User, suffix: str = "") -> Club:
    return create_club(
        db,
        name=f"FC Test Club {suffix or uuid.uuid4().hex[:6]}",
        city="Budapest",
        country="HU",
        created_by_id=admin.id,
    )


def _make_log(db: Session, club: Club, admin: User) -> CsvImportLog:
    log = CsvImportLog(
        club_id=club.id,
        uploaded_by=admin.id,
        filename="test.csv",
        status="PROCESSING",
    )
    db.add(log)
    db.flush()
    return log


def _admin_client(test_db: Session, admin: User) -> TestClient:
    def override_db():
        yield test_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user_web] = lambda: admin
    return TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})


def _minimal_csv(rows: list[dict]) -> bytes:
    """Build a minimal CSV bytes from a list of row dicts."""
    headers = ["first_name", "last_name", "email", "age_group", "team_name", "club_name", "initial_credits"]
    lines = [",".join(headers)]
    for r in rows:
        lines.append(",".join(r.get(h, "") for h in headers))
    return "\n".join(lines).encode("utf-8")


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestClubCreate:
    """CLUB-01: POST /admin/clubs/create"""

    def test_club_01_create_club(self, test_db: Session):
        admin = _make_admin(test_db)
        client = _admin_client(test_db, admin)
        try:
            unique = uuid.uuid4().hex[:8]
            resp = client.post(
                "/admin/clubs/create",
                data={
                    "name": f"FC Vasas Test {unique}",
                    "city": "Budapest",
                    "country": "HU",
                    "contact_email": f"vasas{unique}@test.com",
                },
                follow_redirects=False,
            )
            # Expect redirect to club detail
            assert resp.status_code == 303
            location = resp.headers.get("location", "")
            assert "/admin/clubs/" in location

            # Verify DB state
            club = test_db.query(Club).filter(Club.name == f"FC Vasas Test {unique}").first()
            assert club is not None
            assert club.city == "Budapest"
            assert club.country == "HU"
            assert club.code  # auto-generated
            assert "-" in club.code or club.code.isalpha()  # normalised form
            assert club.is_active is True
        finally:
            app.dependency_overrides.clear()


class TestCsvImport:
    """CLUB-02 through CLUB-07: CSV import scenarios."""

    def _five_row_csv(self, club_name: str) -> bytes:
        rows = [
            {"first_name": "Adam",  "last_name": "Nagy",   "email": f"adam.n.{uuid.uuid4().hex[:6]}@lfa-t.com",  "age_group": "U15", "team_name": "Team Alpha", "club_name": club_name},
            {"first_name": "Bela",  "last_name": "Kiss",   "email": f"bela.k.{uuid.uuid4().hex[:6]}@lfa-t.com",  "age_group": "U15", "team_name": "Team Alpha", "club_name": club_name},
            {"first_name": "Csaba", "last_name": "Toth",   "email": f"csaba.t.{uuid.uuid4().hex[:6]}@lfa-t.com", "age_group": "U12", "team_name": "Team Beta",  "club_name": club_name},
            {"first_name": "Denes", "last_name": "Kovacs", "email": f"denes.k.{uuid.uuid4().hex[:6]}@lfa-t.com", "age_group": "U12", "team_name": "Team Beta",  "club_name": club_name},
            {"first_name": "Emil",  "last_name": "Varga",  "email": f"emil.v.{uuid.uuid4().hex[:6]}@lfa-t.com",  "age_group": "U12", "team_name": "Team Beta",  "club_name": club_name},
        ]
        return _minimal_csv(rows)

    def test_club_02_import_creates_users_teams(self, test_db: Session):
        """5 valid rows → 5 users, 2 teams, rows_created=5."""
        admin = _make_admin(test_db)
        club = _make_club(test_db, admin, suffix="CSV02")
        log = _make_log(test_db, club, admin)
        test_db.commit()  # persist so import_rows can see club.id
        test_db.refresh(club)
        test_db.refresh(log)

        csv_bytes = self._five_row_csv(club.name)
        rows = csv_import_service.parse_csv(csv_bytes)
        result = csv_import_service.import_rows(
            test_db, rows, log, admin_user=admin, default_club_id=club.id
        )
        test_db.commit()
        test_db.refresh(result)

        assert result.rows_created == 5
        assert result.rows_failed == 0
        assert result.total_rows == 5

        teams = test_db.query(Team).filter(Team.club_id == club.id).all()
        assert len(teams) == 2
        team_names = {t.name for t in teams}
        assert "Team Alpha" in team_names
        assert "Team Beta" in team_names

        alpha = next(t for t in teams if t.name == "Team Alpha")
        beta = next(t for t in teams if t.name == "Team Beta")
        alpha_members = test_db.query(TeamMember).filter(
            TeamMember.team_id == alpha.id, TeamMember.is_active.is_(True)
        ).all()
        beta_members = test_db.query(TeamMember).filter(
            TeamMember.team_id == beta.id, TeamMember.is_active.is_(True)
        ).all()
        assert len(alpha_members) == 2
        assert len(beta_members) == 3
        # First member should be captain
        assert alpha.captain_user_id is not None

    def test_club_03_import_idempotency(self, test_db: Session):
        """Second import with same emails → rows_updated, 0 created."""
        admin = _make_admin(test_db)
        club = _make_club(test_db, admin, suffix="CSV03")
        test_db.commit()
        test_db.refresh(club)

        csv_bytes = self._five_row_csv(club.name)
        rows = csv_import_service.parse_csv(csv_bytes)

        # First import
        log1 = _make_log(test_db, club, admin)
        test_db.flush()
        csv_import_service.import_rows(test_db, rows, log1, admin_user=admin, default_club_id=club.id)
        test_db.commit()
        test_db.refresh(log1)

        assert log1.rows_created == 5

        # Second import — same rows
        log2 = _make_log(test_db, club, admin)
        test_db.flush()
        result2 = csv_import_service.import_rows(test_db, rows, log2, admin_user=admin, default_club_id=club.id)
        test_db.commit()
        test_db.refresh(result2)

        assert result2.rows_created == 0
        assert result2.rows_updated == 5
        assert result2.rows_failed == 0

        # No new users created
        all_users = (
            test_db.query(User)
            .filter(User.email.in_([r["email"] for r in rows]))
            .all()
        )
        assert len(all_users) == 5  # exact same 5

    def test_club_04_missing_email_row_skipped(self, test_db: Session):
        """Row without email → skipped, error recorded, other rows succeed."""
        admin = _make_admin(test_db)
        club = _make_club(test_db, admin, suffix="CSV04")
        test_db.commit()
        test_db.refresh(club)

        ok_email = f"ok.user.csv04.{uuid.uuid4().hex[:6]}@lfa-t.com"
        csv_bytes = (
            b"first_name,last_name,email,age_group,team_name,club_name\n"
            b"Valid,User," + ok_email.encode() + b",U15,Alpha Team,FC Test\n"
            b"Missing,Email,,U15,Alpha Team,FC Test\n"
        )
        rows = csv_import_service.parse_csv(csv_bytes)
        log = _make_log(test_db, club, admin)
        test_db.flush()

        result = csv_import_service.import_rows(test_db, rows, log, admin_user=admin, default_club_id=club.id)
        test_db.commit()
        test_db.refresh(result)

        assert result.rows_created == 1
        assert result.rows_failed == 1
        assert len(result.errors) == 1
        assert "email" in result.errors[0]["reason"].lower()

    def test_club_05_duplicate_email_updates_user(self, test_db: Session):
        """Existing email → UPDATE name/position, no duplicate user created."""
        admin = _make_admin(test_db)
        email = f"existing.csv05.{uuid.uuid4().hex[:6]}@lfa-t.com"

        # Pre-create user with old name
        existing = User(
            email=email,
            name="Old Name",
            first_name="Old",
            last_name="Name",
            password_hash=get_password_hash("x"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(existing)
        test_db.flush()
        old_id = existing.id

        club = _make_club(test_db, admin, suffix="CSV05")
        test_db.commit()
        test_db.refresh(club)
        test_db.refresh(existing)

        csv_bytes = (
            b"first_name,last_name,email\n"
            + f"NewFirst,NewLast,{email}\n".encode()
        )
        rows = csv_import_service.parse_csv(csv_bytes)
        log = _make_log(test_db, club, admin)
        test_db.flush()

        result = csv_import_service.import_rows(test_db, rows, log, admin_user=admin, default_club_id=club.id)
        test_db.commit()
        test_db.refresh(result)
        test_db.refresh(existing)

        # Same user — no duplicate
        assert result.rows_created == 0
        assert result.rows_updated == 1

        # Name updated
        assert existing.first_name == "NewFirst"
        assert existing.last_name == "NewLast"
        assert existing.id == old_id

        # No duplicate user
        count = test_db.query(User).filter(User.email == email).count()
        assert count == 1

    def test_club_06_initial_credits_creates_transaction(self, test_db: Session):
        """initial_credits > 0 → CreditTransaction created + balance updated."""
        admin = _make_admin(test_db)
        email = f"credit.csv06.{uuid.uuid4().hex[:6]}@lfa-t.com"
        club = _make_club(test_db, admin, suffix="CSV06")
        test_db.commit()
        test_db.refresh(club)

        csv_bytes = (
            b"first_name,last_name,email,initial_credits\n"
            + f"Credit,Player,{email},250\n".encode()
        )
        rows = csv_import_service.parse_csv(csv_bytes)
        log = _make_log(test_db, club, admin)
        test_db.flush()

        csv_import_service.import_rows(test_db, rows, log, admin_user=admin, default_club_id=club.id)
        test_db.commit()

        # CreditTransaction created
        tx = (
            test_db.query(CreditTransaction)
            .join(User, CreditTransaction.user_id == User.id)
            .filter(User.email == email)
            .first()
        )
        assert tx is not None
        assert tx.amount == 250

        # User balance updated
        user = test_db.query(User).filter(User.email == email).first()
        assert user.credit_balance == 250

    def test_club_07_initial_credits_idempotency(self, test_db: Session):
        """Second import with same email+credits → NO duplicate CreditTransaction."""
        admin = _make_admin(test_db)
        email = f"credit.csv07.{uuid.uuid4().hex[:6]}@lfa-t.com"
        club = _make_club(test_db, admin, suffix="CSV07")
        test_db.commit()
        test_db.refresh(club)

        csv_bytes = (
            b"first_name,last_name,email,initial_credits\n"
            + f"Idem,Player,{email},100\n".encode()
        )
        rows = csv_import_service.parse_csv(csv_bytes)

        # First import
        log1 = _make_log(test_db, club, admin)
        test_db.flush()
        csv_import_service.import_rows(test_db, rows, log1, admin_user=admin, default_club_id=club.id)
        test_db.commit()

        # Second import (same rows, different log)
        log2 = _make_log(test_db, club, admin)
        test_db.flush()
        csv_import_service.import_rows(test_db, rows, log2, admin_user=admin, default_club_id=club.id)
        test_db.commit()

        user = test_db.query(User).filter(User.email == email).first()
        txs = (
            test_db.query(CreditTransaction)
            .filter(CreditTransaction.user_id == user.id)
            .all()
        )
        # Only ONE transaction (first import); second import skipped due to idempotency_key
        assert len(txs) == 1
        assert user.credit_balance == 100


class TestPromotionWizard:
    """CLUB-08: POST /admin/clubs/{id}/promotion → N tournaments + team enrollments."""

    def test_club_08_promotion_creates_tournaments_per_age_group(self, test_db: Session):
        """2 age groups → 2 Semester(TOURNAMENT) + TournamentTeamEnrollment per team."""
        admin = _make_admin(test_db)
        club = _make_club(test_db, admin, suffix="PROMO08")
        test_db.flush()

        # Create 2 teams per age group, each with at least one active member
        from app.models.team import Team as TeamModel
        teams_u12 = []
        teams_u15 = []
        for i in range(2):
            # Each team needs a user to serve as a member (guard rejects empty teams)
            player_u12 = User(
                email=f"u12-p{i}-{uuid.uuid4().hex[:6]}@club.test",
                name=f"U12 Player {i}",
                password_hash="x",
                role=UserRole.STUDENT,
                is_active=True,
            )
            player_u15 = User(
                email=f"u15-p{i}-{uuid.uuid4().hex[:6]}@club.test",
                name=f"U15 Player {i}",
                password_hash="x",
                role=UserRole.STUDENT,
                is_active=True,
            )
            test_db.add(player_u12)
            test_db.add(player_u15)
            test_db.flush()

            team_u12 = TeamModel(
                name=f"U12 Team {i}",
                code=f"U12T{i}-{uuid.uuid4().hex[:4]}",
                club_id=club.id,
                age_group_label="U12",
                is_active=True,
            )
            team_u15 = TeamModel(
                name=f"U15 Team {i}",
                code=f"U15T{i}-{uuid.uuid4().hex[:4]}",
                club_id=club.id,
                age_group_label="U15",
                is_active=True,
            )
            test_db.add(team_u12)
            test_db.add(team_u15)
            test_db.flush()

            test_db.add(TeamMember(team_id=team_u12.id, user_id=player_u12.id, role="PLAYER", is_active=True))
            test_db.add(TeamMember(team_id=team_u15.id, user_id=player_u15.id, role="PLAYER", is_active=True))

            teams_u12.append(team_u12)
            teams_u15.append(team_u15)
        test_db.commit()
        test_db.refresh(club)
        for t in teams_u12 + teams_u15:
            test_db.refresh(t)

        client = _admin_client(test_db, admin)
        try:
            resp = client.post(
                f"/admin/clubs/{club.id}/promotion",
                data={
                    "tournament_name": "Vasas Cup 2026",
                    "start_date": "2026-05-01",
                    "end_date": "2026-05-02",
                    "campus_id": "",
                    "game_preset_id": "",
                    "tournament_type_id": "",
                    "age_groups": ["U12", "U15"],
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "/admin/promotion-events" in resp.headers.get("location", "")
        finally:
            app.dependency_overrides.clear()

        # 2 promotion events created
        tournaments = (
            test_db.query(Semester)
            .filter(
                Semester.semester_category == SemesterCategory.PROMOTION_EVENT,
                Semester.name.like("Vasas Cup 2026%"),
            )
            .all()
        )
        assert len(tournaments) == 2

        # age_group is normalized via _normalize_club_age_group(): U12→PRE, U15→YOUTH
        age_labels = {t.age_group for t in tournaments}
        assert "PRE" in age_labels, f"Expected PRE (from U12), got: {age_labels}"
        assert "YOUTH" in age_labels, f"Expected YOUTH (from U15), got: {age_labels}"

        # organizer_club_id is set on every promotion event (P2-A)
        for tournament in tournaments:
            assert tournament.organizer_club_id == club.id, (
                f"organizer_club_id expected {club.id}, got {tournament.organizer_club_id}"
            )
            assert tournament.organizer_sponsor_id is None

        # Each tournament has 2 teams enrolled
        for tournament in tournaments:
            enrollments = (
                test_db.query(TournamentTeamEnrollment)
                .filter(TournamentTeamEnrollment.semester_id == tournament.id)
                .all()
            )
            assert len(enrollments) == 2
            # All admin-bypassed → payment_verified
            for enr in enrollments:
                assert enr.payment_verified is True
                assert enr.is_active is True
