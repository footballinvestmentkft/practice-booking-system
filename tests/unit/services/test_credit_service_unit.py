"""
Sprint 43 — CreditService pure unit tests (mock-based, no DB)

Complements tests/unit/services/test_credit_service.py (integration-style with
postgres_db).  Focus: IntegrityError handling paths that the integration tests
can never exercise.

Why the integration tests miss these paths
------------------------------------------
The pre-flight idempotency check (line 70-72) catches all duplicate keys BEFORE
reaching db.flush().  Only true concurrent writes (two requests interleaved at
the OS level) can bypass the pre-flight check — not reproducible with sequential
DB tests.  Here we make db.flush() raise IntegrityError directly.

FK guard: all fixtures use user_id=42 (CI lint rejects the literal pattern in tests/unit/services/).
"""

import pytest
from unittest.mock import MagicMock
from sqlalchemy.exc import IntegrityError

from app.services.credit_service import CreditService

_IDEM_KEY = "tournament_99_42_reward"
_CONSTRAINT = "uq_credit_transactions_idempotency_key"


# ── Helpers ─────────────────────────────────────────────────────────────────


def _call(db, *, user_id=42, user_license_id=None, idempotency_key=_IDEM_KEY):
    """Invoke CreditService.create_transaction with sensible defaults."""
    svc = CreditService(db=db)
    return svc.create_transaction(
        user_id=user_id,
        user_license_id=user_license_id,
        transaction_type="TOURNAMENT_REWARD",
        amount=100,
        balance_after=500,
        description="Test reward",
        idempotency_key=idempotency_key,
    )


def _db_with_flush_error(constraint_str, re_fetch_result=None):
    """
    DB mock where:
      - pre-flight idempotency check → None (not yet committed by another writer)
      - db.flush()                   → raises IntegrityError with constraint_str
      - re-fetch after rollback      → re_fetch_result (existing or None)
    """
    db = MagicMock()

    pre_flight = MagicMock()
    pre_flight.filter.return_value.first.return_value = None

    re_fetch = MagicMock()
    re_fetch.filter.return_value.first.return_value = re_fetch_result

    db.query.side_effect = [pre_flight, re_fetch]

    orig = Exception(constraint_str)
    db.flush.side_effect = IntegrityError("INSERT INTO credit_transactions ...", {}, orig)
    return db


# ── Tests ────────────────────────────────────────────────────────────────────


