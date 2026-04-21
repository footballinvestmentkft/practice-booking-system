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

def test_xp_transactions_constraint():
    """
    Verify xp_transactions constraint state after migration 2026_04_20_0900.

    uq_xp_transactions_user_semester_type was intentionally dropped — multiple rows
    of the same (user_id, semester_id, transaction_type) must now be allowed (required
    for training segment XP rows).

    uq_xp_transaction_idempotency (partial UNIQUE on idempotency_key WHERE NOT NULL)
    must still be present — it is the sole uniqueness guard for keyed transactions.

    Self-contained: seeds a minimal test user for FK satisfaction, cleans up after.
    Does not require a pre-seeded DB (runs in CI on a fresh migration).
    """
    engine = create_engine(DATABASE_URL)

    # 1. Verify the old composite constraint is gone
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT constraint_name FROM information_schema.table_constraints
            WHERE table_name = 'xp_transactions'
              AND constraint_name = 'uq_xp_transactions_user_semester_type'
        """)).fetchone()
        assert row is None, (
            "uq_xp_transactions_user_semester_type must be absent after migration 2026_04_20_0900"
        )

    # 2. Verify the partial idempotency index is still present
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT indexname FROM pg_indexes
            WHERE tablename = 'xp_transactions'
              AND indexname = 'uq_xp_transaction_idempotency'
        """)).fetchone()
        assert row is not None, (
            "uq_xp_transaction_idempotency partial index must still be present"
        )

    # Seed a minimal test user to satisfy the FK on user_id (CI has no pre-seeded data).
    # Only name/email/password_hash are required; all other columns have DB defaults.
    _CI_EMAIL = "ci_xp_constraint_test@test.invalid"
    with engine.begin() as conn:
        test_user_id = conn.execute(text("""
            INSERT INTO users
                (name, email, password_hash, role,
                 payment_verified, credit_balance, credit_purchased,
                 xp_balance, nda_accepted, parental_consent)
            VALUES
                ('CI XP Constraint Test', :email, 'dummy_hash_ci', 'STUDENT',
                 false, 0, 0,
                 0, false, false)
            ON CONFLICT (email) DO UPDATE SET name = EXCLUDED.name
            RETURNING id
        """), {"email": _CI_EMAIL}).scalar()

    try:
        # 3. Verify duplicate (user_id, semester_id, transaction_type) rows ARE now allowed.
        # semester_id is nullable — use NULL to avoid needing a seeded semester row.
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO xp_transactions
                    (user_id, transaction_type, amount, balance_after, description)
                VALUES (:uid, 'TEST_DUPLICATE_ALLOWED', 10, 10, 'first')
            """), {"uid": test_user_id})
            # This second insert must succeed (composite constraint is gone)
            conn.execute(text("""
                INSERT INTO xp_transactions
                    (user_id, transaction_type, amount, balance_after, description)
                VALUES (:uid, 'TEST_DUPLICATE_ALLOWED', 10, 10, 'second')
            """), {"uid": test_user_id})
        with engine.begin() as conn:
            conn.execute(text(
                "DELETE FROM xp_transactions WHERE transaction_type = 'TEST_DUPLICATE_ALLOWED';"
            ))

        # 4. Verify duplicate idempotency_key rows are still blocked by the partial index
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO xp_transactions
                    (user_id, transaction_type, amount, balance_after, idempotency_key)
                VALUES (:uid, 'TEST_IDEM', 10, 10, 'test_idem_key_pr_a')
            """), {"uid": test_user_id})
            conn.commit()
            sp = conn.begin_nested()
            try:
                conn.execute(text("""
                    INSERT INTO xp_transactions
                        (user_id, transaction_type, amount, balance_after, idempotency_key)
                    VALUES (:uid, 'TEST_IDEM', 10, 10, 'test_idem_key_pr_a')
                """), {"uid": test_user_id})
                pytest.fail("Duplicate idempotency_key must be blocked by uq_xp_transaction_idempotency")
            except IntegrityError as e:
                sp.rollback()
                assert "uq_xp_transaction_idempotency" in str(e), (
                    f"Wrong constraint triggered: {e}"
                )
            conn.execute(text(
                "DELETE FROM xp_transactions WHERE transaction_type = 'TEST_IDEM';"
            ))
            conn.commit()
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM users WHERE email = :email"), {"email": _CI_EMAIL})


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
