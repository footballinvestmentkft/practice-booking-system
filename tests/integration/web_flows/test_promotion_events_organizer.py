"""
Promotion Event Organizer Integration Tests — ORG-01 through ORG-04

  ORG-01  Promotion wizard sets organizer_club_id on every created event
  ORG-02  GET /admin/promotion-events → organizer club name visible in page
  ORG-03  Sponsor can be set as organizer; promotion-events page shows sponsor name
  ORG-04  Standalone event (no organizer) shows "— standalone —" in promotion-events page

DONE = pytest tests/integration/web_flows/test_promotion_events_organizer.py -v
"""
import uuid
import pytest
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db
from app.dependencies import get_current_user_web, get_current_admin_user_hybrid
from app.models.user import User, UserRole
from app.models.club import Club
from app.models.sponsor import Sponsor
from app.models.semester import Semester, SemesterCategory
from app.models.campus import Campus
from app.models.location import Location
from app.models.team import Team, TeamMember
from app.models.tournament_type import TournamentType as TournamentTypeModel
from app.core.security import get_password_hash
from app.services.club_service import create_club


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"admin-org+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Org Admin",
        password_hash=get_password_hash("Admin1234!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_club_with_team(db: Session, admin: User, age_group: str = "U12") -> Club:
    club = create_club(
        db,
        name=f"Org Club {uuid.uuid4().hex[:6]}",
        city="Budapest",
        country="HU",
        created_by_id=admin.id,
    )
    team = Team(
        club_id=club.id,
        name=f"{club.name} {age_group}",
        age_group_label=age_group,
        is_active=True,
    )
    db.add(team)
    db.flush()
    member = TeamMember(
        team_id=team.id,
        user_id=admin.id,
        is_active=True,
    )
    db.add(member)
    db.flush()
    return club


def _make_campus(db: Session) -> Campus:
    loc = Location(
        name=f"Org Location {uuid.uuid4().hex[:6]}",
        city=f"OrgCity-{uuid.uuid4().hex[:6]}",
        country="HU",
    )
    db.add(loc)
    db.flush()
    campus = Campus(
        name=f"Org Campus {uuid.uuid4().hex[:6]}",
        location_id=loc.id,
        is_active=True,
    )
    db.add(campus)
    db.flush()
    return campus


def _make_tournament_type(db: Session) -> TournamentTypeModel:
    tt = TournamentTypeModel(
        code=f"league-org-{uuid.uuid4().hex[:4]}",
        display_name="League (Org Test)",
        format="HEAD_TO_HEAD",
        config={},
    )
    db.add(tt)
    db.flush()
    return tt


def _make_sponsor(db: Session, admin: User) -> Sponsor:
    s = Sponsor(
        name=f"Org Sponsor {uuid.uuid4().hex[:6]}",
        code=f"OS-{uuid.uuid4().hex[:6].upper()}",
        is_active=True,
        created_by=admin.id,
    )
    db.add(s)
    db.flush()
    return s


def _admin_client(test_db: Session, admin: User) -> TestClient:
    def override_db():
        yield test_db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user_web] = lambda: admin
    app.dependency_overrides[get_current_admin_user_hybrid] = lambda: admin
    return TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestPromotionWizardSetsOrganizerClub:
    """ORG-01: POST /admin/clubs/{id}/promotion sets organizer_club_id"""

    def test_org_01_wizard_sets_organizer_club_id(self, test_db: Session):
        admin = _make_admin(test_db)
        club = _make_club_with_team(test_db, admin, age_group="U12")
        campus = _make_campus(test_db)
        tt = _make_tournament_type(test_db)
        test_db.commit()

        client = _admin_client(test_db, admin)
        event_name = f"ORG01 Test Event {uuid.uuid4().hex[:6]}"
        try:
            resp = client.post(
                f"/admin/clubs/{club.id}/promotion",
                data={
                    "tournament_name": event_name,
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-03",
                    "campus_id": str(campus.id),
                    "tournament_type_id": str(tt.id),
                    "game_preset_id": "",
                    "age_groups": ["U12"],
                },
                follow_redirects=False,
            )
            assert resp.status_code in (302, 303), f"Expected redirect, got {resp.status_code}: {resp.text[:200]}"

            test_db.expire_all()
            event = (
                test_db.query(Semester)
                .filter(
                    Semester.name.like(f"{event_name}%"),
                    Semester.semester_category == SemesterCategory.PROMOTION_EVENT,
                )
                .first()
            )
            assert event is not None, f"No promotion event found with name like '{event_name}%'"
            assert event.organizer_club_id == club.id, (
                f"organizer_club_id expected {club.id}, got {event.organizer_club_id}"
            )
            assert event.organizer_sponsor_id is None
        finally:
            app.dependency_overrides.clear()


