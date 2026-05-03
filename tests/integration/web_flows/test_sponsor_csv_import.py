"""
Sponsor Audience CSV Import Tests — SPON-CSV-01 through SPON-CSV-10

  SPON-CSV-01  GET /csv-import → 200, sponsor name visible
  SPON-CSV-02  POST /preview valid CSV → 200, age breakdown, consent breakdown
  SPON-CSV-03  POST /apply → SponsorAudienceEntry created, 0 Club/Team/User side effects
  SPON-CSV-04  Re-import same email → 1 entry (updated, not duplicate)
  SPON-CSV-05  consent_given=False → status=SUPPRESSED
  SPON-CSV-06  DOB conflict with age_category → DOB wins, warning in log
  SPON-CSV-07  Forbidden column (club_name) → global warning, row not failed, 0 Club created
  SPON-CSV-08  Email match to existing User → user_id set, User profile unchanged
  SPON-CSV-09  Inactive sponsor → import routes blocked (303 redirect)
  SPON-CSV-10  apply → CsvImportLog.sponsor_id = sponsor.id, club_id NULL

DONE = pytest tests/integration/web_flows/test_sponsor_csv_import.py -v
"""
import base64
import io
import uuid
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from app.main import app
from app.database import get_db
from app.dependencies import get_current_user_web
from app.models.user import User, UserRole
from app.models.sponsor import Sponsor, SponsorAudienceEntry, SponsorCampaign
from app.models.club import Club, CsvImportLog
from app.models.team import Team, TeamMember
from app.core.security import get_password_hash


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"admin-csv+{uuid.uuid4().hex[:8]}@lfa.com",
        name="CSV Admin",
        password_hash=get_password_hash("Admin1234!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_sponsor(db: Session, admin: User, *, active: bool = True) -> Sponsor:
    s = Sponsor(
        name=f"Puma Test {uuid.uuid4().hex[:6]}",
        code=f"PUMA-{uuid.uuid4().hex[:5].upper()}",
        is_active=active,
        created_by=admin.id,
    )
    db.add(s)
    db.flush()
    return s


def _make_campaign(db: Session, sponsor: Sponsor, admin: User) -> SponsorCampaign:
    c = SponsorCampaign(
        sponsor_id=sponsor.id,
        name=f"CSV Campaign {uuid.uuid4().hex[:4]}",
        campaign_type="IMPORT",
        status="ACTIVE",
        created_by=admin.id,
    )
    db.add(c)
    db.flush()
    return c


def _client(db: Session, admin: User) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user_web] = lambda: admin
    return TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})


def _csv(rows: list[str]) -> bytes:
    header = "first_name,last_name,email,date_of_birth,age_category,consent_given,consent_source,campaign_source"
    return "\n".join([header] + rows).encode()


def _upload_file(content: bytes, filename: str = "test.csv"):
    return ("file", (filename, io.BytesIO(content), "text/csv"))


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestCsvUploadForm:
    """SPON-CSV-01"""

    def test_spon_csv_01_upload_form_loads(self, test_db: Session):
        """GET /campaigns/{cid}/import → 200, sponsor name visible."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()
        client = _client(test_db, admin)
        try:
            resp = client.get(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import"
            )
            assert resp.status_code == 200
            assert sponsor.name in resp.text
            assert "Import Audience" in resp.text
            assert "first_name" in resp.text   # column hint table
        finally:
            app.dependency_overrides.clear()


class TestCsvPreview:
    """SPON-CSV-02"""

    def test_spon_csv_02_preview_shows_breakdown(self, test_db: Session):
        """POST /campaigns/{cid}/import/preview valid CSV → 200, age breakdown, consent counts visible."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()
        client = _client(test_db, admin)

        csv_content = _csv([
            "Kovács,Dávid,k.david@test.com,2012-03-15,U12,1,test form,Puma 2026",
            "Nagy,Péter,n.peter@test.com,2014-07-22,U10,1,test form,Puma 2026",
            "Szabó,Anna,s.anna@test.com,,YOUTH,0,,",
        ])
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/preview",
                files=[_upload_file(csv_content)],
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
            assert "Preview" in resp.text
            assert "3" in resp.text           # total rows
            # Age categories should appear
            assert "PRE" in resp.text         # U12 and U10 both → PRE
            assert "YOUTH" in resp.text
            # Consent breakdown
            assert "Contactable" in resp.text
            assert "Suppressed" in resp.text
            # Must NOT have written anything to DB
            count = test_db.query(SponsorAudienceEntry).filter(
                SponsorAudienceEntry.sponsor_id == sponsor.id
            ).count()
            assert count == 0, "Preview must not write SponsorAudienceEntry rows"
        finally:
            app.dependency_overrides.clear()


