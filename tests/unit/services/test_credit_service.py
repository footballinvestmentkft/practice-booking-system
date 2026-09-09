"""
Unit Tests: CreditService

Tests the centralized credit transaction service in isolation.
"""

import pytest
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime

from app.services.credit_service import CreditService
from app.models.credit_transaction import CreditTransaction


class TestCreditService:
    """Unit tests for CreditService"""

    def test_create_transaction_success(self, postgres_db: Session, user_factory):
        """Test creating a new credit transaction"""
        service = CreditService(postgres_db)

        # Create test user dynamically
        user = user_factory(name="Credit Test User")

        idempotency_key = "test_create_transaction_123"

        (transaction, created) = service.create_transaction(
            user_id=user.id,
            user_license_id=None,
            transaction_type="TEST_REWARD",
            amount=100,
            balance_after=100,
            description="Test transaction",
            idempotency_key=idempotency_key,
            semester_id=None
        )

        assert created is True, "Transaction should be marked as created"
        assert transaction.id is not None, "Transaction should have an ID"
        assert transaction.amount == 100
        assert transaction.idempotency_key == idempotency_key

        # Cleanup
        postgres_db.delete(transaction)
        postgres_db.commit()

    def test_create_transaction_idempotent_return(self, postgres_db: Session, user_factory):
        """Test that duplicate idempotency_key returns existing transaction"""
        service = CreditService(postgres_db)

        # Create test user dynamically
        user = user_factory(name="Idempotency Test User")

        idempotency_key = "test_idempotent_456"

        # First call - should create
        (transaction1, created1) = service.create_transaction(
            user_id=user.id,
            user_license_id=None,
            transaction_type="TEST_REWARD",
            amount=100,
            balance_after=100,
            description="First transaction",
            idempotency_key=idempotency_key,
            semester_id=None
        )

        assert created1 is True
        transaction1_id = transaction1.id

        # Second call - should return existing
        (transaction2, created2) = service.create_transaction(
            user_id=user.id,
            user_license_id=None,
            transaction_type="TEST_REWARD",
            amount=100,
            balance_after=200,  # Different balance
            description="Second transaction (should be ignored)",
            idempotency_key=idempotency_key,  # Same key!
            semester_id=None
        )

        assert created2 is False, "Second call should return existing transaction"
        assert transaction2.id == transaction1_id, "Should return same transaction"
        assert transaction2.balance_after == 100, "Should have original balance, not new one"

        # Verify only ONE transaction in database
        count = postgres_db.query(CreditTransaction).filter(
            CreditTransaction.idempotency_key == idempotency_key
        ).count()
        assert count == 1, f"Expected 1 transaction, found {count}"

        # Cleanup
        postgres_db.delete(transaction1)
        postgres_db.commit()

    def test_create_transaction_validation_both_user_ids(self, postgres_db: Session, user_factory):
        """Test that providing both user_id and user_license_id raises error"""
        service = CreditService(postgres_db)

        # Create test user dynamically
        user = user_factory(name="Validation Test User")

        with pytest.raises(ValueError) as exc_info:
            service.create_transaction(
                user_id=user.id,
                user_license_id=3,  # Both provided - invalid!
                transaction_type="TEST_REWARD",
                amount=100,
                balance_after=100,
                description="Invalid transaction",
                idempotency_key="test_validation_both"
            )

        assert "Legacy license wallet is read-only" in str(exc_info.value)

    def test_create_transaction_validation_no_user_ids(self, postgres_db: Session):
        """Test that providing neither user_id nor user_license_id raises error"""
        service = CreditService(postgres_db)

        with pytest.raises(ValueError) as exc_info:
            service.create_transaction(
                user_id=None,
                user_license_id=None,  # Neither provided - invalid!
                transaction_type="TEST_REWARD",
                amount=100,
                balance_after=100,
                description="Invalid transaction",
                idempotency_key="test_validation_none"
            )

        assert "Global credit transactions require user_id" in str(exc_info.value)

    def test_generate_idempotency_key_format(self):
        """Test idempotency key generation format"""
        key = CreditService.generate_idempotency_key(
            source_type="tournament",
            source_id=123,
            user_id=5,
            operation="reward"
        )

        assert key == "tournament_123_5_reward"

    def test_generate_idempotency_key_lowercase(self):
        """Test that idempotency keys are lowercase"""
        key = CreditService.generate_idempotency_key(
            source_type="TOURNAMENT",  # Uppercase
            source_id=123,
            user_id=5,
            operation="REWARD"  # Uppercase
        )

        assert key == "tournament_123_5_reward", "Key should be lowercase"

    def test_create_transaction_with_license_context_but_global_user_owner(self, postgres_db: Session, user_factory):
        """License may be context, while the global User remains the owner."""
        from app.models.license import UserLicense
        from datetime import datetime, timezone

        service = CreditService(postgres_db)

        # Create test user dynamically
        user = user_factory(name="License Test User")

        # Create user license for that user
        user_license = UserLicense(
            user_id=user.id,
            specialization_type="PLAYER",
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.now(timezone.utc)
        )
        postgres_db.add(user_license)
        postgres_db.flush()

        idempotency_key = "test_user_license_789"

        (transaction, created) = service.create_transaction(
            user_id=user.id,
            user_license_id=None,
            context_user_license_id=user_license.id,
            transaction_type="TEST_REWARD",
            amount=50,
            balance_after=50,
            description="Test with user_license_id",
            idempotency_key=idempotency_key
        )

        assert created is True
        assert transaction.user_id == user.id
        assert transaction.user_license_id is None
        assert transaction.context_user_license_id == user_license.id

        # Cleanup
        postgres_db.delete(transaction)
        postgres_db.delete(user_license)
        postgres_db.commit()

    def test_race_condition_handling(self, postgres_db: Session, user_factory):
        """
        Test that race condition (concurrent creates) is handled gracefully.

        Simulates two requests trying to create the same transaction simultaneously.
        """
        service = CreditService(postgres_db)

        # Create test user dynamically
        user = user_factory(name="Race Condition Test User")

        idempotency_key = "test_race_condition_999"

        # First request creates transaction
        (transaction1, created1) = service.create_transaction(
            user_id=user.id,
            user_license_id=None,
            transaction_type="TEST_REWARD",
            amount=100,
            balance_after=100,
            description="First request",
            idempotency_key=idempotency_key,
            semester_id=None
        )

        assert created1 is True

        # Commit to database
        postgres_db.commit()

        # Second request (simulating race condition) - should get existing
        (transaction2, created2) = service.create_transaction(
            user_id=user.id,
            user_license_id=None,
            transaction_type="TEST_REWARD",
            amount=100,
            balance_after=200,
            description="Second request (race condition)",
            idempotency_key=idempotency_key,
            semester_id=None
        )

        assert created2 is False, "Second request should get existing transaction"
        assert transaction2.id == transaction1.id

        # Cleanup
        postgres_db.delete(transaction1)
        postgres_db.commit()
