"""
Integration tests — tournament edit page field rendering.

  EDIT-UI-01  PROMOTION_EVENT (multi-age) → id="basic-age-group-readonly" present,
              id="basic-age-group" absent
  EDIT-UI-02  PROMOTION_EVENT (single-age) → id="basic-age-group-readonly" present,
              id="basic-age-group" absent
  EDIT-UI-03  Non-promotion event → id="basic-age-group" present
  EDIT-UI-06  PROMOTION_EVENT → campaign audience placeholder shown, standard
              enrolled-players section absent
  EDIT-UI-07  Non-promotion event → standard enrolled-players section shown,
              campaign audience placeholder absent
  EDIT-UI-08  PROMOTION_EVENT with linked campaign → id="section-campaign-audience"
              present, id="campaign-audience-backlink" present
  EDIT-UI-09  PROMOTION_EVENT without campaign link → id="section-campaign-audience"
              present, id="campaign-audience-no-link" shown (fallback)
  EDIT-UI-10  Non-promotion event → id="section-campaign-audience" absent
  EDIT-UI-11  PROMOTION_EVENT: DELETED entry excluded, ACTIVE entry shown
  EDIT-UI-12  PROMOTION_EVENT: all-DELETED campaign → empty/fallback (no table rows)

DONE = pytest tests/integration/web_flows/test_tournament_edit_ui.py -v
"""
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db
from app.dependencies import get_current_user_web
from app.models.user import User, UserRole
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.sponsor import Sponsor, SponsorCampaign, SponsorAudienceEntry
from app.models.club import CsvImportLog
from app.core.security import get_password_hash


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"edit-ui-admin+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Edit UI Admin",
        password_hash=get_password_hash("Admin1234!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_promotion_semester(db: Session, age_groups: list) -> Semester:
    sem = Semester(
        code=f"PROMO-UI-{uuid.uuid4().hex[:6]}",
        name="UI Test Promo Event",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
        status=SemesterStatus.DRAFT,
        tournament_status="DRAFT",
        semester_category=SemesterCategory.PROMOTION_EVENT,
        age_group=age_groups[0] if len(age_groups) == 1 else None,
        age_groups=age_groups,
        enrollment_cost=0,
    )
    db.add(sem)
    db.flush()
    return sem


def _make_mini_season_semester(db: Session) -> Semester:
    sem = Semester(
        code=f"MINI-UI-{uuid.uuid4().hex[:6]}",
        name="UI Test Mini Season",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
        status=SemesterStatus.DRAFT,
        tournament_status="DRAFT",
        semester_category=SemesterCategory.MINI_SEASON,
        age_group="AMATEUR",
        enrollment_cost=0,
    )
    db.add(sem)
    db.flush()
    return sem


def _client(db: Session, admin: User) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user_web] = lambda: admin
    return TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})


# ── EDIT-UI-01 ────────────────────────────────────────────────────────────────

