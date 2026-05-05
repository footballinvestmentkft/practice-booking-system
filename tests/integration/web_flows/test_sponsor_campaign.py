"""
P3 SponsorCampaign — isolation + guard tests (SPON-CAM-01 through SPON-CAM-20)

  SPON-CAM-01  Same email in 2 campaigns → 2 separate entries, both ACTIVE
  SPON-CAM-02  rollback_import(log1) → only campaign1 entries DELETED; campaign2 untouched
  SPON-CAM-03  promote entry from campaign1 → campaign2 entry user_id remains None
  SPON-CAM-04  suppress_entry in campaign1 → campaign2 entry status unchanged
  SPON-CAM-05  Migration backfill integrity: no NULL campaign_id after _make_* helpers
  SPON-CAM-06  apply_import without campaign_id → explicit ValueError (not silent fail)
  SPON-CAM-07  Legacy GET /audience → 303 redirect (HTTP-level test)
  SPON-CAM-08  Campaign detail shows specialization_type, credit_grant_amount, unlock_cost
  SPON-CAM-09  Close campaign → status becomes CLOSED
  SPON-CAM-10  Close campaign idempotent (already CLOSED → no error, stays CLOSED)
  SPON-CAM-11  CLOSED campaign: import/apply returns 303 with closed error message
  SPON-CAM-12  CLOSED campaign: import/preview returns 303 with closed error message
  SPON-CAM-13  CLOSED campaign: audience/promote returns 303 with closed error message
  SPON-CAM-14  Edit campaign name → name updated
  SPON-CAM-15  Edit campaign credit fields → grant/unlock updated (ACTIVE only)
  SPON-CAM-16  Edit validation: empty name rejected
  SPON-CAM-17  Edit validation: credit_grant < 100 rejected
  SPON-CAM-18  Edit validation: unlock_cost > credit_grant rejected
  SPON-CAM-19  CLOSED campaign edit: credit fields locked (values unchanged)
  SPON-CAM-20  CLOSED campaign edit: name field is still editable

DONE = pytest tests/integration/web_flows/test_sponsor_campaign.py -v
"""
import uuid

import pytest
from sqlalchemy.orm import Session

