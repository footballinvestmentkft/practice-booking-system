"""
Credit Transaction Service - Centralized Credit Management

This service provides the SINGLE SOURCE OF TRUTH for creating credit transactions.
All credit transaction creation MUST go through this service to prevent dual-path bugs.

Business Invariant: One credit transaction per idempotency_key
"""

from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from typing import Optional, Literal
from datetime import datetime
import logging

from ..models.credit_transaction import CreditTransaction
from ..models.user import User

logger = logging.getLogger(__name__)


class InsufficientCreditsError(Exception):
    def __init__(self, required: int, available: int):
        self.required = required
        self.available = available
        super().__init__(f"Insufficient credits: required {required}, available {available}")


class CreditService:
    """Centralized service for credit transaction management"""

    def __init__(self, db: Session):
        self.db = db

    def create_transaction(
        self,
        user_id: Optional[int],
        user_license_id: Optional[int],
        transaction_type: str,
        amount: int,
        balance_after: int,
        description: str,
        idempotency_key: str,
        semester_id: Optional[int] = None,
        enrollment_id: Optional[int] = None,
        sponsor_id: Optional[int] = None,
        campaign_id: Optional[int] = None,
    ) -> tuple[CreditTransaction, bool]:
        """
        Create a credit transaction with idempotency protection.

        Args:
            user_id: User ID (either this or user_license_id must be set)
            user_license_id: UserLicense ID (either this or user_id must be set)
            transaction_type: Type of transaction (e.g., "TOURNAMENT_REWARD")
            amount: Credit amount (positive for awards, negative for deductions)
            balance_after: User's balance after this transaction
            description: Human-readable description
            idempotency_key: Unique key to prevent duplicates
            semester_id: Optional semester reference
            enrollment_id: Optional enrollment reference

        Returns:
            Tuple of (CreditTransaction, created)
            - created=True: Transaction was created
            - created=False: Transaction already existed (idempotent return)

        Raises:
            ValueError: If business rules are violated
            IntegrityError: If database constraints are violated (shouldn't happen if idempotency_key is unique)
        """
        # Validate business rules
        if user_id is None and user_license_id is None:
            raise ValueError("Either user_id or user_license_id must be provided")

        if user_id is not None and user_license_id is not None:
            raise ValueError("Only one of user_id or user_license_id can be provided")

        # Check for existing transaction (idempotency)
        existing = self.db.query(CreditTransaction).filter(
            CreditTransaction.idempotency_key == idempotency_key
        ).first()

        if existing:
            logger.info(
                f"🔒 IDEMPOTENT RETURN: Credit transaction with key '{idempotency_key}' "
                f"already exists (id={existing.id}). Returning existing transaction."
            )
            return (existing, False)

        # Create new transaction
        transaction = CreditTransaction(
            user_id=user_id,
            user_license_id=user_license_id,
            transaction_type=transaction_type,
            amount=amount,
            balance_after=balance_after,
            description=description,
            idempotency_key=idempotency_key,
            semester_id=semester_id,
            enrollment_id=enrollment_id,
            sponsor_id=sponsor_id,
            campaign_id=campaign_id,
            created_at=datetime.utcnow()
        )

        try:
            self.db.add(transaction)
            self.db.flush()  # Flush to get ID and check constraints

            logger.info(
                f"✅ Credit transaction created: id={transaction.id}, "
                f"type={transaction_type}, amount={amount}, "
                f"key={idempotency_key}"
            )

            return (transaction, True)

        except IntegrityError as e:
            # If unique constraint violation on idempotency_key, return existing
            if "uq_credit_transactions_idempotency_key" in str(e):
                self.db.rollback()

                # Fetch the existing transaction
                existing = self.db.query(CreditTransaction).filter(
                    CreditTransaction.idempotency_key == idempotency_key
                ).first()

                if existing:
                    logger.warning(
                        f"🔒 RACE CONDITION: Credit transaction with key '{idempotency_key}' "
                        f"was created by another request. Returning existing transaction (id={existing.id})."
                    )
                    return (existing, False)
                else:
                    logger.error(
                        f"❌ CRITICAL: IntegrityError on idempotency_key but transaction not found! "
                        f"key={idempotency_key}"
                    )
                    raise ValueError(
                        f"Credit transaction with key '{idempotency_key}' failed due to race condition"
                    ) from e
            else:
                # Other integrity error - re-raise
                logger.error(f"❌ IntegrityError creating credit transaction: {e}")
                raise

    def deduct(
        self,
        user: User,
        amount: int,
        transaction_type: str,
        description: str,
        idempotency_key: str,
        sponsor_id: Optional[int] = None,
        campaign_id: Optional[int] = None,
    ) -> CreditTransaction:
        """
        Atomically deduct credits from a user's balance.

        Uses a SAVEPOINT so the caller owns the outer transaction (no commit here).

        Raises:
            InsufficientCreditsError: if user has fewer credits than amount
        """
        with self.db.begin_nested():
            result = self.db.execute(
                text(
                    "UPDATE users SET credit_balance = credit_balance - :amount "
                    "WHERE id = :uid AND credit_balance >= :amount "
                    "RETURNING credit_balance"
                ),
                {"amount": amount, "uid": user.id},
            ).fetchone()

            if result is None:
                self.db.refresh(user)
                raise InsufficientCreditsError(required=amount, available=user.credit_balance)

            new_balance = result[0]
            transaction, _ = self.create_transaction(
                user_id=user.id,
                user_license_id=None,
                transaction_type=transaction_type,
                amount=-amount,
                balance_after=new_balance,
                description=description,
                idempotency_key=idempotency_key,
                sponsor_id=sponsor_id,
                campaign_id=campaign_id,
            )

        user.credit_balance = new_balance
        logger.info(
            f"💳 Deducted {amount} credits from user {user.id}: "
            f"balance {new_balance + amount} → {new_balance} ({transaction_type})"
        )
        return transaction

    def award(
        self,
        user: User,
        amount: int,
        transaction_type: str,
        description: str,
        idempotency_key: str,
        sponsor_id: Optional[int] = None,
        campaign_id: Optional[int] = None,
    ) -> tuple[CreditTransaction, bool]:
        """
        Award (add) credits to a user's balance within the caller's transaction.

        Checks idempotency BEFORE the SQL UPDATE to avoid phantom balance changes
        on retry.  No internal SAVEPOINT — caller owns the outer transaction and
        must call db.commit() (or db.rollback()) when appropriate.

        Returns:
            (transaction, created) — created=False means idempotent no-op.
        """
        # Idempotency-first: do NOT run SQL UPDATE if transaction already exists.
        existing = self.db.query(CreditTransaction).filter(
            CreditTransaction.idempotency_key == idempotency_key
        ).first()
        if existing:
            logger.info(
                f"🔒 IDEMPOTENT AWARD: key='{idempotency_key}' already exists "
                f"(id={existing.id}). Skipping."
            )
            return (existing, False)

        result = self.db.execute(
            text(
                "UPDATE users SET credit_balance = credit_balance + :amount "
                "WHERE id = :uid RETURNING credit_balance"
            ),
            {"amount": amount, "uid": user.id},
        ).fetchone()

        new_balance = result[0]
        transaction = CreditTransaction(
            user_id=user.id,
            user_license_id=None,
            transaction_type=transaction_type,
            amount=+amount,
            balance_after=new_balance,
            description=description,
            idempotency_key=idempotency_key,
            sponsor_id=sponsor_id,
            campaign_id=campaign_id,
            created_at=datetime.utcnow(),
        )
        self.db.add(transaction)
        self.db.flush()

        user.credit_balance = new_balance
        logger.info(
            f"🎁 Awarded {amount} credits to user {user.id}: "
            f"balance {new_balance - amount} → {new_balance} ({transaction_type})"
        )
        return (transaction, True)

    def deduct_batch(
        self,
        user: User,
        amount: int,
        transaction_type: str,
        description: str,
        idempotency_key: str,
        sponsor_id: Optional[int] = None,
        campaign_id: Optional[int] = None,
    ) -> CreditTransaction:
        """
        Deduct credits within the caller's transaction (no internal SAVEPOINT).

        For use in batch operations where the caller manages the outer transaction
        and an InsufficientCreditsError must roll back the whole batch.

        Raises:
            InsufficientCreditsError: if user has fewer credits than amount.
        """
        result = self.db.execute(
            text(
                "UPDATE users SET credit_balance = credit_balance - :amount "
                "WHERE id = :uid AND credit_balance >= :amount "
                "RETURNING credit_balance"
            ),
            {"amount": amount, "uid": user.id},
        ).fetchone()

        if result is None:
            self.db.refresh(user)
            raise InsufficientCreditsError(required=amount, available=user.credit_balance)

        new_balance = result[0]
        transaction, _ = self.create_transaction(
            user_id=user.id,
            user_license_id=None,
            transaction_type=transaction_type,
            amount=-amount,
            balance_after=new_balance,
            description=description,
            idempotency_key=idempotency_key,
            sponsor_id=sponsor_id,
            campaign_id=campaign_id,
        )
        user.credit_balance = new_balance
        logger.info(
            f"💳 Deducted {amount} credits from user {user.id}: "
            f"balance {new_balance + amount} → {new_balance} ({transaction_type})"
        )
        return transaction

    @staticmethod
    def generate_idempotency_key(
        source_type: str,
        source_id: int,
        user_id: int,
        operation: str
    ) -> str:
        """
        Generate idempotency key for credit transactions.

        Format: {source_type}_{source_id}_{user_id}_{operation}

        Examples:
            - "tournament_123_reward_5" (tournament 123 rewarding user 5)
            - "enrollment_456_refund_7" (enrollment 456 refunding user 7)
            - "session_789_attendance_bonus_3" (session 789 awarding attendance bonus to user 3)

        Args:
            source_type: Type of source (tournament, enrollment, session, etc.)
            source_id: ID of the source
            user_id: User receiving/losing credits
            operation: Operation type (reward, refund, deduction, etc.)

        Returns:
            Idempotency key string
        """
        return f"{source_type}_{source_id}_{user_id}_{operation}".lower()