class TestPromotionEventsPageShowsOrganizer:
    """ORG-02: GET /admin/promotion-events shows organizer club name"""

    def test_org_02_organizer_club_name_in_page(self, test_db: Session):
        admin = _make_admin(test_db)
        club = _make_club_with_team(test_db, admin, age_group="U15")
        campus = _make_campus(test_db)
        tt = _make_tournament_type(test_db)
        test_db.commit()

        client = _admin_client(test_db, admin)
        event_name = f"ORG02 Visibility {uuid.uuid4().hex[:6]}"
        try:
            resp = client.post(
                f"/admin/clubs/{club.id}/promotion",
                data={
                    "tournament_name": event_name,
                    "start_date": "2026-08-05",
                    "end_date": "2026-08-07",
                    "campus_id": str(campus.id),
                    "tournament_type_id": str(tt.id),
                    "game_preset_id": "",
                    "age_groups": ["U15"],
                },
                follow_redirects=False,
            )
            assert resp.status_code in (302, 303)

            page = client.get("/admin/promotion-events")
            assert page.status_code == 200
            assert club.name in page.text, (
                f"Organizer club name '{club.name}' not found in /admin/promotion-events"
            )
        finally:
            app.dependency_overrides.clear()


class TestSponsorOrganizerOnEvent:
    """ORG-03: Sponsor can be directly set as organizer; page shows sponsor name"""

    def test_org_03_sponsor_organizer_visible_in_page(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campus = _make_campus(test_db)

        # Create a promo event and manually assign the sponsor as organizer
        uid = uuid.uuid4().hex[:8]
        event = Semester(
            code=f"ORG03-{uid}",
            name=f"ORG03 Sponsor Event {uid}",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 3),
            status="DRAFT",
            tournament_status="DRAFT",
            semester_category=SemesterCategory.PROMOTION_EVENT,
            enrollment_cost=0,
            campus_id=campus.id,
            organizer_sponsor_id=sponsor.id,
        )
        test_db.add(event)
        test_db.commit()

        client = _admin_client(test_db, admin)
        try:
            page = client.get("/admin/promotion-events")
            assert page.status_code == 200
            assert sponsor.name in page.text, (
                f"Sponsor organizer name '{sponsor.name}' not found in /admin/promotion-events"
            )
        finally:
            app.dependency_overrides.clear()


class TestStandaloneOrganizerCell:
    """ORG-04: Standalone promotion event shows '— standalone —' in the organizer cell."""

    def test_org_04_standalone_event_shows_standalone_label(self, test_db: Session):
        admin = _make_admin(test_db)
        campus = _make_campus(test_db)

        uid = uuid.uuid4().hex[:8]
        from app.models.semester import Semester, SemesterCategory
        event = Semester(
            code=f"ORG04-{uid}",
            name=f"ORG04 Standalone {uid}",
            start_date=date(2026, 11, 1),
            end_date=date(2026, 11, 3),
            status="DRAFT",
            tournament_status="DRAFT",
            semester_category=SemesterCategory.PROMOTION_EVENT,
            enrollment_cost=0,
            campus_id=campus.id,
            # Both organizer FKs explicitly NULL — standalone
            organizer_club_id=None,
            organizer_sponsor_id=None,
        )
        test_db.add(event)
        test_db.commit()

        client = _admin_client(test_db, admin)
        try:
            page = client.get("/admin/promotion-events")
            assert page.status_code == 200
            assert "standalone" in page.text.lower(), (
                "Expected '— standalone —' label for standalone event in /admin/promotion-events, "
                "but 'standalone' not found in page HTML"
            )
        finally:
            app.dependency_overrides.clear()
