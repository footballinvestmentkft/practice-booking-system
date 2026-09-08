"""PostgreSQL evidence for P0 credit replay, rollback and concurrency rules."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker

from app.api.web_routes.onboarding import lfa_player_onboarding_cancel
from app.api.web_routes.specialization import specialization_unlock
from app.database import engine
from app.models.credit_transaction import CreditTransaction, TransactionType
from app.models.audit_log import AuditLog
from app.models.license import UserLicense
from app.models.user import User, UserRole
from app.services.credit_service import (
    CreditService,
    IdempotencyConflictError,
    InsufficientCreditsError,
)
from app.services.license_renewal_service import (
    InsufficientCreditsError as RenewalInsufficientCreditsError,
    LicenseRenewalService,
)


SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)


def _new_user(*, balance: int) -> tuple[int, str]:
    marker = uuid4().hex
    with SessionFactory.begin() as db:
        user = User(
            name="P0 PostgreSQL User",
            email=f"p0-{marker}@example.invalid",
            password_hash="not-used",
            role=UserRole.STUDENT,
            is_active=True,
            date_of_birth=datetime(1990, 1, 1, tzinfo=timezone.utc),
            credit_balance=balance,
        )
        db.add(user)
        db.flush()
        return user.id, marker


def _balance_and_ledger(user_id: int):
    with SessionFactory() as db:
        balance = db.query(User.credit_balance).filter(User.id == user_id).scalar()
        ledger = (
            db.query(CreditTransaction)
            .filter(CreditTransaction.user_id == user_id)
            .order_by(CreditTransaction.id)
            .all()
        )
        return balance, ledger


def test_f04_replaying_same_debit_key_changes_balance_once():
    user_id, marker = _new_user(balance=100)
    key = f"p0-replay-{marker}"

    for _ in range(2):
        with SessionFactory() as db:
            user = db.get(User, user_id)
            CreditService(db).deduct(
                user=user,
                amount=30,
                transaction_type="P0_TEST_DEBIT",
                description="P0 replay test",
                idempotency_key=key,
            )
            db.commit()

    balance, ledger = _balance_and_ledger(user_id)
    assert balance == 70
    assert [(row.amount, row.balance_after) for row in ledger] == [(-30, 70)]


def test_f04_reusing_key_for_different_debit_is_rejected_without_drift():
    user_id, marker = _new_user(balance=100)
    key = f"p0-conflict-{marker}"

    with SessionFactory() as db:
        CreditService(db).deduct(
            user=db.get(User, user_id),
            amount=30,
            transaction_type="P0_TEST_DEBIT",
            description="original operation",
            idempotency_key=key,
        )
        db.commit()

    with SessionFactory() as db:
        with pytest.raises(IdempotencyConflictError):
            CreditService(db).deduct(
                user=db.get(User, user_id),
                amount=20,
                transaction_type="P0_TEST_DEBIT",
                description="different operation",
                idempotency_key=key,
            )
        db.rollback()

    balance, ledger = _balance_and_ledger(user_id)
    assert balance == 70
    assert [(row.amount, row.balance_after) for row in ledger] == [(-30, 70)]


def test_f04_rollback_reverts_balance_and_ledger_together():
    user_id, marker = _new_user(balance=100)
    with SessionFactory() as db:
        user = db.get(User, user_id)
        CreditService(db).deduct(
            user=user,
            amount=40,
            transaction_type="P0_TEST_DEBIT",
            description="P0 rollback test",
            idempotency_key=f"p0-rollback-{marker}",
        )
        db.rollback()

    balance, ledger = _balance_and_ledger(user_id)
    assert balance == 100
    assert ledger == []


def test_f04_concurrent_same_key_is_exactly_once():
    user_id, marker = _new_user(balance=100)
    key = f"p0-concurrent-replay-{marker}"

    def debit_once():
        with SessionFactory() as db:
            user = db.get(User, user_id)
            tx = CreditService(db).deduct(
                user=user,
                amount=30,
                transaction_type="P0_TEST_DEBIT",
                description="P0 concurrent replay test",
                idempotency_key=key,
            )
            db.commit()
            return tx.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: debit_once(), range(2)))

    balance, ledger = _balance_and_ledger(user_id)
    assert balance == 70
    assert len(set(ids)) == 1
    assert len(ledger) == 1


def test_f05_concurrent_unlocks_for_different_programs_cannot_overspend():
    user_id, _ = _new_user(balance=150)

    def unlock(spec: str):
        with SessionFactory() as db:
            user = db.get(User, user_id)
            try:
                result = asyncio.run(
                    specialization_unlock(
                        specialization=spec,
                        duration_months=1,
                        db=db,
                        current_user=user,
                    )
                )
                return ("ok", result)
            except HTTPException as exc:
                db.rollback()
                return ("http", exc.status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(unlock, ["LFA_PLAYER", "GANCUJU_PLAYER"]))

    balance, ledger = _balance_and_ledger(user_id)
    with SessionFactory() as db:
        license_count = db.query(UserLicense).filter(UserLicense.user_id == user_id).count()

    assert sorted(kind for kind, _ in results) == ["http", "ok"]
    assert any(value == 400 for kind, value in results if kind == "http")
    assert balance == 50
    assert license_count == 1
    assert len(ledger) == 1
    assert ledger[0].amount == -100


def test_f05_concurrent_renewals_cannot_overspend_and_are_replay_safe():
    user_id, marker = _new_user(balance=1500)
    with SessionFactory.begin() as db:
        license = UserLicense(
            user_id=user_id,
            specialization_type="LFA_COACH",
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.now(timezone.utc),
            renewal_cost=1000,
            is_active=True,
        )
        db.add(license)
        db.flush()
        license_id = license.id

    def renew(suffix: str):
        with SessionFactory() as db:
            try:
                result = LicenseRenewalService.renew_license(
                    license_id=license_id,
                    renewal_months=12,
                    admin_id=1,
                    db=db,
                    idempotency_key=f"p0-renew-{marker}-{suffix}",
                )
                return ("ok", result["remaining_credits"])
            except RenewalInsufficientCreditsError:
                db.rollback()
                return ("insufficient", None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(renew, ["a", "b"]))

    balance, ledger = _balance_and_ledger(user_id)
    assert sorted(kind for kind, _ in results) == ["insufficient", "ok"]
    assert balance == 500
    assert len(ledger) == 1
    assert ledger[0].amount == -1000


def test_f05_renewal_replay_does_not_extend_or_audit_twice():
    user_id, marker = _new_user(balance=1500)
    key = f"p0-renew-replay-{marker}"
    with SessionFactory.begin() as db:
        license = UserLicense(
            user_id=user_id,
            specialization_type="LFA_COACH",
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.now(timezone.utc),
            renewal_cost=1000,
            is_active=True,
        )
        db.add(license)
        db.flush()
        license_id = license.id

    expirations = []
    for _ in range(2):
        with SessionFactory() as db:
            result = LicenseRenewalService.renew_license(
                license_id=license_id,
                renewal_months=12,
                admin_id=1,
                db=db,
                idempotency_key=key,
            )
            expirations.append(result["new_expiration"])

    balance, ledger = _balance_and_ledger(user_id)
    with SessionFactory() as db:
        audit_count = db.query(AuditLog).filter(
            AuditLog.action == "LICENSE_RENEWED",
            AuditLog.resource_id == license_id,
        ).count()

    assert expirations[0] == expirations[1]
    assert balance == 500
    assert len(ledger) == 1
    assert audit_count == 1


def test_f06_cancellation_refunds_recorded_amount_once_and_keeps_history():
    user_id, marker = _new_user(balance=0)
    with SessionFactory.begin() as db:
        user = db.get(User, user_id)
        user.specialization = "LFA_FOOTBALL_PLAYER"
        license = UserLicense(
            user_id=user_id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.now(timezone.utc),
            onboarding_completed=False,
            is_active=True,
        )
        db.add(license)
        db.flush()
        license_id = license.id
        db.add(
            CreditTransaction(
                user_license_id=license_id,
                amount=-250,
                transaction_type=TransactionType.SPECIALIZATION_UNLOCK.value,
                description="Recorded 3-month unlock",
                balance_after=0,
                idempotency_key=f"legacy-unlock-{marker}",
            )
        )

    for _ in range(2):
        with SessionFactory() as db:
            user = db.get(User, user_id)
            asyncio.run(
                lfa_player_onboarding_cancel(
                    request=object(), db=db, user=user
                )
            )

    balance, ledger = _balance_and_ledger(user_id)
    with SessionFactory() as db:
        license = db.get(UserLicense, license_id)

    assert balance == 250
    assert license is not None
    assert license.is_active is False
    assert sorted(row.amount for row in ledger) == [250]
    with SessionFactory() as db:
        original = db.query(CreditTransaction).filter(
            CreditTransaction.user_license_id == license_id
        ).one()
        assert original.amount == -250


def test_f06_ambiguous_unlock_history_blocks_refund_without_mutation():
    user_id, marker = _new_user(balance=0)
    with SessionFactory.begin() as db:
        license = UserLicense(
            user_id=user_id,
            specialization_type="LFA_FOOTBALL_PLAYER",
            current_level=1,
            max_achieved_level=1,
            started_at=datetime.now(timezone.utc),
            onboarding_completed=False,
            is_active=True,
        )
        db.add(license)
        db.flush()
        license_id = license.id
        db.add_all([
            CreditTransaction(
                user_license_id=license_id,
                transaction_type=TransactionType.SPECIALIZATION_UNLOCK.value,
                amount=-100,
                balance_after=0,
                description="legacy unlock",
                idempotency_key=f"p0-legacy-a-{marker}",
            ),
            CreditTransaction(
                user_id=user_id,
                transaction_type=TransactionType.SPECIALIZATION_UNLOCK.value,
                amount=-250,
                balance_after=0,
                description="duplicate unlock evidence",
                idempotency_key=f"license_unlock_{license_id}",
            ),
        ])

    with SessionFactory() as db:
        user = db.get(User, user_id)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                lfa_player_onboarding_cancel(
                    request=None,
                    db=db,
                    user=user,
                )
            )
        assert exc.value.status_code == 409

    balance, ledger = _balance_and_ledger(user_id)
    with SessionFactory() as db:
        license = db.get(UserLicense, license_id)
    assert balance == 0
    assert license.is_active is True
    assert [row.amount for row in ledger] == [-250]
