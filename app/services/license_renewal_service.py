"""
💰 License Renewal Service
===========================
Service for managing license renewals with credit-based payments.

Business Rules:
1. Renewal cost: 1000 credits (configurable per license)
2. Renewal period: 12 or 24 months (admin choice)
3. User must have sufficient credit balance
4. Payment must be verified by admin
5. Expired licenses become inactive automatically
"""
from typing import Dict
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from app.models.license import UserLicense
from app.models.user import User
from app.models.audit_log import AuditLog
from app.services.credit_service import (
    CreditService,
    InsufficientCreditsError as CreditInsufficientCreditsError,
)


class InsufficientCreditsError(Exception):
    """Raised when user doesn't have enough credits for renewal."""


class LicenseNotFoundError(Exception):
    """Raised when license doesn't exist."""


class LicenseRenewalService:
    """Service for license renewal operations"""

    DEFAULT_RENEWAL_COST = 1000  # credits
    VALID_RENEWAL_PERIODS = [12, 24]  # months

    @classmethod
    def check_license_expiration(cls, license: UserLicense) -> bool:
        """
        Check if license has expired and update is_active status.

        Args:
            license: UserLicense object

        Returns:
            True if license is active (not expired), False if expired
        """
        # If no expiration date set, license is perpetual (active)
        if not license.expires_at:
            return True

        # Check if expired (handle both naive and aware datetimes)
        now = datetime.now(timezone.utc)
        expires_at = license.expires_at

        # If expires_at is naive, make it timezone-aware (assume UTC)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        if expires_at < now:
            # License expired - deactivate
            if license.is_active:
                license.is_active = False
            return False

        # License still valid
        return True

    @classmethod
    def renew_license(
        cls,
        license_id: int,
        renewal_months: int,
        admin_id: int,
        db: Session,
        idempotency_key: str,
        payment_verified: bool = True,
    ) -> Dict[str, any]:
        """
        Renew a license for specified months.

        Args:
            license_id: ID of license to renew
            renewal_months: 12 or 24 months
            admin_id: Admin user ID who approved renewal
            db: Database session
            payment_verified: Whether payment has been verified (default: True)

        Returns:
            {
                "success": bool,
                "license_id": int,
                "new_expiration": datetime,
                "credits_charged": int,
                "remaining_credits": int,
                "message": str
            }

        Raises:
            LicenseNotFoundError: If license doesn't exist
            InsufficientCreditsError: If user doesn't have enough credits
            ValueError: If renewal_months not in [12, 24]
        """
        # Validate renewal period
        if renewal_months not in cls.VALID_RENEWAL_PERIODS:
            raise ValueError(f"Renewal period must be {cls.VALID_RENEWAL_PERIODS}, got {renewal_months}")

        # Get license
        license = db.query(UserLicense).filter(UserLicense.id == license_id).first()
        if not license:
            raise LicenseNotFoundError(f"License {license_id} not found")

        # Get user
        user = db.query(User).filter(User.id == license.user_id).first()
        if not user:
            raise LicenseNotFoundError(f"User {license.user_id} not found for license {license_id}")

        # Serialize every credit mutation for this user, then refresh both objects.
        # Renewal, unlock and cancellation all take this same row lock first.
        db.refresh(user, with_for_update=True)
        db.refresh(license)

        # Get renewal cost (from license or default)
        renewal_cost = license.renewal_cost or cls.DEFAULT_RENEWAL_COST

        # Calculate new expiration date
        now = datetime.now(timezone.utc)

        if license.expires_at:
            # Make expires_at timezone-aware if it's naive
            expires_at = license.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)

            if expires_at > now:
                # License not yet expired - add to existing expiration
                new_expiration = expires_at + timedelta(days=renewal_months * 30)
            else:
                # License expired - start from now
                new_expiration = now + timedelta(days=renewal_months * 30)
        else:
            # Never had expiration - start from now
            new_expiration = now + timedelta(days=renewal_months * 30)

        previous_expiration = license.expires_at

        try:
            _, created = CreditService(db).deduct_with_status(
                user=user,
                amount=renewal_cost,
                transaction_type="LICENSE_RENEWAL",
                description=(
                    f"License renewed for {renewal_months} months "
                    f"({license.specialization_type} Level {license.current_level})"
                ),
                idempotency_key=idempotency_key,
            )
        except CreditInsufficientCreditsError as exc:
            raise InsufficientCreditsError(
                f"User {user.id} has {exc.available} credits, "
                f"needs {exc.required} for renewal"
            ) from exc

        # An exact replay must not extend the licence or duplicate its audit row.
        if not created:
            db.commit()
            return {
                "success": True,
                "license_id": license_id,
                "specialization_type": license.specialization_type,
                "current_level": license.current_level,
                "new_expiration": license.expires_at,
                "credits_charged": renewal_cost,
                "remaining_credits": user.credit_balance,
                "renewal_months": renewal_months,
                "message": "License renewal already processed",
            }

        # Update license
        license.expires_at = new_expiration
        license.last_renewed_at = now
        license.is_active = True  # Reactivate if was expired
        if payment_verified:
            license.payment_verified = True

        # Create audit log
        audit_log = AuditLog(
            action="LICENSE_RENEWED",
            user_id=user.id,
            resource_type="license",
            resource_id=license_id,
            details={
                "license_id": license_id,
                "specialization_type": license.specialization_type,
                "current_level": license.current_level,
                "renewal_months": renewal_months,
                "credits_charged": renewal_cost,
                "new_expiration": new_expiration.isoformat(),
                "previous_expiration": previous_expiration.isoformat() if previous_expiration else None,
                "admin_id": admin_id,
                "payment_verified": payment_verified
            }
        )
        db.add(audit_log)

        # Commit transaction
        db.commit()
        db.refresh(license)
        db.refresh(user)
        persisted_expiration = license.expires_at

        return {
            "success": True,
            "license_id": license_id,
            "specialization_type": license.specialization_type,
            "current_level": license.current_level,
            "new_expiration": persisted_expiration,
            "credits_charged": renewal_cost,
            "remaining_credits": user.credit_balance,
            "renewal_months": renewal_months,
            "message": f"License renewed for {renewal_months} months until {persisted_expiration.strftime('%Y-%m-%d')}"
        }

    @classmethod
    def get_expiring_licenses(
        cls,
        days_threshold: int,
        db: Session
    ) -> list:
        """
        Get all licenses expiring within specified days.

        Args:
            days_threshold: Number of days to look ahead
            db: Database session

        Returns:
            List of UserLicense objects expiring soon
        """
        # Use naive datetime for database comparison (database stores naive timestamps)
        now = datetime.now()
        threshold_date = now + timedelta(days=days_threshold)

        licenses = db.query(UserLicense).filter(
            UserLicense.expires_at.isnot(None),
            UserLicense.expires_at <= threshold_date,
            UserLicense.expires_at >= now,
            UserLicense.is_active == True
        ).all()

        return licenses

    @classmethod
    def bulk_check_expirations(cls, db: Session) -> Dict[str, int]:
        """
        Check all licenses and deactivate expired ones.

        This should be run as a scheduled task (cronjob).

        Args:
            db: Database session

        Returns:
            {
                "total_checked": int,
                "expired_count": int,
                "still_active": int
            }
        """
        # Get all licenses with expiration dates
        licenses = db.query(UserLicense).filter(
            UserLicense.expires_at.isnot(None)
        ).all()

        expired_count = 0
        still_active = 0

        for license in licenses:
            if cls.check_license_expiration(license):
                still_active += 1
            else:
                expired_count += 1

        # Commit all deactivations
        db.commit()

        return {
            "total_checked": len(licenses),
            "expired_count": expired_count,
            "still_active": still_active
        }

    @classmethod
    def get_license_status(cls, license: UserLicense) -> Dict[str, any]:
        """
        Get detailed status of a license.

        Args:
            license: UserLicense object

        Returns:
            {
                "is_active": bool,
                "expires_at": datetime or None,
                "days_until_expiration": int or None,
                "is_expired": bool,
                "needs_renewal": bool,
                "status": str  # "active", "expiring_soon", "expired", "perpetual"
            }
        """
        now = datetime.now(timezone.utc)

        # No expiration date = perpetual license
        if not license.expires_at:
            return {
                "is_active": license.is_active,
                "expires_at": None,
                "days_until_expiration": None,
                "is_expired": False,
                "needs_renewal": False,
                "status": "perpetual"
            }

        # Make expires_at timezone-aware if it's naive
        expires_at = license.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        # Calculate days until expiration
        time_until_expiration = expires_at - now
        days_until_expiration = time_until_expiration.days

        # Determine status
        is_expired = days_until_expiration < 0
        expiring_soon = 0 < days_until_expiration <= 30  # Within 30 days

        if is_expired:
            status = "expired"
        elif expiring_soon:
            status = "expiring_soon"
        else:
            status = "active"

        return {
            "is_active": license.is_active and not is_expired,
            "expires_at": license.expires_at,
            "days_until_expiration": days_until_expiration,
            "is_expired": is_expired,
            "needs_renewal": is_expired or expiring_soon,
            "status": status
        }
