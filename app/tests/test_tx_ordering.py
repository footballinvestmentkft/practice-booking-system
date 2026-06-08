"""
TX-ORDER-01..05 — Credit transaction history ordering tests.

Covers GET /api/v1/users/me/credit-transactions
  TX-ORDER-01  user-level + license-level transactions sorted created_at DESC, id DESC
  TX-ORDER-02  same created_at → id DESC is the tiebreaker
  TX-ORDER-03  unlock + coupon transactions both present and correctly ordered
  TX-ORDER-04  credit_balance field matches current DB balance
  TX-ORDER-05  limit=50 caps results at 50 entries
"""
import pytest
from datetime import datetime, timezone, timedelta

from ..models.user import User, UserRole
from ..models.license import UserLicense
from ..models.credit_transaction import CreditTransaction, TransactionType
from ..core.security import get_password_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TX_URL = "/api/v1/users/me/credit-transactions"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_student(db, email: str, balance: int = 500) -> User:
    user = User(
        name="Order Test Student",
        email=email,
        password_hash=get_password_hash("pass123"),
        role=UserRole.STUDENT,
        is_active=True,
        credit_balance=balance,
        credit_purchased=balance,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client, email: str, password: str = "pass123") -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login failed: {r.json()}"
    return r.json()["access_token"]


def _make_license(db, user: User) -> UserLicense:
    lic = UserLicense(
        user_id=user.id,
        specialization_type="LFA_FOOTBALL_PLAYER",
        current_level=1,
        max_achieved_level=1,
        started_at=datetime.now(timezone.utc),
        is_active=True,
        onboarding_completed=False,
    )
    db.add(lic)
    db.commit()
    db.refresh(lic)
    return lic


def _make_tx(db, user_id=None, license_id=None, amount=100,
             tx_type=TransactionType.ADMIN_ADJUSTMENT.value,
             created_at=None, idempotency_key=None, balance_after=0) -> CreditTransaction:
    ct = CreditTransaction(
        user_id=user_id,
        user_license_id=license_id,
        transaction_type=tx_type,
        amount=amount,
        balance_after=balance_after,
        description=f"test tx",
        idempotency_key=idempotency_key or f"test-{id(object())}-{amount}",
        created_at=created_at or datetime.now(timezone.utc),
    )
    db.add(ct)
    db.commit()
    db.refresh(ct)
    return ct


# ---------------------------------------------------------------------------
# TX-ORDER-01 — created_at DESC, id DESC ordering across user + license txs
# ---------------------------------------------------------------------------

