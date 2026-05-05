"""
Sponsor Promote Credit Flow Tests — SPON-CREDIT-01 through SPON-CREDIT-10

  SPON-CREDIT-01  Grant + unlock always issued as a pair after successful promote
  SPON-CREDIT-02  Retry (double-promote call) → credit_balance unchanged, no new CreditTransactions
  SPON-CREDIT-03  Existing user without campaign's license → new UserLicense created, credits issued
  SPON-CREDIT-04  Existing user with existing license → already_linked, no credit duplication
  SPON-CREDIT-05  unlock_cost > credit_grant_amount → campaign create route 303+error
  SPON-CREDIT-06  credit_grant_amount < 100 → campaign create route 303+error
  SPON-CREDIT-07  Balance invariant: SUM(ct.amount) WHERE user_id == user.credit_balance
  SPON-CREDIT-08  3-entry batch → 3 SPONSOR_CREDIT_GRANT + 3 SPECIALIZATION_UNLOCK rows
  SPON-CREDIT-09  All settlement rows have sponsor_id + campaign_id
  SPON-CREDIT-10  Partial-failure rollback: grant would succeed but unlock raises →
                  nothing committed (via InsufficientCreditsError simulation)

DONE = pytest tests/integration/web_flows/test_sponsor_promote_credits.py -v
"""
import uuid
from datetime import date, datetime
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user_web
from app.main import app
from app.models.credit_transaction import CreditTransaction
from app.models.license import UserLicense
from app.models.sponsor import Sponsor, SponsorAudienceEntry, SponsorCampaign
from app.models.user import User, UserRole
from app.core.security import get_password_hash
from app.services.credit_service import InsufficientCreditsError
from app.services.sponsor_promote_service import promote_entries


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_admin(db: Session) -> User:
    u = User(
        email=f"admin-cred+{uuid.uuid4().hex[:8]}@lfa.com",
        name="Credit Admin",
        password_hash=get_password_hash("Admin1234!"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_sponsor(db: Session, admin: User) -> Sponsor:
    s = Sponsor(
        name=f"CreditSponsor {uuid.uuid4().hex[:6]}",
        code=f"CRED-{uuid.uuid4().hex[:5].upper()}",
        is_active=True,
        created_by=admin.id,
    )
    db.add(s)
    db.flush()
    return s


def _make_campaign(
    db: Session,
    sponsor: Sponsor,
    admin: User,
    *,
    credit_grant_amount: int = 100,
    unlock_cost: int = 100,
    specialization_type: str = "LFA_FOOTBALL_PLAYER",
) -> SponsorCampaign:
    c = SponsorCampaign(
        sponsor_id=sponsor.id,
        name=f"CreditCampaign {uuid.uuid4().hex[:4]}",
        campaign_type="IMPORT",
        status="ACTIVE",
        created_by=admin.id,
        specialization_type=specialization_type,
        credit_grant_amount=credit_grant_amount,
        unlock_cost=unlock_cost,
    )
    db.add(c)
    db.flush()
    return c


def _make_import_log(db: Session, sponsor: Sponsor, campaign: SponsorCampaign, admin: User):
    from app.models.club import CsvImportLog
    log = CsvImportLog(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        filename="test.csv",
        total_rows=1,
        uploaded_by=admin.id,
        status="DONE",
    )
    db.add(log)
    db.flush()
    return log


def _make_entry(
    db: Session,
    sponsor: Sponsor,
    campaign: SponsorCampaign,
    log,
    admin: User,
    *,
    email: str | None = None,
    status: str = "ACTIVE",
    consent_given: bool = True,
    position: str | None = "STRIKER",
    dob: date | None = date(2005, 3, 15),
) -> SponsorAudienceEntry:
    e = SponsorAudienceEntry(
        sponsor_id=sponsor.id,
        campaign_id=campaign.id,
        import_log_id=log.id,
        email=email or f"cred+{uuid.uuid4().hex[:8]}@test.com",
        first_name="Credit",
        last_name="User",
        status=status,
        consent_given=consent_given,
        position=position,
        date_of_birth=dob,
        imported_by=admin.id,
    )
    db.add(e)
    db.flush()
    return e


def _client(db: Session, admin: User) -> TestClient:
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user_web] = lambda: admin
    return TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"})


def _credit_rows(db: Session, user: User, tx_type: str) -> list[CreditTransaction]:
    return (
        db.query(CreditTransaction)
        .filter(
            CreditTransaction.user_id == user.id,
            CreditTransaction.transaction_type == tx_type,
        )
        .all()
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestSponsorPromoteCredits:

    def test_spon_credit_01_grant_and_unlock_always_paired(self, test_db: Session):
        """After a successful promote, exactly 1 GRANT + 1 UNLOCK per promoted user."""
        admin    = _make_admin(test_db)
        sponsor  = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log      = _make_import_log(test_db, sponsor, campaign, admin)
        entry    = _make_entry(test_db, sponsor, campaign, log, admin)

        result = promote_entries([entry.id], sponsor.id, test_db, admin,
                                 campaign_id=campaign.id)

        assert result.promoted == 1
        assert result.credits_granted == 100
        assert result.unlock_deductions == 100

        test_db.expire_all()
        promoted_user = test_db.query(User).filter(User.id == entry.user_id).first()

        grants  = _credit_rows(test_db, promoted_user, "SPONSOR_CREDIT_GRANT")
        unlocks = _credit_rows(test_db, promoted_user, "SPECIALIZATION_UNLOCK")
        assert len(grants)  == 1
        assert len(unlocks) == 1
        assert grants[0].amount  == +100
        assert unlocks[0].amount == -100

    def test_spon_credit_02_retry_no_duplicate_credit(self, test_db: Session):
        """Calling promote_entries twice on the same entry doesn't duplicate credit rows
        and leaves credit_balance unchanged on the second call."""
        admin    = _make_admin(test_db)
        sponsor  = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log      = _make_import_log(test_db, sponsor, campaign, admin)
        entry    = _make_entry(test_db, sponsor, campaign, log, admin)

        # First promote
        r1 = promote_entries([entry.id], sponsor.id, test_db, admin,
                             campaign_id=campaign.id)
        assert r1.promoted == 1
        test_db.expire_all()

        user = test_db.query(User).filter(User.id == entry.user_id).first()
        balance_after_first = user.credit_balance

        # Second promote (same entry_ids)
        r2 = promote_entries([entry.id], sponsor.id, test_db, admin,
                             campaign_id=campaign.id)
        assert r2.promoted == 0
        assert r2.already_linked == 1
        test_db.expire_all()

        user = test_db.query(User).filter(User.id == entry.user_id).first()
        assert user.credit_balance == balance_after_first

        grants  = _credit_rows(test_db, user, "SPONSOR_CREDIT_GRANT")
        unlocks = _credit_rows(test_db, user, "SPECIALIZATION_UNLOCK")
        assert len(grants)  == 1, "No duplicate grant on retry"
        assert len(unlocks) == 1, "No duplicate unlock on retry"

    def test_spon_credit_03_existing_user_no_license_gets_new_license_and_credits(
        self, test_db: Session
    ):
        """Existing user without the campaign's specialization license receives a new
        UserLicense AND full credit grant+unlock."""
        admin    = _make_admin(test_db)
        sponsor  = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin,
                                  specialization_type="LFA_FOOTBALL_PLAYER")
        log      = _make_import_log(test_db, sponsor, campaign, admin)

        # Pre-create a User with a DIFFERENT specialization license
        existing_email = f"existing+{uuid.uuid4().hex[:8]}@test.com"
        existing_user  = User(
            email=existing_email,
            name="Existing User",
            password_hash=get_password_hash(uuid.uuid4().hex),
            role=UserRole.STUDENT,
            is_active=True,
        )
        test_db.add(existing_user)
        test_db.flush()
        # No LFA_FOOTBALL_PLAYER license — user has none at all

        entry = _make_entry(test_db, sponsor, campaign, log, admin,
                            email=existing_email)

        result = promote_entries([entry.id], sponsor.id, test_db, admin,
                                 campaign_id=campaign.id)

        assert result.promoted == 1
        test_db.expire_all()

        # New license created for this user
        license = (
            test_db.query(UserLicense)
            .filter(
                UserLicense.user_id == existing_user.id,
                UserLicense.specialization_type == "LFA_FOOTBALL_PLAYER",
                UserLicense.is_active == True,
            )
            .first()
        )
        assert license is not None, "UserLicense must be created for existing user"

        # Credits issued
        grants = _credit_rows(test_db, existing_user, "SPONSOR_CREDIT_GRANT")
        assert len(grants) == 1

    def test_spon_credit_04_existing_user_with_license_already_linked(self, test_db: Session):
        """Existing user that was already promoted → already_linked, zero new credit rows."""
        admin    = _make_admin(test_db)
        sponsor  = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log      = _make_import_log(test_db, sponsor, campaign, admin)
        entry    = _make_entry(test_db, sponsor, campaign, log, admin)

        # First promote — creates User, license, credits
        r1 = promote_entries([entry.id], sponsor.id, test_db, admin,
                             campaign_id=campaign.id)
        assert r1.promoted == 1
        test_db.expire_all()

        user = test_db.query(User).filter(User.id == entry.user_id).first()
        grants_before = len(_credit_rows(test_db, user, "SPONSOR_CREDIT_GRANT"))

        # Second promote — already_linked path
        r2 = promote_entries([entry.id], sponsor.id, test_db, admin,
                             campaign_id=campaign.id)
        assert r2.already_linked == 1
        assert r2.promoted == 0
        test_db.expire_all()

        grants_after = len(_credit_rows(test_db, user, "SPONSOR_CREDIT_GRANT"))
        assert grants_after == grants_before, "No extra credit rows for already-linked entry"

    def test_spon_credit_05_unlock_exceeds_grant_route_rejects(self, test_db: Session):
        """Campaign create POST with unlock_cost > credit_grant_amount → 303 + error."""
        admin   = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        client  = _client(test_db, admin)

        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns",
                data={
                    "csrf_token": "",
                    "name": "Bad Campaign",
                    "campaign_type": "IMPORT",
                    "specialization_type": "LFA_FOOTBALL_PLAYER",
                    "credit_grant_amount": "100",
                    "unlock_cost": "150",   # exceeds grant
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "error" in resp.headers["location"]
        finally:
            app.dependency_overrides.clear()

        # No campaign created
        count = (
            test_db.query(SponsorCampaign)
            .filter(
                SponsorCampaign.sponsor_id == sponsor.id,
                SponsorCampaign.name == "Bad Campaign",
            )
            .count()
        )
        assert count == 0

    def test_spon_credit_06_grant_below_100_route_rejects(self, test_db: Session):
        """Campaign create POST with credit_grant_amount < 100 → 303 + error."""
        admin   = _make_admin(test_db)
        sponsor = _make_sponsor(test_db, admin)
        client  = _client(test_db, admin)

        try:
            resp = client.post(
                f"/admin/sponsors/{sponsor.id}/campaigns",
                data={
                    "csrf_token": "",
                    "name": "Cheap Campaign",
                    "campaign_type": "IMPORT",
                    "specialization_type": "LFA_FOOTBALL_PLAYER",
                    "credit_grant_amount": "50",   # below minimum
                    "unlock_cost": "50",
                },
                follow_redirects=False,
            )
            assert resp.status_code == 303
            assert "error" in resp.headers["location"]
        finally:
            app.dependency_overrides.clear()

    def test_spon_credit_07_balance_invariant_sum_equals_balance(self, test_db: Session):
        """After promote: SUM(credit_transactions.amount) WHERE user_id == user.credit_balance."""
        admin    = _make_admin(test_db)
        sponsor  = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin,
                                  credit_grant_amount=100, unlock_cost=100)
        log   = _make_import_log(test_db, sponsor, campaign, admin)
        entry = _make_entry(test_db, sponsor, campaign, log, admin)

        promote_entries([entry.id], sponsor.id, test_db, admin,
                        campaign_id=campaign.id)
        test_db.expire_all()

        user = test_db.query(User).filter(User.id == entry.user_id).first()
        tx_sum = (
            test_db.query(func.sum(CreditTransaction.amount))
            .filter(CreditTransaction.user_id == user.id)
            .scalar()
            or 0
        )
        assert tx_sum == user.credit_balance, (
            f"Ledger invariant broken: SUM={tx_sum}, credit_balance={user.credit_balance}"
        )

    def test_spon_credit_08_batch_three_entries_correct_row_count(self, test_db: Session):
        """3-entry batch → exactly 3 SPONSOR_CREDIT_GRANT + 3 SPECIALIZATION_UNLOCK rows."""
        admin    = _make_admin(test_db)
        sponsor  = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log      = _make_import_log(test_db, sponsor, campaign, admin)

        entries = [
            _make_entry(test_db, sponsor, campaign, log, admin)
            for _ in range(3)
        ]
        entry_ids = [e.id for e in entries]

        result = promote_entries(entry_ids, sponsor.id, test_db, admin,
                                 campaign_id=campaign.id)
        assert result.promoted == 3

        grants = (
            test_db.query(CreditTransaction)
            .filter(
                CreditTransaction.campaign_id == campaign.id,
                CreditTransaction.transaction_type == "SPONSOR_CREDIT_GRANT",
            )
            .count()
        )
        unlocks = (
            test_db.query(CreditTransaction)
            .filter(
                CreditTransaction.campaign_id == campaign.id,
                CreditTransaction.transaction_type == "SPECIALIZATION_UNLOCK",
            )
            .count()
        )
        assert grants == 3
        assert unlocks == 3

    def test_spon_credit_09_settlement_columns_populated(self, test_db: Session):
        """Every SPONSOR_CREDIT_GRANT / SPECIALIZATION_UNLOCK row carries sponsor_id
        and campaign_id — required for settlement queries."""
        admin    = _make_admin(test_db)
        sponsor  = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log      = _make_import_log(test_db, sponsor, campaign, admin)
        entry    = _make_entry(test_db, sponsor, campaign, log, admin)

        promote_entries([entry.id], sponsor.id, test_db, admin,
                        campaign_id=campaign.id)

        rows = (
            test_db.query(CreditTransaction)
            .filter(
                CreditTransaction.campaign_id == campaign.id,
                CreditTransaction.transaction_type.in_(
                    ["SPONSOR_CREDIT_GRANT", "SPECIALIZATION_UNLOCK"]
                ),
            )
            .all()
        )
        assert len(rows) == 2
        for row in rows:
            assert row.sponsor_id == sponsor.id, "sponsor_id must be set for settlement"
            assert row.campaign_id == campaign.id, "campaign_id must be set for settlement"

    def test_spon_credit_10_partial_failure_rolls_back_atomically(self, test_db: Session):
        """If the unlock deduction fails (e.g. InsufficientCreditsError), the grant
        must not be committed either — the entire batch rolls back.

        Setup data is committed to the SAVEPOINT so it survives the rollback of
        the failed promote attempt.  The promote changes (user_id, grant TX) are
        flushed but never committed; after an explicit rollback they are undone.
        """
        admin    = _make_admin(test_db)
        sponsor  = _make_sponsor(test_db, admin)
        campaign = _make_campaign(test_db, sponsor, admin)
        log      = _make_import_log(test_db, sponsor, campaign, admin)
        entry    = _make_entry(test_db, sponsor, campaign, log, admin)

        # Commit test data to the SAVEPOINT so it survives a subsequent rollback.
        test_db.commit()
        entry_id   = entry.id
        sponsor_id = sponsor.id
        campaign_id = campaign.id

        from app.services import sponsor_promote_service as svc_module

        def _failing_apply(user, campaign, credit_svc):
            # Award the grant (flushes SQL UPDATE + CreditTransaction)
            credit_svc.award(
                user=user,
                amount=campaign.credit_grant_amount,
                transaction_type="SPONSOR_CREDIT_GRANT",
                description="test grant",
                idempotency_key=f"sponsor_grant:{campaign.id}:{user.id}",
                sponsor_id=campaign.sponsor_id,
                campaign_id=campaign.id,
            )
            # Then simulate unlock failure
            raise InsufficientCreditsError(required=campaign.unlock_cost, available=0)

        with patch.object(svc_module, "_apply_credits", side_effect=_failing_apply):
            with pytest.raises(InsufficientCreditsError):
                promote_entries([entry_id], sponsor_id, test_db, admin,
                                campaign_id=campaign_id)

        # promote_entries raised without committing; caller must rollback.
        # This mirrors production: FastAPI exception handler closes/rolls back the session.
        test_db.rollback()
        test_db.expire_all()

        # entry.user_id must remain NULL — promote was not committed
        fresh_entry = (
            test_db.query(SponsorAudienceEntry)
            .filter(SponsorAudienceEntry.id == entry_id)
            .first()
        )
        assert fresh_entry is not None, "Entry must exist (committed in setup)"
        assert fresh_entry.user_id is None, (
            "entry.user_id must be NULL after partial-failure rollback"
        )

        # No credit rows for this campaign
        credit_count = (
            test_db.query(CreditTransaction)
            .filter(CreditTransaction.campaign_id == campaign_id)
            .count()
        )
        assert credit_count == 0, (
            "No CreditTransaction rows must persist after partial-failure rollback"
        )