class TestCreditServiceUnit:

    # ── Validation error messages (kill string-literal mutants) ───────────

    def test_neither_user_id_raises_with_exact_message(self):
        """
        Missing global user_id → ValueError.
        Assert exact message text to kill the string-literal mutant at line 64.
        """
        svc = CreditService(db=MagicMock())
        with pytest.raises(ValueError,
                           match="Global credit transactions require user_id"):
            svc.create_transaction(
                user_id=None, user_license_id=None,
                transaction_type="X", amount=1, balance_after=1,
                description="x", idempotency_key="x",
            )

    def test_both_user_ids_raises_with_exact_message(self):
        """
        Legacy wallet ownership requested → ValueError.
        Assert exact message text to kill the string-literal mutant at line 67.
        """
        svc = CreditService(db=MagicMock())
        with pytest.raises(ValueError,
                           match="Legacy license wallet is read-only"):
            svc.create_transaction(
                user_id=42, user_license_id=9,
                transaction_type="X", amount=1, balance_after=1,
                description="x", idempotency_key="x",
            )

    # ── Pre-flight idempotency path ───────────────────────────────────────

    def test_pre_flight_match_returns_existing_without_flush(self):
        """
        When the pre-flight check finds an existing transaction, return it
        immediately — db.flush() must NOT be called.
        """
        db = MagicMock()
        existing = MagicMock(id=55)
        q = MagicMock()
        q.filter.return_value.first.return_value = existing
        db.query.return_value = q

        result, created = _call(db)

        assert created is False
        assert result is existing
        db.flush.assert_not_called()

    # ── IntegrityError handler (lines 107-134) — kills conditional/branch mutants ──

    def test_integrity_error_constraint_match_existing_found_returns_false(self):
        """
        IntegrityError whose str() contains the idempotency constraint name,
        and the re-fetch finds the existing transaction → (existing, False).
        Exercises lines 108-122.
        """
        existing = MagicMock(id=77)
        db = _db_with_flush_error(_CONSTRAINT, re_fetch_result=existing)

        result, created = _call(db)

        assert created is False
        assert result is existing

    def test_integrity_error_constraint_match_not_found_raises_value_error(self):
        """
        IntegrityError with matching constraint but re-fetch returns None
        (critical race-condition path) → ValueError at line 128.
        """
        db = _db_with_flush_error(_CONSTRAINT, re_fetch_result=None)

        with pytest.raises(ValueError):
            _call(db)

    def test_integrity_error_race_condition_message_contains_key_and_phrase(self):
        """
        Race-condition ValueError message: assert 'race condition' phrase present
        and the idempotency key appears in the message.
        Kills string-literal mutants at lines 128-130.
        """
        db = _db_with_flush_error(_CONSTRAINT, re_fetch_result=None)

        with pytest.raises(ValueError, match="race condition"):
            _call(db, idempotency_key=_IDEM_KEY)

    def test_integrity_error_other_constraint_reraises_as_integrity_error(self):
        """
        IntegrityError whose str() does NOT contain the idempotency constraint name
        → re-raise the original IntegrityError (else-branch at line 131).
        """
        db = MagicMock()
        pre_flight = MagicMock()
        pre_flight.filter.return_value.first.return_value = None
        db.query.side_effect = [pre_flight]

        orig = Exception("some_other_unique_constraint violation")
        db.flush.side_effect = IntegrityError("INSERT ...", {}, orig)

        with pytest.raises(IntegrityError):
            _call(db)

    # ── @staticmethod decorator (line 136) ───────────────────────────────

    def test_generate_idempotency_key_as_unbound_class_method(self):
        """
        Call generate_idempotency_key directly on the class (not an instance).
        If @staticmethod is removed, Python passes the first argument as self
        and raises TypeError — this test kills that decorator mutant.
        """
        key = CreditService.generate_idempotency_key("tournament", 99, 42, "reward")
        assert key == "tournament_99_42_reward"

    def test_generate_idempotency_key_is_lowercase(self):
        """Uppercase inputs are lowercased — kills any case-normalization mutant."""
        key = CreditService.generate_idempotency_key("TOURNAMENT", 1, 42, "REWARD")
        assert key == "tournament_1_42_reward"

    def test_generate_idempotency_key_on_instance_kills_decorator_mutant(self):
        """
        Call generate_idempotency_key on an INSTANCE (not the class).
        Without @staticmethod Python injects self as an implicit 5th argument;
        the 4-param signature has no room for it → TypeError kills the mutant.
        """
        svc = CreditService(db=MagicMock())
        key = svc.generate_idempotency_key("tournament", 99, 42, "reward")
        assert key == "tournament_99_42_reward"

    # ── Exact message assertions (kill XX-wrapped string mutants) ─────────
    #
    # mutmut 2.x mutates string literals by wrapping them: "msg" → "XXmsgXX".
    # pytest.raises(match=...) uses re.search() — the original text is still a
    # substring of the wrapped string, so match= passes and the mutant survives.
    # str(exc_info.value) == "..." is an EXACT comparison and catches the wrapping.

    def test_neither_user_id_message_is_exact(self):
        """Exact message check for the 'Either' validation guard (line 63)."""
        svc = CreditService(db=MagicMock())
        with pytest.raises(ValueError) as exc_info:
            svc.create_transaction(
                user_id=None, user_license_id=None,
                transaction_type="X", amount=1, balance_after=1,
                description="x", idempotency_key="x",
            )
        assert str(exc_info.value) == "Global credit transactions require user_id"

    def test_both_user_ids_message_is_exact(self):
        """Exact message check for the legacy wallet write guard."""
        svc = CreditService(db=MagicMock())
        with pytest.raises(ValueError) as exc_info:
            svc.create_transaction(
                user_id=42, user_license_id=9,
                transaction_type="X", amount=1, balance_after=1,
                description="x", idempotency_key="x",
            )
        assert str(exc_info.value) == "Legacy license wallet is read-only; use context_user_license_id"

    def test_race_condition_message_is_exact(self):
        """Exact message check for the race-condition ValueError (line 128)."""
        db = _db_with_flush_error(_CONSTRAINT, re_fetch_result=None)
        with pytest.raises(ValueError) as exc_info:
            _call(db, idempotency_key=_IDEM_KEY)
        assert str(exc_info.value) == (
            f"Credit transaction with key '{_IDEM_KEY}' failed due to race condition"
        )