class TestTransactionOrdering:

    def test_tx_order_01_desc_ordering_user_and_license_level(self, client, db_session):
        """TX-ORDER-01: user-level and license-level transactions sorted DESC."""
        student = _make_student(db_session, "txo01@test.com", balance=500)
        lic     = _make_license(db_session, student)
        token   = _login(client, "txo01@test.com")

        now = datetime.now(timezone.utc)

        t1 = _make_tx(db_session, user_id=student.id,  amount=75,
                      tx_type=TransactionType.INVITATION_BONUS.value,
                      created_at=now - timedelta(hours=3),
                      idempotency_key="txo01-t1", balance_after=75)
        t2 = _make_tx(db_session, license_id=lic.id,   amount=-100,
                      tx_type=TransactionType.SPECIALIZATION_UNLOCK.value,
                      created_at=now - timedelta(hours=2),
                      idempotency_key="txo01-t2", balance_after=75)
        t3 = _make_tx(db_session, user_id=student.id,  amount=100,
                      tx_type=TransactionType.ADMIN_ADJUSTMENT.value,
                      created_at=now - timedelta(hours=1),
                      idempotency_key="txo01-t3", balance_after=175)

        r = client.get(TX_URL, headers=_auth(token))
        assert r.status_code == 200
        ids = [tx["id"] for tx in r.json()["transactions"]]

        assert ids.index(t3.id) < ids.index(t2.id) < ids.index(t1.id), (
            f"Expected t3 > t2 > t1 (DESC), got order: {ids}"
        )

    # -----------------------------------------------------------------------
    # TX-ORDER-02 — same created_at → id DESC
    # -----------------------------------------------------------------------

    def test_tx_order_02_same_created_at_id_desc_tiebreak(self, client, db_session):
        """TX-ORDER-02: two txs with identical created_at → higher id comes first."""
        student = _make_student(db_session, "txo02@test.com", balance=300)
        token   = _login(client, "txo02@test.com")

        same_ts = datetime.now(timezone.utc)

        ta = _make_tx(db_session, user_id=student.id, amount=50,
                      created_at=same_ts, idempotency_key="txo02-ta", balance_after=50)
        tb = _make_tx(db_session, user_id=student.id, amount=60,
                      created_at=same_ts, idempotency_key="txo02-tb", balance_after=110)

        r = client.get(TX_URL, headers=_auth(token))
        assert r.status_code == 200
        ids = [tx["id"] for tx in r.json()["transactions"]]

        # tb has higher id → should come first (DESC)
        assert ids.index(tb.id) < ids.index(ta.id), (
            f"Expected tb (id={tb.id}) before ta (id={ta.id}), got: {ids}"
        )

    # -----------------------------------------------------------------------
    # TX-ORDER-03 — unlock + coupon both present, correctly interleaved
    # -----------------------------------------------------------------------

    def test_tx_order_03_unlock_and_coupon_interleaved(self, client, db_session):
        """TX-ORDER-03: SPECIALIZATION_UNLOCK and ADMIN_ADJUSTMENT both returned."""
        student = _make_student(db_session, "txo03@test.com", balance=400)
        lic     = _make_license(db_session, student)
        token   = _login(client, "txo03@test.com")

        now = datetime.now(timezone.utc)

        coupon = _make_tx(db_session, user_id=student.id, amount=100,
                          tx_type=TransactionType.ADMIN_ADJUSTMENT.value,
                          created_at=now - timedelta(minutes=10),
                          idempotency_key="txo03-coupon", balance_after=100)
        unlock = _make_tx(db_session, license_id=lic.id, amount=-100,
                          tx_type=TransactionType.SPECIALIZATION_UNLOCK.value,
                          created_at=now - timedelta(minutes=5),
                          idempotency_key="txo03-unlock", balance_after=0)

        r = client.get(TX_URL, headers=_auth(token))
        assert r.status_code == 200
        txs = r.json()["transactions"]
        tx_ids  = [t["id"] for t in txs]
        tx_types = [t["transaction_type"] for t in txs]

        assert coupon.id in tx_ids, "Coupon tx missing from response"
        assert unlock.id in tx_ids, "Unlock tx missing from response"
        # Unlock is more recent → appears first (DESC)
        assert tx_ids.index(unlock.id) < tx_ids.index(coupon.id)

    # -----------------------------------------------------------------------
    # TX-ORDER-04 — credit_balance field is current DB balance
    # -----------------------------------------------------------------------

    def test_tx_order_04_credit_balance_field_current(self, client, db_session):
        """TX-ORDER-04: response credit_balance equals current user.credit_balance."""
        student = _make_student(db_session, "txo04@test.com", balance=750)
        token   = _login(client, "txo04@test.com")

        _make_tx(db_session, user_id=student.id, amount=200,
                 idempotency_key="txo04-t1", balance_after=750)

        r = client.get(TX_URL, headers=_auth(token))
        assert r.status_code == 200

        db_session.refresh(student)
        assert r.json()["credit_balance"] == student.credit_balance

    # -----------------------------------------------------------------------
    # TX-ORDER-05 — limit=50 caps at 50 results
    # -----------------------------------------------------------------------

    def test_tx_order_05_limit_50_caps_results(self, client, db_session):
        """TX-ORDER-05: 55 transactions exist → response returns at most 50."""
        student = _make_student(db_session, "txo05@test.com", balance=0)
        token   = _login(client, "txo05@test.com")

        now = datetime.now(timezone.utc)
        for i in range(55):
            _make_tx(db_session, user_id=student.id, amount=1,
                     created_at=now - timedelta(seconds=i),
                     idempotency_key=f"txo05-{i}", balance_after=i)

        r = client.get(TX_URL, headers=_auth(token))
        assert r.status_code == 200
        body = r.json()
        assert len(body["transactions"]) <= 50
        assert body["total_count"] >= 55
