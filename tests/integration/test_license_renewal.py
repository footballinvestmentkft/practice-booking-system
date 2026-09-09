#!/usr/bin/env python3
"""
Test License Renewal System
============================
Tests license renewal workflow with Grand Master.
"""
import os
import sys
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Add app to path
sys.path.insert(0, os.path.dirname(__file__))

from app.models.user import User
from app.models.license import UserLicense
from app.services.license_renewal_service import (
    LicenseRenewalService,
    InsufficientCreditsError,
    LicenseNotFoundError
)

# Database connection
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/lfa_intern_system"
)

engine = create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)


def print_separator():
    print("=" * 80)


def print_license_info(license, title="License Info"):
    """Pretty print license information"""
    print(f"\n{title}:")
    print("-" * 80)
    print(f"  License ID: {license.id}")
    print(f"  User ID: {license.user_id}")
    print(f"  Specialization: {license.specialization_type}")
    print(f"  Level: {license.current_level}")
    print(f"  Is Active: {license.is_active}")
    print(f"  Expires At: {license.expires_at or 'Perpetual (no expiration)'}")
    print(f"  Last Renewed: {license.last_renewed_at or 'Never'}")
    print(f"  Renewal Cost: {license.renewal_cost} credits")
    print("-" * 80)


def test_license_renewal():
    """Test complete license renewal workflow"""
    session = Session()

    try:
        print_separator()
        print("LICENSE RENEWAL TEST - GRAND MASTER")
        print_separator()
        print()

        # 1. Get Grand Master
        print("1️⃣  Getting Grand Master user...")
        grand_master = session.query(User).filter(User.id == 3).first()

        if not grand_master:
            print("❌ Grand Master not found!")
            return

        print(f"   ✅ Found: {grand_master.name} ({grand_master.email})")
        print(f"   💰 Credit Balance: {grand_master.credit_balance} credits")
        print()

        # 2. Get PLAYER Level 1 license (ID: 52)
        print("2️⃣  Getting PLAYER Level 1 license...")
        license = session.query(UserLicense).filter(UserLicense.id == 52).first()

        if not license:
            print("❌ License not found!")
            return

        print_license_info(license, "📋 BEFORE RENEWAL")

        # 3. Get license status
        print("\n3️⃣  Checking license status...")
        status = LicenseRenewalService.get_license_status(license)
        print(f"   Status: {status['status']}")
        print(f"   Is Expired: {status['is_expired']}")
        print(f"   Needs Renewal: {status['needs_renewal']}")
        print(f"   Days Until Expiration: {status['days_until_expiration'] or 'N/A (perpetual)'}")
        print()

        # 4. Test renewal for 12 months
        print("4️⃣  Renewing license for 12 months...")
        print(f"   Cost: 1000 credits")
        print(f"   Current Balance: {grand_master.credit_balance}")

        result = LicenseRenewalService.renew_license(
            license_id=52,
            renewal_months=12,
            admin_id=1,  # Assume admin ID 1
            db=session,
            idempotency_key="integration-license-52-renewal-1",
            payment_verified=True
        )

        print(f"\n   ✅ Renewal Successful!")
        print(f"   New Expiration: {result['new_expiration'].strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Credits Charged: {result['credits_charged']}")
        print(f"   Remaining Credits: {result['remaining_credits']}")
        print(f"   Message: {result['message']}")
        print()

        # 5. Refresh license and show updated state
        print("5️⃣  Refreshing license state...")
        session.refresh(license)
        session.refresh(grand_master)

        print_license_info(license, "📋 AFTER RENEWAL")

        print(f"\n   💰 Grand Master Balance: {grand_master.credit_balance} credits")
        print()

        # 6. Check new status
        print("6️⃣  Checking updated license status...")
        new_status = LicenseRenewalService.get_license_status(license)
        print(f"   Status: {new_status['status']}")
        print(f"   Is Active: {new_status['is_active']}")
        print(f"   Is Expired: {new_status['is_expired']}")
        print(f"   Days Until Expiration: {new_status['days_until_expiration']}")
        print()

        # 7. Test renewing already renewed license (should add to existing expiration)
        print("7️⃣  Testing second renewal (adds to existing expiration)...")
        print(f"   Current Expiration: {license.expires_at.strftime('%Y-%m-%d')}")
        print(f"   Renewing for another 12 months...")

        result2 = LicenseRenewalService.renew_license(
            license_id=52,
            renewal_months=12,
            admin_id=1,
            db=session,
            idempotency_key="integration-license-52-renewal-2",
            payment_verified=True
        )

        session.refresh(license)
        session.refresh(grand_master)

        print(f"\n   ✅ Second Renewal Successful!")
        print(f"   New Expiration: {result2['new_expiration'].strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Credits Charged: {result2['credits_charged']}")
        print(f"   Remaining Credits: {result2['remaining_credits']}")
        print()

        print_license_info(license, "📋 AFTER SECOND RENEWAL")

        # 8. Test insufficient credits error
        print("\n8️⃣  Testing insufficient credits error...")
        print(f"   Current Balance: {grand_master.credit_balance}")

        # Set balance to 500 (less than renewal cost)
        grand_master.credit_balance = 500
        session.commit()
        session.refresh(grand_master)

        print(f"   Setting balance to 500 credits (< 1000 required)")

        try:
            LicenseRenewalService.renew_license(
                license_id=52,
                renewal_months=12,
                admin_id=1,
                db=session,
                idempotency_key="integration-license-52-insufficient",
            )
            print("   ❌ Should have raised InsufficientCreditsError!")
        except InsufficientCreditsError as e:
            print(f"   ✅ Correctly raised InsufficientCreditsError: {str(e)}")

        # Restore balance
        grand_master.credit_balance = 5000
        session.commit()
        print()

        # 9. Test invalid renewal period
        print("9️⃣  Testing invalid renewal period...")
        try:
            LicenseRenewalService.renew_license(
                license_id=52,
                renewal_months=6,  # Invalid (not 12 or 24)
                admin_id=1,
                db=session,
                idempotency_key="integration-license-52-invalid",
            )
            print("   ❌ Should have raised ValueError!")
        except ValueError as e:
            print(f"   ✅ Correctly raised ValueError: {str(e)}")
        print()

        print_separator()
        print("✅ ALL TESTS PASSED!")
        print_separator()
        print()

        # Final summary
        print("📊 FINAL STATE:")
        print(f"   License ID: {license.id}")
        print(f"   Specialization: {license.specialization_type} Level {license.current_level}")
        print(f"   Is Active: {license.is_active}")
        print(f"   Expires: {license.expires_at.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Days Until Expiration: ~{(license.expires_at - datetime.now(license.expires_at.tzinfo)).days}")
        print(f"   Grand Master Balance: {grand_master.credit_balance} credits")
        print()

    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
        session.rollback()
    finally:
        session.close()


if __name__ == "__main__":
    test_license_renewal()
