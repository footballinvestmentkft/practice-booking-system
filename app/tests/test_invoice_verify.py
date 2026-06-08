"""
INV-VERIFY-01..09 + INV-TX-EDGE-01 — Invoice verification and credit grant tests.

Covers POST /api/v1/invoices/{invoice_id}/verify  (admin only)
  INV-VERIFY-01  pending → verify → user.credit_balance increases
  INV-VERIFY-02  verify → CreditTransaction created (type=PURCHASE)
  INV-VERIFY-03  CreditTransaction.amount == invoice.credit_amount
  INV-VERIFY-04  invoice.status == "verified" after verify
  INV-VERIFY-05  invoice.verified_at is not None after verify
  INV-VERIFY-06  duplicate verify → 400 (no double-credit)
  INV-VERIFY-07  non-admin → 401/403 (cannot verify)
  INV-VERIFY-08  verified invoice transaction appears in /users/me/credit-transactions
  INV-VERIFY-09  /users/me/credit-transactions credit_balance field is fresh

Covers GET /api/v1/users/me/credit-transactions  (early-return fix)
  INV-TX-EDGE-01 user with no UserLicense still sees user-level transactions
"""
import pytest
from datetime import datetime, timezone

from ..models.user import User, UserRole
from ..models.invoice_request import InvoiceRequest, InvoiceRequestStatus
from ..models.credit_transaction import CreditTransaction, TransactionType
from ..core.security import get_password_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VERIFY_URL = "/api/v1/invoices/{invoice_id}/verify"
TX_URL     = "/api/v1/users/me/credit-transactions"


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_student(db, email: str, balance: int = 0) -> User:
    user = User(
        name="Test Student",
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


def _make_admin(db, email: str = "admin_inv@test.com") -> User:
    user = User(
        name="Admin Verifier",
        email=email,
        password_hash=get_password_hash("admin123"),
        role=UserRole.ADMIN,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _login(client, email: str, password: str = "pass123") -> str:
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login failed for {email}: {r.json()}"
    return r.json()["access_token"]


def _make_invoice(db, user: User, credit_amount: int = 500, amount_eur: float = 500.0) -> InvoiceRequest:
    inv = InvoiceRequest(
        user_id=user.id,
        payment_reference=f"TEST-INV-{user.id}-{credit_amount}",
        amount_eur=amount_eur,
        credit_amount=credit_amount,
        status=InvoiceRequestStatus.PENDING.value,
        created_at=datetime.now(timezone.utc),
    )
    db.add(inv)
    db.commit()
    db.refresh(inv)
    return inv


# ---------------------------------------------------------------------------
# INV-VERIFY-01 — balance increases
# ---------------------------------------------------------------------------

class TestInvoiceVerifyBalanceGrant:

    def test_inv_verify_01_balance_increases(self, client, db_session):
        """INV-VERIFY-01: pending invoice verify → user.credit_balance grows."""
        student = _make_student(db_session, "inv_v01@test.com", balance=100)
        admin   = _make_admin(db_session, "inv_admin_v01@test.com")
        invoice = _make_invoice(db_session, student, credit_amount=500)

        admin_token = _login(client, "inv_admin_v01@test.com", "admin123")
        url = VERIFY_URL.format(invoice_id=invoice.id)
        r = client.post(url, headers=_auth(admin_token))

        assert r.status_code == 200, r.json()
        db_session.refresh(student)
        assert student.credit_balance == 600  # 100 + 500

    # -----------------------------------------------------------------------
    # INV-VERIFY-02 — CreditTransaction created
    # -----------------------------------------------------------------------

    def test_inv_verify_02_credit_transaction_created(self, client, db_session):
        """INV-VERIFY-02: verify → CreditTransaction with type=PURCHASE exists."""
        student = _make_student(db_session, "inv_v02@test.com", balance=0)
        admin   = _make_admin(db_session, "inv_admin_v02@test.com")
        invoice = _make_invoice(db_session, student, credit_amount=250)

        admin_token = _login(client, "inv_admin_v02@test.com", "admin123")
        client.post(VERIFY_URL.format(invoice_id=invoice.id), headers=_auth(admin_token))

        ct = (
            db_session.query(CreditTransaction)
            .filter(
                CreditTransaction.user_id == student.id,
                CreditTransaction.transaction_type == TransactionType.PURCHASE.value,
            )
            .first()
        )
        assert ct is not None, "Expected a PURCHASE CreditTransaction after verify"

    # -----------------------------------------------------------------------
    # INV-VERIFY-03 — transaction amount matches invoice credit_amount
    # -----------------------------------------------------------------------

    def test_inv_verify_03_transaction_amount_matches(self, client, db_session):
        """INV-VERIFY-03: CreditTransaction.amount == invoice.credit_amount."""
        student = _make_student(db_session, "inv_v03@test.com", balance=0)
        admin   = _make_admin(db_session, "inv_admin_v03@test.com")
        invoice = _make_invoice(db_session, student, credit_amount=1000)

        admin_token = _login(client, "inv_admin_v03@test.com", "admin123")
        client.post(VERIFY_URL.format(invoice_id=invoice.id), headers=_auth(admin_token))

        ct = (
            db_session.query(CreditTransaction)
            .filter(
                CreditTransaction.user_id == student.id,
                CreditTransaction.transaction_type == TransactionType.PURCHASE.value,
            )
            .first()
        )
        assert ct is not None
        assert ct.amount == 1000

    # -----------------------------------------------------------------------
    # INV-VERIFY-04 — invoice.status == "verified"
    # -----------------------------------------------------------------------

    def test_inv_verify_04_invoice_status_verified(self, client, db_session):
        """INV-VERIFY-04: invoice.status == 'verified' after successful verify."""
        student = _make_student(db_session, "inv_v04@test.com", balance=0)
        admin   = _make_admin(db_session, "inv_admin_v04@test.com")
        invoice = _make_invoice(db_session, student, credit_amount=500)

        admin_token = _login(client, "inv_admin_v04@test.com", "admin123")
        r = client.post(VERIFY_URL.format(invoice_id=invoice.id), headers=_auth(admin_token))

        assert r.status_code == 200
        db_session.refresh(invoice)
        assert invoice.status == "verified"

    # -----------------------------------------------------------------------
    # INV-VERIFY-05 — verified_at is set
    # -----------------------------------------------------------------------

    def test_inv_verify_05_verified_at_set(self, client, db_session):
        """INV-VERIFY-05: invoice.verified_at is not None after verify."""
        student = _make_student(db_session, "inv_v05@test.com", balance=0)
        admin   = _make_admin(db_session, "inv_admin_v05@test.com")
        invoice = _make_invoice(db_session, student, credit_amount=500)

        assert invoice.verified_at is None  # pre-condition

        admin_token = _login(client, "inv_admin_v05@test.com", "admin123")
        client.post(VERIFY_URL.format(invoice_id=invoice.id), headers=_auth(admin_token))

        db_session.refresh(invoice)
        assert invoice.verified_at is not None

    # -----------------------------------------------------------------------
    # INV-VERIFY-06 — duplicate verify is rejected, no double-credit
    # -----------------------------------------------------------------------

    def test_inv_verify_06_duplicate_verify_rejected(self, client, db_session):
        """INV-VERIFY-06: second verify → 400; credit_balance unchanged."""
        student = _make_student(db_session, "inv_v06@test.com", balance=0)
        admin   = _make_admin(db_session, "inv_admin_v06@test.com")
        invoice = _make_invoice(db_session, student, credit_amount=500)

        admin_token = _login(client, "inv_admin_v06@test.com", "admin123")
        url = VERIFY_URL.format(invoice_id=invoice.id)

        r1 = client.post(url, headers=_auth(admin_token))
        assert r1.status_code == 200

        r2 = client.post(url, headers=_auth(admin_token))
        assert r2.status_code == 400
        body = r2.json()
        # Global error handler wraps as {"error": {"message": "..."}} or {"detail": "..."}
        error_text = (
            body.get("detail", "")
            or (body.get("error") or {}).get("message", "")
        ).lower()
        assert "already verified" in error_text, f"Unexpected body: {body}"

        db_session.refresh(student)
        assert student.credit_balance == 500  # granted once, not twice

    # -----------------------------------------------------------------------
    # INV-VERIFY-07 — non-admin cannot verify
    # -----------------------------------------------------------------------

    def test_inv_verify_07_non_admin_rejected(self, client, db_session):
        """INV-VERIFY-07: student Bearer token → 401/403 on verify."""
        student = _make_student(db_session, "inv_v07@test.com", balance=0)
        invoice = _make_invoice(db_session, student, credit_amount=500)

        student_token = _login(client, "inv_v07@test.com")
        url = VERIFY_URL.format(invoice_id=invoice.id)
        r = client.post(url, headers=_auth(student_token))

        assert r.status_code in (401, 403)
        db_session.refresh(student)
        assert student.credit_balance == 0  # no change

    # -----------------------------------------------------------------------
    # INV-VERIFY-08 — verified transaction appears in /users/me/credit-transactions
    # -----------------------------------------------------------------------

    def test_inv_verify_08_transaction_in_history(self, client, db_session):
        """INV-VERIFY-08: PURCHASE tx visible in /users/me/credit-transactions after verify."""
        student = _make_student(db_session, "inv_v08@test.com", balance=0)
        admin   = _make_admin(db_session, "inv_admin_v08@test.com")
        invoice = _make_invoice(db_session, student, credit_amount=750)

        admin_token   = _login(client, "inv_admin_v08@test.com", "admin123")
        student_token = _login(client, "inv_v08@test.com")

        client.post(VERIFY_URL.format(invoice_id=invoice.id), headers=_auth(admin_token))

        r = client.get(TX_URL, headers=_auth(student_token))
        assert r.status_code == 200, r.json()

        body = r.json()
        txs = body.get("transactions", [])
        purchase_txs = [t for t in txs if t["transaction_type"] == "PURCHASE"]
        assert len(purchase_txs) >= 1, "Expected at least one PURCHASE transaction"
        amounts = [t["amount"] for t in purchase_txs]
        assert 750 in amounts

    # -----------------------------------------------------------------------
    # INV-VERIFY-09 — credit_balance in response is fresh
    # -----------------------------------------------------------------------

    def test_inv_verify_09_credit_balance_fresh_in_response(self, client, db_session):
        """INV-VERIFY-09: credit_balance in /credit-transactions equals DB balance after verify."""
        student = _make_student(db_session, "inv_v09@test.com", balance=200)
        admin   = _make_admin(db_session, "inv_admin_v09@test.com")
        invoice = _make_invoice(db_session, student, credit_amount=300)

        admin_token   = _login(client, "inv_admin_v09@test.com", "admin123")
        student_token = _login(client, "inv_v09@test.com")

        client.post(VERIFY_URL.format(invoice_id=invoice.id), headers=_auth(admin_token))

        r = client.get(TX_URL, headers=_auth(student_token))
        assert r.status_code == 200

        api_balance = r.json().get("credit_balance")
        db_session.refresh(student)

        assert api_balance == 500           # 200 + 300
        assert api_balance == student.credit_balance


# ---------------------------------------------------------------------------
# INV-TX-EDGE-01 — user with no UserLicense sees user-level transactions
# ---------------------------------------------------------------------------

class TestCreditTransactionsEarlyReturnFix:

    def test_inv_tx_edge_01_no_license_user_sees_direct_transactions(self, client, db_session):
        """INV-TX-EDGE-01: user with no UserLicense still sees user_license_id=NULL transactions."""
        # User has no UserLicense records at all (fresh account, no GānCuju licenses)
        student = _make_student(db_session, "inv_edge01@test.com", balance=0)
        student_token = _login(client, "inv_edge01@test.com")

        # Manually insert a direct user-level transaction (no user_license_id)
        ct = CreditTransaction(
            user_id=student.id,
            transaction_type=TransactionType.ADMIN_ADJUSTMENT.value,
            amount=100,
            balance_after=100,
            description="Direct grant — no license",
            user_license_id=None,
            idempotency_key=f"test-edge01-{student.id}",
        )
        db_session.add(ct)
        db_session.commit()

        r = client.get(TX_URL, headers=_auth(student_token))
        assert r.status_code == 200, r.json()

        body = r.json()
        txs = body.get("transactions", [])
        assert len(txs) >= 1, "Expected at least one transaction (early return bug would give 0)"
        ids = [t["id"] for t in txs]
        assert ct.id in ids
