"""
Unit tests for campaign_enrollment_service.bulk_enroll_from_campaign().

Test IDs: BULKENR-01 … BULKENR-10

These tests use a real test DB (test_db fixture) rather than mocks because
the service writes SemesterEnrollment rows and the UniqueConstraint
(user_id, semester_id, user_license_id) must be exercised at DB level.
"""
import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.core.security import get_password_hash
from app.models.license import UserLicense
from app.models.semester import Semester, SemesterStatus, SemesterCategory
from app.models.semester_enrollment import SemesterEnrollment, EnrollmentStatus
from app.models.sponsor import Sponsor, SponsorCampaign, SponsorAudienceEntry
from app.models.club import CsvImportLog
from app.models.user import User, UserRole
from app.services.tournament.campaign_enrollment_service import bulk_enroll_from_campaign


# ── Seed helpers ──────────────────────────────────────────────────────────────

def _admin(db: Session) -> User:
    u = User(
        email=f"admin+{uuid.uuid4().hex[:8]}@test.com",
        name="Test Admin",
        password_hash=get_password_hash("x"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _sponsor(db: Session) -> Sponsor:
    s = Sponsor(
        name=f"Sponsor {uuid.uuid4().hex[:6]}",
        code=f"SP-{uuid.uuid4().hex[:6]}",
        is_active=True,
    )
    db.add(s)
    db.flush()
    return s


def _campaign(db: Session, sponsor: Sponsor) -> SponsorCampaign:
    c = SponsorCampaign(
        sponsor_id=sponsor.id,
        name=f"Campaign {uuid.uuid4().hex[:6]}",
        campaign_type="IMPORT",
        status="ACTIVE",
    )
    db.add(c)
    db.flush()
    return c


def _promo_tournament(db: Session, sponsor: Sponsor, campaign: SponsorCampaign,
                      status: str = "DRAFT") -> Semester:
    sem = Semester(
        code=f"PROMO-{uuid.uuid4().hex[:6]}",
        name="Bulk Enroll Test Event",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 2),
        status=SemesterStatus.DRAFT,
        tournament_status=status,
        semester_category=SemesterCategory.PROMOTION_EVENT,
        age_groups=["PRE"],
        enrollment_cost=0,
        organizer_sponsor_id=sponsor.id,
        organizer_campaign_id=campaign.id,
    )
    db.add(sem)
    db.flush()
    return sem


def _user_with_license(db: Session, email_hint: str = "") -> tuple[User, UserLicense]:
    u = User(
        email=f"player{email_hint}+{uuid.uuid4().hex[:8]}@test.com",
        name=f"Player {email_hint}",
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


def _audience_entry(db: Session, sponsor: Sponsor, campaign: SponsorCampaign,
                    user: User | None = None,
                    status: str = "ACTIVE",
                    consent_given: bool = True) -> SponsorAudienceEntry:
    log = CsvImportLog(sponsor_id=sponsor.id, campaign_id=campaign.id)
    db.add(log)
    db.flush()
    entry = SponsorAudienceEntry(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        import_log_id=log.id,
        first_name="Test",
        last_name=f"Entry {uuid.uuid4().hex[:4]}",
        email=f"entry+{uuid.uuid4().hex[:8]}@test.com",
        status=status,
        consent_given=consent_given,
        user_id=user.id if user else None,
    )
    db.add(entry)
    db.flush()
    return entry


# ── BULKENR-01 ────────────────────────────────────────────────────────────────

class TestBulkEnrollEligibleEntries:
    """BULKENR-01: 3 eligible entries → 3 SemesterEnrollment rows created."""

    def test_bulkenr_01(self, test_db: Session):
        admin = _admin(test_db)
        sponsor = _sponsor(test_db)
        campaign = _campaign(test_db, sponsor)
        t = _promo_tournament(test_db, sponsor, campaign, status="DRAFT")

        users_and_licenses = [_user_with_license(test_db, str(i)) for i in range(3)]
        for user, _ in users_and_licenses:
            _audience_entry(test_db, sponsor, campaign, user=user)
        test_db.commit()

        result = bulk_enroll_from_campaign(test_db, t.id, admin.id)
        test_db.commit()

        assert result["enrolled_count"] == 3
        assert result["skipped_count"] == 0
        assert len(result["enrolled"]) == 3

        enrollments = test_db.query(SemesterEnrollment).filter(
            SemesterEnrollment.semester_id == t.id,
            SemesterEnrollment.is_active == True,
            SemesterEnrollment.request_status == EnrollmentStatus.APPROVED,
        ).all()
        assert len(enrollments) == 3
        assert all(e.payment_verified for e in enrollments)
        assert all(e.approved_by == admin.id for e in enrollments)


# ── BULKENR-02 ────────────────────────────────────────────────────────────────

class TestBulkEnrollIdempotent:
    """BULKENR-02: calling twice → second call skips all (already enrolled)."""

    def test_bulkenr_02(self, test_db: Session):
        admin = _admin(test_db)
        sponsor = _sponsor(test_db)
        campaign = _campaign(test_db, sponsor)
        t = _promo_tournament(test_db, sponsor, campaign, status="DRAFT")

        user, _ = _user_with_license(test_db, "idem")
        _audience_entry(test_db, sponsor, campaign, user=user)
        test_db.commit()

        # First call
        r1 = bulk_enroll_from_campaign(test_db, t.id, admin.id)
        test_db.commit()
        assert r1["enrolled_count"] == 1

        # Second call — idempotent
        r2 = bulk_enroll_from_campaign(test_db, t.id, admin.id)
        test_db.commit()
        assert r2["enrolled_count"] == 0
        assert r2["skipped_count"] == 1
        assert r2["skipped"][0]["reason"] == "already enrolled"

        # Still exactly 1 enrollment
        count = test_db.query(SemesterEnrollment).filter(
            SemesterEnrollment.semester_id == t.id,
            SemesterEnrollment.is_active == True,
        ).count()
        assert count == 1


# ── BULKENR-03 ────────────────────────────────────────────────────────────────

class TestBulkEnrollReactivatesInactive:
    """BULKENR-03: inactive enrollment re-activated rather than duplicate insert."""

    def test_bulkenr_03(self, test_db: Session):
        admin = _admin(test_db)
        sponsor = _sponsor(test_db)
        campaign = _campaign(test_db, sponsor)
        t = _promo_tournament(test_db, sponsor, campaign, status="DRAFT")

        user, lic = _user_with_license(test_db, "react")
        _audience_entry(test_db, sponsor, campaign, user=user)

        # Pre-existing inactive enrollment
        inactive = SemesterEnrollment(
            user_id=user.id,
            semester_id=t.id,
            user_license_id=lic.id,
            request_status=EnrollmentStatus.APPROVED,
            is_active=False,
            payment_verified=True,
            enrolled_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc),
            requested_at=__import__('datetime').datetime.now(__import__('datetime').timezone.utc),
        )
        test_db.add(inactive)
        test_db.commit()

        result = bulk_enroll_from_campaign(test_db, t.id, admin.id)
        test_db.commit()

        assert result["enrolled_count"] == 1
        assert result["skipped_count"] == 0

        # Exactly 1 row (reactivated, not duplicated)
        rows = test_db.query(SemesterEnrollment).filter(
            SemesterEnrollment.semester_id == t.id,
            SemesterEnrollment.user_id == user.id,
        ).all()
        assert len(rows) == 1
        assert rows[0].is_active is True


# ── BULKENR-04 ────────────────────────────────────────────────────────────────

class TestBulkEnrollSkipsSuppressed:
    """BULKENR-04: SUPPRESSED entry excluded (no consent)."""

    def test_bulkenr_04(self, test_db: Session):
        admin = _admin(test_db)
        sponsor = _sponsor(test_db)
        campaign = _campaign(test_db, sponsor)
        t = _promo_tournament(test_db, sponsor, campaign)

        user, _ = _user_with_license(test_db, "sup")
        _audience_entry(test_db, sponsor, campaign, user=user, status="SUPPRESSED")
        test_db.commit()

        result = bulk_enroll_from_campaign(test_db, t.id, admin.id)
        test_db.commit()

        assert result["enrolled_count"] == 0
        assert test_db.query(SemesterEnrollment).filter(
            SemesterEnrollment.semester_id == t.id
        ).count() == 0


# ── BULKENR-05 ────────────────────────────────────────────────────────────────

class TestBulkEnrollSkipsUnpromoted:
    """BULKENR-05: entry with user_id=None (not promoted) excluded."""

    def test_bulkenr_05(self, test_db: Session):
        admin = _admin(test_db)
        sponsor = _sponsor(test_db)
        campaign = _campaign(test_db, sponsor)
        t = _promo_tournament(test_db, sponsor, campaign)

        _audience_entry(test_db, sponsor, campaign, user=None)
        test_db.commit()

        result = bulk_enroll_from_campaign(test_db, t.id, admin.id)
        test_db.commit()

        assert result["enrolled_count"] == 0


# ── BULKENR-06 ────────────────────────────────────────────────────────────────

class TestBulkEnrollSkipsInactiveUser:
    """BULKENR-06: promoted user with is_active=False → skipped."""

    def test_bulkenr_06(self, test_db: Session):
        admin = _admin(test_db)
        sponsor = _sponsor(test_db)
        campaign = _campaign(test_db, sponsor)
        t = _promo_tournament(test_db, sponsor, campaign)

        user, _ = _user_with_license(test_db, "inact")
        user.is_active = False
        _audience_entry(test_db, sponsor, campaign, user=user)
        test_db.commit()

        result = bulk_enroll_from_campaign(test_db, t.id, admin.id)
        test_db.commit()

        assert result["enrolled_count"] == 0
        assert result["skipped"][0]["reason"] == "user inactive or not found"


# ── BULKENR-07 ────────────────────────────────────────────────────────────────

class TestBulkEnrollSkipsNoLicense:
    """BULKENR-07: promoted user without active LFA license → skipped."""

    def test_bulkenr_07(self, test_db: Session):
        admin = _admin(test_db)
        sponsor = _sponsor(test_db)
        campaign = _campaign(test_db, sponsor)
        t = _promo_tournament(test_db, sponsor, campaign)

        # User without license
        u = User(
            email=f"nolic+{uuid.uuid4().hex[:8]}@test.com",
            name="No License",
            password_hash=get_password_hash("x"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(u)
        test_db.flush()
        _audience_entry(test_db, sponsor, campaign, user=u)
        test_db.commit()

        result = bulk_enroll_from_campaign(test_db, t.id, admin.id)
        test_db.commit()

        assert result["enrolled_count"] == 0
        assert "license" in result["skipped"][0]["reason"]


# ── BULKENR-08 ────────────────────────────────────────────────────────────────

class TestBulkEnrollRejectsNonPromo:
    """BULKENR-08: non-PROMOTION_EVENT tournament → ValueError."""

    def test_bulkenr_08(self, test_db: Session):
        admin = _admin(test_db)
        non_promo = Semester(
            code=f"MINI-{uuid.uuid4().hex[:6]}",
            name="Mini Season",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 2),
            status=SemesterStatus.DRAFT,
            tournament_status="DRAFT",
            semester_category=SemesterCategory.MINI_SEASON,
            age_group="AMATEUR",
            enrollment_cost=0,
        )
        test_db.add(non_promo)
        test_db.commit()

        with pytest.raises(ValueError, match="PROMOTION_EVENT"):
            bulk_enroll_from_campaign(test_db, non_promo.id, admin.id)


# ── BULKENR-09 ────────────────────────────────────────────────────────────────

class TestBulkEnrollRejectsFrozenStatus:
    """BULKENR-09: CHECK_IN_OPEN or later → ValueError (frozen)."""

    @pytest.mark.parametrize("frozen_status", ["CHECK_IN_OPEN", "IN_PROGRESS", "COMPLETED"])
    def test_bulkenr_09(self, test_db: Session, frozen_status: str):
        admin = _admin(test_db)
        sponsor = _sponsor(test_db)
        campaign = _campaign(test_db, sponsor)
        t = _promo_tournament(test_db, sponsor, campaign, status=frozen_status)
        test_db.commit()

        with pytest.raises(ValueError, match="frozen|CHECK_IN_OPEN"):
            bulk_enroll_from_campaign(test_db, t.id, admin.id)


# ── BULKENR-10 ────────────────────────────────────────────────────────────────

class TestBulkEnrollRejectsNoCampaignLinkage:
    """BULKENR-10: organizer_campaign_id=None → ValueError."""

    def test_bulkenr_10(self, test_db: Session):
        admin = _admin(test_db)
        t = Semester(
            code=f"PROMO-NC-{uuid.uuid4().hex[:6]}",
            name="No Campaign Promo",
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 2),
            status=SemesterStatus.DRAFT,
            tournament_status="DRAFT",
            semester_category=SemesterCategory.PROMOTION_EVENT,
            age_groups=["PRE"],
            enrollment_cost=0,
            organizer_sponsor_id=None,
            organizer_campaign_id=None,
        )
        test_db.add(t)
        test_db.commit()

        with pytest.raises(ValueError, match="organizer"):
            bulk_enroll_from_campaign(test_db, t.id, admin.id)


# ── BULKENR-ENROLLED_CLOSED ───────────────────────────────────────────────────

class TestBulkEnrollInEnrollmentClosed:
    """BULKENR-EC: bulk enroll also works when status=ENROLLMENT_CLOSED."""

    def test_bulkenr_enrollment_closed(self, test_db: Session):
        admin = _admin(test_db)
        sponsor = _sponsor(test_db)
        campaign = _campaign(test_db, sponsor)
        t = _promo_tournament(test_db, sponsor, campaign, status="ENROLLMENT_CLOSED")

        user, _ = _user_with_license(test_db, "ec")
        _audience_entry(test_db, sponsor, campaign, user=user)
        test_db.commit()

        result = bulk_enroll_from_campaign(test_db, t.id, admin.id)
        test_db.commit()

        assert result["enrolled_count"] == 1


# ── BULKENR-11 ────────────────────────────────────────────────────────────────

class TestBulkEnrollInEnrollmentOpen:
    """BULKENR-11: bulk_enroll_from_campaign allowed when status=ENROLLMENT_OPEN.

    Recovery path: PROMOTION_EVENT tournaments that entered ENROLLMENT_OPEN via
    legacy data or direct API call must be able to bulk-enroll before locking.
    """

    def test_bulkenr_11_enrollment_open(self, test_db: Session):
        admin = _admin(test_db)
        sponsor = _sponsor(test_db)
        campaign = _campaign(test_db, sponsor)
        t = _promo_tournament(test_db, sponsor, campaign, status="ENROLLMENT_OPEN")

        user, _ = _user_with_license(test_db, "eo")
        _audience_entry(test_db, sponsor, campaign, user=user)
        test_db.commit()

        result = bulk_enroll_from_campaign(test_db, t.id, admin.id)
        test_db.commit()

        assert result["enrolled_count"] == 1
        assert result["skipped_count"] == 0


# ── BULKENR-12 ────────────────────────────────────────────────────────────────

class TestBulkEnrollNullStatus:
    """BULKENR-12: NULL tournament_status treated as DRAFT — service does not reject it.

    Root cause fix: service was doing `tournament.tournament_status not in _ALLOWED_STATUSES`
    which evaluates None as NOT in the set, raising ValueError even though NULL ≡ DRAFT
    (same fallback used in the template and route).
    """

    def test_bulkenr_12_null_status_succeeds(self, test_db: Session):
        admin = _admin(test_db)
        sponsor = _sponsor(test_db)
        campaign = _campaign(test_db, sponsor)
        t = _promo_tournament(test_db, sponsor, campaign, status="DRAFT")
        # Simulate a tournament where tournament_status was never explicitly set
        t.tournament_status = None
        test_db.flush()

        user, _ = _user_with_license(test_db, "null")
        _audience_entry(test_db, sponsor, campaign, user=user)
        test_db.commit()

        # Must NOT raise ValueError for NULL status
        result = bulk_enroll_from_campaign(test_db, t.id, admin.id)
        test_db.commit()

        assert result["enrolled_count"] == 1
        assert result["skipped_count"] == 0


# ── BULKENR-13 ────────────────────────────────────────────────────────────────

class TestBulkEnrollAllSkippedNoLicense:
    """BULKENR-13: promoted entries (user_id set) with no LFA_FOOTBALL_PLAYER license
    → all skipped, enrolled_count=0, skipped_count=N, reason surfaced.

    This is the scenario behind the P0 silent fail: the template eligible count was
    inflated (no license check), but the service skipped everyone — result was
    enrolled_count=0 with no visible feedback.
    """

    def test_bulkenr_13_no_license_all_skipped(self, test_db: Session):
        admin = _admin(test_db)
        sponsor = _sponsor(test_db)
        campaign = _campaign(test_db, sponsor)
        t = _promo_tournament(test_db, sponsor, campaign, status="DRAFT")

        # User without LFA_FOOTBALL_PLAYER license
        user = User(
            email=f"nolic+{uuid.uuid4().hex[:8]}@test.com",
            name="No License Player",
            password_hash=get_password_hash("x"),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(user)
        test_db.flush()
        # Deliberately no UserLicense added

        _audience_entry(test_db, sponsor, campaign, user=user)
        test_db.commit()

        result = bulk_enroll_from_campaign(test_db, t.id, admin.id)
        test_db.commit()

        assert result["enrolled_count"] == 0
        assert result["skipped_count"] == 1
        assert result["skipped"][0]["user_id"] == user.id
        assert "license" in result["skipped"][0]["reason"]
