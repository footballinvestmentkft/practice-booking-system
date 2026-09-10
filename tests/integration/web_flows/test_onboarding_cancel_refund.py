"""
Integration tests — P0: cancellation refunds the recorded debit and retains history.

Tests:
  CREF-01: cancel refund → user.credit_balance increases by 100
  CREF-02: licence and original debit remain as immutable history
  CREF-03: transaction type is REFUND, amount is +100
  CREF-04: GET /credits returns 200 and renders the REFUND row
"""
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db
from app.models.user import User, UserRole
from app.models.license import UserLicense
from app.models.credit_transaction import CreditTransaction, TransactionType
from app.core.security import get_password_hash
from app.core.auth import create_access_token


# ── helpers ──────────────────────────────────────────────────────────────────

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _student(db: Session, credits: int = 0) -> User:
    u = User(
        email=f"cref.{_uid()}@e2e.test",
        name="Refund Test Student",
        password_hash=get_password_hash("TestPass123"),
        role=UserRole.STUDENT,
        is_active=True,
        credit_balance=credits,
        credit_purchased=credits,
    )
    db.add(u)
    db.flush()
    return u


def _incomplete_lfa_license(db: Session, user: User) -> UserLicense:
    lic = UserLicense(
        user_id=user.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        current_level=1,
        started_at=datetime.now(timezone.utc),
        payment_verified=True,
        payment_verified_at=datetime.now(timezone.utc),
        onboarding_completed=False,
    )
    db.add(lic)
    db.flush()
    db.add(CreditTransaction(
        user_id=user.id,
        context_user_license_id=lic.id,
        transaction_type=TransactionType.SPECIALIZATION_UNLOCK.value,
        amount=-100,
        balance_after=user.credit_balance,
        description="Recorded unlock charge",
        idempotency_key=f"license_unlock_{lic.id}",
    ))
    return lic


def _cancel(client: TestClient, token: str):
    client.cookies.set("access_token", token)
    client.get("/login")
    csrf_token = client.cookies.get("csrf_token")
    return client.post(
        "/specialization/lfa-player/onboarding-cancel",
        headers={"X-CSRF-Token": csrf_token},
        follow_redirects=False,
    )


@pytest.fixture
def auth_client(test_db: Session):
    def _override():
        try:
            yield test_db
        finally:
            pass
    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── CREF-01: balance increases by 100 ────────────────────────────────────────

def test_cref01_cancel_refunds_100_credits(auth_client: TestClient, test_db: Session):
    student = _student(test_db, credits=0)
    _incomplete_lfa_license(test_db, student)
    # Set user's specialization so cancel finds it
    student.specialization = "LFA_FOOTBALL_PLAYER"
    test_db.commit()

    token = create_access_token(data={"sub": student.email})
    resp = _cancel(auth_client, token)
    assert resp.status_code in (302, 303)

    test_db.expire(student)
    test_db.refresh(student)
    assert student.credit_balance == 100, f"Expected 100, got {student.credit_balance}"


# ── CREF-02: licence and transaction history are retained ────────────────────

def test_cref02_license_and_original_debit_are_retained(
    auth_client: TestClient, test_db: Session
):
    student = _student(test_db, credits=0)
    lic = _incomplete_lfa_license(test_db, student)
    student.specialization = "LFA_FOOTBALL_PLAYER"
    license_id = lic.id
    test_db.commit()

    token = create_access_token(data={"sub": student.email})
    _cancel(auth_client, token)

    retained_license = test_db.query(UserLicense).filter(
        UserLicense.id == license_id
    ).first()
    assert retained_license is not None
    assert retained_license.is_active is False

    original = test_db.query(CreditTransaction).filter(
        CreditTransaction.context_user_license_id == license_id,
        CreditTransaction.transaction_type == TransactionType.SPECIALIZATION_UNLOCK.value,
    ).one()
    assert original.amount == -100


# ── CREF-03: transaction type + amount ───────────────────────────────────────

def test_cref03_refund_transaction_type_and_amount(
    auth_client: TestClient, test_db: Session
):
    student = _student(test_db, credits=0)
    _incomplete_lfa_license(test_db, student)
    student.specialization = "LFA_FOOTBALL_PLAYER"
    test_db.commit()

    token = create_access_token(data={"sub": student.email})
    _cancel(auth_client, token)

    tx = test_db.query(CreditTransaction).filter(
        CreditTransaction.user_id == student.id,
        CreditTransaction.transaction_type == TransactionType.REFUND.value,
    ).first()
    assert tx is not None
    assert tx.transaction_type == TransactionType.REFUND.value
    assert tx.amount == 100
    assert tx.balance_after == 100
    assert tx.user_license_id is None, "Should be user_id-based, not license-based"


# ── CREF-04: /credits page renders REFUND row ─────────────────────────────────

def test_cref04_credits_page_shows_refund_after_cancel(
    auth_client: TestClient, test_db: Session
):
    student = _student(test_db, credits=0)
    _incomplete_lfa_license(test_db, student)
    student.specialization = "LFA_FOOTBALL_PLAYER"
    test_db.commit()

    token = create_access_token(data={"sub": student.email})

    # Perform cancel
    _cancel(auth_client, token)

    # Visit /credits
    resp = auth_client.get(
        "/credits",
        cookies={"access_token": token},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Refund" in resp.text, "REFUND badge/row not visible on /credits after cancel"