class TestCsvApply:
    """SPON-CSV-03, SPON-CSV-10"""

    def test_spon_csv_03_apply_creates_entries_no_side_effects(self, test_db: Session):
        """POST /campaigns/{cid}/import/apply → SponsorAudienceEntry created, 0 Club/Team/TeamMember/User side effects."""
        from app.models.team import TournamentTeamEnrollment

        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()
        client = _client(test_db, admin)

        csv_content = _csv([
            "Kovács,Dávid,k.david@apply.test,2012-03-15,U12,1,form,Puma 2026",
            "Nagy,Péter,n.peter@apply.test,,YOUTH,1,form,Puma 2026",
        ])
        b64 = base64.b64encode(csv_content).decode()

        clubs_before  = test_db.query(Club).count()
        teams_before  = test_db.query(Team).count()
        users_before  = test_db.query(User).count()
        tte_before    = test_db.query(TournamentTeamEnrollment).count()

        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/apply",
                data={"csv_data": b64, "filename": "apply_test.csv"},
                follow_redirects=False,
            )
            assert resp.status_code == 303

            # SponsorAudienceEntry created
            entries = test_db.query(SponsorAudienceEntry).filter(
                SponsorAudienceEntry.sponsor_id == sponsor.id
            ).all()
            assert len(entries) == 2, f"Expected 2 entries, got {len(entries)}"

            # No Club, Team, TeamMember, TournamentTeamEnrollment, User created
            assert test_db.query(Club).count() == clubs_before, "No Club must be created"
            assert test_db.query(Team).count() == teams_before, "No Team must be created"
            assert test_db.query(User).count() == users_before, "No User must be created"
            assert test_db.query(TeamMember).filter(
                TeamMember.user_id.in_([e.user_id for e in entries if e.user_id])
            ).count() == 0, "No TeamMember must be created"
            assert test_db.query(TournamentTeamEnrollment).count() == tte_before, \
                "No TournamentTeamEnrollment must be created"

        finally:
            app.dependency_overrides.clear()

    def test_spon_csv_10_log_has_sponsor_id_no_club_id(self, test_db: Session):
        """POST /campaigns/{cid}/import/apply → CsvImportLog.sponsor_id = sponsor.id, club_id NULL."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()
        client = _client(test_db, admin)

        csv_content = _csv(["Alpha,Beta,ab@log.test,,PRO,1,,"])
        b64 = base64.b64encode(csv_content).decode()
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/apply",
                data={"csv_data": b64, "filename": "log_test.csv"},
                follow_redirects=False,
            )
            assert resp.status_code == 303

            log = test_db.query(CsvImportLog).filter(
                CsvImportLog.sponsor_id == sponsor.id
            ).order_by(CsvImportLog.id.desc()).first()
            assert log is not None, "CsvImportLog must be created"
            assert log.sponsor_id == sponsor.id
            assert log.club_id is None, "CsvImportLog.club_id must be NULL for sponsor import"
            assert log.rows_created == 1
        finally:
            app.dependency_overrides.clear()


class TestReImport:
    """SPON-CSV-04"""

    def test_spon_csv_04_reimport_updates_not_duplicates(self, test_db: Session):
        """Re-import same email in same campaign → 1 SponsorAudienceEntry (updated, not duplicate)."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()
        client = _client(test_db, admin)

        email = f"reimport+{uuid.uuid4().hex[:6]}@test.com"
        csv1 = _csv([f"First,Last,{email},,YOUTH,1,,"])
        csv2 = _csv([f"Updated,Name,{email},,AMATEUR,1,,"])

        b64_1 = base64.b64encode(csv1).decode()
        b64_2 = base64.b64encode(csv2).decode()
        try:
            client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/apply",
                data={"csv_data": b64_1, "filename": "run1.csv"},
                follow_redirects=False,
            )
            client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/apply",
                data={"csv_data": b64_2, "filename": "run2.csv"},
                follow_redirects=False,
            )

            entries = test_db.query(SponsorAudienceEntry).filter(
                SponsorAudienceEntry.sponsor_id == sponsor.id,
                SponsorAudienceEntry.email == email,
            ).all()
            assert len(entries) == 1, f"Re-import must not duplicate — got {len(entries)} entries"
            assert entries[0].first_name == "Updated", "Re-import must update first_name"
            assert entries[0].age_category == "AMATEUR", "Re-import must update age_category"
        finally:
            app.dependency_overrides.clear()


