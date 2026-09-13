"""Transactional XP ledger/balance primitive.

Business services provide a stable idempotency key. This primitive never commits,
so the source outcome, ledger, balance and projection roll back together.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.gamification import UserStats
from app.models.user import User
from app.models.xp_transaction import XPTransaction


class XPWriteError(ValueError):
    pass


class XPWriteService:
    def __init__(self, db: Session):
        self.db = db

    def award(self, *, user_id: int, amount: int, transaction_type: str,
              idempotency_key: str, description: str | None = None,
              semester_id: int | None = None) -> tuple[UserStats, XPTransaction, bool]:
        if not idempotency_key or not isinstance(amount, int) or amount < 0:
            raise XPWriteError("INVALID_XP_COMMAND")
        user = self.db.query(User).filter(User.id == user_id).with_for_update().one_or_none()
        if user is None:
            raise XPWriteError("USER_NOT_FOUND")
        existing = self.db.query(XPTransaction).filter_by(idempotency_key=idempotency_key).one_or_none()
        if existing is not None:
            if existing.user_id != user_id or existing.amount != amount or existing.transaction_type != transaction_type:
                raise XPWriteError("XP_IDEMPOTENCY_CONFLICT")
            stats = self.db.query(UserStats).filter_by(user_id=user_id).one_or_none()
            if stats is None:
                raise XPWriteError("XP_LEDGER_PROJECTION_DIVERGENCE")
            return stats, existing, False

        user.xp_balance = int(user.xp_balance or 0) + amount
        transaction = XPTransaction(
            user_id=user_id, transaction_type=transaction_type, amount=amount,
            balance_after=user.xp_balance, description=description,
            idempotency_key=idempotency_key, semester_id=semester_id,
        )
        self.db.add(transaction)
        stats = self.db.query(UserStats).filter_by(user_id=user_id).with_for_update().one_or_none()
        if stats is None:
            stats = UserStats(user_id=user_id, total_xp=0, level=1)
            self.db.add(stats)
        stats.total_xp = int(stats.total_xp or 0) + amount
        # Preserve the established Player level boundary: level 2 starts at
        # 2,000 XP (1,000-1,999 XP remains level 1).
        stats.level = max(1, stats.total_xp // 1000)
        stats.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        return stats, transaction, True
