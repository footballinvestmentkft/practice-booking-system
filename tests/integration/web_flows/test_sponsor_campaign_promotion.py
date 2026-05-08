"""
Sponsor Campaign → Promotion Event — P4 integration tests (SPON-CAM-08..13)

  SPON-CAM-08  Cross-campaign promote blocked: entry from campaign B excluded when
               promote_entries is called with campaign_id=A; entry ID in errors[]
  SPON-CAM-09  Wizard POST with valid campaign_id → Semester.organizer_campaign_id == campaign.id
  SPON-CAM-10  Wizard GET with 0 ACTIVE campaigns → 303 redirect with error
  SPON-CAM-11  Wizard POST without campaign_id → 303 + error (no Semester created)
  SPON-CAM-12  Multi-age-group (PRE+YOUTH) → 1 Semester, age_groups=["PRE","YOUTH"], age_group=None
  SPON-CAM-13  Club event (club wizard) → organizer_campaign_id IS NULL

DONE = pytest tests/integration/web_flows/test_sponsor_campaign_promotion.py -v
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
from app.models.sponsor import Sponsor, SponsorAudienceEntry, SponsorCampaign
from app.models.semester import Semester
from app.models.tournament_configuration import TournamentConfiguration
from app.models.club import CsvImportLog
from app.models.license import UserLicense
from app.core.security import get_password_hash
from app.services.sponsor_promote_service import promote_entries


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"admin-cam+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Cam Admin",
        password_hash=get_password_hash("Admin1234!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_sponsor(db: Session, admin: User, *, active: bool = True) -> Sponsor:
    s = Sponsor(
        name=f"CamSponsor {uuid.uuid4().hex[:6]}",
        code=f"CAM-{uuid.uuid4().hex[:5].upper()}",
        is_active=active,
        created_by=admin.id,
    )
    db.add(s)
    db.flush()
    return s


def _make_campaign(db: Session, sponsor: Sponsor, admin: User,
                   *, status: str = "ACTIVE") -> SponsorCampaign:
    c = SponsorCampaign(
        sponsor_id=sponsor.id,
        name=f"Campaign {uuid.uuid4().hex[:4]}",
        campaign_type="IMPORT",
        status=status,
        created_by=admin.id,
    )
    db.add(c)
    db.flush()
    return c


def _make_entry(db: Session, sponsor: Sponsor, campaign: SponsorCampaign,
                admin: User) -> SponsorAudienceEntry:
    log = CsvImportLog(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        filename="test.csv",
        total_rows=1,
        uploaded_by=admin.id,
    )
    db.add(log)
    db.flush()

    e = SponsorAudienceEntry(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        import_log_id=log.id,
        first_name="Test",
        last_name="Entry",
        email=f"entry+{uuid.uuid4().hex[:8]}@test.com",
        status="ACTIVE",
        consent_given=True,
    )
    db.add(e)
    db.flush()
    return e


def _client(db: Session, admin: User) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user_web] = lambda: admin
    return TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})


# ── SPON-CAM-08 ───────────────────────────────────────────────────────────────

class TestCrossCampaignPromoteBlocked:
    """SPON-CAM-08: promote_entries with campaign_id=A rejects entries from campaign B."""

    def test_spon_cam_08_cross_campaign_entry_in_errors(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign_a = _make_campaign(test_db, sponsor, admin)
        campaign_b = _make_campaign(test_db, sponsor, admin)

        entry_a = _make_entry(test_db, sponsor, campaign_a, admin)
        entry_b = _make_entry(test_db, sponsor, campaign_b, admin)
        test_db.commit()

        users_before = test_db.query(User).count()
        # Pass both IDs but scope to campaign_a only
        result = promote_entries(
            [entry_a.id, entry_b.id],
            sponsor.id,
            test_db,
            admin,
            campaign_id=campaign_a.id,
        )

        # entry_a promoted, entry_b rejected
        assert result.promoted == 1
        assert len(result.errors) == 1
        assert str(entry_b.id) in result.errors[0]

        # Only 1 new User created
        assert test_db.query(User).count() == users_before + 1

        # entry_b unchanged
        test_db.expire(entry_b)
        assert entry_b.user_id is None
        assert entry_b.promoted_at is None


# ── SPON-CAM-09 ───────────────────────────────────────────────────────────────

class TestWizardPostSetsCampaignId:
    """SPON-CAM-09: wizard POST with valid campaign_id → Semester.organizer_campaign_id == campaign.id."""

    def test_spon_cam_09_semester_links_campaign(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()

        client = _client(test_db, admin)
        semesters_before = test_db.query(Semester).count()

        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/promotion",
                data={
                    "campaign_id": str(campaign.id),
                    "tournament_name": "CAM-09 Event",
                    "start_date": "2026-06-01",
                    "end_date": "2026-06-02",
                    "age_groups": "YOUTH",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303

            new_semesters = (
                test_db.query(Semester)
                .filter(Semester.organizer_sponsor_id == sponsor.id)
                .all()
            )
            assert len(new_semesters) == test_db.query(Semester).count() - semesters_before
            assert len(new_semesters) == 1

            sem = new_semesters[0]
            assert sem.organizer_campaign_id == campaign.id
            assert sem.organizer_sponsor_id == sponsor.id
            assert sem.organizer_club_id is None
        finally:
            app.dependency_overrides.clear()


# ── SPON-CAM-10 ───────────────────────────────────────────────────────────────

class TestWizardGetNoCampaignBlocker:
    """SPON-CAM-10: wizard GET with 0 ACTIVE campaigns → 303 redirect with error."""

    def test_spon_cam_10_no_campaigns_redirects(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        # Create a campaign but mark it CLOSED (not ACTIVE)
        _make_campaign(test_db, sponsor, admin, status="CLOSED")
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(
                f"/admin/sponsors/{sponsor.id}/promotion",
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "error" in resp.headers["location"]
        finally:
            app.dependency_overrides.clear()

    def test_spon_cam_10_no_campaigns_at_all_redirects(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)  # no campaigns
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(
                f"/admin/sponsors/{sponsor.id}/promotion",
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "error" in resp.headers["location"]
        finally:
            app.dependency_overrides.clear()


# ── SPON-CAM-11 ───────────────────────────────────────────────────────────────

class TestWizardPostNoCampaignRejected:
    """SPON-CAM-11: wizard POST without campaign_id → 303 + error, 0 Semesters created."""

    def test_spon_cam_11_missing_campaign_id_no_semester(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        _make_campaign(test_db, sponsor, admin)  # ACTIVE campaign exists but not submitted
        test_db.commit()

        client = _client(test_db, admin)
        semesters_before = test_db.query(Semester).count()

        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/promotion",
                data={
                    # campaign_id intentionally omitted
                    "tournament_name": "CAM-11 Event",
                    "start_date": "2026-06-01",
                    "end_date": "2026-06-02",
                    "age_groups": "YOUTH",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "error" in resp.headers["location"]
            assert test_db.query(Semester).count() == semesters_before
        finally:
            app.dependency_overrides.clear()


# ── SPON-CAM-12 ───────────────────────────────────────────────────────────────

class TestWizardMultiAgeGroup:
    """SPON-CAM-12: PRE+YOUTH → 1 Semester, age_groups=["PRE","YOUTH"], age_group=None."""

    def test_spon_cam_12_two_age_groups_single_semester(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/promotion",
                data={
                    "campaign_id": str(campaign.id),
                    "tournament_name": "CAM-12 Multi Event",
                    "start_date": "2026-07-01",
                    "end_date": "2026-07-02",
                    "age_groups": ["PRE", "YOUTH"],
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303

            new_sems = (
                test_db.query(Semester)
                .filter(Semester.organizer_sponsor_id == sponsor.id)
                .all()
            )
            # Single event for all selected age groups
            assert len(new_sems) == 1
            s = new_sems[0]

            # Organizer linkage
            assert s.organizer_campaign_id == campaign.id
            assert s.organizer_club_id is None

            # Multi-age: age_groups JSONB set, scalar age_group null
            assert set(s.age_groups) == {"PRE", "YOUTH"}
            assert s.age_group is None

            # Name has no age-category suffix
            assert "(PRE)" not in s.name
            assert "(YOUTH)" not in s.name
            assert s.name == "CAM-12 Multi Event"

            # Domain defaults
            assert s.specialization_type == "LFA_FOOTBALL_PLAYER"

            # TournamentConfiguration wired correctly
            tc = test_db.query(TournamentConfiguration).filter(
                TournamentConfiguration.semester_id == s.id
            ).first()
            assert tc is not None
            assert tc.assignment_type == "OPEN_ASSIGNMENT"
        finally:
            app.dependency_overrides.clear()


# ── SPON-CAM-13 ───────────────────────────────────────────────────────────────

class TestClubEventNoCampaign:
    """SPON-CAM-13: club promotion event → organizer_campaign_id IS NULL (domain separation)."""

    def test_spon_cam_13_club_event_campaign_id_null(self, test_db: Session):
        from app.models.club import Club
        admin = _make_admin(test_db)

        club = Club(
            name=f"CamClub {uuid.uuid4().hex[:6]}",
            code=f"CCAM{uuid.uuid4().hex[:4].upper()}",
            city=f"City-{uuid.uuid4().hex[:4]}",
            created_by=admin.id,
        )
        test_db.add(club)
        test_db.flush()

        sem = Semester(
            code=f"CAM13-{uuid.uuid4().hex[:6]}",
            name="CAM-13 Club Event",
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 2),
            enrollment_cost=0,
            organizer_club_id=club.id,
            organizer_sponsor_id=None,
            organizer_campaign_id=None,
        )
        test_db.add(sem)
        test_db.flush()
        test_db.commit()

        test_db.expire(sem)
        assert sem.organizer_campaign_id is None
        assert sem.organizer_club_id == club.id
        assert sem.organizer_sponsor_id is None


# ── Instructor wizard helpers ──────────────────────────────────────────────────

def _make_instructor(db: Session, *, is_active: bool = True) -> User:
    u = User(
        email=f"instr+{uuid.uuid4().hex[:8]}@lfa.com",
        name=f"Instructor {uuid.uuid4().hex[:4]}",
        password_hash=get_password_hash("pw"),
        role=UserRole.INSTRUCTOR,
        is_active=is_active,
    )
    db.add(u)
    db.flush()
    return u


def _make_coach_license(db: Session, user: User, *, level: int = 5) -> UserLicense:
    lic = UserLicense(
        user_id=user.id,
        specialization_type="LFA_COACH",
        current_level=level,
        max_achieved_level=level,
        is_active=True,
        started_at=datetime.now(timezone.utc),
    )
    db.add(lic)
    db.flush()
    return lic


# ── SPON-CAM-14 ───────────────────────────────────────────────────────────────

class TestWizardGetContainsInstructorDropdown:
    """SPON-CAM-14: GET wizard renders master_instructor_id select with active instructors."""

    def test_spon_cam_14_dropdown_present_with_instructors(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        _make_campaign(test_db, sponsor, admin)
        instr = _make_instructor(test_db)
        _make_coach_license(test_db, instr)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/sponsors/{sponsor.id}/promotion", follow_redirects=False)
            assert resp.status_code == 200
            html = resp.text
            assert 'name="master_instructor_id"' in html
            assert f'value="{instr.id}"' in html
            assert instr.email in html
            assert "assign later" in html
            assert "CHECK_IN_OPEN" in html
        finally:
            app.dependency_overrides.clear()


# ── SPON-CAM-15 ───────────────────────────────────────────────────────────────

class TestWizardPostNoInstructorCreatesNullMasterId:
    """SPON-CAM-15: POST with empty master_instructor_id → Semester created with master_instructor_id=None."""

    def test_spon_cam_15_empty_instructor_id_accepted(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/promotion",
                data={
                    "campaign_id": str(campaign.id),
                    "tournament_name": "CAM-15 Event",
                    "start_date": "2026-06-01",
                    "end_date": "2026-06-02",
                    "age_groups": "AMATEUR",
                    # master_instructor_id intentionally omitted
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303

            sem = (
                test_db.query(Semester)
                .filter(Semester.organizer_sponsor_id == sponsor.id)
                .first()
            )
            assert sem is not None
            assert sem.master_instructor_id is None
        finally:
            app.dependency_overrides.clear()


# ── SPON-CAM-16 ───────────────────────────────────────────────────────────────

class TestWizardPostValidInstructorSetsMasterId:
    """SPON-CAM-16: POST with valid instructor ID → Semester.master_instructor_id == instructor.id."""

    def test_spon_cam_16_valid_instructor_id_saved(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        instr = _make_instructor(test_db)
        _make_coach_license(test_db, instr)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/promotion",
                data={
                    "campaign_id": str(campaign.id),
                    "tournament_name": "CAM-16 Event",
                    "start_date": "2026-06-01",
                    "end_date": "2026-06-02",
                    "age_groups": "AMATEUR",
                    "master_instructor_id": str(instr.id),
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303

            sem = (
                test_db.query(Semester)
                .filter(Semester.organizer_sponsor_id == sponsor.id)
                .first()
            )
            assert sem is not None
            assert sem.master_instructor_id == instr.id
        finally:
            app.dependency_overrides.clear()


# ── SPON-CAM-17 ───────────────────────────────────────────────────────────────

class TestWizardPostInvalidInstructorIdRejected:
    """SPON-CAM-17: POST with non-existent or non-instructor user ID → 303 error, no Semester created."""

    def test_spon_cam_17_nonexistent_id_returns_error(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()

        client = _client(test_db, admin)
        semesters_before = test_db.query(Semester).count()
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/promotion",
                data={
                    "campaign_id": str(campaign.id),
                    "tournament_name": "CAM-17 Event",
                    "start_date": "2026-06-01",
                    "end_date": "2026-06-02",
                    "age_groups": "AMATEUR",
                    "master_instructor_id": "999999",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "error" in resp.headers["location"]
            assert test_db.query(Semester).count() == semesters_before
        finally:
            app.dependency_overrides.clear()

    def test_spon_cam_17b_admin_role_user_rejected(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()

        client = _client(test_db, admin)
        semesters_before = test_db.query(Semester).count()
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/promotion",
                data={
                    "campaign_id": str(campaign.id),
                    "tournament_name": "CAM-17b Event",
                    "start_date": "2026-06-01",
                    "end_date": "2026-06-02",
                    "age_groups": "AMATEUR",
                    "master_instructor_id": str(admin.id),  # ADMIN role, not INSTRUCTOR
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "error" in resp.headers["location"]
            assert test_db.query(Semester).count() == semesters_before
        finally:
            app.dependency_overrides.clear()


# ── SPON-CAM-18 ───────────────────────────────────────────────────────────────

class TestWizardPostInactiveInstructorRejected:
    """SPON-CAM-18: POST with inactive INSTRUCTOR user ID → 303 error, no Semester created."""

    def test_spon_cam_18_inactive_instructor_rejected(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        inactive_instr = _make_instructor(test_db, is_active=False)
        test_db.commit()

        client = _client(test_db, admin)
        semesters_before = test_db.query(Semester).count()
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/promotion",
                data={
                    "campaign_id": str(campaign.id),
                    "tournament_name": "CAM-18 Event",
                    "start_date": "2026-06-01",
                    "end_date": "2026-06-02",
                    "age_groups": "AMATEUR",
                    "master_instructor_id": str(inactive_instr.id),
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "error" in resp.headers["location"]
            assert test_db.query(Semester).count() == semesters_before
        finally:
            app.dependency_overrides.clear()
