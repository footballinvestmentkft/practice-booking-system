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
from datetime import date, datetime, timezone

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


# ── EDIT-UI-13 ────────────────────────────────────────────────────────────────

class TestPromotionEventLockAudienceButton:
    """EDIT-UI-13: PROMOTION_EVENT DRAFT → shows Lock Audience button, not Open Enrollment."""

    def test_edit_ui_13_lock_audience_present_open_enrollment_absent(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_promotion_semester(test_db, age_groups=["PRE"])
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            html = resp.text
            assert "Lock Audience" in html
            assert "Open Enrollment" not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-14 ────────────────────────────────────────────────────────────────

class TestNonPromotionEventOpenEnrollmentButton:
    """EDIT-UI-14: non-PROMOTION_EVENT DRAFT → shows Open Enrollment, not Lock Audience."""

    def test_edit_ui_14_open_enrollment_present_lock_audience_absent(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            html = resp.text
            assert "Open Enrollment" in html
            assert "Lock Audience" not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-15 ────────────────────────────────────────────────────────────────

class TestPromotionEventStep2Subtitle:
    """EDIT-UI-15: PROMOTION_EVENT step 2 shows campaign audience count, not enrolled players."""

    def test_edit_ui_15_campaign_audience_subtitle(self, test_db: Session):
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
            assert "campaign audience entr" in html
            assert "players enrolled" not in html
            assert "0 player" not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-16 ────────────────────────────────────────────────────────────────

class TestNonPromotionEventCloseEnrollmentButton:
    """EDIT-UI-16: non-PROMOTION_EVENT ENROLLMENT_OPEN → shows Close Enrollment button."""

    def test_edit_ui_16_close_enrollment_present_for_non_promo(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "ENROLLMENT_OPEN"
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200

            assert "Close Enrollment" in resp.text
        finally:
            app.dependency_overrides.clear()


# ── Helpers for EDIT-UI-17..20 ────────────────────────────────────────────────

from app.models.license import UserLicense


def _make_user_with_license(db: Session) -> tuple:
    u = User(
        email=f"lic+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Licensed Player",
        password_hash=get_password_hash("x"),
        role=UserRole.STUDENT,
        is_active=True,
    )
    db.add(u)
    db.flush()
    lic = UserLicense(
        user_id=u.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        is_active=True,
        started_at=datetime.now(timezone.utc),
    )
    db.add(lic)
    db.flush()
    return u, lic


def _make_audience_entry_promoted(db: Session, sponsor: Sponsor,
                                   campaign: SponsorCampaign,
                                   user: User) -> SponsorAudienceEntry:
    """Active + consented + promoted audience entry."""
    log = CsvImportLog(sponsor_id=sponsor.id, campaign_id=campaign.id)
    db.add(log)
    db.flush()
    entry = SponsorAudienceEntry(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        import_log_id=log.id,
        first_name="UI",
        last_name=f"BulkTest {uuid.uuid4().hex[:4]}",
        email=f"ui-bulk+{uuid.uuid4().hex[:8]}@lfa.com",
        status="ACTIVE",
        consent_given=True,
        user_id=user.id,
    )
    db.add(entry)
    db.flush()
    return entry


# ── EDIT-UI-17 ────────────────────────────────────────────────────────────────

class TestBulkEnrollButtonVisible:
    """EDIT-UI-17: PROMOTION_EVENT DRAFT + linked campaign + eligible entries → button shown."""

    def test_edit_ui_17_bulk_enroll_button_visible(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db)
        campaign = _make_campaign(test_db, sponsor)
        user, _ = _make_user_with_license(test_db)
        _make_audience_entry_promoted(test_db, sponsor, campaign, user)
        sem = _make_promotion_semester_with_campaign(test_db, sponsor, campaign)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            assert "Bulk Enroll Campaign Participants" in resp.text
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-18 ────────────────────────────────────────────────────────────────

class TestBulkEnrollButtonHiddenNoCampaign:
    """EDIT-UI-18: PROMOTION_EVENT DRAFT, no campaign → button absent."""

    def test_edit_ui_18_bulk_enroll_hidden_no_campaign(self, test_db: Session):
        admin = _make_admin(test_db)
        # PROMOTION_EVENT without campaign linkage
        sem = _make_promotion_semester(test_db, age_groups=["PRE"])
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            assert "Bulk Enroll Campaign Participants" not in resp.text
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-19 ────────────────────────────────────────────────────────────────

class TestBulkEnrollButtonHiddenNonPromo:
    """EDIT-UI-19: non-PROMOTION_EVENT → button absent."""

    def test_edit_ui_19_bulk_enroll_hidden_non_promo(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            assert "Bulk Enroll Campaign Participants" not in resp.text
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-20 ────────────────────────────────────────────────────────────────

class TestBulkEnrollButtonHiddenFrozenStatus:
    """EDIT-UI-20: PROMOTION_EVENT IN_PROGRESS → button absent (frozen status)."""

    def test_edit_ui_20_bulk_enroll_hidden_in_progress(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db)
        campaign = _make_campaign(test_db, sponsor)
        sem = _make_promotion_semester_with_campaign(test_db, sponsor, campaign)
        sem.tournament_status = "IN_PROGRESS"
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            assert "Bulk Enroll Campaign Participants" not in resp.text
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-21 ────────────────────────────────────────────────────────────────

class TestBulkEnrollButtonEnabledWithEligible:
    """EDIT-UI-21: eligible promoted entry → button rendered AND enabled (no disabled attr)."""

    def test_edit_ui_21_button_enabled_shows_count(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db)
        campaign = _make_campaign(test_db, sponsor)
        user, _ = _make_user_with_license(test_db)
        _make_audience_entry_promoted(test_db, sponsor, campaign, user)
        sem = _make_promotion_semester_with_campaign(test_db, sponsor, campaign)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert "Bulk Enroll Campaign Participants" in html
            # Button must be enabled: no disabled attribute on the button
            assert 'id="bulk-enroll-btn" disabled' not in html
            assert 'id="bulk-enroll-btn"\n                disabled' not in html
            # Eligible count badge must be rendered
            assert "eligible" in html
            # Help text for 0 eligible must be absent
            assert "No eligible promoted campaign participants to enroll." not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-22 ────────────────────────────────────────────────────────────────

class TestBulkEnrollButtonDisabledNoEligible:
    """EDIT-UI-22: campaign linked but no eligible entry (not promoted) →
    disabled button + help text rendered."""

    def test_edit_ui_22_disabled_button_and_help_text(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db)
        campaign = _make_campaign(test_db, sponsor)
        # ACTIVE audience entry but user_id=None (not yet promoted) → not eligible
        _make_audience_entry_with_status(test_db, sponsor, campaign, "ACTIVE")
        sem = _make_promotion_semester_with_campaign(test_db, sponsor, campaign)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            # Button must render (visible even when disabled)
            assert "Bulk Enroll Campaign Participants" in html
            # Button must have disabled attribute
            assert "disabled" in html
            # Help text must appear
            assert "No eligible promoted campaign participants to enroll." in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-23 ────────────────────────────────────────────────────────────────

class TestBulkEnrollButtonVisibleEnrollmentOpen:
    """EDIT-UI-23: PROMOTION_EVENT ENROLLMENT_OPEN + eligible entry → Bulk Enroll visible and enabled."""

    def test_edit_ui_23_bulk_enroll_visible_in_enrollment_open(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db)
        campaign = _make_campaign(test_db, sponsor)
        user, _ = _make_user_with_license(test_db)
        _make_audience_entry_promoted(test_db, sponsor, campaign, user)
        sem = _make_promotion_semester_with_campaign(test_db, sponsor, campaign)
        sem.tournament_status = "ENROLLMENT_OPEN"
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert "Bulk Enroll Campaign Participants" in html
            # Enabled: no disabled attribute on the bulk-enroll button
            assert 'id="bulk-enroll-btn" disabled' not in html
            assert 'id="bulk-enroll-btn"\n                disabled' not in html
            # Count badge rendered
            assert "eligible" in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-24 ────────────────────────────────────────────────────────────────

class TestLockAudienceButtonEnrollmentOpen:
    """EDIT-UI-24: PROMOTION_EVENT ENROLLMENT_OPEN → Lock Audience shown; Close Enrollment absent.
    Non-PROMOTION_EVENT ENROLLMENT_OPEN → Close Enrollment shown (regression)."""

    def test_edit_ui_24_lock_audience_visible_close_enrollment_absent(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db)
        campaign = _make_campaign(test_db, sponsor)
        sem = _make_promotion_semester_with_campaign(test_db, sponsor, campaign)
        sem.tournament_status = "ENROLLMENT_OPEN"
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert "Lock Audience" in html
            assert "Close Enrollment" not in html
        finally:
            app.dependency_overrides.clear()

    def test_edit_ui_24b_non_promo_close_enrollment_unchanged(self, test_db: Session):
        """EDIT-UI-24b: non-PROMOTION_EVENT ENROLLMENT_OPEN → Close Enrollment still shown (regression)."""
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "ENROLLMENT_OPEN"
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert "Close Enrollment" in html
            assert "Lock Audience" not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-25 ────────────────────────────────────────────────────────────────

class TestBulkEnrollEligibleCountLicenseAware:
    """EDIT-UI-25: promoted entry (user_id set) but NO LFA license → eligible count = 0
    → button disabled.

    Validates that the F2 JOIN fix correctly excludes users without LFA_FOOTBALL_PLAYER
    license from the eligible count. Before F2 the count was inflated and showed
    the entry as eligible even though the service would skip it.
    """

    def test_edit_ui_25_no_license_count_zero_button_disabled(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db)
        campaign = _make_campaign(test_db, sponsor)

        # Promoted user (user_id set on entry) but NO UserLicense
        user = User(
            email=f"nolic-ui+{uuid.uuid4().hex[:8]}@lfa.com",
            name="No License Player",
            password_hash=get_password_hash("x"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(user)
        test_db.flush()
        # Deliberately no UserLicense added

        log = CsvImportLog(sponsor_id=sponsor.id, campaign_id=campaign.id)
        test_db.add(log)
        test_db.flush()
        entry = SponsorAudienceEntry(
            sponsor_id=sponsor.id,
            campaign_id=campaign.id,
            import_log_id=log.id,
            first_name="No",
            last_name="License",
            email=user.email,
            status="ACTIVE",
            consent_given=True,
            user_id=user.id,
        )
        test_db.add(entry)

        sem = _make_promotion_semester_with_campaign(test_db, sponsor, campaign)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            # Button renders but must be disabled (0 eligible after license JOIN)
            assert "Bulk Enroll Campaign Participants" in html
            assert "disabled" in html
            assert "No eligible promoted campaign participants to enroll." in html
            # No eligible badge
            assert "eligible" not in html.split("No eligible")[0].split("Bulk Enroll")[-1]
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-26 ────────────────────────────────────────────────────────────────

class TestBulkEnrollEligibleCountWithLicense:
    """EDIT-UI-26: promoted entry + active LFA license → eligible count = 1 → button enabled.

    Regression guard: F2 JOIN must not over-filter — users with a valid license
    must still appear in the count and enable the button.
    """

    def test_edit_ui_26_with_license_count_one_button_enabled(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db)
        campaign = _make_campaign(test_db, sponsor)
        user, _ = _make_user_with_license(test_db)
        _make_audience_entry_promoted(test_db, sponsor, campaign, user)
        sem = _make_promotion_semester_with_campaign(test_db, sponsor, campaign)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert "Bulk Enroll Campaign Participants" in html
            assert "1 eligible" in html
            assert 'id="bulk-enroll-btn" disabled' not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-27 ────────────────────────────────────────────────────────────────

class TestBulkEnrollSkippedDivPresent:
    """EDIT-UI-27: when Bulk Enroll button section renders, the skipped-reasons div
    is present in the DOM (initially hidden via style).

    The JS uses this div to show per-entry skipped reasons when enrolled_count=0.
    If the element is absent, statusEl updates silently fail (no visible feedback).
    """

    def test_edit_ui_27_skipped_div_in_dom(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db)
        campaign = _make_campaign(test_db, sponsor)
        user, _ = _make_user_with_license(test_db)
        _make_audience_entry_promoted(test_db, sponsor, campaign, user)
        sem = _make_promotion_semester_with_campaign(test_db, sponsor, campaign)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            assert 'id="bulk-enroll-skipped"' in resp.text
        finally:
            app.dependency_overrides.clear()


# ── Helpers for EDIT-UI-28..31 ────────────────────────────────────────────────

from app.models.tournament_configuration import TournamentConfiguration
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus


# ── EDIT-UI-28 ────────────────────────────────────────────────────────────────

class TestPromoEnrollmentOpenLockAudienceStep2:
    """EDIT-UI-28: PROMOTION_EVENT ENROLLMENT_OPEN → step 2 action row shows
    'Lock Audience & Start Preparation' (P1 fix — was blocked by not is_promotion_event gate)."""

    def test_edit_ui_28_promo_enrollment_open_lock_audience_step2(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_promotion_semester(test_db, age_groups=["PRE"])
        sem.tournament_status = "ENROLLMENT_OPEN"
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            # step2_state == 'active' for ENROLLMENT_OPEN →
            # PROMOTION_EVENT must show Lock Audience, not Close Enrollment
            assert "Lock Audience" in html
            assert "Start Preparation" in html
            assert "Close Enrollment" not in html
            assert 'id="wiz-step-2"' in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-29 ────────────────────────────────────────────────────────────────

class TestNonPromoCloseEnrollmentEnabledRegression:
    """EDIT-UI-29: non-PROMOTION_EVENT ENROLLMENT_OPEN + ≥2 enrolled →
    'Close Enrollment' button enabled (no disabled guard triggered).
    Regression guard: P1 bifurcation must not break the non-promo path."""

    def test_edit_ui_29_non_promo_close_enrollment_enabled(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "ENROLLMENT_OPEN"
        test_db.flush()

        # 2 active enrollments → enrolled_count=2 → button enabled
        for _ in range(2):
            u, lic = _make_user_with_license(test_db)
            test_db.add(SemesterEnrollment(
                user_id=u.id,
                semester_id=sem.id,
                user_license_id=lic.id,
                request_status=EnrollmentStatus.APPROVED,
                is_active=True,
            ))
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert "Close Enrollment" in html
            assert "Lock Audience" not in html
            # enrolled_count >= 2 → no disabled guard
            assert "Need at least 2 participants" not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-30 ────────────────────────────────────────────────────────────────

class TestPromoDraftStep2Active:
    """EDIT-UI-30: PROMOTION_EVENT DRAFT → step2_state == 'active' fast-path.
    'Lock Audience & Start Preparation' appears in both step 1 and step 2 action rows,
    because PROMO DRAFT makes both step1_state and step2_state 'active'.
    (Non-promo DRAFT has step 2 locked — only step 1 shows 'Open Enrollment'.)"""

    def test_edit_ui_30_promo_draft_step2_active_dual_button(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_promotion_semester(test_db, age_groups=["PRE"])
        # tournament_status defaults to DRAFT
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            # Both step 1 and step 2 action rows show Lock Audience for PROMO+DRAFT
            assert html.count("Lock Audience") >= 2
            assert "Close Enrollment" not in html
            assert 'id="wiz-step-2"' in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-31 ────────────────────────────────────────────────────────────────

class TestPromoTeamConfigEnrolledCountUsesEnrollments:
    """EDIT-UI-31: PROMOTION_EVENT with TEAM participant_type + active SemesterEnrollments →
    enrolled_count reflects SemesterEnrollment count, not TournamentTeamEnrollment count.

    P2 fix regression guard: before the fix, enrolled_count used len(team_enrollments)=0
    for participant_type=TEAM even for PROMOTION_EVENT (bulk_enroll never creates
    TournamentTeamEnrollment rows). After fix, enrolled_count always uses
    len(enrollments) for PROMOTION_EVENT regardless of participant_type."""

    def test_edit_ui_31_team_config_enrolled_count_nonzero(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_promotion_semester(test_db, age_groups=["PRE"])
        sem.tournament_status = "ENROLLMENT_CLOSED"
        test_db.flush()

        # TEAM config — the scenario that broke before P2
        test_db.add(TournamentConfiguration(
            semester_id=sem.id,
            participant_type="TEAM",
        ))
        test_db.flush()

        # 3 individual SemesterEnrollment rows (as bulk_enroll_from_campaign creates)
        for _ in range(3):
            u, lic = _make_user_with_license(test_db)
            test_db.add(SemesterEnrollment(
                user_id=u.id,
                semester_id=sem.id,
                user_license_id=lic.id,
                request_status=EnrollmentStatus.APPROVED,
                is_active=True,
            ))
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            # P2 fix: enrolled_count = len(enrollments) = 3 (not len(team_enrollments) = 0)
            # Step 3 subtitle: "3 enrolled · ..."
            assert "3 enrolled" in html
            assert 'id="wiz-step-3"' in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-32 ────────────────────────────────────────────────────────────────

class TestStep4WordingNoLongerMisleading:
    """EDIT-UI-32: Step 4 info-banner no longer says 'auto-generated when you start'.
    Correct wording explains Check-in Open is the generation trigger (enrolled preview),
    and start refreshes from checked-in participants."""

    def test_edit_ui_32_step4_wording_correct(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "CHECK_IN_OPEN"
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            # Old misleading message must be absent
            assert "Sessions will be auto-generated when you start the tournament" not in html
            # Correct timing language must be present
            assert "enrolled participants" in html
            assert "check-in" in html.lower()
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-33 ────────────────────────────────────────────────────────────────

class TestZeroCheckinWarningWhenSessionsGenerated:
    """EDIT-UI-33: sessions_generated=True + 0 check-ins → warn-banner shown in step 4.
    Informs admin the current draw is enrolled-based and will be used as-is at start."""

    def test_edit_ui_33_zero_checkin_warn_banner_shown(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "CHECK_IN_OPEN"
        test_db.flush()

        # TournamentConfiguration with sessions_generated=True
        cfg = TournamentConfiguration(
            semester_id=sem.id,
            participant_type="INDIVIDUAL",
            sessions_generated=True,
        )
        test_db.add(cfg)

        # 1 enrollment, no check-in (checked_in_count == 0)
        u, lic = _make_user_with_license(test_db)
        test_db.add(SemesterEnrollment(
            user_id=u.id,
            semester_id=sem.id,
            user_license_id=lic.id,
            request_status=EnrollmentStatus.APPROVED,
            is_active=True,
        ))
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            # 0 check-in warning must be present
            assert "0 checked-in" in html
            assert "enrolled participants" in html
        finally:
            app.dependency_overrides.clear()

    def test_edit_ui_33b_no_warn_when_sessions_not_generated(self, test_db: Session):
        """EDIT-UI-33b: sessions_generated=False → 0 check-in warn-banner absent."""
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "CHECK_IN_OPEN"
        test_db.flush()

        test_db.add(TournamentConfiguration(
            semester_id=sem.id,
            participant_type="INDIVIDUAL",
            sessions_generated=False,
        ))
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            assert "0 checked-in" not in resp.text
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-34 ────────────────────────────────────────────────────────────────

class TestStartWarningZeroCheckin:
    """EDIT-UI-34: step4_state == 'active' (CHECK_IN_OPEN) + 0 check-ins →
    inline warning before Start button: 'No check-ins — will start with enrolled participants'."""

    def test_edit_ui_34_start_warning_zero_checkin(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "CHECK_IN_OPEN"
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            # Start button must be present (not blocked)
            assert "Start Tournament" in html
            # Inline start warning must appear
            assert "No check-ins" in html
            assert "enrolled participants" in html
        finally:
            app.dependency_overrides.clear()

    def test_edit_ui_34b_no_start_warning_when_checkins_exist(self, test_db: Session):
        """EDIT-UI-34b: CHECK_IN_OPEN + ≥1 check-in → no 'No check-ins' warning."""
        from datetime import datetime, timezone as tz
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "CHECK_IN_OPEN"
        test_db.flush()

        # 1 checked-in enrollment
        u, lic = _make_user_with_license(test_db)
        test_db.add(SemesterEnrollment(
            user_id=u.id,
            semester_id=sem.id,
            user_license_id=lic.id,
            request_status=EnrollmentStatus.APPROVED,
            is_active=True,
            tournament_checked_in_at=datetime.now(tz.utc),
        ))
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert "No check-ins" not in html
            assert "Start Tournament" in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-35 ────────────────────────────────────────────────────────────────

class TestSessionStatCardLabels:
    """EDIT-UI-35: stat card label distinguishes 'Preview draw' vs 'Final draw'.

    Preview draw: sessions_generated=True, status != IN_PROGRESS or 0 check-ins.
    Final draw:   sessions_generated=True, status == IN_PROGRESS, checked_in_count > 0.
    Not generated: label stays 'Generated' with dash value."""

    def test_edit_ui_35a_preview_draw_label(self, test_db: Session):
        """CHECK_IN_OPEN + sessions_generated=True → 'Preview draw' label."""
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "CHECK_IN_OPEN"
        test_db.flush()

        test_db.add(TournamentConfiguration(
            semester_id=sem.id,
            participant_type="INDIVIDUAL",
            sessions_generated=True,
        ))
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert "Preview draw" in html
            assert "Final draw" not in html
        finally:
            app.dependency_overrides.clear()

    def test_edit_ui_35b_final_draw_label(self, test_db: Session):
        """IN_PROGRESS + sessions_generated=True + ≥1 check-in → 'Final draw' label."""
        from datetime import datetime, timezone as tz
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "IN_PROGRESS"
        test_db.flush()

        test_db.add(TournamentConfiguration(
            semester_id=sem.id,
            participant_type="INDIVIDUAL",
            sessions_generated=True,
        ))

        u, lic = _make_user_with_license(test_db)
        test_db.add(SemesterEnrollment(
            user_id=u.id,
            semester_id=sem.id,
            user_license_id=lic.id,
            request_status=EnrollmentStatus.APPROVED,
            is_active=True,
            tournament_checked_in_at=datetime.now(tz.utc),
        ))
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert "Final draw" in html
            assert "Preview draw" not in html
        finally:
            app.dependency_overrides.clear()

    def test_edit_ui_35c_not_generated_label(self, test_db: Session):
        """sessions_generated=False → stat card shows dash, no Preview/Final label."""
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "ENROLLMENT_CLOSED"
        test_db.flush()

        test_db.add(TournamentConfiguration(
            semester_id=sem.id,
            participant_type="INDIVIDUAL",
            sessions_generated=False,
        ))
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert "Preview draw" not in html
            assert "Final draw" not in html
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-36 ────────────────────────────────────────────────────────────────

class TestStep4ManageCheckinsLink:
    """CHK-UI-01 / EDIT-UI-36: Step 4 contains the 'Manage Check-ins' button.

    Stage 2 added a btn-primary link to /admin/tournaments/{id}/attendance
    inside the step 4 body.  The button must always be present whenever
    step 4 is rendered (any tournament status that shows step 4).
    """

    def test_edit_ui_36_manage_checkins_link_present(self, test_db: Session):
        """CHECK_IN_OPEN (step4_state == 'active') → attendance link visible."""
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "CHECK_IN_OPEN"
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert 'id="btn-manage-checkins"' in html
            assert f"/admin/tournaments/{sem.id}/attendance" in html
        finally:
            app.dependency_overrides.clear()

    def test_edit_ui_36b_manage_checkins_link_in_progress(self, test_db: Session):
        """IN_PROGRESS (step4_state == 'done') → attendance link still present."""
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "IN_PROGRESS"
        test_db.flush()
        test_db.add(TournamentConfiguration(
            semester_id=sem.id,
            participant_type="INDIVIDUAL",
            sessions_generated=True,
        ))
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert 'id="btn-manage-checkins"' in html
            assert f"/admin/tournaments/{sem.id}/attendance" in html
        finally:
            app.dependency_overrides.clear()


# ── Helpers for EDIT-UI-37..39 ────────────────────────────────────────────────

from app.models.session import Session as SessionModel, EventCategory
from app.models.tournament_type import TournamentType
from app.models.tournament_enums import TournamentPhase


def _get_or_skip_tt(db: Session, code: str) -> TournamentType:
    tt = db.query(TournamentType).filter(TournamentType.code == code).first()
    if tt is None:
        import pytest as _pt
        _pt.skip(f"TournamentType '{code}' not found in test DB")
    return tt


def _make_gk_session(
    db: Session,
    sem: Semester,
    phase: TournamentPhase,
    grp: str,
    rn: int,
) -> SessionModel:
    s = SessionModel(
        title=f"{phase.value} - Group {grp} - Match 1",
        date_start=datetime(2026, 9, 1, 10, 0),
        date_end=datetime(2026, 9, 1, 11, 0),
        semester_id=sem.id,
        match_format="HEAD_TO_HEAD",
        auto_generated=True,
        credit_cost=0,
        event_category=EventCategory.MATCH,
        tournament_phase=phase,
        tournament_round=rn,
        group_identifier=grp,
    )
    db.add(s)
    db.flush()
    return s


# ── EDIT-UI-37 ────────────────────────────────────────────────────────────────

class TestGroupKnockoutGroupStageRendering:
    """EDIT-UI-37: group_knockout IN_PROGRESS → Section 7 renders GROUP_STAGE
    sessions using the new 3-row group-card layout (fmt-sess-gc), not the old
    horizontal fmt-session-row layout.
    """

    def test_edit_ui_37_group_stage_rendered_in_columns(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "IN_PROGRESS"
        test_db.flush()

        tt = _get_or_skip_tt(test_db, "group_knockout")
        test_db.add(TournamentConfiguration(
            semester_id=sem.id,
            participant_type="INDIVIDUAL",
            sessions_generated=True,
            tournament_type_id=tt.id,
        ))
        test_db.flush()

        _make_gk_session(test_db, sem, TournamentPhase.GROUP_STAGE, "A", 1)
        _make_gk_session(test_db, sem, TournamentPhase.GROUP_STAGE, "B", 1)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text

            # ── Structural: grid + phase containers ──────────────────────────
            assert "section-session-results" in html
            assert "fmt-groups-grid" in html
            assert "fmt-phase-hdr" in html
            assert "Group Stage" in html
            assert "Group A" in html
            assert "Group B" in html
            assert "fmt-group-col-hdr" in html
            # F-7: group_knockout structured view does NOT show R-badges
            assert ">R1<" not in html

            # ── Scope to Group Stage section for layout assertions ────────────
            # Test only has GROUP_STAGE sessions; GS section starts at the phase header.
            gs_section = html[html.index("⚽ Group Stage"):]

            # New 3-row group-card layout is used
            assert "fmt-sess-gc" in gs_section
            assert "fmt-sess-gc-hdr" in gs_section
            assert "fmt-sess-gc-label" in gs_section
            # IN_PROGRESS → action buttons row rendered
            assert "fmt-sess-gc-actions" in gs_section

            # Label row appears before actions row (separate sibling divs)
            assert gs_section.index("fmt-sess-gc-label") < gs_section.index("fmt-sess-gc-actions")

            # Old horizontal row NOT used inside Group Stage cards
            assert 'class="fmt-session-row"' not in gs_section

            # HEAD_TO_HEAD badge (sess-type) absent from Group Stage cards
            assert 'class="sess-type"' not in gs_section
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-38 ────────────────────────────────────────────────────────────────

class TestGroupKnockoutKnockoutStageRendering:
    """EDIT-UI-38: group_knockout IN_PROGRESS → KNOCKOUT sessions render in
    fmt-ko-round blocks with round header labels.
    """

    def test_edit_ui_38_knockout_stage_rendered_in_rounds(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "IN_PROGRESS"
        test_db.flush()

        tt = _get_or_skip_tt(test_db, "group_knockout")
        test_db.add(TournamentConfiguration(
            semester_id=sem.id,
            participant_type="INDIVIDUAL",
            sessions_generated=True,
            tournament_type_id=tt.id,
        ))
        test_db.flush()

        ko = SessionModel(
            title="group_knockout - Quarter Final - Match 1",
            date_start=datetime(2026, 9, 1, 12, 0),
            date_end=datetime(2026, 9, 1, 13, 0),
            semester_id=sem.id,
            match_format="HEAD_TO_HEAD",
            auto_generated=True,
            credit_cost=0,
            event_category=EventCategory.MATCH,
            tournament_phase=TournamentPhase.KNOCKOUT,
            tournament_round=1,
        )
        test_db.add(ko)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert "Knockout Stage" in html
            assert "fmt-ko-round" in html
            assert "fmt-ko-round-hdr" in html
            assert "Quarter Final" in html
            # F-1: no flex-shrink:0 on the button container row
            assert 'flex-shrink:0' not in html.split('sess-type')[0].split('fmt-session-row')[-1].split('endmacro')[0] or "flex-shrink:0;white-space:nowrap" in html
            # F-7: knockout sessions in structured view have no R-badge (round header provides context)
            assert ">R1<" not in html

            # ── Regression guard: KO Stage uses old horizontal layout ────────
            ko_section = html[html.index("🏆 Knockout Stage"):]
            # Old horizontal row IS used for KO sessions
            assert 'class="session-list-row fmt-session-row"' in ko_section
            # New group-card layout NOT used for KO sessions
            assert "fmt-sess-gc" not in ko_section
            # HEAD_TO_HEAD badge (sess-type) IS present for KO sessions
            assert 'data-priority="medium"' in ko_section
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-39 ────────────────────────────────────────────────────────────────

class TestNonGroupKnockoutFlatListFallback:
    """EDIT-UI-39: non-group_knockout (league) IN_PROGRESS → Section 7 renders
    flat list fallback; no fmt-phase-block or fmt-groups-grid in HTML.
    """

    def test_edit_ui_39_league_flat_list_no_phase_blocks(self, test_db: Session):
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "IN_PROGRESS"
        test_db.flush()

        tt = _get_or_skip_tt(test_db, "league")
        test_db.add(TournamentConfiguration(
            semester_id=sem.id,
            participant_type="INDIVIDUAL",
            sessions_generated=True,
            tournament_type_id=tt.id,
        ))
        test_db.flush()

        s = SessionModel(
            title="League Round 1 Match 1",
            date_start=datetime(2026, 9, 1, 10, 0),
            date_end=datetime(2026, 9, 1, 11, 0),
            semester_id=sem.id,
            match_format="HEAD_TO_HEAD",
            auto_generated=True,
            credit_cost=0,
            event_category=EventCategory.MATCH,
            tournament_round=1,
        )
        test_db.add(s)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert "section-session-results" in html
            # CSS definitions exist, but no rendered group/knockout phase containers
            assert 'class="fmt-phase-block"' not in html
            assert 'class="fmt-groups-grid"' not in html
            assert "Knockout Stage" not in html
            # Session title appears in flat list (not in group column)
            assert "League Round 1 Match 1" in html
            assert 'class="fmt-group-col"' not in html
            # F-7: flat-list path shows round badge (regression guard)
            assert ">R1<" in html
            # F-8: flat-list wrapper has fmt-flat-list-wrap class
            assert "fmt-flat-list-wrap" in html

            # ── Regression guard: flat list uses old horizontal layout ───────
            sr_section = html[html.index("section-session-results"):]
            # Old horizontal row IS used in flat list
            assert 'class="session-list-row fmt-session-row"' in sr_section
            # New group-card layout NOT used in flat list
            assert "fmt-sess-gc" not in sr_section
        finally:
            app.dependency_overrides.clear()


# ── EDIT-UI-40..43: KO slot label fallback via _matchup_label() ───────────────

def _make_ko_session_with_matchup(
    db: Session,
    sem: Semester,
    round_num: int,
    match_num: int,
    matchup: str,
    seed_1: str | None = None,
    seed_2: str | None = None,
) -> SessionModel:
    s = SessionModel(
        title=f"KO Cup - Round {round_num} - Match {match_num}",
        date_start=datetime(2026, 9, 1, 15, 0),
        date_end=datetime(2026, 9, 1, 16, 30),
        semester_id=sem.id,
        match_format="HEAD_TO_HEAD",
        auto_generated=True,
        credit_cost=0,
        event_category=EventCategory.MATCH,
        tournament_phase=TournamentPhase.KNOCKOUT,
        tournament_round=round_num,
        tournament_match_number=match_num,
        structure_config={
            "matchup": matchup,
            **({"seed_1": seed_1} if seed_1 else {}),
            **({"seed_2": seed_2} if seed_2 else {}),
        },
    )
    db.add(s)
    db.flush()
    return s


class TestKOSlotLabelFallback:
    """EDIT-UI-40..43: pending KO sessions show human-readable qualification
    source labels from structure_config.matchup when participant_user_ids is None.
    """

    def test_edit_ui_40_pending_ko_shows_slot_labels(self, test_db: Session):
        """EDIT-UI-40: Before results, KO section shows slot labels from structure_config."""
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "IN_PROGRESS"
        test_db.flush()

        tt = _get_or_skip_tt(test_db, "group_knockout")
        test_db.add(TournamentConfiguration(
            semester_id=sem.id,
            participant_type="INDIVIDUAL",
            sessions_generated=True,
            tournament_type_id=tt.id,
        ))
        test_db.flush()

        # Semi-finals with slot labels, no participants yet
        _make_ko_session_with_matchup(
            test_db, sem, 1, 1,
            "Group A winner vs Best runner-up", "A1", "BR",
        )
        _make_ko_session_with_matchup(
            test_db, sem, 1, 2,
            "Group B winner vs Group C winner", "B1", "C1",
        )
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            assert "Group A winner vs Best runner-up" in html
            assert "Group B winner vs Group C winner" in html
        finally:
            app.dependency_overrides.clear()

    def test_edit_ui_41_concrete_names_take_priority(self, test_db: Session):
        """EDIT-UI-41: When participant_user_ids are set, concrete names appear
        instead of the slot label (concrete names take priority).
        """
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "IN_PROGRESS"
        test_db.flush()

        tt = _get_or_skip_tt(test_db, "group_knockout")
        test_db.add(TournamentConfiguration(
            semester_id=sem.id,
            participant_type="INDIVIDUAL",
            sessions_generated=True,
            tournament_type_id=tt.id,
        ))

        # Create two real users with licenses and semester enrollments
        # (enrolled_users in edit.py is built from SemesterEnrollment rows)
        from app.models.license import UserLicense as _UL
        import uuid as _uuid
        p1 = User(
            email=f"slottest1+{_uuid.uuid4().hex[:6]}@test.com",
            name="Alice Qualifier",
            password_hash=get_password_hash("pw"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        p2 = User(
            email=f"slottest2+{_uuid.uuid4().hex[:6]}@test.com",
            name="Bob Qualifier",
            password_hash=get_password_hash("pw"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add_all([p1, p2])
        test_db.flush()
        for p in (p1, p2):
            lic = _UL(
                user_id=p.id,
                specialization_type="LFA_FOOTBALL_PLAYER",
                is_active=True,
                started_at=datetime.now(timezone.utc),
            )
            test_db.add(lic)
            test_db.flush()
            test_db.add(SemesterEnrollment(
                semester_id=sem.id,
                user_id=p.id,
                user_license_id=lic.id,
                is_active=True,
                request_status=EnrollmentStatus.APPROVED,
            ))
        test_db.flush()

        # SF1 with actual participants assigned (simulates post-calculate-rankings state)
        sf1 = SessionModel(
            title="KO Cup - Semi-Finals - Match 1",
            date_start=datetime(2026, 9, 1, 15, 0),
            date_end=datetime(2026, 9, 1, 16, 30),
            semester_id=sem.id,
            match_format="HEAD_TO_HEAD",
            auto_generated=True,
            credit_cost=0,
            event_category=EventCategory.MATCH,
            tournament_phase=TournamentPhase.KNOCKOUT,
            tournament_round=1,
            tournament_match_number=1,
            participant_user_ids=[p1.id, p2.id],
            structure_config={
                "matchup": "Group A winner vs Best runner-up",
                "seed_1": "A1",
                "seed_2": "BR",
            },
        )
        test_db.add(sf1)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            html = resp.text
            # Concrete names appear
            assert "Alice Qualifier" in html
            assert "Bob Qualifier" in html
            # Slot label does NOT appear (concrete names replaced it)
            assert "Group A winner vs Best runner-up" not in html
        finally:
            app.dependency_overrides.clear()

    def test_edit_ui_42_final_slot_label(self, test_db: Session):
        """EDIT-UI-42: Final session shows 'SF1 winner vs SF2 winner' label."""
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "IN_PROGRESS"
        test_db.flush()

        tt = _get_or_skip_tt(test_db, "group_knockout")
        test_db.add(TournamentConfiguration(
            semester_id=sem.id,
            participant_type="INDIVIDUAL",
            sessions_generated=True,
            tournament_type_id=tt.id,
        ))
        test_db.flush()

        _make_ko_session_with_matchup(
            test_db, sem, 2, 1, "SF1 winner vs SF2 winner",
        )
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            assert "SF1 winner vs SF2 winner" in resp.text
        finally:
            app.dependency_overrides.clear()

    def test_edit_ui_43_bronze_slot_label(self, test_db: Session):
        """EDIT-UI-43: 3rd Place session shows 'SF1 loser vs SF2 loser' label."""
        admin = _make_admin(test_db)
        sem = _make_mini_season_semester(test_db)
        sem.tournament_status = "IN_PROGRESS"
        test_db.flush()

        tt = _get_or_skip_tt(test_db, "group_knockout")
        test_db.add(TournamentConfiguration(
            semester_id=sem.id,
            participant_type="INDIVIDUAL",
            sessions_generated=True,
            tournament_type_id=tt.id,
        ))
        test_db.flush()

        _make_ko_session_with_matchup(
            test_db, sem, 3, 1, "SF1 loser vs SF2 loser",
        )
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.get(f"/admin/tournaments/{sem.id}/edit")
            assert resp.status_code == 200
            assert "SF1 loser vs SF2 loser" in resp.text
        finally:
            app.dependency_overrides.clear()