from app.models.club import CsvImportLog
from app.models.sponsor import Sponsor, SponsorAudienceEntry, SponsorCampaign
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from app.services.sponsor_cleanup_service import rollback_import, suppress_entry
from app.services.sponsor_promote_service import promote_entries
from app.services.sponsor_csv_import_service import apply_import


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"admin-cam+{uuid.uuid4().hex[:8]}@lfa.com",
        name="CAM Admin",
        password_hash=get_password_hash("Admin1234!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_sponsor(db: Session, admin: User) -> Sponsor:
    s = Sponsor(
        name=f"CAM Sponsor {uuid.uuid4().hex[:6]}",
        code=f"CAM-{uuid.uuid4().hex[:5].upper()}",
        is_active=True,
        created_by=admin.id,
    )
    db.add(s)
    db.flush()
    return s


def _make_campaign(db: Session, sponsor: Sponsor, admin: User, name: str = "Campaign") -> SponsorCampaign:
    c = SponsorCampaign(
        sponsor_id=sponsor.id,
        name=f"{name} {uuid.uuid4().hex[:4]}",
        campaign_type="IMPORT",
        status="ACTIVE",
        created_by=admin.id,
    )
    db.add(c)
    db.flush()
    return c


def _make_log(db: Session, sponsor: Sponsor, admin: User,
              campaign: SponsorCampaign, filename: str = "test.csv") -> CsvImportLog:
    log = CsvImportLog(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        filename=filename,
        total_rows=1,
        uploaded_by=admin.id,
    )
    db.add(log)
    db.flush()
    return log


def _make_entry(
    db: Session,
    sponsor: Sponsor,
    campaign: SponsorCampaign,
    log: CsvImportLog,
    *,
    email: str | None = None,
    status: str = "ACTIVE",
    consent_given: bool = True,
    user_id: int | None = None,
) -> SponsorAudienceEntry:
    e = SponsorAudienceEntry(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        import_log_id=log.id,
        first_name="Test",
        last_name="Player",
        email=email or f"cam+{uuid.uuid4().hex[:8]}@test.com",
        status=status,
        consent_given=consent_given,
        user_id=user_id,
    )
    db.add(e)
    db.flush()
    return e


# ── SPON-CAM-01 ───────────────────────────────────────────────────────────────

class TestSameEmailTwoCampaigns:
    """SPON-CAM-01: Same email can appear in 2 campaigns → 2 independent entries."""

    def test_spon_cam_01_same_email_two_campaigns(self, test_db: Session):
        admin   = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        c1 = _make_campaign(test_db, sponsor, admin, "Spring Cup")
        c2 = _make_campaign(test_db, sponsor, admin, "Summer Cup")
        log1 = _make_log(test_db, sponsor, admin, c1)
        log2 = _make_log(test_db, sponsor, admin, c2)

        shared_email = f"shared+{uuid.uuid4().hex[:6]}@test.com"
        e1 = _make_entry(test_db, sponsor, c1, log1, email=shared_email)
        e2 = _make_entry(test_db, sponsor, c2, log2, email=shared_email)
        test_db.commit()

        assert e1.id != e2.id
        assert e1.campaign_id == c1.id
        assert e2.campaign_id == c2.id
        assert e1.email == e2.email
        assert e1.status == "ACTIVE"
        assert e2.status == "ACTIVE"


# ── SPON-CAM-02 ───────────────────────────────────────────────────────────────

class TestRollbackCampaignIsolation:
    """SPON-CAM-02: rollback_import(log1) deletes campaign1 entries only."""

    def test_spon_cam_02_rollback_does_not_touch_other_campaign(self, test_db: Session):
        admin   = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        c1 = _make_campaign(test_db, sponsor, admin, "Campaign A")
        c2 = _make_campaign(test_db, sponsor, admin, "Campaign B")
        log1 = _make_log(test_db, sponsor, admin, c1)
        log2 = _make_log(test_db, sponsor, admin, c2)

        e1 = _make_entry(test_db, sponsor, c1, log1, status="ACTIVE")
        e2 = _make_entry(test_db, sponsor, c2, log2, status="ACTIVE")
        test_db.commit()

        result = rollback_import(log1.id, sponsor.id, test_db, admin)

        assert result.deleted == 1
        assert not result.errors

        test_db.expire(e1)
        test_db.expire(e2)
        assert e1.status == "DELETED"   # campaign1 entry affected
        assert e2.status == "ACTIVE"    # campaign2 entry untouched


# ── SPON-CAM-03 ───────────────────────────────────────────────────────────────

class TestPromoteCampaignIsolation:
    """SPON-CAM-03: promote entry from campaign1 → campaign2 entry user_id unchanged."""

    def test_spon_cam_03_promote_does_not_touch_other_campaign(self, test_db: Session):
        admin   = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        c1 = _make_campaign(test_db, sponsor, admin, "Campaign A")
        c2 = _make_campaign(test_db, sponsor, admin, "Campaign B")
        log1 = _make_log(test_db, sponsor, admin, c1)
        log2 = _make_log(test_db, sponsor, admin, c2)

        shared_email = f"promo+{uuid.uuid4().hex[:6]}@test.com"
        e1 = _make_entry(test_db, sponsor, c1, log1, email=shared_email)
        e2 = _make_entry(test_db, sponsor, c2, log2, email=shared_email)
        test_db.commit()

        users_before = test_db.query(User).count()
        result = promote_entries([e1.id], sponsor.id, test_db, admin)

        assert result.promoted == 1

        test_db.expire(e1)
        test_db.expire(e2)
        assert e1.user_id is not None     # campaign1 entry promoted
        assert e2.user_id is None          # campaign2 entry untouched
        assert test_db.query(User).count() == users_before + 1


# ── SPON-CAM-04 ───────────────────────────────────────────────────────────────

class TestSuppressCampaignIsolation:
    """SPON-CAM-04: suppress in campaign1 → campaign2 entry status unchanged."""

    def test_spon_cam_04_suppress_does_not_touch_other_campaign(self, test_db: Session):
        admin   = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        c1 = _make_campaign(test_db, sponsor, admin, "Campaign A")
        c2 = _make_campaign(test_db, sponsor, admin, "Campaign B")
        log1 = _make_log(test_db, sponsor, admin, c1)
        log2 = _make_log(test_db, sponsor, admin, c2)

        shared_email = f"supr+{uuid.uuid4().hex[:6]}@test.com"
        e1 = _make_entry(test_db, sponsor, c1, log1, email=shared_email)
        e2 = _make_entry(test_db, sponsor, c2, log2, email=shared_email)
        test_db.commit()

        result = suppress_entry(e1.id, sponsor.id, test_db, admin)

        assert result.suppressed == 1
        assert not result.errors

        test_db.expire(e1)
        test_db.expire(e2)
        assert e1.status == "SUPPRESSED"  # campaign1 entry suppressed
        assert e2.status == "ACTIVE"       # campaign2 entry untouched


# ── SPON-CAM-05 ───────────────────────────────────────────────────────────────

class TestCampaignIdNeverNull:
    """SPON-CAM-05: created entries always have campaign_id set (no NULL leak)."""

    def test_spon_cam_05_entry_campaign_id_is_set(self, test_db: Session):
        admin   = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        c1 = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, c1)
        e   = _make_entry(test_db, sponsor, c1, log)
        test_db.commit()

        test_db.expire(e)
        assert e.campaign_id == c1.id
        assert e.campaign_id is not None