class TestConsentStatus:
    """SPON-CSV-05"""

    def test_spon_csv_05_no_consent_gives_suppressed_status(self, test_db: Session):
        """consent_given=False (or missing) → status=SUPPRESSED."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()
        client = _client(test_db, admin)

        csv_content = _csv([
            "NoConsent,User,no.consent@test.com,,YOUTH,0,,",
            "MissingConsent,User,missing.consent@test.com,,PRO,,,",
            "HasConsent,User,has.consent@test.com,,AMATEUR,1,,",
        ])
        b64 = base64.b64encode(csv_content).decode()
        try:
            client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/apply",
                data={"csv_data": b64, "filename": "consent_test.csv"},
                follow_redirects=False,
            )

            suppressed = test_db.query(SponsorAudienceEntry).filter(
                SponsorAudienceEntry.sponsor_id == sponsor.id,
                SponsorAudienceEntry.status == "SUPPRESSED",
            ).count()
            active = test_db.query(SponsorAudienceEntry).filter(
                SponsorAudienceEntry.sponsor_id == sponsor.id,
                SponsorAudienceEntry.status == "ACTIVE",
            ).count()
            assert suppressed == 2, f"Expected 2 SUPPRESSED, got {suppressed}"
            assert active == 1, f"Expected 1 ACTIVE, got {active}"
        finally:
            app.dependency_overrides.clear()


class TestDobConflict:
    """SPON-CSV-06"""

    def test_spon_csv_06_dob_overrides_age_category(self, test_db: Session):
        """DOB derives YOUTH (2010) but CSV says PRO → DOB wins, warning in log."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()
        client = _client(test_db, admin)

        # 2010-01-01 → age ~16 → YOUTH; CSV says PRO → conflict
        csv_content = _csv(["Conflict,User,conflict@test.com,2010-01-01,PRO,1,,"])
        b64 = base64.b64encode(csv_content).decode()
        try:
            client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/apply",
                data={"csv_data": b64, "filename": "dob_test.csv"},
                follow_redirects=False,
            )

            entry = test_db.query(SponsorAudienceEntry).filter(
                SponsorAudienceEntry.sponsor_id == sponsor.id,
                SponsorAudienceEntry.email == "conflict@test.com",
            ).first()
            assert entry is not None
            assert entry.age_category == "YOUTH", (
                f"DOB (2010) must derive YOUTH, but got {entry.age_category}"
            )
            assert entry.age_raw == "PRO", (
                f"Original CSV value must be preserved in age_raw, got {entry.age_raw}"
            )

            # Warning must be in log.errors
            log = test_db.query(CsvImportLog).filter(
                CsvImportLog.sponsor_id == sponsor.id
            ).order_by(CsvImportLog.id.desc()).first()
            assert log is not None
            warning_texts = [e.get("reason", "") for e in (log.errors or [])]
            assert any("conflict" in w.lower() or "DOB" in w for w in warning_texts), (
                f"Expected DOB conflict warning in log.errors, got: {warning_texts}"
            )
        finally:
            app.dependency_overrides.clear()


