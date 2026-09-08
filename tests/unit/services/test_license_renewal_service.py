"""
Unit tests for app/services/license_renewal_service.py

Mock-based: no DB fixture needed.
Covers: check_license_expiration, renew_license, get_expiring_licenses,
        bulk_check_expirations, get_license_status.
"""
import pytest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call
from app.services.license_renewal_service import (
    LicenseRenewalService,
    InsufficientCreditsError,
    LicenseNotFoundError,
)
from app.services.credit_service import InsufficientCreditsError as CreditInsufficientCreditsError


@pytest.fixture(autouse=True)
def _credit_service_double(monkeypatch):
    """Keep these mock-only tests focused on renewal behavior."""
    class FakeCreditService:
        def __init__(self, db):
            self.db = db

        def deduct_with_status(
            self, *, user, amount, transaction_type, description, idempotency_key
        ):
            if user.credit_balance < amount:
                raise CreditInsufficientCreditsError(amount, user.credit_balance)
            user.credit_balance -= amount
            return SimpleNamespace(balance_after=user.credit_balance), True

    monkeypatch.setattr(
        "app.services.license_renewal_service.CreditService", FakeCreditService
    )


# ── helpers ──────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _license(
    expires_at=None,
    is_active=True,
    renewal_cost=None,
    user_id=42,
    license_id=10,
    spec_type="LFA_COACH",
    level=4,
):
    lic = MagicMock()
    lic.id = license_id
    lic.user_id = user_id
    lic.expires_at = expires_at
    lic.is_active = is_active
    lic.renewal_cost = renewal_cost
    lic.specialization_type = spec_type
    lic.current_level = level
    return lic


def _user(user_id=42, credit_balance=2000):
    u = MagicMock()
    u.id = user_id
    u.credit_balance = credit_balance
    return u