# ── SPON-CAM-06 ───────────────────────────────────────────────────────────────

class TestApplyImportGuard:
    """SPON-CAM-06: apply_import without campaign_id raises ValueError — not a silent fail."""

    def test_spon_cam_06_apply_import_requires_campaign_id(self, test_db: Session):
        admin   = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        test_db.commit()

        csv_content = (
            "first_name,last_name,email,consent_given\n"
            "Test,Player,guard-test@test.com,1\n"
        ).encode()

        from app.models.sponsor import Sponsor as SponsorModel
        sponsor_obj = test_db.query(SponsorModel).filter(SponsorModel.id == sponsor.id).first()

        with pytest.raises((ValueError, TypeError)):
            # campaign_id omitted entirely → must raise, not silently pass
            apply_import(csv_content, sponsor_obj, test_db, admin, filename="guard.csv")


# ── SPON-CAM-07 ───────────────────────────────────────────────────────────────

class TestLegacyAudienceRedirect:
    """SPON-CAM-07: GET /admin/sponsors/{id}/audience → 303 redirect to sponsor detail."""

    def test_spon_cam_07_legacy_audience_url_redirects(self, test_db: Session):
        from urllib.parse import urlencode
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import get_db
        from app.dependencies import get_current_user_web

        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        test_db.commit()

        def _override_db():
            yield test_db

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user_web] = lambda: admin

        try:
            client = TestClient(
                app,
                headers={"Authorization": "Bearer test-csrf-bypass"},
                follow_redirects=False,
            )
            resp = client.get(f"/admin/sponsors/{sponsor.id}/audience")
            assert resp.status_code == 303
            assert f"/admin/sponsors/{sponsor.id}" in resp.headers["location"]
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)


# ── SPON-CAM-08 ───────────────────────────────────────────────────────────────

class TestCampaignDetailP7Fields:
    """SPON-CAM-08: Campaign detail page renders specialization_type, credit_grant_amount,
    and unlock_cost as read-only meta-items."""

    def _client_and_campaign(self, test_db: Session):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import get_db
        from app.dependencies import get_current_user_web

        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = SponsorCampaign(
            sponsor_id=sponsor.id,
            name=f"P7 Test {uuid.uuid4().hex[:4]}",
            campaign_type="IMPORT",
            status="ACTIVE",
            specialization_type="LFA_FOOTBALL_PLAYER",
            credit_grant_amount=200,
            unlock_cost=150,
            created_by=admin.id,
        )
        test_db.add(campaign)
        test_db.commit()

        def _override_db():
            yield test_db

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user_web] = lambda: admin

        client = TestClient(app, follow_redirects=False)
        return client, sponsor, campaign, app

    def test_spon_cam_08a_specialization_type_displayed(self, test_db: Session):
        client, sponsor, campaign, app = self._client_and_campaign(test_db)
        from app.database import get_db
        from app.dependencies import get_current_user_web
        try:
            resp = client.get(f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}")
            assert resp.status_code == 200
            assert "LFA_FOOTBALL_PLAYER" in resp.text
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)

    def test_spon_cam_08b_credit_grant_amount_displayed(self, test_db: Session):
        client, sponsor, campaign, app = self._client_and_campaign(test_db)
        from app.database import get_db
        from app.dependencies import get_current_user_web
        try:
            resp = client.get(f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}")
            assert resp.status_code == 200
            assert "200" in resp.text
            assert "Credit Grant" in resp.text
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)

    def test_spon_cam_08c_unlock_cost_displayed(self, test_db: Session):
        client, sponsor, campaign, app = self._client_and_campaign(test_db)
        from app.database import get_db
        from app.dependencies import get_current_user_web
        try:
            resp = client.get(f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}")
            assert resp.status_code == 200
            assert "150" in resp.text
            assert "Unlock Cost" in resp.text
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)