class TestPromotionEventMultiAge:
    """EDIT-UI-01: multi-age PROMOTION_EVENT → readonly div, no select."""

    def test_edit_ui_01_readonly_present_select_absent(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_promotion_semester(test_db, age_groups=["PRE", "YOUTH"])
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            html = resp.text
            assert 'id="basic-age-group-readonly"' in html
            assert 'id="basic-age-group"' not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-02 ────────────────────────────────────────────────────────────────

class TestPromotionEventSingleAge:
    """EDIT-UI-02: single-age PROMOTION_EVENT → readonly div, no select."""

    def test_edit_ui_02_readonly_present_select_absent(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_promotion_semester(test_db, age_groups=["PRE"])
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            html = resp.text
            assert 'id="basic-age-group-readonly"' in html
            assert 'id="basic-age-group"' not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-03 ────────────────────────────────────────────────────────────────

class TestNonPromotionEventSelect:
    """EDIT-UI-03: non-promotion event → age group <select> rendered normally."""

    def test_edit_ui_03_select_present(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            assert 'id="basic-age-group"' in resp.text
            assert 'id="basic-age-group-readonly"' not in resp.text
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-06 ────────────────────────────────────────────────────────────────

class TestPromotionEventEnrolledPlayersSection:
    """EDIT-UI-06: PROMOTION_EVENT → campaign audience placeholder shown; standard
    enrolled-players section absent."""

    def test_edit_ui_06_campaign_placeholder_present_enrolled_absent(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_promotion_semester(test_db, age_groups=["PRE", "YOUTH"])
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            html = resp.text
            assert 'id="section-campaign-audience-placeholder"' in html
            assert 'id="section-checkin"' not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-07 ────────────────────────────────────────────────────────────────

class TestNonPromotionEventEnrolledPlayersSection:
    """EDIT-UI-07: non-promotion event → standard enrolled-players section shown;
    campaign audience placeholder absent."""

    def test_edit_ui_07_enrolled_section_present_placeholder_absent(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            html = resp.text
            assert 'id="section-checkin"' in html
            assert 'id="section-campaign-audience-placeholder"' not in html
        finally:
            app.dependency_overrides.clear()


# ── Helpers for EDIT-UI-08/09/10 ─────────────────────────────────────────────

def _make_sponsor(db: Session) -> Sponsor:
    s = Sponsor(
        name=f"Test Sponsor {uuid.uuid4().hex[:6]}",
        code=f"TSP-{uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    db.add(s)
    db.flush()
    return s


def _make_campaign(db: Session, sponsor: Sponsor) -> SponsorCampaign:
    c = SponsorCampaign(
        sponsor_id=sponsor.id,
        name=f"Test Campaign {uuid.uuid4().hex[:6]}",
        campaign_type="IMPORT",
        status="ACTIVE",
    )
    db.add(c)
    db.flush()
    return c


def _make_audience_entry(db: Session, sponsor: Sponsor, campaign: SponsorCampaign) -> SponsorAudienceEntry:
    log = CsvImportLog(sponsor_id=sponsor.id, campaign_id=campaign.id)
    db.add(log)
    db.flush()
    entry = SponsorAudienceEntry(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        import_log_id=log.id,
        first_name="Test",
        last_name="Player",
        email=f"audience+{uuid.uuid4().hex[:8]}@lfa.com",
        status="ACTIVE",
    )
    db.add(entry)
    db.flush()
    return entry


def _make_promotion_semester_with_campaign(
    db: Session, sponsor: Sponsor, campaign: SponsorCampaign
) -> Semester:
    sem = Semester(
        code=f"PROMO-CA-{uuid.uuid4().hex[:6]}",
        name="UI Test Promo With Campaign",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
        status=SemesterStatus.DRAFT,
        tournament_status="DRAFT",
        semester_category=SemesterCategory.PROMOTION_EVENT,
        age_group=None,
        age_groups=["PRE", "YOUTH"],
        enrollment_cost=0,
        organizer_sponsor_id=sponsor.id,
        organizer_campaign_id=campaign.id,
    )
    db.add(sem)
    db.flush()
    return sem


# ── EDIT-UI-08 ────────────────────────────────────────────────────────────────

class TestCampaignAudienceSectionWithLink:
    """EDIT-UI-08: PROMOTION_EVENT with linked campaign → audience section and
    backlink rendered."""

    def test_edit_ui_08_section_and_backlink_present(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db)
        campaign = _make_campaign(test_db, sponsor)
        _make_audience_entry(test_db, sponsor, campaign)
        sem = _make_promotion_semester_with_campaign(test_db, sponsor, campaign)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            html = resp.text
            assert 'id="section-campaign-audience"' in html
            assert 'id="campaign-audience-backlink"' in html
            assert 'id="campaign-audience-no-link"' not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-09 ────────────────────────────────────────────────────────────────

class TestCampaignAudienceSectionFallback:
    """EDIT-UI-09: PROMOTION_EVENT without campaign link → audience section shows
    fallback message."""

    def test_edit_ui_09_section_present_fallback_shown(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_promotion_semester(test_db, age_groups=["PRE"])
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            html = resp.text
            assert 'id="section-campaign-audience"' in html
            assert 'id="campaign-audience-no-link"' in html
            assert 'id="campaign-audience-backlink"' not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-10 ────────────────────────────────────────────────────────────────

class TestNonPromotionEventNoCampaignAudienceSection:
    """EDIT-UI-10: non-promotion event → campaign audience section absent."""

    def test_edit_ui_10_section_absent(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            assert 'id="section-campaign-audience"' not in resp.text
        finally:
            app.dependency_overrides.clear()


# ── Helpers for EDIT-UI-11/12 ─────────────────────────────────────────────────

def _make_audience_entry_with_status(
    db: Session, sponsor: Sponsor, campaign: SponsorCampaign, status: str, email_hint: str = ""
) -> SponsorAudienceEntry:
    log = CsvImportLog(sponsor_id=sponsor.id, campaign_id=campaign.id)
    db.add(log)
    db.flush()
    entry = SponsorAudienceEntry(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        import_log_id=log.id,
        first_name="Test",
        last_name=f"Player{email_hint}",
        email=f"audience-{status.lower()}{email_hint}+{uuid.uuid4().hex[:8]}@lfa.com",
        status=status,
    )
    db.add(entry)
    db.flush()
    return entry


# ── EDIT-UI-11 ────────────────────────────────────────────────────────────────

class TestCampaignAudienceDeletedFiltered:
    """EDIT-UI-11: DELETED entry is excluded; ACTIVE entry appears in the table."""

    def test_edit_ui_11_deleted_excluded_active_shown(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db)
        campaign = _make_campaign(test_db, sponsor)
        active_entry = _make_audience_entry_with_status(test_db, sponsor, campaign, "ACTIVE", "a")
        _make_audience_entry_with_status(test_db, sponsor, campaign, "DELETED", "d")
        sem = _make_promotion_semester_with_campaign(test_db, sponsor, campaign)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            html = resp.text
            # ACTIVE entry email must appear in the rendered table
            assert active_entry.email in html
            # DELETED entry last name suffix must NOT appear
            assert "PlayerDELETED" not in html
            assert "Playerd" not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-12 ────────────────────────────────────────────────────────────────

class TestCampaignAudienceAllDeletedFallback:
    """EDIT-UI-12: when every entry in the campaign is DELETED, the section renders
    the empty-state message (no misleading participant table)."""

    def test_edit_ui_12_all_deleted_shows_empty_state(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db)
        campaign = _make_campaign(test_db, sponsor)
        _make_audience_entry_with_status(test_db, sponsor, campaign, "DELETED", "x")
        _make_audience_entry_with_status(test_db, sponsor, campaign, "DELETED", "y")
        sem = _make_promotion_semester_with_campaign(test_db, sponsor, campaign)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            html = resp.text
            # Section must exist (campaign IS linked)
            assert 'id="section-campaign-audience"' in html
            # No audience table rows rendered — the "No audience entries" fallback appears
            assert "No audience entries found for this campaign" in html
            # The deleted entry emails must not be visible
            assert "Playerx" not in html
            assert "Playery" not in html
        finally:
            app.dependency_overrides.clear()
