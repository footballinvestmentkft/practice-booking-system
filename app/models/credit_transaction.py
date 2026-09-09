"""
💰 Credit Transaction Model
Tracks all credit-related transactions (purchases, enrollments, refunds)
"""
import enum
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, CheckConstraint, event
from sqlalchemy.orm import relationship
from datetime import datetime, timezone
from typing import Dict, Any

from ..database import Base


class TransactionType(enum.Enum):
    """Credit transaction types"""
    PURCHASE = "PURCHASE"           # User purchased credits (500/1000/2000 EUR)
    ENROLLMENT = "ENROLLMENT"       # Credits deducted for semester enrollment
    REFUND = "REFUND"              # Credits refunded (enrollment withdrawal before approval)
    ADMIN_ADJUSTMENT = "ADMIN_ADJUSTMENT"  # Manual admin adjustment
    MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"  # Manual adjustment (alias for ADMIN_ADJUSTMENT)
    TOURNAMENT_REWARD = "TOURNAMENT_REWARD"  # Tournament placement rewards
    EXPIRATION = "EXPIRATION"      # Credits expired (2 year limit)
    INVITATION_BONUS = "INVITATION_BONUS"    # Bonus credits granted on registration via invitation code
    SPECIALIZATION_UNLOCK = "SPECIALIZATION_UNLOCK"  # Credits spent to unlock a specialization
    CARD_DESIGN_UNLOCK = "CARD_DESIGN_UNLOCK"  # Credits spent to acquire a card design entitlement
    BALL_ANNOTATION_REWARD = "BALL_ANNOTATION_REWARD"  # Credit reward for approved corrected annotation
    ACADEMY_ID_COLOR_UNLOCK = "ACADEMY_ID_COLOR_UNLOCK"  # Credits spent to unlock a premium Academy ID colour


class CreditTransaction(Base):
    """Track all credit balance changes with full audit trail

    New writes use ``user_id`` as the only balance owner. ``user_license_id``
    remains solely for historical rows; ``context_user_license_id`` carries
    optional specialization context for new rows.
    """
    __tablename__ = "credit_transactions"

    id = Column(Integer, primary_key=True, index=True)

    # Historical rows may be license-owned; all new writes are user-owned.
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    user_license_id = Column(Integer, ForeignKey("user_licenses.id", ondelete="RESTRICT"), nullable=True, index=True)
    context_user_license_id = Column(
        Integer,
        ForeignKey("user_licenses.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Optional specialization context; never a credit owner",
    )

    # Transaction details
    transaction_type = Column(String(50), nullable=False)  # PURCHASE, ENROLLMENT, REFUND, etc.
    amount = Column(Integer, nullable=False)               # +1000 or -250 (negative for deductions)
    balance_after = Column(Integer, nullable=False)        # Balance snapshot after transaction
    description = Column(Text, nullable=True)              # Human-readable description

    # Idempotency key for preventing duplicate transactions (added 2026-02-01)
    idempotency_key = Column(String(255), nullable=False, unique=True, index=True)

    # Related entities (optional)
    semester_id = Column(Integer, ForeignKey("semesters.id", ondelete="SET NULL"), nullable=True)
    enrollment_id = Column(Integer, ForeignKey("semester_enrollments.id", ondelete="SET NULL"), nullable=True)

    # Admin audit: which admin performed this adjustment (NULL for system/user actions)
    performed_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="Admin user who performed this adjustment (NULL for system/user-initiated transactions)",
    )

    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), index=True)

    # Ensure exactly one of user_id or user_license_id is set
    __table_args__ = (
        CheckConstraint(
            '(user_id IS NOT NULL AND user_license_id IS NULL) OR (user_id IS NULL AND user_license_id IS NOT NULL)',
            name='check_one_credit_reference'
        ),
    )

    # Relationships
    user = relationship("User", foreign_keys=[user_id], back_populates="credit_transactions", passive_deletes=True)
    user_license = relationship("UserLicense", back_populates="credit_transactions", foreign_keys=[user_license_id])
    context_user_license = relationship("UserLicense", foreign_keys=[context_user_license_id])
    semester = relationship("Semester")
    enrollment = relationship("SemesterEnrollment")
    performed_by = relationship("User", foreign_keys=[performed_by_user_id])

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "user_license_id": self.user_license_id,
            "context_user_license_id": self.context_user_license_id,
            "transaction_type": self.transaction_type,
            "amount": self.amount,
            "balance_after": self.balance_after,
            "description": self.description,
            "idempotency_key": self.idempotency_key,
            "semester_id": self.semester_id,
            "enrollment_id": self.enrollment_id,
            "performed_by_user_id": self.performed_by_user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@event.listens_for(CreditTransaction, "before_insert")
def _enforce_global_credit_owner(mapper, connection, target):
    if target.user_id is None or target.user_license_id is not None:
        raise ValueError(
            "New credit ledger rows require global user ownership; legacy license wallet is read-only"
        )