class TestForbiddenColumns:
    """SPON-CSV-07"""

    def test_spon_csv_07_forbidden_columns_ignored_no_club_created(self, test_db: Session):
        """CSV with club_name column → global warning, row imported, 0 Club created."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()
        client = _client(test_db, admin)

        clubs_before = test_db.query(Club).count()

        # CSV with forbidden club_name column
        header = "first_name,last_name,email,club_name,age_category,consent_given"
        row = "Forbidden,Col,forbidden.col@test.com,PumaFC,YOUTH,1"
        csv_content = f"{header}\n{row}".encode()

        b64 = base64.b64encode(csv_content).decode()
        try:
            # Preview must show global warning
            resp_preview = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/preview",
                files=[_upload_file(csv_content)],
            )
            assert resp_preview.status_code == 200
            assert "club_name" in resp_preview.text.lower(), (
                "Preview must mention forbidden column club_name in warning"
            )

            # Apply — row must be imported, but no Club created
            client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/apply",
                data={"csv_data": b64, "filename": "forbidden.csv"},
                follow_redirects=False,
            )

            entry = test_db.query(SponsorAudienceEntry).filter(
                SponsorAudienceEntry.sponsor_id == sponsor.id,
                SponsorAudienceEntry.email == "forbidden.col@test.com",
            ).first()
            assert entry is not None, "Row must be imported despite forbidden column"
            assert test_db.query(Club).count() == clubs_before, "No Club must be created"
        finally:
            app.dependency_overrides.clear()


class TestUserMatch:
    """SPON-CSV-08"""

    def test_spon_csv_08_email_match_links_user_id_no_profile_change(self, test_db: Session):
        """Existing User email match → user_id set on entry, User profile NOT modified."""
        from app.models.license import UserLicense

        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)

        existing_user = User(
            email=f"existing+{uuid.uuid4().hex[:6]}@lfa.com",
            name="Existing Player",
            password_hash=get_password_hash("Pass1234!"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(existing_user)
        test_db.commit()
        original_name = existing_user.name

        client = _client(test_db, admin)

        csv_content = _csv([
            f"CSV,Name,{existing_user.email},,YOUTH,1,,",
        ])
        b64 = base64.b64encode(csv_content).decode()
        try:
            client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/apply",
                data={"csv_data": b64, "filename": "usermatch.csv"},
                follow_redirects=False,
            )

            entry = test_db.query(SponsorAudienceEntry).filter(
                SponsorAudienceEntry.sponsor_id == sponsor.id,
                SponsorAudienceEntry.email == existing_user.email,
            ).first()
            assert entry is not None
            assert entry.user_id == existing_user.id, (
                "user_id must be linked for email-matched User"
            )

            # User profile must NOT be modified by import
            test_db.expire(existing_user)
            assert existing_user.name == original_name, (
                f"User name must not be modified by sponsor import "
                f"(expected '{original_name}', got '{existing_user.name}')"
            )
        finally:
            app.dependency_overrides.clear()


class TestInactiveGuard:
    """SPON-CSV-09"""

    def test_spon_csv_09_inactive_sponsor_blocked(self, test_db: Session):
        """Inactive sponsor → campaign-scoped upload/preview/apply routes return 303 with error."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin, active=False)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()
        client = _client(test_db, admin)

        dummy_csv = _csv(["A,B,ab@inactive.test,,YOUTH,1,,"])
        b64 = base64.b64encode(dummy_csv).decode()
        try:
            # GET upload form — inactive guard fires
            r1 = client.get(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import",
                follow_redirects=False,
            )
            assert r1.status_code == 303
            assert "error" in r1.headers.get("location", "").lower()

            # POST preview — inactive guard fires
            r2 = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/preview",
                files=[_upload_file(dummy_csv)],
                follow_redirects=False,
            )
            assert r2.status_code == 303

            # POST apply — inactive guard fires
            r3 = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/apply",
                data={"csv_data": b64, "filename": "inactive.csv"},
                follow_redirects=False,
            )
            assert r3.status_code == 303

            # No entries created
            count = test_db.query(SponsorAudienceEntry).filter(
                SponsorAudienceEntry.sponsor_id == sponsor.id
            ).count()
            assert count == 0, "Inactive sponsor must not accumulate any audience entries"
        finally:
            app.dependency_overrides.clear()


class TestAudienceList:
    """SPON-CSV-11"""

    def test_spon_csv_11_audience_list_shows_imported_entries(self, test_db: Session):
        """GET /campaigns/{cid}/audience → 200, shows all imported entries."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()
        client = _client(test_db, admin)

        csv_content = _csv([
            "Alice,Smith,alice.s@aud.test,2010-01-01,YOUTH,1,form,campaign",
            "Bob,Jones,bob.j@aud.test,,PRE,0,,",
        ])
        b64 = base64.b64encode(csv_content).decode()
        try:
            client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/apply",
                data={"csv_data": b64, "filename": "aud.csv"},
                follow_redirects=False,
            )

            resp = client.get(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/audience"
            )
            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

            body = resp.text
            assert "alice.s@aud.test" in body
            assert "bob.j@aud.test" in body
            assert "ACTIVE" in body
            assert "SUPPRESSED" in body
            assert "YOUTH" in body
            assert "PRE" in body
            assert sponsor.name in body
        finally:
            app.dependency_overrides.clear()
