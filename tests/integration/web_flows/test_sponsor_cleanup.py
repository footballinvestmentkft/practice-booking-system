"""
Sponsor Audience P2-E — Cleanup / Rollback Tests (SPON-CL-01 through SPON-CL-08)

  SPON-CL-01  suppress_entry → status=SUPPRESSED, User count unchanged
  SPON-CL-02  unlink_entry → user_id=None, promoted_at=None, promoted_by=None;
              User and UserLicense NOT modified
  SPON-CL-03  rollback_import → only unpromoted entries → DELETED, count correct
  SPON-CL-04  rollback_import with promoted entries → promoted skipped, in result.skipped
  SPON-CL-05  DELETED entry is not promotable (promote skips it)
  SPON-CL-06  CSV re-import does NOT reactivate a DELETED entry
  SPON-CL-07  rollback on already-DELETED entries → result.already_deleted counts them,
              result.deleted==0 (no silent no-op)
  SPON-CL-08  rollback on mixed batch → deleted + already_deleted counted separately

DONE = pytest tests/integration/web_flows/test_sponsor_cleanup.py -v
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.club import CsvImportLog
from app.models.license import UserLicense
from app.models.sponsor import Sponsor, SponsorAudienceEntry, SponsorCampaign
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from app.services.sponsor_cleanup_service import (
    suppress_entry,
    soft_delete_entry,
    unlink_entry,
    rollback_import,
)
from app.services.sponsor_promote_service import promote_entries
from app.services.sponsor_csv_import_service import apply_import


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"admin-cl+{uuid.uuid4().hex[:8]}@lfa.com",
        name="CL Admin",
        password_hash=get_password_hash("Admin1234!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_sponsor(db: Session, admin: User) -> Sponsor:
    s = Sponsor(
        name=f"CL Sponsor {uuid.uuid4().hex[:6]}",
        code=f"CLS-{uuid.uuid4().hex[:5].upper()}",
        is_active=True,
        created_by=admin.id,
    )
    db.add(s)
    db.flush()
    return s


def _make_log(
    db: Session,
    sponsor: Sponsor,
    admin: User,
    filename: str = "test.csv",
    campaign: SponsorCampaign | None = None,
) -> CsvImportLog:
    log = CsvImportLog(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id if campaign else None,
        filename=filename,
        total_rows=1,
        uploaded_by=admin.id,
    )
    db.add(log)
    db.flush()
    return log


def _make_campaign(db: Session, sponsor: Sponsor, admin: User, name: str = "Test Campaign") -> SponsorCampaign:
    c = SponsorCampaign(
        sponsor_id=sponsor.id,
        name=name,
        campaign_type="IMPORT",
        status="ACTIVE",
        created_by=admin.id,
    )
    db.add(c)
    db.flush()
    return c


def _make_entry(
    db: Session,
    sponsor: Sponsor,
    log: CsvImportLog,
    *,
    campaign_id: int | None = None,
    status: str = "ACTIVE",
    consent_given: bool = True,
    user_id: int | None = None,
    promoted_at: datetime | None = None,
    promoted_by: int | None = None,
    email: str | None = None,
) -> SponsorAudienceEntry:
    e = SponsorAudienceEntry(
        sponsor_id=sponsor.id,
        campaign_id=campaign_id or log.campaign_id,
        import_log_id=log.id,
        first_name="Test",
        last_name="Player",
        email=email or f"cl+{uuid.uuid4().hex[:8]}@test.com",
        status=status,
        consent_given=consent_given,
        user_id=user_id,
        promoted_at=promoted_at,
        promoted_by=promoted_by,
    )
    db.add(e)
    db.flush()
    return e


def _make_user(db: Session) -> User:
    u = User(
        email=f"player-cl+{uuid.uuid4().hex[:8]}@test.com",
        name="CL Player",
        password_hash=get_password_hash("Pass1234!"),
        role=UserRole.STUDENT,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


# ── SPON-CL-01 ────────────────────────────────────────────────────────────────

class TestSuppress:
    """SPON-CL-01: suppress_entry → SUPPRESSED, User count unchanged."""

    def test_spon_cl_01_suppress_active_entry(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, campaign=campaign)
        entry = _make_entry(test_db, sponsor, log)
        test_db.commit()

        users_before = test_db.query(User).count()
        result = suppress_entry(entry.id, sponsor.id, test_db, admin)

        assert result.suppressed == 1
        assert not result.errors

        test_db.expire(entry)
        assert entry.status == "SUPPRESSED"
        assert test_db.query(User).count() == users_before

    def test_spon_cl_01_suppress_already_deleted_returns_error(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, campaign=campaign)
        entry = _make_entry(test_db, sponsor, log, status="DELETED")
        test_db.commit()

        result = suppress_entry(entry.id, sponsor.id, test_db, admin)
        assert result.suppressed == 0
        assert result.errors


# ── SPON-CL-02 ────────────────────────────────────────────────────────────────

class TestUnlink:
    """SPON-CL-02: unlink clears user_id/promoted fields; User and UserLicense untouched."""

    def test_spon_cl_02_unlink_clears_promotion_fields(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, campaign=campaign)

        linked_user = _make_user(test_db)
        lic = UserLicense(
            user_id=linked_user.id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.now(timezone.utc),
            is_active=True,
        )
        test_db.add(lic)
        test_db.flush()

        entry = _make_entry(
            test_db, sponsor, log,
            user_id=linked_user.id,
            promoted_at=datetime.now(timezone.utc),
            promoted_by=admin.id,
        )
        test_db.commit()

        original_user_name = linked_user.name
        users_before = test_db.query(User).count()
        licenses_before = test_db.query(UserLicense).count()

        result = unlink_entry(entry.id, sponsor.id, test_db, admin)

        assert result.unlinked == 1
        assert result.unlinked_user_id == linked_user.id
        assert not result.errors

        test_db.expire(entry)
        assert entry.user_id is None
        assert entry.promoted_at is None
        assert entry.promoted_by is None
        # Status must be unchanged (was ACTIVE, stays ACTIVE)
        assert entry.status == "ACTIVE"

        # User and UserLicense untouched
        assert test_db.query(User).count() == users_before
        assert test_db.query(UserLicense).count() == licenses_before
        test_db.expire(linked_user)
        assert linked_user.name == original_user_name

    def test_spon_cl_02_unlink_no_user_returns_error(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, campaign=campaign)
        entry = _make_entry(test_db, sponsor, log)  # no user_id
        test_db.commit()

        result = unlink_entry(entry.id, sponsor.id, test_db, admin)
        assert result.unlinked == 0
        assert result.errors


# ── SPON-CL-03 ────────────────────────────────────────────────────────────────

class TestRollbackUnpromoted:
    """SPON-CL-03: rollback only soft-deletes unpromoted entries."""

    def test_spon_cl_03_rollback_deletes_only_unpromoted(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, campaign=campaign)

        e1 = _make_entry(test_db, sponsor, log, status="ACTIVE")
        e2 = _make_entry(test_db, sponsor, log, status="SUPPRESSED")
        test_db.commit()

        result = rollback_import(log.id, sponsor.id, test_db, admin)

        assert result.deleted == 2
        assert result.skipped == 0
        assert not result.errors

        test_db.expire(e1)
        test_db.expire(e2)
        assert e1.status == "DELETED"
        assert e2.status == "DELETED"


# ── SPON-CL-04 ────────────────────────────────────────────────────────────────

class TestRollbackSkipsPromoted:
    """SPON-CL-04: rollback skips promoted entries, reports them in result.skipped."""

    def test_spon_cl_04_rollback_skips_promoted(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, campaign=campaign)

        linked_user = _make_user(test_db)
        e_promoted = _make_entry(test_db, sponsor, log, user_id=linked_user.id)
        e_unpromoted = _make_entry(test_db, sponsor, log)
        test_db.commit()

        users_before = test_db.query(User).count()
        result = rollback_import(log.id, sponsor.id, test_db, admin)

        assert result.deleted == 1
        assert result.skipped == 1

        test_db.expire(e_promoted)
        test_db.expire(e_unpromoted)
        assert e_promoted.status == "ACTIVE"   # promoted entry untouched
        assert e_unpromoted.status == "DELETED"
        assert test_db.query(User).count() == users_before  # no User deleted


# ── SPON-CL-05 ────────────────────────────────────────────────────────────────

class TestDeletedNotPromotable:
    """SPON-CL-05: DELETED entry is not promotable — promote skips it."""

    def test_spon_cl_05_deleted_entry_skipped_by_promote(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, campaign=campaign)
        entry = _make_entry(test_db, sponsor, log, status="DELETED")
        test_db.commit()

        users_before = test_db.query(User).count()
        result = promote_entries([entry.id], sponsor.id, test_db, admin)

        # DELETED status != ACTIVE → skipped
        assert result.skipped == 1
        assert result.promoted == 0
        assert test_db.query(User).count() == users_before

        test_db.expire(entry)
        assert entry.user_id is None


# ── SPON-CL-06 ────────────────────────────────────────────────────────────────

class TestReimportDoesNotReactivateDeleted:
    """SPON-CL-06: CSV re-import does NOT change status of DELETED entry back to ACTIVE."""

    def test_spon_cl_06_reimport_preserves_deleted_status(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)

        # Create a campaign for the re-import
        campaign = SponsorCampaign(
            sponsor_id=sponsor.id,
            name="CL-06 Campaign",
            campaign_type="IMPORT",
            status="ACTIVE",
            created_by=admin.id,
        )
        test_db.add(campaign)
        test_db.flush()

        # Pre-seed a DELETED entry for an email that will be in the CSV
        log0 = _make_log(test_db, sponsor, admin, filename="original.csv")
        log0.campaign_id = campaign.id
        entry = _make_entry(
            test_db, sponsor, log0,
            status="DELETED",
            email="reimport-cl@test.com",
        )
        entry.campaign_id = campaign.id
        test_db.commit()

        # Re-import the same email with consent=1 (would normally create ACTIVE)
        csv_content = (
            "first_name,last_name,email,date_of_birth,age_category,consent_given,consent_source,campaign_source\n"
            "Re,Import,reimport-cl@test.com,,YOUTH,1,form,campaign\n"
        ).encode()

        from app.models.sponsor import Sponsor as SponsorModel
        sponsor_obj = test_db.query(SponsorModel).filter(SponsorModel.id == sponsor.id).first()
        apply_import(csv_content, sponsor_obj, test_db, admin,
                     campaign_id=campaign.id, filename="reimport.csv")

        test_db.expire(entry)
        # Status must remain DELETED — import cannot reactivate
        assert entry.status == "DELETED"


# ── SPON-CL-07 ────────────────────────────────────────────────────────────────

class TestRollbackAlreadyDeleted:
    """SPON-CL-07: rollback on already-DELETED entries → already_deleted counter, deleted==0."""

    def test_spon_cl_07_already_deleted_counted_not_silent(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, campaign=campaign)

        e1 = _make_entry(test_db, sponsor, log, status="DELETED")
        e2 = _make_entry(test_db, sponsor, log, status="DELETED")
        test_db.commit()

        result = rollback_import(log.id, sponsor.id, test_db, admin)

        # No new deletions — everything was already DELETED
        assert result.deleted == 0
        assert result.skipped == 0
        assert result.already_deleted == 2
        assert not result.errors


# ── SPON-CL-08 ────────────────────────────────────────────────────────────────

class TestRollbackMixedBatch:
    """SPON-CL-08: rollback on mixed batch → deleted + already_deleted counted separately."""

    def test_spon_cl_08_mixed_batch_counted_separately(self, test_db: Session):
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log = _make_log(test_db, sponsor, admin, campaign=campaign)

        e_active     = _make_entry(test_db, sponsor, log, status="ACTIVE")
        e_suppressed = _make_entry(test_db, sponsor, log, status="SUPPRESSED")
        e_deleted    = _make_entry(test_db, sponsor, log, status="DELETED")
        test_db.commit()

        result = rollback_import(log.id, sponsor.id, test_db, admin)

        assert result.deleted == 2         # ACTIVE + SUPPRESSED → deleted
        assert result.already_deleted == 1  # DELETED → counted, not silenced
        assert result.skipped == 0
        assert not result.errors

        test_db.expire(e_active)
        test_db.expire(e_suppressed)
        test_db.expire(e_deleted)
        assert e_active.status == "DELETED"
        assert e_suppressed.status == "DELETED"
        assert e_deleted.status == "DELETED"  # unchanged
