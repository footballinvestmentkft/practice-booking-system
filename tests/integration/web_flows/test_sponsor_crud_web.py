"""
Sponsor CRUD Web Flow Tests — SPON-01 through SPON-16

  SPON-01  GET /admin/sponsors → 200 (empty list)
  SPON-02  POST /admin/sponsors/new → sponsor created, redirects to detail
  SPON-03  POST /admin/sponsors/new duplicate code → 400 with error message
  SPON-04  POST /admin/sponsors/new with primary contact → contact created (is_primary=True)
  SPON-05  GET /admin/sponsors/{id} → 200, shows name, code
  SPON-06  POST /admin/sponsors/{id}/edit → fields updated
  SPON-07  POST /admin/sponsors/{id}/contacts/add → contact added
  SPON-08  POST /admin/sponsors/{id}/contacts/add (second primary) → redirect with error
  SPON-09  POST /admin/sponsors/{id}/contacts/{cid}/delete → contact removed
  SPON-10  GET /admin/sponsors/{id} non-existent → 404
  SPON-11  POST /admin/sponsors/new then GET /admin/sponsors → sponsor appears in list
  SPON-12  POST duplicate code → primary contact fields repopulated in response
  SPON-13  GET /admin/sponsors/{id} → linked events View link uses /admin/tournaments/ prefix
  SPON-14  GET /admin/sponsors/{id} → Create Promotion Event CTA + Import Audience present,
           no Club-page copy
  SPON-15  GET + POST /admin/sponsors/{id}/promotion → INDIVIDUAL PROMOTION_EVENT created,
           organizer_sponsor_id set, organizer_club_id NULL, no TournamentTeamEnrollment
  SPON-16  POST /admin/sponsors/{id}/toggle → active/inactive toggle works

DONE = pytest tests/integration/web_flows/test_sponsor_crud_web.py -v
"""
import uuid
import pytest
from sqlalchemy.orm import Session

