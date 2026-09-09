"""WS1 global-credit authority evidence on disposable PostgreSQL."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.database import SessionLocal
from app.models.credit_transaction import CreditTransaction, TransactionType
from app.models.license import UserLicense
from app.models.user import User, UserRole
from app.services.credit_service import CreditService


pytestmark = pytest.mark.postgres


def _user_and_license():
    db = SessionLocal()
    user = User(
        name="WS1 Credit",
        email=f"ws1-credit-{uuid4().hex}@example.test",
        password_hash="test-only-hash",
        role=UserRole.STUDENT,
        is_active=True,
        date_of_birth=datetime(1990, 1, 1),
        credit_balance=100,
    )
    db.add(user)
    db.flush()
    license_row = UserLicense(
        user_id=user.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        canonical_program_id="LFA_FOOTBALL_PLAYER",
        current_level=1,
        max_achieved_level=1,
        started_at=datetime.now(timezone.utc),
        is_active=True,
        credit_balance=0,
    )
    db.add(license_row)
    db.commit()
    ids = user.id, license_row.id
    db.close()
    return ids


def test_global_user_balance_is_authority_and_legacy_wallet_is_unchanged():
    user_id, license_id = _user_and_license()
    db = SessionLocal()
    user = db.get(User, user_id)
    key = f"ws1-global-credit-{uuid4().hex}"
    transaction = CreditService(db).deduct(
        user=user,
        amount=25,
        transaction_type=TransactionType.ENROLLMENT.value,
        description="WS1 global authority proof",
        idempotency_key=key,
    )
    transaction.context_user_license_id = license_id
    db.commit()

    assert db.get(User, user_id).credit_balance == 75
    assert db.get(UserLicense, license_id).credit_balance == 0
    row = db.query(CreditTransaction).filter_by(idempotency_key=key).one()
    assert (row.user_id, row.user_license_id, row.context_user_license_id) == (
        user_id,
        None,
        license_id,
    )

    replay = CreditService(db).deduct(
        user=user,
        amount=25,
        transaction_type=TransactionType.ENROLLMENT.value,
        description="WS1 global authority proof",
        idempotency_key=key,
    )
    db.commit()
    assert replay.id == row.id
    assert db.get(User, user_id).credit_balance == 75
    assert db.query(CreditTransaction).filter_by(idempotency_key=key).count() == 1
    db.close()


def test_new_license_owned_ledger_row_is_rejected_without_mutating_balances():
    user_id, license_id = _user_and_license()
    db = SessionLocal()
    db.add(CreditTransaction(
        user_id=None,
        user_license_id=license_id,
        transaction_type=TransactionType.ENROLLMENT.value,
        amount=-10,
        balance_after=0,
        description="Forbidden legacy write",
        idempotency_key=f"ws1-forbidden-{uuid4().hex}",
    ))
    with pytest.raises(ValueError, match="legacy license wallet is read-only"):
        db.flush()
    db.rollback()
    assert db.get(User, user_id).credit_balance == 100
    assert db.get(UserLicense, license_id).credit_balance == 0
    db.close()


def test_postgresql_rejects_direct_new_legacy_wallet_ledger_row():
    user_id, license_id = _user_and_license()
    db = SessionLocal()
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO credit_transactions "
                "(user_license_id, transaction_type, amount, balance_after, description, idempotency_key, created_at) "
                "VALUES (:license_id, 'ADMIN_ADJUSTMENT', 1, 1, 'forbidden legacy write', :key, NOW())"
            ),
            {"license_id": license_id, "key": f"ws1-direct-legacy-{uuid4().hex}"},
        )
        db.commit()
    db.rollback()
    assert db.get(User, user_id).credit_balance == 100
    assert db.get(UserLicense, license_id).credit_balance == 0
    db.close()


def test_new_legacy_wallet_balance_and_direct_balance_change_are_rejected():
    db = SessionLocal()
    user = User(
        name="WS1 Wallet Guard",
        email=f"ws1-wallet-{uuid4().hex}@example.test",
        password_hash="test-only-hash",
        role=UserRole.STUDENT,
        is_active=True,
        date_of_birth=datetime(1990, 1, 1),
        credit_balance=100,
    )
    db.add(user)
    db.flush()
    db.add(UserLicense(
        user_id=user.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        current_level=1,
        max_achieved_level=1,
        started_at=datetime.now(timezone.utc),
        is_active=True,
        credit_balance=1,
    ))
    with pytest.raises(ValueError, match="Legacy license wallet is read-only"):
        db.flush()
    db.rollback()

    user_id, license_id = _user_and_license()
    with pytest.raises(DBAPIError):
        db.execute(
            text("UPDATE user_licenses SET credit_balance = 1 WHERE id = :license_id"),
            {"license_id": license_id},
        )
        db.commit()
    db.rollback()
    assert db.get(User, user_id).credit_balance == 100
    assert db.get(UserLicense, license_id).credit_balance == 0
    db.close()
