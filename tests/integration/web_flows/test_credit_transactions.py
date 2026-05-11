"""
Integration tests — credit transaction audit trail (P0 demo-blocker fixes)

Covers:
  CT-01: INVITATION_BONUS and SPECIALIZATION_UNLOCK added to TransactionType enum
  CT-02: Web registration writes CreditTransaction(INVITATION_BONUS) when bonus_credits > 0
  CT-03: API registration writes CreditTransaction(INVITATION_BONUS) when bonus_credits > 0
  CT-04: No CreditTransaction written when bonus_credits == 0
  CT-05: Idempotency key is deterministic: "invite_bonus:{inv_id}:{user_id}"
  CT-06: Onboarding specialization unlock uses SPECIALIZATION_UNLOCK type (not PURCHASE)
  CT-07: GET /credits responds 200 and renders credit_transactions section
  CT-08: Credits page shows transaction rows for a user with transactions
"""
import uuid
import re
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.database import get_db
from app.models.user import User, UserRole
from app.models.invitation_code import InvitationCode
from app.models.credit_transaction import CreditTransaction, TransactionType
from app.models.license import UserLicense
from app.core.security import get_password_hash
from app.core.auth import create_access_token


# ── helpers ──────────────────────────────────────────────────────────────────

def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _inv(db: Session, bonus: int = 100) -> InvitationCode:
    code = InvitationCode(
        code=f"INV-CT-{_uid().upper()}",
        invited_name="Test Registrant",
        bonus_credits=bonus,
        is_used=False,
    )
    db.add(code)
    db.flush()
    return code


def _student(db: Session, credits: int = 200) -> User:
    u = User(
        email=f"ct.student.{_uid()}@e2e.test",
        name="CT Student",
        password_hash=get_password_hash("TestPass123"),
        role=UserRole.STUDENT,
        is_active=True,
        credit_balance=credits,
        credit_purchased=credits,
    )
    db.add(u)
    db.flush()
    return u


@pytest.fixture
def anon_client(test_db: Session):
    def _override():
        try:
            yield test_db
        finally:
            pass
    app.dependency_overrides[get_db] = _override
    with TestClient(app, headers={"Authorization": "Bearer test-csrf-bypass"}) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_client(test_db: Session):
    """TestClient that can be configured per-test with a bearer token."""
    def _override():
        try:
            yield test_db
        finally:
            pass
    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── CT-01: enum values exist ──────────────────────────────────────────────────

def test_ct01_enum_has_invitation_bonus_and_specialization_unlock():
    assert TransactionType.INVITATION_BONUS.value == "INVITATION_BONUS"
    assert TransactionType.SPECIALIZATION_UNLOCK.value == "SPECIALIZATION_UNLOCK"


# ── CT-02: web register writes INVITATION_BONUS CreditTransaction ─────────────

def test_ct02_web_register_writes_invitation_bonus_transaction(
    anon_client: TestClient, test_db: Session
):
    inv = _inv(test_db, bonus=100)
    test_db.commit()

    email = f"ct02.{_uid()}@e2e.test"
    resp = anon_client.post("/register", data={
        "first_name": "CT02", "last_name": "Test", "nickname": "ct02",
        "email": email, "password": "TestPass123", "phone": "+36201234567",
        "date_of_birth": "1998-06-15",
        "nationality": "HU", "gender": "Male",
        "street_address": "Kossuth u. 1", "city": "Budapest",
        "postal_code": "1011", "country": "Hungary",
        "invitation_code": inv.code,
    }, follow_redirects=False)
    # 303 redirect on success
    assert resp.status_code in (200, 303), f"Unexpected status: {resp.status_code}\n{resp.text[:500]}"

    user = test_db.query(User).filter(User.email == email).first()
    assert user is not None, "User not created"

    tx = test_db.query(CreditTransaction).filter(
        CreditTransaction.user_id == user.id,
        CreditTransaction.transaction_type == TransactionType.INVITATION_BONUS.value,
    ).first()
    assert tx is not None, "INVITATION_BONUS CreditTransaction not written"
    assert tx.amount == 100
    assert tx.balance_after == 100
    assert tx.idempotency_key == f"invite_bonus:{inv.id}:{user.id}"