from fastapi.testclient import TestClient
from app.main import app
from app.database import get_db
from app.dependencies import get_current_user_web
from app.models.user import User, UserRole
from app.models.sponsor import Sponsor, SponsorCampaign, SponsorContact
from app.core.security import get_password_hash


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"admin-spon+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Sponsor Admin",
        password_hash=get_password_hash("Admin1234!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_sponsor(db: Session, admin: User, suffix: str = "") -> Sponsor:
    s = Sponsor(
        name=f"Test Partner {suffix or uuid.uuid4().hex[:6]}",
        code=f"TP-{uuid.uuid4().hex[:6].upper()}",
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
    return TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestSponsorList:
    """SPON-01, SPON-11"""

    def test_spon_01_list_empty(self, test_db: Session):
        admin = _make_admin(test_db)
        client = _admin_client(test_db, admin)
        try:
            resp = client.get("/admin/sponsors")
            assert resp.status_code == 200
            assert "Partners" in resp.text
        finally:
            app.dependency_overrides.clear()

    def test_spon_11_created_sponsor_appears_in_list(self, test_db: Session):
        admin = _make_admin(test_db)
        client = _admin_client(test_db, admin)
        code = f"LIST-{uuid.uuid4().hex[:4].upper()}"
        try:
            # Create via route so it's committed within the overridden session
            create_resp = client.post(
                "/admin/sponsors/new",
                data={"name": "List Check Sponsor", "code": code},
                follow_redirects=False,
            )
            assert create_resp.status_code == 303

            resp = client.get("/admin/sponsors")
            assert resp.status_code == 200
            assert "List Check Sponsor" in resp.text
        finally:
            app.dependency_overrides.clear()


class TestSponsorCreate:
    """SPON-02, SPON-03, SPON-04"""

    def test_spon_02_create_sponsor(self, test_db: Session):
        admin = _make_admin(test_db)
        client = _admin_client(test_db, admin)
        code = f"ADIDAS-{uuid.uuid4().hex[:4].upper()}"
        try:
            resp = client.post(
                "/admin/sponsors/new",
                data={
                    "name": "Adidas Hungary",
                    "code": code,
                    "brand_category": "Sportswear",
                    "city": "Budapest",
                    "country": "Hungary",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303
            location = resp.headers.get("location", "")
            assert "/admin/sponsors/" in location

            sponsor = test_db.query(Sponsor).filter(Sponsor.code == code).first()
            assert sponsor is not None
            assert sponsor.name == "Adidas Hungary"
            assert sponsor.brand_category == "Sportswear"
            assert sponsor.is_active is True
            assert sponsor.created_by == admin.id
        finally:
            app.dependency_overrides.clear()

    def test_spon_03_duplicate_code_returns_400(self, test_db: Session):
        admin = _make_admin(test_db)
        client = _admin_client(test_db, admin)
        code = f"DUP-{uuid.uuid4().hex[:4].upper()}"
        try:
            # First creation via route (so the session owns the committed record)
            first = client.post(
                "/admin/sponsors/new",
                data={"name": "First Sponsor", "code": code},
                follow_redirects=False,
            )
            assert first.status_code == 303

            # Second creation with same code → 400 with error message
            resp = client.post(
                "/admin/sponsors/new",
                data={"name": "Duplicate Partner", "code": code},
                follow_redirects=False,
            )
            assert resp.status_code == 400
            assert "already in use" in resp.text
        finally:
            app.dependency_overrides.clear()

    def test_spon_04_create_with_primary_contact(self, test_db: Session):
        admin = _make_admin(test_db)
        client = _admin_client(test_db, admin)
        code = f"NIKE-{uuid.uuid4().hex[:4].upper()}"
        try:
            resp = client.post(
                "/admin/sponsors/new",
                data={
                    "name": "Nike Hungary",
                    "code": code,
                    "contact_name": "Kovács János",
                    "contact_role": "Partnership Manager",
                    "contact_email_primary": "janos@nike.hu",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303

            sponsor = test_db.query(Sponsor).filter(Sponsor.code == code).first()
            assert sponsor is not None

            primary = (
                test_db.query(SponsorContact)
                .filter(SponsorContact.sponsor_id == sponsor.id, SponsorContact.is_primary == True)  # noqa: E712
                .first()
            )
            assert primary is not None
            assert primary.name == "Kovács János"
            assert primary.role == "Partnership Manager"
        finally:
            app.dependency_overrides.clear()


class TestSponsorDetail:
    """SPON-05, SPON-10"""

    def test_spon_05_detail_200(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin, suffix="Detail")
        test_db.commit()
        client = _admin_client(test_db, admin)
        try:
            resp = client.get(f"/admin/sponsors/{sponsor.id}")
            assert resp.status_code == 200
            assert sponsor.name in resp.text
            assert sponsor.code in resp.text
        finally:
            app.dependency_overrides.clear()

    def test_spon_10_detail_404_unknown(self, test_db: Session):
        admin = _make_admin(test_db)
        client = _admin_client(test_db, admin)
        try:
            resp = client.get("/admin/sponsors/9999999")
            assert resp.status_code == 404
        finally:
            app.dependency_overrides.clear()


class TestSponsorEdit:
    """SPON-06"""

    def test_spon_06_edit_sponsor(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin, suffix="Edit")
        test_db.commit()
        client = _admin_client(test_db, admin)
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/edit",
                data={
                    "name": "Updated Partner Name",
                    "brand_category": "Insurance",
                    "city": "Debrecen",
                    "country": "Hungary",
                    "is_active": "on",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303

            test_db.expire_all()
            updated = test_db.query(Sponsor).filter(Sponsor.id == sponsor.id).first()
            assert updated.name == "Updated Partner Name"
            assert updated.brand_category == "Insurance"
            assert updated.city == "Debrecen"
            assert updated.is_active is True
        finally:
            app.dependency_overrides.clear()


class TestSponsorContacts:
    """SPON-07, SPON-08, SPON-09"""

    def test_spon_07_add_contact(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin, suffix="Contacts")
        test_db.commit()
        client = _admin_client(test_db, admin)
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/contacts/add",
                data={
                    "contact_name": "Kiss Péter",
                    "contact_role": "Account Manager",
                    "contact_email": "peter@partner.com",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303

            contact = (
                test_db.query(SponsorContact)
                .filter(SponsorContact.sponsor_id == sponsor.id)
                .first()
            )
            assert contact is not None
            assert contact.name == "Kiss Péter"
            assert contact.role == "Account Manager"
            assert contact.is_primary is False
        finally:
            app.dependency_overrides.clear()

    def test_spon_08_second_primary_returns_error_redirect(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin, suffix="DupPrimary")
        existing_primary = SponsorContact(
            sponsor_id=sponsor.id,
            name="First Primary",
            is_primary=True,
        )
        test_db.add(existing_primary)
        test_db.commit()
        client = _admin_client(test_db, admin)
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/contacts/add",
                data={
                    "contact_name": "Second Primary",
                    "is_primary": "on",
                },
                follow_redirects=False,
            )
            # Should redirect with error= in URL, NOT create a second primary
            assert resp.status_code == 303
            location = resp.headers.get("location", "")
            assert "error=" in location

            count = (
                test_db.query(SponsorContact)
                .filter(SponsorContact.sponsor_id == sponsor.id, SponsorContact.is_primary == True)  # noqa: E712
                .count()
            )
            assert count == 1
        finally:
            app.dependency_overrides.clear()

    def test_spon_09_delete_contact(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin, suffix="DelContact")
        contact = SponsorContact(sponsor_id=sponsor.id, name="To Delete")
        test_db.add(contact)
        test_db.commit()
        contact_id = contact.id
        client = _admin_client(test_db, admin)
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/contacts/{contact_id}/delete",
                follow_redirects=False,
            )
            assert resp.status_code == 303

            deleted = test_db.query(SponsorContact).filter(SponsorContact.id == contact_id).first()
            assert deleted is None
        finally:
            app.dependency_overrides.clear()


class TestSponsorUXFixes:
    """SPON-12..14: UX fixes — primary contact repopulation, detail page link + CTA."""

    def test_spon_12_primary_contact_repopulated_on_duplicate_code_error(self, test_db: Session):
        """SPON-12: After duplicate-code 400, primary contact fields survive in the response.

        The route now includes contact_name/role/email_primary/phone in the form dict
        so the template can repopulate them instead of silently losing the user's input.
        """
        admin = _make_admin(test_db)
        client = _admin_client(test_db, admin)
        code = f"DUP12-{uuid.uuid4().hex[:4].upper()}"
        try:
            # First creation — establishes the code
            first = client.post(
                "/admin/sponsors/new",
                data={"name": "First Sponsor", "code": code},
                follow_redirects=False,
            )
            assert first.status_code == 303

            # Second creation with same code + primary contact data
            resp = client.post(
                "/admin/sponsors/new",
                data={
                    "name": "Duplicate Partner",
                    "code": code,
                    "contact_name": "Kovács János",
                    "contact_role": "Partnership Manager",
                    "contact_email_primary": "janos@partner.com",
                    "contact_phone": "+36 1 234 5678",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 400
            assert "already in use" in resp.text

            # Primary contact fields must be repopulated in the response HTML
            assert "Kovács János" in resp.text, (
                "contact_name not repopulated after duplicate code error"
            )
            assert "Partnership Manager" in resp.text, (
                "contact_role not repopulated after duplicate code error"
            )
            assert "janos@partner.com" in resp.text, (
                "contact_email_primary not repopulated after duplicate code error"
            )
        finally:
            app.dependency_overrides.clear()

    def test_spon_13_detail_view_link_uses_tournaments_prefix(self, test_db: Session):
        """SPON-13: Linked promotion events 'View' link must use /admin/tournaments/{id}/edit.

        The broken /admin/promotion-events/{id} route was replaced with the correct
        /admin/tournaments/{id}/edit route in the detail template.
        """
        from datetime import date
        from app.models.semester import Semester, SemesterCategory
        from app.models.campus import Campus
        from app.models.location import Location

        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin, suffix="LinkTest")

        # Create a minimal campus for the event
        loc = Location(name=f"LinkLoc-{uuid.uuid4().hex[:6]}", city=f"City-{uuid.uuid4().hex[:4]}", country="HU")
        test_db.add(loc)
        test_db.flush()
        campus = Campus(name=f"LinkCampus-{uuid.uuid4().hex[:6]}", location_id=loc.id, is_active=True)
        test_db.add(campus)
        test_db.flush()

        # Create a promotion event linked to this sponsor
        uid = uuid.uuid4().hex[:8]
        event = Semester(
            code=f"LNK-{uid}",
            name=f"Link Test Event {uid}",
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 3),
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
            resp = client.get(f"/admin/sponsors/{sponsor.id}")
            assert resp.status_code == 200

            # Must use /admin/tournaments/{id}/edit — NOT /admin/promotion-events/{id}
            assert f"/admin/tournaments/{event.id}/edit" in resp.text, (
                f"Expected /admin/tournaments/{event.id}/edit in detail page HTML, "
                f"but it was not found — broken link may still be present"
            )
            assert f"/admin/promotion-events/{event.id}" not in resp.text, (
                "Old broken /admin/promotion-events/{id} link still present in detail page"
            )
        finally:
            app.dependency_overrides.clear()

    def test_spon_14_detail_dashboard_elements_present(self, test_db: Session):
        """SPON-14: Sponsor detail page is a self-contained partner dashboard.

        Required elements:
        - Create Promotion Event CTA → /admin/sponsors/{id}/promotion
        - Import Audience section visible (placeholder state)
        - No Club-page copy (sponsor-only language)
        """
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin, suffix="CTA")
        test_db.commit()
        client = _admin_client(test_db, admin)
        try:
            resp = client.get(f"/admin/sponsors/{sponsor.id}")
            assert resp.status_code == 200

            # Create Promotion Event CTA must point to the sponsor promotion wizard
            assert f"/admin/sponsors/{sponsor.id}/promotion" in resp.text, (
                f"Create Promotion Event CTA (/admin/sponsors/{sponsor.id}/promotion) "
                "not found in sponsor detail page"
            )
            assert "Create Promotion Event" in resp.text, (
                "Create Promotion Event label not found in sponsor detail page"
            )

            # Import Audience section must be present (placeholder state is acceptable)
            assert "Import Audience" in resp.text, (
                "Import Audience section not found in sponsor detail page — "
                "must show audience upload entry point even in placeholder state"
            )

            # Must NOT contain old misleading Club-page copy
            # (nav bar legitimately contains /admin/clubs — check content copy only)
            assert "Club page" not in resp.text, (
                "Sponsor detail page must not contain 'Club page' guidance — "
                "sponsor promotion events are sponsor-only"
            )
            assert "go to a Club" not in resp.text, (
                "Sponsor detail page must not redirect user to Club page for creating events"
            )
        finally:
            app.dependency_overrides.clear()


class TestSponsorPromotionWizard:
    """SPON-15: Sponsor promotion wizard creates INDIVIDUAL PROMOTION_EVENT with correct organizer."""

    def test_spon_15_wizard_creates_individual_promo_event(self, test_db: Session):
        """SPON-15: POST /admin/sponsors/{id}/promotion creates:
        - Semester with semester_category=PROMOTION_EVENT
        - organizer_sponsor_id = sponsor.id
        - organizer_club_id IS NULL
        - TournamentConfiguration.participant_type = INDIVIDUAL
        - NO TournamentTeamEnrollment
        - Event visible in sponsor detail linked events list
        """
        from datetime import date
        from app.models.semester import Semester, SemesterCategory
        from app.models.tournament_configuration import TournamentConfiguration
        from app.models.team import TournamentTeamEnrollment
        from app.models.location import Location
        from app.models.campus import Campus
        from app.models.tournament_type import TournamentType as TournamentTypeModel

        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin, suffix="WizardTest")

        # P4: wizard now requires an ACTIVE campaign — create one before hitting GET/POST
        campaign = SponsorCampaign(
            sponsor_id=sponsor.id,
            name="Wizard Test Campaign",
            campaign_type="IMPORT",
            status="ACTIVE",
            created_by=admin.id,
        )
        test_db.add(campaign)
        test_db.flush()

        loc = Location(
            name=f"WizLoc-{uuid.uuid4().hex[:6]}",
            city=f"WizCity-{uuid.uuid4().hex[:6]}",
            country="HU",
        )
        test_db.add(loc)
        test_db.flush()
        campus = Campus(
            name=f"WizCampus-{uuid.uuid4().hex[:6]}",
            location_id=loc.id,
            is_active=True,
        )
        test_db.add(campus)
        test_db.flush()

        tt = TournamentTypeModel(
            code=f"wiz-tt-{uuid.uuid4().hex[:4]}",
            display_name="Wizard Test Format",
            format="HEAD_TO_HEAD",
            config={},
        )
        test_db.add(tt)
        test_db.commit()

        client = _admin_client(test_db, admin)
        event_name = f"Puma Spring Challenge {uuid.uuid4().hex[:6]}"
        try:
            # GET wizard form — must render without errors
            get_resp = client.get(f"/admin/sponsors/{sponsor.id}/promotion")
            assert get_resp.status_code == 200, (
                f"GET /admin/sponsors/{sponsor.id}/promotion returned {get_resp.status_code}"
            )
            assert "Create Promotion Event" in get_resp.text
            assert sponsor.name in get_resp.text
            # Wizard must NOT mention Club or Team
            # (nav bar legitimately contains /admin/clubs — check content copy only)
            assert "Club page" not in get_resp.text
            assert "go to a Club" not in get_resp.text

            # POST wizard — create event (P4: campaign_id required)
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/promotion",
                data={
                    "campaign_id": str(campaign.id),
                    "tournament_name": event_name,
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-03",
                    "campus_id": str(campus.id),
                    "tournament_type_id": str(tt.id),
                    "age_groups": ["YOUTH"],
                },
                follow_redirects=False,
            )
            assert resp.status_code in (302, 303), (
                f"Expected redirect, got {resp.status_code}: {resp.text[:200]}"
            )

            test_db.expire_all()

            # Verify Semester
            event = (
                test_db.query(Semester)
                .filter(
                    Semester.name.like(f"{event_name}%"),
                    Semester.semester_category == SemesterCategory.PROMOTION_EVENT,
                )
                .first()
            )
            assert event is not None, (
                f"No PROMOTION_EVENT Semester found with name like '{event_name}%'"
            )
            assert event.organizer_sponsor_id == sponsor.id, (
                f"organizer_sponsor_id expected {sponsor.id}, got {event.organizer_sponsor_id}"
            )
            assert event.organizer_club_id is None, (
                f"organizer_club_id must be NULL for sponsor event, got {event.organizer_club_id}"
            )
            assert event.organizer_campaign_id == campaign.id, (
                f"organizer_campaign_id expected {campaign.id}, got {event.organizer_campaign_id}"
            )
            assert event.age_group == "YOUTH", (
                f"age_group expected YOUTH, got {event.age_group}"
            )

            # Verify TournamentConfiguration.participant_type = INDIVIDUAL
            config = test_db.query(TournamentConfiguration).filter(
                TournamentConfiguration.semester_id == event.id
            ).first()
            assert config is not None, "TournamentConfiguration not created for sponsor event"
            assert config.participant_type == "INDIVIDUAL", (
                f"participant_type must be INDIVIDUAL for sponsor event, got {config.participant_type}"
            )

            # Verify NO TournamentTeamEnrollment — sponsor events have no teams
            enrollments = test_db.query(TournamentTeamEnrollment).filter(
                TournamentTeamEnrollment.semester_id == event.id
            ).all()
            assert len(enrollments) == 0, (
                f"Sponsor promotion event must have 0 TournamentTeamEnrollments, got {len(enrollments)}"
            )

            # Verify event visible in sponsor detail page
            detail_resp = client.get(f"/admin/sponsors/{sponsor.id}")
            assert detail_resp.status_code == 200
            assert event.name in detail_resp.text, (
                f"Created event '{event.name}' not visible in sponsor detail linked events list"
            )

        finally:
            app.dependency_overrides.clear()


class TestSponsorToggle:
    """SPON-16: POST /admin/sponsors/{id}/toggle flips active/inactive status."""

    def test_spon_16_toggle_active_inactive(self, test_db: Session):
        """SPON-16: Toggle deactivates an active sponsor, then reactivates it.

        Verifies:
        - POST /admin/sponsors/{id}/toggle → 303 redirect
        - sponsor.is_active flipped in DB
        - Detail page reflects new status
        - Second toggle restores original state
        """
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin, suffix="Toggle")
        test_db.commit()
        assert sponsor.is_active is True

        client = _admin_client(test_db, admin)
        try:
            # First toggle: active → inactive
            resp1 = client.post(
                f"/admin/sponsors/{sponsor.id}/toggle",
                follow_redirects=False,
            )
            assert resp1.status_code == 303, (
                f"Toggle expected 303, got {resp1.status_code}"
            )
            test_db.expire(sponsor)
            assert sponsor.is_active is False, (
                "Sponsor should be inactive after first toggle"
            )

            # Detail page must show inactive badge
            detail1 = client.get(f"/admin/sponsors/{sponsor.id}")
            assert detail1.status_code == 200
            assert "Inactive" in detail1.text, (
                "Detail page must show Inactive badge after toggle"
            )

            # Second toggle: inactive → active
            resp2 = client.post(
                f"/admin/sponsors/{sponsor.id}/toggle",
                follow_redirects=False,
            )
            assert resp2.status_code == 303
            test_db.expire(sponsor)
            assert sponsor.is_active is True, (
                "Sponsor should be active again after second toggle"
            )

        finally:
            app.dependency_overrides.clear()