# ── SPON-CAM-09 / SPON-CAM-10 ────────────────────────────────────────────────

class TestCampaignClose:
    """SPON-CAM-09/10: POST .../close sets status=CLOSED; idempotent second call."""

    def _setup(self, test_db: Session):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import get_db
        from app.dependencies import get_current_user_web

        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()

        def _override_db():
            yield test_db

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user_web] = lambda: admin
        client = TestClient(
            app,
            headers={"Authorization": "Bearer test-csrf-bypass"},
            follow_redirects=False,
        )
        return client, sponsor, campaign, app

    def test_spon_cam_09_close_sets_closed_status(self, test_db: Session):
        from app.database import get_db
        from app.dependencies import get_current_user_web
        client, sponsor, campaign, app = self._setup(test_db)
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/close",
                data={"csrf_token": "test"},
            )
            assert resp.status_code == 303
            assert "flash=Campaign+closed" in resp.headers["location"]
            test_db.expire(campaign)
            assert campaign.status == "CLOSED"
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)

    def test_spon_cam_10_close_idempotent(self, test_db: Session):
        from app.database import get_db
        from app.dependencies import get_current_user_web
        client, sponsor, campaign, app = self._setup(test_db)
        try:
            campaign.status = "CLOSED"
            test_db.commit()
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/close",
                data={"csrf_token": "test"},
            )
            assert resp.status_code == 303
            test_db.expire(campaign)
            assert campaign.status == "CLOSED"
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)


# ── SPON-CAM-11 / SPON-CAM-12 / SPON-CAM-13 ─────────────────────────────────

class TestClosedCampaignGuards:
    """SPON-CAM-11/12/13: CLOSED campaign blocks import apply, import preview, and promote."""

    def _setup(self, test_db: Session):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import get_db
        from app.dependencies import get_current_user_web

        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        campaign.status = "CLOSED"
        test_db.commit()

        def _override_db():
            yield test_db

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user_web] = lambda: admin
        client = TestClient(
            app,
            headers={"Authorization": "Bearer test-csrf-bypass"},
            follow_redirects=False,
        )
        return client, sponsor, campaign, app

    def test_spon_cam_11_closed_campaign_import_apply_blocked(self, test_db: Session):
        import base64
        from app.database import get_db
        from app.dependencies import get_current_user_web
        client, sponsor, campaign, app = self._setup(test_db)
        try:
            csv_b64 = base64.b64encode(b"first_name,last_name,email,consent_given\n").decode()
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/apply",
                data={"csrf_token": "test", "csv_data": csv_b64, "filename": "test.csv"},
            )
            assert resp.status_code == 303
            assert "closed" in resp.headers["location"].lower()
            assert "import" in resp.headers["location"].lower()
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)

    def test_spon_cam_12_closed_campaign_import_preview_blocked(self, test_db: Session):
        from io import BytesIO
        from app.database import get_db
        from app.dependencies import get_current_user_web
        client, sponsor, campaign, app = self._setup(test_db)
        try:
            csv_bytes = b"first_name,last_name,email,consent_given\nTest,Player,t@t.com,1\n"
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/preview",
                files={"file": ("test.csv", BytesIO(csv_bytes), "text/csv")},
            )
            assert resp.status_code == 303
            assert "closed" in resp.headers["location"].lower()
            assert "import" in resp.headers["location"].lower()
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)

    def test_spon_cam_13_closed_campaign_promote_blocked(self, test_db: Session):
        from app.database import get_db
        from app.dependencies import get_current_user_web
        client, sponsor, campaign, app = self._setup(test_db)
        try:
            # CLOSED guard fires before processing entry IDs — dummy ID is fine
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/audience/promote",
                data={"csrf_token": "test", "entry_ids": "9999"},
            )
            assert resp.status_code == 303
            assert "closed" in resp.headers["location"].lower()
            assert "promote" in resp.headers["location"].lower()
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)


# ── SPON-CAM-14 through SPON-CAM-20 ──────────────────────────────────────────