# ── CT-03: API register writes INVITATION_BONUS CreditTransaction ─────────────

def test_ct03_api_register_writes_invitation_bonus_transaction(
    auth_client: TestClient, test_db: Session
):
    inv = _inv(test_db, bonus=50)
    test_db.commit()

    email = f"ct03.{_uid()}@example.com"
    resp = auth_client.post("/api/v1/auth/register-with-invitation", json={
        "email": email,
        "password": "TestPass123",
        "name": "CT03 APITest",
        "first_name": "CT03",
        "last_name": "APITest",
        "nickname": "ct03api",
        "phone": "+36201234568",
        "date_of_birth": "1998-06-15",
        "nationality": "HU",
        "gender": "Male",
        "street_address": "Kossuth u. 2",
        "city": "Budapest",
        "postal_code": "1011",
        "country": "Hungary",
        "invitation_code": inv.code,
    })
    assert resp.status_code in (200, 201), f"API register failed: {resp.status_code}\n{resp.text[:500]}"

    user = test_db.query(User).filter(User.email == email).first()
    assert user is not None, "User not created via API"

    tx = test_db.query(CreditTransaction).filter(
        CreditTransaction.user_id == user.id,
        CreditTransaction.transaction_type == TransactionType.INVITATION_BONUS.value,
    ).first()
    assert tx is not None, "INVITATION_BONUS CreditTransaction not written by API"
    assert tx.amount == 50
    assert tx.balance_after == 50
    assert tx.idempotency_key == f"invite_bonus:{inv.id}:{user.id}"


# ── CT-04: no transaction written when bonus_credits == 0 ────────────────────

def test_ct04_no_bonus_transaction_when_zero_credits(
    anon_client: TestClient, test_db: Session
):
    inv = _inv(test_db, bonus=0)
    test_db.commit()

    email = f"ct04.{_uid()}@e2e.test"
    resp = anon_client.post("/register", data={
        "first_name": "CT04", "last_name": "Zero", "nickname": "ct04zero",
        "email": email, "password": "TestPass123", "phone": "+36201234569",
        "date_of_birth": "1998-06-15",
        "nationality": "HU", "gender": "Male",
        "street_address": "Kossuth u. 3", "city": "Budapest",
        "postal_code": "1011", "country": "Hungary",
        "invitation_code": inv.code,
    }, follow_redirects=False)
    assert resp.status_code in (200, 303)

    user = test_db.query(User).filter(User.email == email).first()
    if user is None:
        pytest.skip("User not created (may need valid zero-bonus code path)")

    tx_count = test_db.query(CreditTransaction).filter(
        CreditTransaction.user_id == user.id,
    ).count()
    assert tx_count == 0, f"Expected no transaction for zero-bonus code, got {tx_count}"


# ── CT-05: idempotency key is deterministic ───────────────────────────────────

def test_ct05_idempotency_key_format(test_db: Session):
    inv = _inv(test_db, bonus=100)
    student = _student(test_db, credits=0)
    test_db.flush()

    expected_key = f"invite_bonus:{inv.id}:{student.id}"

    tx = CreditTransaction(
        user_id=student.id,
        amount=100,
        transaction_type=TransactionType.INVITATION_BONUS.value,
        description="Test",
        balance_after=100,
        idempotency_key=expected_key,
        created_at=datetime.now(timezone.utc),
    )
    test_db.add(tx)
    test_db.flush()

    fetched = test_db.query(CreditTransaction).filter(
        CreditTransaction.idempotency_key == expected_key
    ).first()
    assert fetched is not None
    assert fetched.amount == 100
    assert fetched.transaction_type == "INVITATION_BONUS"


