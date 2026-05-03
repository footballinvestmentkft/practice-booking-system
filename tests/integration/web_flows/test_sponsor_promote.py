"""
Sponsor Audience Promote-to-User Tests — SPON-P-01 through SPON-P-10

  SPON-P-01  ACTIVE entry, no User → User + UserLicense created, user_id set
  SPON-P-02  ACTIVE entry, existing email → user_id linked, User profile unchanged
  SPON-P-03  Already-promoted entry (user_id set) → full no-op, no duplicate User
  SPON-P-04  SUPPRESSED entry → 0 Users, skipped counter
  SPON-P-05  consent_given=False entry → 0 Users, skipped (explicit consent guard)
  SPON-P-06  Bulk: 3 ACTIVE + 1 SUPPRESSED → 3 promoted, 1 skipped, 1 commit
  SPON-P-07  0 SemesterEnrollment created by promote
  SPON-P-08  promoted_at / promoted_by correctly set
  SPON-P-09  Inactive sponsor → 303 redirect, 0 Users created
  SPON-P-10  CSV apply path still creates 0 Users (P2-B regression)

DONE = pytest tests/integration/web_flows/test_sponsor_promote.py -v
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
from app.models.license import UserLicense
from app.core.security import get_password_hash
from app.services.sponsor_promote_service import promote_entries


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"admin-promo+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Promo Admin",
        password_hash=get_password_hash("Admin1234!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_sponsor(db: Session, admin: User, *, active: bool = True) -> Sponsor:
    s = Sponsor(
        name=f"Promo Sponsor {uuid.uuid4().hex[:6]}",
        code=f"PRMO-{uuid.uuid4().hex[:5].upper()}",
        is_active=active,
        created_by=admin.id,
    )
    db.add(s)
    db.flush()
    return s


def _make_campaign(db: Session, sponsor: Sponsor, admin: User) -> SponsorCampaign:
    c = SponsorCampaign(
        sponsor_id=sponsor.id,
        name=f"Promo Campaign {uuid.uuid4().hex[:4]}",
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
    admin: User,
    *,
    email: str | None = None,
    status: str = "ACTIVE",
    consent_given: bool = True,
    user_id: int | None = None,
) -> SponsorAudienceEntry:
    from app.models.club import CsvImportLog
    campaign = _make_campaign(db, sponsor, admin)
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
        last_name="User",
        email=email or f"promo+{uuid.uuid4().hex[:8]}@test.com",
        status=status,
        consent_given=consent_given,
        user_id=user_id,
    )
    db.add(e)
    db.flush()
    return e


def _client(db: Session, admin: User) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user_web] = lambda: admin
    return TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})


def _csv_bytes(rows: list[str]) -> bytes:
    header = "first_name,last_name,email,date_of_birth,age_category,consent_given,consent_source,campaign_source"
    return "\n".join([header] + rows).encode()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestPromoteNewUser:
    """SPON-P-01"""

    def test_spon_p_01_active_entry_creates_user_and_license(self, test_db: Session):
        """ACTIVE entry with no User → User + UserLicense created, entry.user_id set."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        entry = _make_entry(test_db, sponsor, admin)
        test_db.commit()

        users_before = test_db.query(User).count()
        result = promote_entries([entry.id], sponsor.id, test_db, admin)

        assert result.promoted == 1
        assert result.already_linked == 0
        assert result.skipped == 0
        assert not result.errors

        # User created
        assert test_db.query(User).count() == users_before + 1
        test_db.expire(entry)
        assert entry.user_id is not None

        # UserLicense created
        lic = test_db.query(UserLicense).filter(UserLicense.user_id == entry.user_id).first()
        assert lic is not None
        assert lic.specialization_type == "LFA_FOOTBALL_PLAYER"
        assert lic.current_level == 1
        assert lic.is_active is True