class TestCampaignEdit:
    """SPON-CAM-14..20: Campaign EDIT route — happy path, validation, CLOSED lock."""

    def _setup(self, test_db: Session, *, closed: bool = False):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import get_db
        from app.dependencies import get_current_user_web

        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = SponsorCampaign(
            sponsor_id=sponsor.id,
            name=f"Edit Test {uuid.uuid4().hex[:4]}",
            campaign_type="IMPORT",
            status="CLOSED" if closed else "ACTIVE",
            specialization_type="LFA_FOOTBALL_PLAYER",
            credit_grant_amount=200,
            unlock_cost=100,
            created_by=admin.id,
        )
        test_db.add(campaign)
        test_db.commit()

        def _override_db():
            yield test_db

        app.dependency_overrides[get_db] = _override_db
        app.dependency_overrides[get_current_user_web] = lambda: admin
        client = TestClient(
            app,
            headers={"Authorization": "Bearer test-csrf-bypass"},
            follow_redirects=False,
        )
        return client, sponsor, campaign, app

    def _post_edit(self, client, sponsor, campaign, *, name=None, credit_grant=None, unlock=None):
        return client.post(
            f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/edit",
            data={
                "csrf_token": "test",
                "name": name if name is not None else campaign.name,
                "credit_grant_amount": str(credit_grant if credit_grant is not None else campaign.credit_grant_amount),
                "unlock_cost": str(unlock if unlock is not None else campaign.unlock_cost),
            },
        )

    def test_spon_cam_14_edit_name_updated(self, test_db: Session):
        from app.database import get_db
        from app.dependencies import get_current_user_web
        client, sponsor, campaign, app = self._setup(test_db)
        try:
            resp = self._post_edit(client, sponsor, campaign, name="New Name")
            assert resp.status_code == 303
            assert "flash=Campaign+updated" in resp.headers["location"]
            test_db.expire(campaign)
            assert campaign.name == "New Name"
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)

    def test_spon_cam_15_edit_credit_fields_updated(self, test_db: Session):
        from app.database import get_db
        from app.dependencies import get_current_user_web
        client, sponsor, campaign, app = self._setup(test_db)
        try:
            resp = self._post_edit(client, sponsor, campaign, credit_grant=300, unlock=150)
            assert resp.status_code == 303
            assert "flash=Campaign+updated" in resp.headers["location"]
            test_db.expire(campaign)
            assert campaign.credit_grant_amount == 300
            assert campaign.unlock_cost == 150
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)

    def test_spon_cam_16_edit_empty_name_rejected(self, test_db: Session):
        from app.database import get_db
        from app.dependencies import get_current_user_web
        client, sponsor, campaign, app = self._setup(test_db)
        try:
            resp = self._post_edit(client, sponsor, campaign, name="")
            assert resp.status_code == 303
            assert "error=" in resp.headers["location"]
            assert "name" in resp.headers["location"].lower()
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)

    def test_spon_cam_17_edit_credit_grant_below_100_rejected(self, test_db: Session):
        from app.database import get_db
        from app.dependencies import get_current_user_web
        client, sponsor, campaign, app = self._setup(test_db)
        try:
            resp = self._post_edit(client, sponsor, campaign, credit_grant=50, unlock=50)
            assert resp.status_code == 303
            assert "error=" in resp.headers["location"]
            assert "100" in resp.headers["location"] or "grant" in resp.headers["location"].lower()
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)

    def test_spon_cam_18_edit_unlock_exceeds_grant_rejected(self, test_db: Session):
        from app.database import get_db
        from app.dependencies import get_current_user_web
        client, sponsor, campaign, app = self._setup(test_db)
        try:
            resp = self._post_edit(client, sponsor, campaign, credit_grant=200, unlock=250)
            assert resp.status_code == 303
            assert "error=" in resp.headers["location"]
            assert "exceed" in resp.headers["location"].lower() or "unlock" in resp.headers["location"].lower()
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)

    def test_spon_cam_19_closed_campaign_credit_change_rejected(self, test_db: Session):
        from app.database import get_db
        from app.dependencies import get_current_user_web
        client, sponsor, campaign, app = self._setup(test_db, closed=True)
        try:
            resp = self._post_edit(client, sponsor, campaign, credit_grant=500, unlock=100)
            assert resp.status_code == 303
            assert "error=" in resp.headers["location"]
            assert "locked" in resp.headers["location"].lower() or "closed" in resp.headers["location"].lower()
            test_db.expire(campaign)
            assert campaign.credit_grant_amount == 200
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)

    def test_spon_cam_20_closed_campaign_name_editable(self, test_db: Session):
        from app.database import get_db
        from app.dependencies import get_current_user_web
        client, sponsor, campaign, app = self._setup(test_db, closed=True)
        try:
            resp = self._post_edit(client, sponsor, campaign, name="Renamed Closed")
            assert resp.status_code == 303
            assert "flash=Campaign+updated" in resp.headers["location"]
            test_db.expire(campaign)
            assert campaign.name == "Renamed Closed"
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user_web, None)