def _db_for_renewal(lic, usr):
    """DB mock returning license then user in sequence."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = [lic, usr]
    return db


# ── check_license_expiration ──────────────────────────────────────────────────

class TestCheckLicenseExpiration:

    def test_no_expires_at_is_perpetual(self):
        lic = _license(expires_at=None)
        assert LicenseRenewalService.check_license_expiration(lic) is True

    def test_future_expiry_returns_true(self):
        lic = _license(expires_at=_now() + timedelta(days=30))
        assert LicenseRenewalService.check_license_expiration(lic) is True

    def test_past_expiry_returns_false(self):
        lic = _license(expires_at=_now() - timedelta(days=1))
        assert LicenseRenewalService.check_license_expiration(lic) is False

    def test_expired_deactivates_is_active(self):
        lic = _license(expires_at=_now() - timedelta(days=10), is_active=True)
        LicenseRenewalService.check_license_expiration(lic)
        assert lic.is_active is False

    def test_still_active_does_not_modify(self):
        lic = _license(expires_at=_now() + timedelta(days=10), is_active=True)
        LicenseRenewalService.check_license_expiration(lic)
        assert lic.is_active is True

    def test_expiry_exactly_at_now_boundary_is_not_expired(self):
        """LR-Exp: expires_at == now is NOT expired — strict < not <= (kills <= mutant)."""
        fixed_now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        lic = _license(expires_at=fixed_now, is_active=True)
        with patch("app.services.license_renewal_service.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_now
            result = LicenseRenewalService.check_license_expiration(lic)
        assert result is True
        assert lic.is_active is True

    def test_naive_datetime_handled(self):
        # Naive datetime (no tzinfo) — service must make it tz-aware
        naive_past = datetime.utcnow() - timedelta(days=5)
        lic = _license(expires_at=naive_past)
        assert LicenseRenewalService.check_license_expiration(lic) is False

    def test_naive_future_datetime_returns_true(self):
        naive_future = datetime.utcnow() + timedelta(days=5)
        lic = _license(expires_at=naive_future)
        assert LicenseRenewalService.check_license_expiration(lic) is True


# ── renew_license — validation ────────────────────────────────────────────────

class TestRenewLicenseValidation:

    def test_invalid_renewal_period_raises(self):
        db = MagicMock()
        with pytest.raises(ValueError, match="Renewal period"):
            LicenseRenewalService.renew_license(
                license_id=1, renewal_months=6, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
            )

    def test_renewal_period_0_raises(self):
        db = MagicMock()
        with pytest.raises(ValueError):
            LicenseRenewalService.renew_license(
                license_id=1, renewal_months=0, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
            )

    def test_license_not_found_raises(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        with pytest.raises(LicenseNotFoundError):
            LicenseRenewalService.renew_license(
                license_id=999, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
            )

    def test_user_not_found_raises(self):
        lic = _license()
        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [lic, None]
        with pytest.raises(LicenseNotFoundError, match="User"):
            LicenseRenewalService.renew_license(
                license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
            )

    def test_insufficient_credits_raises(self):
        lic = _license(renewal_cost=1000)
        usr = _user(credit_balance=500)
        db = _db_for_renewal(lic, usr)
        with pytest.raises(InsufficientCreditsError):
            LicenseRenewalService.renew_license(
                license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
            )


# ── renew_license — success paths ─────────────────────────────────────────────

class TestRenewLicenseSuccess:

    def test_deducts_credits(self):
        lic = _license(renewal_cost=None)  # Uses DEFAULT_RENEWAL_COST = 1000
        usr = _user(credit_balance=2000)
        db = _db_for_renewal(lic, usr)
        LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        assert usr.credit_balance == 1000

    def test_uses_license_renewal_cost_if_set(self):
        lic = _license(renewal_cost=500)
        usr = _user(credit_balance=2000)
        db = _db_for_renewal(lic, usr)
        LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        assert usr.credit_balance == 1500

    def test_returns_success_dict(self):
        lic = _license(renewal_cost=None)
        usr = _user(credit_balance=2000)
        db = _db_for_renewal(lic, usr)
        result = LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        assert result["success"] is True
        assert result["license_id"] == 10
        assert result["credits_charged"] == 1000
        assert result["renewal_months"] == 12

    def test_commits_once(self):
        lic = _license()
        usr = _user(credit_balance=2000)
        db = _db_for_renewal(lic, usr)
        LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        db.commit.assert_called_once()

    def test_audit_log_added(self):
        lic = _license()
        usr = _user(credit_balance=2000)
        db = _db_for_renewal(lic, usr)
        LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        db.add.assert_called()  # audit_log + credit_transaction

    def test_license_reactivated(self):
        lic = _license(is_active=False)
        usr = _user(credit_balance=2000)
        db = _db_for_renewal(lic, usr)
        LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        assert lic.is_active is True

    def test_payment_verified_flag_set(self):
        lic = _license()
        usr = _user(credit_balance=2000)
        db = _db_for_renewal(lic, usr)
        LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal", payment_verified=True
        )
        assert lic.payment_verified is True

    def test_24_month_renewal(self):
        lic = _license(renewal_cost=None)
        usr = _user(credit_balance=5000)
        db = _db_for_renewal(lic, usr)
        result = LicenseRenewalService.renew_license(
            license_id=10, renewal_months=24, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        assert result["renewal_months"] == 24

    def test_not_expired_license_extends_from_current_expiry(self):
        future_expiry = _now() + timedelta(days=60)
        lic = _license(expires_at=future_expiry)
        usr = _user(credit_balance=2000)
        db = _db_for_renewal(lic, usr)
        result = LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        # New expiry should be ~60 + 360 days from now (>360 days from now)
        days_left = (result["new_expiration"] - _now()).days
        assert days_left > 360

    def test_expired_license_extends_from_now(self):
        past_expiry = _now() - timedelta(days=30)
        lic = _license(expires_at=past_expiry)
        usr = _user(credit_balance=2000)
        db = _db_for_renewal(lic, usr)
        result = LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        # New expiry should be ~360 days from now (not 390)
        days_left = (result["new_expiration"] - _now()).days
        assert 355 < days_left < 370

    def test_no_expiry_starts_from_now(self):
        lic = _license(expires_at=None)
        usr = _user(credit_balance=2000)
        db = _db_for_renewal(lic, usr)
        result = LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        days_left = (result["new_expiration"] - _now()).days
        assert 355 < days_left < 370


# ── get_expiring_licenses ─────────────────────────────────────────────────────

class TestGetExpiringLicenses:

    def test_returns_list_from_db(self):
        licenses = [_license(), _license()]
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = licenses
        result = LicenseRenewalService.get_expiring_licenses(days_threshold=30, db=db)
        assert result == licenses

    def test_returns_empty_list_if_none(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        result = LicenseRenewalService.get_expiring_licenses(days_threshold=7, db=db)
        assert result == []

    def test_queries_with_threshold(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        LicenseRenewalService.get_expiring_licenses(days_threshold=14, db=db)
        db.query.assert_called_once()


# ── bulk_check_expirations ────────────────────────────────────────────────────

class TestBulkCheckExpirations:

    def test_counts_active_and_expired(self):
        active_lic = _license(expires_at=_now() + timedelta(days=10))
        expired_lic = _license(expires_at=_now() - timedelta(days=10))
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [active_lic, expired_lic]
        result = LicenseRenewalService.bulk_check_expirations(db)
        assert result["total_checked"] == 2
        assert result["expired_count"] == 1
        assert result["still_active"] == 1

    def test_all_active_returns_zero_expired(self):
        licenses = [
            _license(expires_at=_now() + timedelta(days=i + 1))
            for i in range(3)
        ]
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = licenses
        result = LicenseRenewalService.bulk_check_expirations(db)
        assert result["expired_count"] == 0
        assert result["still_active"] == 3

    def test_all_expired_returns_zero_active(self):
        licenses = [
            _license(expires_at=_now() - timedelta(days=i + 1))
            for i in range(2)
        ]
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = licenses
        result = LicenseRenewalService.bulk_check_expirations(db)
        assert result["expired_count"] == 2
        assert result["still_active"] == 0

    def test_commits_after_check(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        LicenseRenewalService.bulk_check_expirations(db)
        db.commit.assert_called_once()

    def test_empty_list_returns_zeros(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        result = LicenseRenewalService.bulk_check_expirations(db)
        assert result == {"total_checked": 0, "expired_count": 0, "still_active": 0}


# ── get_license_status ────────────────────────────────────────────────────────

class TestGetLicenseStatus:

    def test_perpetual_license(self):
        lic = _license(expires_at=None, is_active=True)
        result = LicenseRenewalService.get_license_status(lic)
        assert result["status"] == "perpetual"
        assert result["is_expired"] is False
        assert result["needs_renewal"] is False
        assert result["days_until_expiration"] is None

    def test_active_license_status(self):
        lic = _license(expires_at=_now() + timedelta(days=60), is_active=True)
        result = LicenseRenewalService.get_license_status(lic)
        assert result["status"] == "active"
        assert result["is_expired"] is False
        assert result["needs_renewal"] is False

    def test_expiring_soon_within_30_days(self):
        lic = _license(expires_at=_now() + timedelta(days=15), is_active=True)
        result = LicenseRenewalService.get_license_status(lic)
        assert result["status"] == "expiring_soon"
        assert result["needs_renewal"] is True

    def test_expired_license(self):
        lic = _license(expires_at=_now() - timedelta(days=5), is_active=False)
        result = LicenseRenewalService.get_license_status(lic)
        assert result["status"] == "expired"
        assert result["is_expired"] is True
        assert result["needs_renewal"] is True
        assert result["is_active"] is False

    def test_days_until_expiration_positive(self):
        lic = _license(expires_at=_now() + timedelta(days=45))
        result = LicenseRenewalService.get_license_status(lic)
        assert result["days_until_expiration"] > 0

    def test_days_until_expiration_negative_when_expired(self):
        lic = _license(expires_at=_now() - timedelta(days=3))
        result = LicenseRenewalService.get_license_status(lic)
        assert result["days_until_expiration"] < 0

    def test_naive_expires_at_handled(self):
        naive_future = datetime.utcnow() + timedelta(days=60)
        lic = _license(expires_at=naive_future)
        result = LicenseRenewalService.get_license_status(lic)
        # Should not raise — naive datetime is handled
        assert result["status"] in ("active", "expiring_soon", "expired")

    def test_is_active_false_when_expired(self):
        lic = _license(expires_at=_now() - timedelta(days=1), is_active=True)
        result = LicenseRenewalService.get_license_status(lic)
        assert result["is_active"] is False


class TestGetLicenseStatusBoundaries:
    """
    Targeted boundary tests to kill comparison-operator mutants in get_license_status.

    Production line 313: is_expired = days_until_expiration < 0
    Production line 314: expiring_soon = 0 < days_until_expiration <= 30

    Killed mutants:
      1094: < 0  →  <= 0  (days=0 wrongly becomes expired)
      1095: < 0  →  < 1   (days=0 wrongly becomes expired)
      1097: 0 <  →  1 <   (days=1 wrongly not expiring_soon)
      1098: 0 <  →  0 <=  (days=0 wrongly becomes expiring_soon)
      1099: <= 30 →  < 30  (days=30 wrongly becomes active)
      1100: <= 30 →  <= 31 (days=31 wrongly becomes expiring_soon)
    """

    def test_zero_days_left_is_active_not_expired(self):
        # days_until_expiration = 0 (expires in a few seconds — same day, .days = 0)
        # Not expired (< 0 is False), not expiring_soon (0 < 0 <= 30 is False) → active
        lic = _license(expires_at=_now() + timedelta(seconds=30), is_active=True)
        result = LicenseRenewalService.get_license_status(lic)
        assert result["days_until_expiration"] == 0
        assert result["is_expired"] is False
        assert result["needs_renewal"] is False
        assert result["status"] == "active"

    def test_one_day_left_is_expiring_soon(self):
        # days_until_expiration = 1 → expiring_soon (0 < 1 <= 30 → True)
        # Use +25h so days=1 despite any microsecond drift in test execution
        lic = _license(expires_at=_now() + timedelta(hours=25), is_active=True)
        result = LicenseRenewalService.get_license_status(lic)
        assert result["status"] == "expiring_soon"
        assert result["needs_renewal"] is True

    def test_exactly_30_days_left_is_expiring_soon(self):
        # days_until_expiration = 30 → expiring_soon (0 < 30 <= 30 → True)
        # Use +30d+1h so days=30 despite any drift
        lic = _license(expires_at=_now() + timedelta(days=30, hours=1), is_active=True)
        result = LicenseRenewalService.get_license_status(lic)
        assert result["status"] == "expiring_soon"
        assert result["needs_renewal"] is True

    def test_exactly_31_days_left_is_active(self):
        # days_until_expiration = 31 → active (0 < 31 <= 30 → False)
        lic = _license(expires_at=_now() + timedelta(days=31, hours=1), is_active=True)
        result = LicenseRenewalService.get_license_status(lic)
        assert result["status"] == "active"
        assert result["needs_renewal"] is False


# ── Sprint 48 additions — targeted mutation kills ─────────────────────────────

class TestRenewLicenseMutationTargets:
    """
    Sprint 48: Kill specific mutants in renew_license that survived Sprint 47.

    Targets:
      L-R1: line 121  `<` → `<=` credit balance exact boundary
      L-R2: line 149  assignment `license.expires_at = new_expiration`
      L-R3: line 150  assignment `license.last_renewed_at = now`
      L-R4: line 152  conditional `if payment_verified:`
      L-R5: lines 143 `timedelta(days=renewal_months * 30)` 24-month precision
    """

    def test_exactly_sufficient_credits_succeeds(self):
        """L-R1: credit_balance == renewal_cost should NOT raise (strict < not <=)."""
        lic = _license(renewal_cost=1000)
        usr = _user(credit_balance=1000)  # exactly enough
        db = _db_for_renewal(lic, usr)
        result = LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        assert result["success"] is True
        assert usr.credit_balance == 0  # 1000 - 1000 = 0

    def test_license_expires_at_updated_on_renewal(self):
        """L-R2: license.expires_at must be set to new_expiration (not just returned)."""
        lic = _license(expires_at=None)  # perpetual → new_expiration = now + 360d
        usr = _user(credit_balance=2000)
        db = _db_for_renewal(lic, usr)
        result = LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        assert lic.expires_at == result["new_expiration"]

    def test_license_last_renewed_at_set_on_renewal(self):
        """L-R3: license.last_renewed_at must be set after renewal."""
        lic = _license()
        usr = _user(credit_balance=2000)
        db = _db_for_renewal(lic, usr)
        LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        # Must be set to some datetime — original MagicMock default is not a datetime
        assert lic.last_renewed_at is not None
        assert isinstance(lic.last_renewed_at, datetime)

    def test_payment_not_verified_does_not_set_flag(self):
        """L-R4: payment_verified=False → conditional skips, lic.payment_verified not set True."""
        lic = _license()
        usr = _user(credit_balance=2000)
        db = _db_for_renewal(lic, usr)
        LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal",
            payment_verified=False
        )
        # If `if payment_verified:` were removed, payment_verified would be True regardless.
        # With the guard in place, lic.payment_verified should remain the MagicMock attribute
        # (not explicitly set to True by the service).
        assert lic.payment_verified is not True

    def test_24_month_renewal_adds_exactly_720_days_from_now(self):
        """L-R5: 24 months = 24*30 = 720 days; tight range kills constant mutations."""
        lic = _license(expires_at=None)  # perpetual → starts from now
        usr = _user(credit_balance=5000)
        db = _db_for_renewal(lic, usr)
        result = LicenseRenewalService.renew_license(
            license_id=10, renewal_months=24, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        days_left = (result["new_expiration"] - _now()).days
        # 24 * 30 = 720; allow 5-day drift for test execution time
        assert 715 < days_left <= 720

    def test_renew_extends_from_existing_non_expired_date(self):
        """L-R6: non-expired license → new_expiration = expires_at + Δ, not now + Δ."""
        # expires_at = now+100d; after 12-month renewal: new = (now+100d) + 360d ≈ now+460d
        # If mutated to − or using now: result ≈ 360d or −260d → assertion fails
        future_expiry = _now() + timedelta(days=100)
        lic = _license(expires_at=future_expiry)
        usr = _user(credit_balance=2000)
        db = _db_for_renewal(lic, usr)
        result = LicenseRenewalService.renew_license(
            license_id=10, renewal_months=12, admin_id=99, db=db, idempotency_key="unit-test-license-renewal"
        )
        days_left = (result["new_expiration"] - _now()).days
        assert 455 < days_left <= 462