class TestPromoteExistingUser:
    """SPON-P-02"""

    def test_spon_p_02_existing_email_links_user_profile_unchanged(self, test_db: Session):
        """Existing User email match → user_id linked, User profile NOT modified."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)

        existing = User(
            email=f"existing+{uuid.uuid4().hex[:6]}@lfa.com",
            name="Original Name",
            password_hash=get_password_hash("Pass1234!"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(existing)
        test_db.flush()
        original_name = existing.name

        entry = _make_entry(test_db, sponsor, admin, email=existing.email)
        test_db.commit()

        users_before = test_db.query(User).count()
        result = promote_entries([entry.id], sponsor.id, test_db, admin)

        assert result.promoted == 1
        assert test_db.query(User).count() == users_before, "No new User must be created"

        test_db.expire(entry)
        assert entry.user_id == existing.id

        # User profile unchanged
        test_db.expire(existing)
        assert existing.name == original_name


class TestIdempotence:
    """SPON-P-03"""

    def test_spon_p_03_already_promoted_is_full_noop(self, test_db: Session):
        """Entry with user_id already set → full no-op, promoted_at/by not overwritten."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)

        # Create a real user to serve as the pre-linked user
        linked_user = User(
            email=f"linked+{uuid.uuid4().hex[:6]}@lfa.com",
            name="Already Linked",
            password_hash=get_password_hash("Pass1234!"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(linked_user)
        test_db.flush()

        entry = _make_entry(test_db, sponsor, admin, user_id=linked_user.id)
        test_db.commit()

        users_before = test_db.query(User).count()
        result = promote_entries([entry.id], sponsor.id, test_db, admin)

        assert result.already_linked == 1
        assert result.promoted == 0
        assert test_db.query(User).count() == users_before, "No new User on no-op"

        # promoted_at must remain NULL (was never set)
        test_db.expire(entry)
        assert entry.promoted_at is None


class TestSuppressedBlocked:
    """SPON-P-04"""

    def test_spon_p_04_suppressed_entry_is_skipped(self, test_db: Session):
        """SUPPRESSED entry → 0 Users, skipped counter +1, entry unchanged."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        entry = _make_entry(test_db, sponsor, admin, status="SUPPRESSED", consent_given=False)
        test_db.commit()

        users_before = test_db.query(User).count()
        result = promote_entries([entry.id], sponsor.id, test_db, admin)

        assert result.skipped == 1
        assert result.promoted == 0
        assert test_db.query(User).count() == users_before

        test_db.expire(entry)
        assert entry.user_id is None
        assert entry.promoted_at is None


class TestConsentGuard:
    """SPON-P-05 — explicit consent guard"""

    def test_spon_p_05_no_consent_is_skipped(self, test_db: Session):
        """ACTIVE status but consent_given=False → 0 Users (explicit consent guard)."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        # Edge case: status=ACTIVE but consent_given=False (should never happen via import,
        # but guard must be explicit and tested)
        entry = _make_entry(test_db, sponsor, admin, status="ACTIVE", consent_given=False)
        test_db.commit()

        users_before = test_db.query(User).count()
        result = promote_entries([entry.id], sponsor.id, test_db, admin)

        assert result.skipped == 1
        assert result.promoted == 0
        assert test_db.query(User).count() == users_before

        test_db.expire(entry)
        assert entry.user_id is None


class TestBulkPromote:
    """SPON-P-06"""

    def test_spon_p_06_bulk_mixed_entries(self, test_db: Session):
        """Bulk: 3 ACTIVE + 1 SUPPRESSED → 3 promoted, 1 skipped."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)

        actives = [_make_entry(test_db, sponsor, admin) for _ in range(3)]
        suppressed = _make_entry(test_db, sponsor, admin, status="SUPPRESSED", consent_given=False)
        test_db.commit()

        all_ids = [e.id for e in actives] + [suppressed.id]
        users_before = test_db.query(User).count()
        result = promote_entries(all_ids, sponsor.id, test_db, admin)

        assert result.promoted == 3
        assert result.skipped == 1
        assert result.already_linked == 0
        assert test_db.query(User).count() == users_before + 3


class TestNoEnrollment:
    """SPON-P-07"""

    def test_spon_p_07_zero_semester_enrollments(self, test_db: Session):
        """Promote creates 0 SemesterEnrollment records."""
        from app.models.semester_enrollment import SemesterEnrollment
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        entry = _make_entry(test_db, sponsor, admin)
        test_db.commit()

        enroll_before = test_db.query(SemesterEnrollment).count()
        promote_entries([entry.id], sponsor.id, test_db, admin)
        assert test_db.query(SemesterEnrollment).count() == enroll_before


class TestAuditFields:
    """SPON-P-08"""

    def test_spon_p_08_promoted_at_and_by_set(self, test_db: Session):
        """promoted_at is set to now, promoted_by = admin.id."""
        from datetime import datetime, timezone, timedelta
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        entry = _make_entry(test_db, sponsor, admin)
        test_db.commit()

        before = datetime.now(timezone.utc) - timedelta(seconds=2)
        promote_entries([entry.id], sponsor.id, test_db, admin)

        test_db.expire(entry)
        assert entry.promoted_at is not None
        assert entry.promoted_at >= before
        assert entry.promoted_by == admin.id


class TestInactiveGuard:
    """SPON-P-09"""

    def test_spon_p_09_inactive_sponsor_returns_303(self, test_db: Session):
        """Inactive sponsor → POST /promote returns 303, 0 Users created."""
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin, active=False)
        entry = _make_entry(test_db, sponsor, admin)
        campaign_id = entry.campaign_id
        test_db.commit()
        client = _client(test_db, admin)

        try:
            users_before = test_db.query(User).count()
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign_id}/audience/promote",
                data={"entry_ids": str(entry.id)},
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert test_db.query(User).count() == users_before
        finally:
            app.dependency_overrides.clear()


class TestCsvApplyStillZeroUsers:
    """SPON-P-10 — P2-B regression"""

    def test_spon_p_10_csv_apply_creates_zero_users(self, test_db: Session):
        """CSV import apply path still creates 0 Users (P2-B invariant)."""
        from app.models.sponsor import SponsorCampaign
        admin = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        test_db.commit()
        client = _client(test_db, admin)

        csv_content = _csv_bytes([
            "Alpha,Beta,alpha.beta@p10.test,2012-01-01,YOUTH,1,form,campaign",
            "Gamma,Delta,gamma.delta@p10.test,,PRE,0,,",
        ])
        b64 = base64.b64encode(csv_content).decode()

        try:
            users_before = test_db.query(User).count()
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns/{campaign.id}/import/apply",
                data={"csv_data": b64, "filename": "p10.csv"},
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert test_db.query(User).count() == users_before, \
                "CSV import must not create any User (P2-B invariant)"
        finally:
            app.dependency_overrides.clear()
