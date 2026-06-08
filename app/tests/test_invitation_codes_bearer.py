"""
INV-BEARER-01..12 — Bearer-auth invitation code redemption tests.

Covers POST /api/v1/invitation-codes/redeem-authenticated
  INV-BEARER-01  valid code → 200, credits awarded, balance updated
  INV-BEARER-02  valid code → CreditTransaction created (INVITATION_BONUS)
  INV-BEARER-03  valid code → invitation_code.is_used=True after redeem
  INV-BEARER-04  code not found → 404
  INV-BEARER-05  already used code → 400
  INV-BEARER-06  expired code → 400
  INV-BEARER-07  email-restricted code, caller email matches → 200
  INV-BEARER-08  email-restricted code, caller email mismatch → 403
  INV-BEARER-09  no Bearer token → 401
  INV-BEARER-10  double redeem same user → 400
  INV-BEARER-11  double redeem different user → 400
  INV-BEARER-12  existing web cookie endpoint still returns 401 without cookie (regression)
"""
import pytest
from datetime import datetime, timezone, timedelta

from ..models.invitation_code import InvitationCode
from ..models.credit_transaction import CreditTransaction, TransactionType
from ..models.user import User, UserRole
from ..core.security import get_password_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

URL = "/api/v1/invitation-codes/redeem-authenticated"
WEB_URL = "/api/v1/invitation-codes/redeem"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_user(db, email: str, balance: int = 0) -> User:
    user = User(
        name="Test User",
        email=email,
        password_hash=get_password_hash("pass123"),
        role=UserRole.STUDENT,
        is_active=True,
        credit_balance=balance,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client, email: str, password: str = "pass123") -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login failed for {email}: {r.json()}"
    return r.json()["access_token"]


def _make_code(
    db,
    bonus_credits: int = 150,
    invited_email: str | None = None,
    is_used: bool = False,
    expires_at: datetime | None = None,
) -> InvitationCode:
    from ..models.invitation_code import InvitationCode as IC
    code = IC(
        code=IC.generate_code(),
        invited_name="Test Invitee",
        invited_email=invited_email,
        bonus_credits=bonus_credits,
        is_used=is_used,
        expires_at=expires_at,
    )
    db.add(code)
    db.commit()
    db.refresh(code)
    return code


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def user_a(db_session):
    return _make_user(db_session, "user_a@test.com", balance=50)


@pytest.fixture
def token_a(client, user_a):
    return _login(client, "user_a@test.com")


@pytest.fixture
def user_b(db_session):
    return _make_user(db_session, "user_b@test.com", balance=0)


@pytest.fixture
def token_b(client, user_b):
    return _login(client, "user_b@test.com")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_inv_bearer_01_valid_code_credits_awarded(client, db_session, user_a, token_a):
    """INV-BEARER-01: valid code → 200, credits_awarded, new_balance = old + bonus."""
    code = _make_code(db_session, bonus_credits=200)
    old_balance = user_a.credit_balance

    r = client.post(URL, json={"code": code.code}, headers=_auth(token_a))

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["bonus_credits"] == 200
    assert body["old_balance"] == old_balance
    assert body["new_balance"] == old_balance + 200

    db_session.refresh(user_a)
    assert user_a.credit_balance == old_balance + 200


def test_inv_bearer_02_credit_transaction_created(client, db_session, user_a, token_a):
    """INV-BEARER-02: successful redeem → CreditTransaction with INVITATION_BONUS type."""
    code = _make_code(db_session, bonus_credits=100)

    client.post(URL, json={"code": code.code}, headers=_auth(token_a))

    ct = (
        db_session.query(CreditTransaction)
        .filter(
            CreditTransaction.user_id == user_a.id,
            CreditTransaction.transaction_type == TransactionType.INVITATION_BONUS.value,
        )
        .first()
    )
    assert ct is not None
    assert ct.amount == 100


def test_inv_bearer_03_code_marked_used(client, db_session, user_a, token_a):
    """INV-BEARER-03: successful redeem → is_used=True, used_by_user_id set."""
    code = _make_code(db_session)

    client.post(URL, json={"code": code.code}, headers=_auth(token_a))

    db_session.refresh(code)
    assert code.is_used is True
    assert code.used_by_user_id == user_a.id
    assert code.used_at is not None


def test_inv_bearer_04_code_not_found(client, token_a):
    """INV-BEARER-04: non-existent code → 404."""
    r = client.post(URL, json={"code": "INV-99999999-XXXXXX"}, headers=_auth(token_a))
    assert r.status_code == 404


def test_inv_bearer_05_already_used_code(client, db_session, user_a, token_a):
    """INV-BEARER-05: already-used code → 400."""
    code = _make_code(db_session, is_used=True)

    r = client.post(URL, json={"code": code.code}, headers=_auth(token_a))

    assert r.status_code == 400


def test_inv_bearer_06_expired_code(client, db_session, user_a, token_a):
    """INV-BEARER-06: expired code (expires_at in the past) → 400."""
    past = datetime.now(timezone.utc) - timedelta(days=1)
    code = _make_code(db_session, expires_at=past)

    r = client.post(URL, json={"code": code.code}, headers=_auth(token_a))

    assert r.status_code == 400


def test_inv_bearer_07_email_restricted_match(client, db_session, user_a, token_a):
    """INV-BEARER-07: email-restricted code, caller's email matches → 200."""
    code = _make_code(db_session, invited_email=user_a.email, bonus_credits=75)

    r = client.post(URL, json={"code": code.code}, headers=_auth(token_a))

    assert r.status_code == 200
    assert r.json()["bonus_credits"] == 75


def test_inv_bearer_08_email_restricted_mismatch(client, db_session, user_a, user_b, token_b):
    """INV-BEARER-08: email-restricted code, caller's email does NOT match → 403."""
    code = _make_code(db_session, invited_email=user_a.email)

    r = client.post(URL, json={"code": code.code}, headers=_auth(token_b))

    assert r.status_code == 403


def test_inv_bearer_09_no_bearer_token(client, db_session):
    """INV-BEARER-09: request without Authorization header → 401."""
    code = _make_code(db_session)

    r = client.post(URL, json={"code": code.code})

    assert r.status_code == 401


def test_inv_bearer_10_double_redeem_same_user(client, db_session, user_a, token_a):
    """INV-BEARER-10: same user redeems same code twice → second attempt → 400."""
    code = _make_code(db_session)

    r1 = client.post(URL, json={"code": code.code}, headers=_auth(token_a))
    assert r1.status_code == 200

    r2 = client.post(URL, json={"code": code.code}, headers=_auth(token_a))
    assert r2.status_code == 400


def test_inv_bearer_11_double_redeem_different_user(client, db_session, user_a, user_b, token_a, token_b):
    """INV-BEARER-11: user_a redeems first, then user_b tries same code → 400."""
    code = _make_code(db_session)

    r1 = client.post(URL, json={"code": code.code}, headers=_auth(token_a))
    assert r1.status_code == 200

    r2 = client.post(URL, json={"code": code.code}, headers=_auth(token_b))
    assert r2.status_code == 400


def test_inv_bearer_12_web_cookie_endpoint_unaffected(client, db_session):
    """INV-BEARER-12: existing web cookie endpoint still rejects requests without cookie → 401."""
    code = _make_code(db_session)

    r = client.post(WEB_URL, json={"code": code.code})

    assert r.status_code == 401