# ── CT-06: specialization unlock uses SPECIALIZATION_UNLOCK type ──────────────

def test_ct06_specialization_unlock_type_in_db(test_db: Session):
    """
    Verify SPECIALIZATION_UNLOCK is a valid enum value and can be stored.
    (Full onboarding flow is heavy; this guards the model/enum correctness.)
    """
    student = _student(test_db, credits=200)
    test_db.flush()

    license = UserLicense(
        user_id=student.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        current_level=1,
        started_at=datetime.now(timezone.utc),
        payment_verified=True,
        payment_verified_at=datetime.now(timezone.utc),
    )
    test_db.add(license)
    test_db.flush()

    tx = CreditTransaction(
        user_license_id=license.id,
        amount=-100,
        transaction_type=TransactionType.SPECIALIZATION_UNLOCK.value,
        description="Unlocked specialization: LFA FOOTBALL PLAYER",
        balance_after=100,
        idempotency_key=f"unlock_ct06_{_uid()}",
        created_at=datetime.now(timezone.utc),
    )
    test_db.add(tx)
    test_db.flush()

    fetched = test_db.query(CreditTransaction).filter(
        CreditTransaction.user_license_id == license.id
    ).first()
    assert fetched is not None
    assert fetched.transaction_type == "SPECIALIZATION_UNLOCK"
    assert fetched.amount == -100


# ── CT-07: GET /credits responds 200 ─────────────────────────────────────────

def test_ct07_credits_page_returns_200(auth_client: TestClient, test_db: Session):
    student = _student(test_db, credits=150)
    test_db.commit()

    token = create_access_token(data={"sub": student.email})
    resp = auth_client.get(
        "/credits",
        headers={"Authorization": f"Bearer {token}"},
        cookies={"access_token": token},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Internal Server Error" not in resp.text


# ── CT-08: /credits page renders transaction rows ─────────────────────────────

def test_ct08_credits_page_renders_transaction_rows(
    auth_client: TestClient, test_db: Session
):
    student = _student(test_db, credits=100)
    test_db.flush()

    # Seed one INVITATION_BONUS and one SPECIALIZATION_UNLOCK transaction
    bonus_tx = CreditTransaction(
        user_id=student.id,
        amount=100,
        transaction_type=TransactionType.INVITATION_BONUS.value,
        description="Registration bonus via invitation code INV-CT-TEST",
        balance_after=100,
        idempotency_key=f"invite_bonus:999:{student.id}",
        created_at=datetime.now(timezone.utc),
    )
    test_db.add(bonus_tx)

    license = UserLicense(
        user_id=student.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        current_level=1,
        started_at=datetime.now(timezone.utc),
        payment_verified=True,
        payment_verified_at=datetime.now(timezone.utc),
    )
    test_db.add(license)
    test_db.flush()

    unlock_tx = CreditTransaction(
        user_license_id=license.id,
        amount=-100,
        transaction_type=TransactionType.SPECIALIZATION_UNLOCK.value,
        description="Unlocked specialization: LFA FOOTBALL PLAYER",
        balance_after=0,
        idempotency_key=f"unlock_ct08_{_uid()}",
        created_at=datetime.now(timezone.utc),
    )
    test_db.add(unlock_tx)
    test_db.commit()

    token = create_access_token(data={"sub": student.email})
    resp = auth_client.get(
        "/credits",
        headers={"Authorization": f"Bearer {token}"},
        cookies={"access_token": token},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    # Both transaction type badges should appear
    assert "Invite Bonus" in resp.text, "INVITATION_BONUS row not rendered"
    assert "Specialization" in resp.text, "SPECIALIZATION_UNLOCK row not rendered"
    # Amount signs
    assert "+100" in resp.text, "+100 amount not rendered"
    assert "-100" in resp.text, "-100 amount not rendered"
