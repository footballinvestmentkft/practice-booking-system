"""
Test that database constraints prevent duplicate transactions.

This test verifies that the unique constraints added in Phase 1
successfully prevent dual-path bugs at the database level.
"""

import os
import sys
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/lfa_intern_system"
)

@pytest.mark.xfail(reason="Requires user_id=2 in dev DB; not present in test environment")
def test_xp_transactions_constraint():
    """Test that xp_transactions prevents duplicates on (user_id, semester_id, transaction_type)"""
    engine = create_engine(DATABASE_URL)

    print("\n🧪 Testing xp_transactions unique constraint...")

    with engine.begin() as conn:
        # Insert first transaction
        conn.execute(text("""
            INSERT INTO xp_transactions (user_id, transaction_type, amount, balance_after, semester_id, description)
            VALUES (2, 'TEST_DUPLICATE', 100, 100, 1, 'Test duplicate prevention - first insert');
        """))
        print("✅ First XP transaction inserted successfully")

        # Try to insert duplicate - should fail
        try:
            conn.execute(text("""
                INSERT INTO xp_transactions (user_id, transaction_type, amount, balance_after, semester_id, description)
                VALUES (2, 'TEST_DUPLICATE', 100, 100, 1, 'Test duplicate prevention - second insert');
            """))
            print("❌ FAILURE: Duplicate XP transaction was allowed!")
            return False
        except IntegrityError as e:
            if "uq_xp_transactions_user_semester_type" in str(e):
                print(f"✅ Duplicate XP transaction correctly blocked by constraint")
                # Rollback the transaction to clean up
                raise  # Re-raise to trigger rollback
            else:
                print(f"❌ FAILURE: Wrong error: {e}")
                raise

    # Clean up
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM xp_transactions WHERE transaction_type = 'TEST_DUPLICATE';"))

    return True


@pytest.mark.xfail(reason="Requires user_id=2 in dev DB; not present in test environment")
def test_skill_rewards_constraint():
    """Test that skill_rewards prevents duplicates on (user_id, source_type, source_id, skill_name)"""
    engine = create_engine(DATABASE_URL)

    print("\n🧪 Testing skill_rewards unique constraint...")

    with engine.begin() as conn:
        # Insert first skill reward
        conn.execute(text("""
            INSERT INTO skill_rewards (user_id, source_type, source_id, skill_name, points_awarded)
            VALUES (2, 'TEST_SESSION', 999999, 'Passing', 10);
        """))
        print("✅ First skill reward inserted successfully")

        # Try to insert duplicate - should fail
        try:
            conn.execute(text("""
                INSERT INTO skill_rewards (user_id, source_type, source_id, skill_name, points_awarded)
                VALUES (2, 'TEST_SESSION', 999999, 'Passing', 15);
            """))
            print("❌ FAILURE: Duplicate skill reward was allowed!")
            return False
        except IntegrityError as e:
            if "uq_skill_rewards_user_source_skill" in str(e):
                print(f"✅ Duplicate skill reward correctly blocked by constraint")
                raise  # Re-raise to trigger rollback
            else:
                print(f"❌ FAILURE: Wrong error: {e}")
                raise

    # Clean up
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM skill_rewards WHERE source_type = 'TEST_SESSION' AND source_id = 999999;"))

    return True


@pytest.mark.xfail(reason="Requires user_id=2 in dev DB; not present in test environment")
def test_credit_transactions_constraint():
    """Test that credit_transactions prevents duplicates on idempotency_key"""
    engine = create_engine(DATABASE_URL)

    print("\n🧪 Testing credit_transactions idempotency_key constraint...")

    with engine.begin() as conn:
        # Insert first credit transaction
        conn.execute(text("""
            INSERT INTO credit_transactions
            (user_id, transaction_type, amount, balance_after, idempotency_key, description, created_at)
            VALUES (2, 'TEST_DUPLICATE', 50, 50, 'test_duplicate_key_12345', 'Test duplicate prevention', NOW());
        """))
        print("✅ First credit transaction inserted successfully")

        # Try to insert duplicate - should fail
        try:
            conn.execute(text("""
                INSERT INTO credit_transactions
                (user_id, transaction_type, amount, balance_after, idempotency_key, description, created_at)
                VALUES (2, 'TEST_DUPLICATE', 50, 50, 'test_duplicate_key_12345', 'Test duplicate prevention again', NOW());
            """))
            print("❌ FAILURE: Duplicate credit transaction was allowed!")
            return False
        except IntegrityError as e:
            if "uq_credit_transactions_idempotency_key" in str(e):
                print(f"✅ Duplicate credit transaction correctly blocked by idempotency_key")
                raise  # Re-raise to trigger rollback
            else:
                print(f"❌ FAILURE: Wrong error: {e}")
                raise

    # Clean up
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM credit_transactions WHERE idempotency_key = 'test_duplicate_key_12345';"))

    return True


def main():
    print("=" * 80)
    print("DATABASE CONSTRAINT TESTS")
    print("Testing that Phase 1 unique constraints prevent dual-path bugs")
    print("=" * 80)

    results = []

    # Test 1: XP Transactions
    try:
        test_xp_transactions_constraint()
    except IntegrityError:
        results.append(("xp_transactions", True))
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        results.append(("xp_transactions", False))

    # Test 2: Skill Rewards
    try:
        test_skill_rewards_constraint()
    except IntegrityError:
        results.append(("skill_rewards", True))
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        results.append(("skill_rewards", False))

    # Test 3: Credit Transactions
    try:
        test_credit_transactions_constraint()
    except IntegrityError:
        results.append(("credit_transactions", True))
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        results.append(("credit_transactions", False))

    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    all_passed = all(passed for _, passed in results)

    for table, passed in results:
        status = "✅ PASSED" if passed else "❌ FAILED"
        print(f"{status}: {table}")

    print("=" * 80)

    if all_passed:
        print("🎉 ALL TESTS PASSED - Database constraints working correctly!")
        print("Phase 1 (Database Protection) is COMPLETE.")
        return 0
    else:
        print("❌ SOME TESTS FAILED - Database constraints not working as expected")
        return 1


if __name__ == "__main__":
    sys.exit(main())
