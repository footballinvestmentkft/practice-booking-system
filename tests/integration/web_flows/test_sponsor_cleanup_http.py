"""
Sponsor Audience P2-E — HTTP-level route smoke tests (manual QA scenarios)

  HTTP-CL-01  POST /suppress → 303 redirect, entry status SUPPRESSED
  HTTP-CL-02  POST /delete   → 303 redirect, entry status DELETED
  HTTP-CL-03  POST /unlink   → 303 redirect, entry user_id NULL
  HTTP-CL-04  POST /promote  → 303 redirect, User created (multi-select form)
  HTTP-CL-05  POST /rollback → 303 redirect, unpromoted entries DELETED

These exercise the actual FastAPI route handlers end-to-end via TestClient,
using dependency override for auth (Bearer CSRF bypass, same pattern as ORG tests).

DONE = pytest tests/integration/web_flows/test_sponsor_cleanup_http.py -v
"""
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db
from app.dependencies import get_current_user_web
from app.models.club import CsvImportLog
from app.models.sponsor import Sponsor, SponsorAudienceEntry, SponsorCampaign
from app.models.user import User, UserRole
from app.core.security import get_password_hash


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"admin-http+{uuid.uuid4().hex[:8]}@lfa.com",
        name="HTTP Admin",
        password_hash=get_password_hash("Admin1234!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_sponsor(db: Session, admin: User) -> Sponsor:
    s = Sponsor(
        name=f"HTTP Sponsor {uuid.uuid4().hex[:6]}",
        code=f"HTTP-{uuid.uuid4().hex[:5].upper()}",
        is_active=True,
        created_by=admin.id,
    )
    db.add(s)
    db.flush()
    return s


def _make_campaign(db: Session, sponsor: Sponsor, admin: User) -> SponsorCampaign:
    c = SponsorCampaign(
        sponsor_id=sponsor.id,
        name=f"HTTP Campaign {uuid.uuid4().hex[:4]}",
        campaign_type="IMPORT",
        status="ACTIVE",
        created_by=admin.id,
    )
    db.add(c)
    db.flush()
    return c


def _make_log(db: Session, sponsor: Sponsor, admin: User,
              campaign: SponsorCampaign) -> CsvImportLog:
    log = CsvImportLog(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        filename="http_test.csv",
        total_rows=1,
        uploaded_by=admin.id,
    )
    db.add(log)
    db.flush()
    return log


def _make_entry(db: Session, sponsor: Sponsor, log: CsvImportLog,
                campaign: SponsorCampaign, **kwargs) -> SponsorAudienceEntry:
    defaults = dict(
        status="ACTIVE",
        consent_given=True,
        first_name="HTTP",
        last_name="Player",
        email=f"http+{uuid.uuid4().hex[:8]}@test.com",
    )
    defaults.update(kwargs)
    e = SponsorAudienceEntry(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        import_log_id=log.id,
        **defaults,
    )
    db.add(e)
    db.flush()
    return e


def _client(test_db: Session, admin: User) -> TestClient:
    def _override_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user_web] = lambda: admin
    return TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})


# ── HTTP-CL-01 ────────────────────────────────────────────────────────────────

class TestHttpSuppress:
    def test_http_cl_01_suppress_returns_303_and_sets_suppressed(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, campaign)
        entry = _make_entry(test_db, sponsor, log, campaign)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/audience/{entry.id}/suppress",
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "flash" in resp.headers["location"]
        finally:
            app.dependency_overrides.clear()

        test_db.expire(entry)
        assert entry.status == "SUPPRESSED"


# ── HTTP-CL-02 ────────────────────────────────────────────────────────────────

class TestHttpDelete:
    def test_http_cl_02_delete_returns_303_and_sets_deleted(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, campaign)
        entry = _make_entry(test_db, sponsor, log, campaign)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/audience/{entry.id}/delete",
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "flash" in resp.headers["location"]
        finally:
            app.dependency_overrides.clear()

        test_db.expire(entry)
        assert entry.status == "DELETED"


# ── HTTP-CL-03 ────────────────────────────────────────────────────────────────

class TestHttpUnlink:
    def test_http_cl_03_unlink_returns_303_and_clears_user_id(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, campaign)

        linked_user = User(
            email=f"linked-http+{uuid.uuid4().hex[:8]}@test.com",
            name="Linked",
            password_hash=get_password_hash("Pass1234!"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(linked_user)
        test_db.flush()

        entry = _make_entry(
            test_db, sponsor, log, campaign,
            user_id=linked_user.id,
            promoted_at=datetime.now(timezone.utc),
            promoted_by=admin.id,
        )
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/audience/{entry.id}/unlink",
                follow_redirects=False,
            )
            assert resp.status_code == 303
            loc = resp.headers["location"]
            assert "flash" in loc
            assert "error" not in loc
        finally:
            app.dependency_overrides.clear()

        test_db.expire(entry)
        assert entry.user_id is None
        assert entry.promoted_at is None
        # User still exists
        test_db.expire(linked_user)
        assert linked_user.id is not None


# ── HTTP-CL-04 ────────────────────────────────────────────────────────────────

class TestHttpPromoteMultiSelect:
    def test_http_cl_04_promote_multi_select_creates_users(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, campaign)

        e1 = _make_entry(test_db, sponsor, log, campaign)
        e2 = _make_entry(test_db, sponsor, log, campaign)
        test_db.commit()

        users_before = test_db.query(User).count()

        client = _client(test_db, admin)
        body = urlencode([("entry_ids", str(e1.id)), ("entry_ids", str(e2.id))])
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/audience/promote",
                content=body.encode(),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                follow_redirects=False,
            )
            assert resp.status_code == 303
            loc = resp.headers["location"]
            assert "promoted" in loc
        finally:
            app.dependency_overrides.clear()

        assert test_db.query(User).count() == users_before + 2

        test_db.expire(e1)
        test_db.expire(e2)
        assert e1.user_id is not None
        assert e2.user_id is not None

    def test_http_cl_04_promote_empty_selection_redirects_no_error(self, test_db: Session):
        """Empty entry_ids → 'No entries selected' flash, not a server error."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/audience/promote",
                data={},
                follow_redirects=False,
            )
            assert resp.status_code == 303
        finally:
            app.dependency_overrides.clear()


# ── HTTP-CL-05 ────────────────────────────────────────────────────────────────

class TestHttpRollback:
    def test_http_cl_05_rollback_deletes_unpromoted_skips_promoted(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, campaign)

        linked_user = User(
            email=f"rb-http+{uuid.uuid4().hex[:8]}@test.com",
            name="RB Linked",
            password_hash=get_password_hash("Pass1234!"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(linked_user)
        test_db.flush()

        e_promoted = _make_entry(test_db, sponsor, log, campaign, user_id=linked_user.id)
        e_unpromoted = _make_entry(test_db, sponsor, log, campaign)
        test_db.commit()

        client = _client(test_db, admin)
        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/{log.id}/rollback",
                follow_redirects=False,
            )
            assert resp.status_code == 303
            loc = resp.headers["location"]
            assert "error" not in loc
        finally:
            app.dependency_overrides.clear()

        test_db.expire(e_promoted)
        test_db.expire(e_unpromoted)
        assert e_unpromoted.status == "DELETED"
        assert e_promoted.status == "ACTIVE"  # promoted entry untouched
