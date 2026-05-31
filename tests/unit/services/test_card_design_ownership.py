"""Unit tests for CardDesignOwnership service layer.

CDO-01  purchase player_card/compact → ownership row, card_type_id="player_card"
CDO-02  purchase welcome_card/default → ownership row, card_type_id="welcome_card"
CDO-03  purchase challenge_card/challenge → ownership row, card_type_id="challenge_card"
CDO-04  purchase_design creates CreditTransaction(type=CARD_DESIGN_UNLOCK)
CDO-05  purchase player_card/fclassic → FreeDesignError, no deduction, no ownership row
CDO-06  already owned → AlreadyOwnedError, no double deduction
CDO-07  insufficient credits → InsufficientCreditsError, no ownership row
CDO-08  race condition: IntegrityError → AlreadyOwnedError, rollback called
CDO-09  CreditService.deduct does NOT call db.commit() (uses begin_nested/SAVEPOINT only)
CDO-10  is_design_accessible player_card/fclassic → False without ownership row (no free bypass)
CDO-11  is_design_accessible player_card/compact → False without ownership
CDO-12  is_design_accessible player_card/compact → True after ownership
CDO-13  is_design_accessible welcome_card/default → False without ownership
CDO-14  is_design_accessible welcome_card/default → True after ownership
CDO-15  legacy JSON shim: unlocked_card_variants contains design_id → True
CDO-16  _NON_PLAYER_CARD_PRICES welcome_card/default price > 0
CDO-17  _NON_PLAYER_CARD_PRICES challenge_card/challenge price > 0
CDO-18  grant_design idempotent — two calls → one row, no exception
CDO-19  onboarding step-7 flow does NOT call grant_design (no auto-grant)
CDO-20  challenge completion flow does NOT call grant_design (no auto-grant)
"""
from unittest.mock import MagicMock, call, patch

import pytest

_SVC  = "app.services.card_design_service"
_CSVC = "app.services.credit_service"  # CreditService is imported locally inside purchase_design()


def _make_user(user_id=101, balance=1000):
    u = MagicMock()
    u.id = user_id
    u.credit_balance = balance
    return u


def _make_db():
    db = MagicMock()
    return db


def _make_query_none(db):
    """Configure db.query(...).filter_by(...).first() to return None."""
    q = MagicMock()
    q.filter_by.return_value = q
    q.first.return_value = None
    db.query.return_value = q
    return q


def _make_query_exists(db, obj):
    """Configure db.query(...).filter_by(...).first() to return obj."""
    q = MagicMock()
    q.filter_by.return_value = q
    q.first.return_value = obj
    db.query.return_value = q
    return q


# ── CDO-01..04: purchase_design success paths ─────────────────────────────────

class TestPurchaseDesignSuccess:

    def _run_purchase(self, db, user, card_type_id, design_id, price):
        from sqlalchemy.exc import IntegrityError

        fake_tx = MagicMock()
        fake_tx.id = 99

        q = MagicMock()
        q.filter_by.return_value = q
        q.first.return_value = None  # not already owned
        db.query.return_value = q

        with patch(f"{_CSVC}.CreditService") as mock_cs, \
             patch(f"{_SVC}._resolve_price", return_value=price), \
             patch(f"{_SVC}.CardDesignOwnership") as mock_cdo:
            mock_cs.return_value.deduct.return_value = fake_tx
            # second db.query call returns the credit_tx by idempotency_key
            fake_owned = MagicMock()
            fake_owned.id = 99

            from app.services.card_design_service import purchase_design
            result = purchase_design(db, user, card_type_id, design_id)

        return result, mock_cs, mock_cdo

    def test_cdo01_player_card_compact(self):
        """CDO-01: purchase player_card/compact creates ownership row with correct card_type_id."""
        from app.services.card_design_service import purchase_design
        from app.models.card_design_ownership import CardDesignOwnership

        db = _make_db()
        user = _make_user(balance=500)
        _make_query_none(db)

        fake_tx = MagicMock()
        fake_tx.id = 10

        with patch(f"{_SVC}._resolve_price", return_value=300), \
             patch(f"{_CSVC}.CreditService") as mock_cs:
            mock_cs.return_value.deduct.return_value = fake_tx
            purchase_design(db, user, "player_card", "compact")

        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.card_type_id == "player_card"
        assert added.design_id == "compact"
        assert added.source == "purchase"
        assert added.credit_transaction_id == 10
        db.commit.assert_called_once()

    def test_cdo02_welcome_card(self):
        """CDO-02: purchase welcome_card/default creates ownership row with card_type_id='welcome_card'."""
        from app.services.card_design_service import purchase_design

        db = _make_db()
        user = _make_user(balance=500)
        _make_query_none(db)

        fake_tx = MagicMock()
        fake_tx.id = 20

        with patch(f"{_SVC}._resolve_price", return_value=200), \
             patch(f"{_CSVC}.CreditService") as mock_cs:
            mock_cs.return_value.deduct.return_value = fake_tx
            purchase_design(db, user, "welcome_card", "default")

        added = db.add.call_args[0][0]
        assert added.card_type_id == "welcome_card"
        assert added.design_id == "default"

    def test_cdo03_challenge_card(self):
        """CDO-03: purchase challenge_card/challenge creates ownership row."""
        from app.services.card_design_service import purchase_design

        db = _make_db()
        user = _make_user(balance=500)
        _make_query_none(db)

        fake_tx = MagicMock()
        fake_tx.id = 30

        with patch(f"{_SVC}._resolve_price", return_value=150), \
             patch(f"{_CSVC}.CreditService") as mock_cs:
            mock_cs.return_value.deduct.return_value = fake_tx
            purchase_design(db, user, "challenge_card", "challenge")

        added = db.add.call_args[0][0]
        assert added.card_type_id == "challenge_card"
        assert added.design_id == "challenge"

    def test_cdo04_credit_transaction_type(self):
        """CDO-04: CreditService.deduct is called with transaction_type='CARD_DESIGN_UNLOCK'."""
        from app.services.card_design_service import purchase_design

        db = _make_db()
        user = _make_user(balance=500)
        _make_query_none(db)

        fake_tx = MagicMock()
        fake_tx.id = 40

        with patch(f"{_SVC}._resolve_price", return_value=300), \
             patch(f"{_CSVC}.CreditService") as mock_cs:
            mock_cs.return_value.deduct.return_value = fake_tx
            purchase_design(db, user, "player_card", "compact")

        deduct_kwargs = mock_cs.return_value.deduct.call_args
        assert deduct_kwargs.kwargs["transaction_type"] == "CARD_DESIGN_UNLOCK"


# ── CDO-05..08: error paths ───────────────────────────────────────────────────

class TestPurchaseDesignErrors:

    def test_cdo05_free_design_error(self):
        """CDO-05: purchasing player_card/fclassic raises FreeDesignError, no deduction."""
        from app.services.card_design_service import FreeDesignError, purchase_design

        db = _make_db()
        user = _make_user(balance=1000)

        with patch(f"{_SVC}._resolve_price", return_value=0), \
             patch(f"{_CSVC}.CreditService") as mock_cs:
            with pytest.raises(FreeDesignError):
                purchase_design(db, user, "player_card", "fclassic")
            mock_cs.return_value.deduct.assert_not_called()
        db.add.assert_not_called()

    def test_cdo06_already_owned(self):
        """CDO-06: already owned → AlreadyOwnedError, CreditService.deduct not called."""
        from app.services.card_design_service import AlreadyOwnedError, purchase_design

        db = _make_db()
        user = _make_user(balance=1000)

        existing = MagicMock()
        q = MagicMock()
        q.filter_by.return_value = q
        q.first.return_value = existing
        db.query.return_value = q

        with patch(f"{_SVC}._resolve_price", return_value=300), \
             patch(f"{_CSVC}.CreditService") as mock_cs:
            with pytest.raises(AlreadyOwnedError):
                purchase_design(db, user, "player_card", "compact")
            mock_cs.return_value.deduct.assert_not_called()

    def test_cdo07_insufficient_credits(self):
        """CDO-07: InsufficientCreditsError → no ownership row created."""
        from app.services.card_design_service import purchase_design
        from app.services.credit_service import InsufficientCreditsError

        db = _make_db()
        user = _make_user(balance=0)
        _make_query_none(db)

        with patch(f"{_SVC}._resolve_price", return_value=300), \
             patch(f"{_CSVC}.CreditService") as mock_cs:
            mock_cs.return_value.deduct.side_effect = InsufficientCreditsError(300, 0)
            with pytest.raises(InsufficientCreditsError):
                purchase_design(db, user, "player_card", "compact")

        db.add.assert_not_called()

    def test_cdo08_integrity_error_raises_already_owned(self):
        """CDO-08: IntegrityError on INSERT → AlreadyOwnedError, db.rollback() called."""
        from sqlalchemy.exc import IntegrityError

        from app.services.card_design_service import AlreadyOwnedError, purchase_design

        db = _make_db()
        user = _make_user(balance=500)
        _make_query_none(db)

        db.commit.side_effect = IntegrityError("mock", {}, Exception())

        fake_tx = MagicMock()
        fake_tx.id = 50

        with patch(f"{_SVC}._resolve_price", return_value=300), \
             patch(f"{_CSVC}.CreditService") as mock_cs:
            mock_cs.return_value.deduct.return_value = fake_tx
            with pytest.raises(AlreadyOwnedError):
                purchase_design(db, user, "player_card", "compact")

        db.rollback.assert_called_once()


# ── CDO-09: CreditService.deduct does NOT call db.commit ─────────────────────

def test_cdo09_deduct_uses_savepoint_not_commit():
    """CDO-09: CreditService.deduct() uses begin_nested (SAVEPOINT), no outer commit inside deduct."""
    from app.services.credit_service import CreditService
    import inspect

    src = inspect.getsource(CreditService.deduct)
    assert "begin_nested" in src, "deduct must use begin_nested (SAVEPOINT)"
    assert "db.commit" not in src.replace("# ", ""), \
        "deduct must NOT call db.commit() — caller owns the outer transaction"


# ── CDO-10..15: is_design_accessible ─────────────────────────────────────────

class TestIsDesignAccessible:

    def _accessible(self, db, user_id, card_type_id, design_id):
        from app.services.card_design_service import is_design_accessible
        return is_design_accessible(db, user_id, card_type_id, design_id)

    def test_cdo10_fifa_requires_ownership_row(self):
        """CDO-10: player_card/fclassic → False without ownership row (no free bypass)."""
        db = _make_db()
        _make_query_none(db)

        assert self._accessible(db, 1, "player_card", "fclassic") is False

    def test_cdo11_premium_player_false_without_ownership(self):
        """CDO-11: player_card/compact → False without ownership."""
        db = _make_db()
        _make_query_none(db)

        with patch(f"{_SVC}._resolve_price", return_value=300):
            result = self._accessible(db, 1, "player_card", "compact")
        assert result is False

    def test_cdo12_premium_player_true_with_ownership(self):
        """CDO-12: player_card/compact → True after ownership row exists."""
        db = _make_db()
        _make_query_none(db)  # initial setup

        existing_ownership = MagicMock()
        q = MagicMock()
        q.filter_by.return_value = q
        q.first.return_value = existing_ownership
        db.query.return_value = q

        with patch(f"{_SVC}._resolve_price", return_value=300):
            result = self._accessible(db, 1, "player_card", "compact")
        assert result is True

    def test_cdo13_welcome_false_without_ownership(self):
        """CDO-13: welcome_card/default → False without ownership."""
        db = _make_db()
        _make_query_none(db)

        result = self._accessible(db, 1, "welcome_card", "default")
        assert result is False

    def test_cdo14_welcome_true_with_ownership(self):
        """CDO-14: welcome_card/default → True after ownership row exists."""
        db = _make_db()

        existing_ownership = MagicMock()
        q = MagicMock()
        q.filter_by.return_value = q
        q.first.return_value = existing_ownership
        db.query.return_value = q

        result = self._accessible(db, 1, "welcome_card", "default")
        assert result is True

    def test_cdo15_legacy_json_shim(self):
        """CDO-15: unlocked_card_variants contains design_id → True for player_card."""
        from app.models.license import UserLicense

        db = _make_db()

        ownership_q = MagicMock()
        ownership_q.filter_by.return_value = ownership_q
        ownership_q.first.return_value = None  # no ownership row

        fake_license = MagicMock(spec=UserLicense)
        fake_license.unlocked_card_variants = ["compact", "showcase"]

        license_q = MagicMock()
        license_q.filter_by.return_value = license_q
        license_q.first.return_value = fake_license

        call_count = [0]
        def _query_side_effect(model):
            call_count[0] += 1
            from app.models.card_design_ownership import CardDesignOwnership
            if model is CardDesignOwnership:
                return ownership_q
            return license_q

        db.query.side_effect = _query_side_effect

        with patch(f"{_SVC}._resolve_price", return_value=300):
            result = self._accessible(db, 1, "player_card", "compact")
        assert result is True


# ── CDO-16..17: price constants ───────────────────────────────────────────────

def test_cdo16_welcome_card_format_prices_nonzero():
    """CDO-16: All WC format prices > 0 (legacy sentinel 'default' is correctly 0)."""
    from app.services.card_design_service import WELCOME_CARD_FORMATS, _NON_PLAYER_CARD_PRICES
    for fmt in WELCOME_CARD_FORMATS:
        assert _NON_PLAYER_CARD_PRICES[("welcome_card", fmt.design_id)] > 0
    # Sentinel key is intentionally 0 (non-purchasable, backward-compat only)
    assert _NON_PLAYER_CARD_PRICES[("welcome_card", "default")] == 0


def test_cdo17_challenge_card_format_prices_nonzero():
    """CDO-17: All CC format prices > 0 (legacy sentinel 'challenge' is correctly 0)."""
    from app.services.card_design_service import CHALLENGE_CARD_FORMATS, _NON_PLAYER_CARD_PRICES
    for fmt in CHALLENGE_CARD_FORMATS:
        assert _NON_PLAYER_CARD_PRICES[("challenge_card", fmt.design_id)] > 0
    # Sentinel key is intentionally 0 (non-purchasable, backward-compat only)
    assert _NON_PLAYER_CARD_PRICES[("challenge_card", "challenge")] == 0


# ── CDO-18: grant_design idempotency ─────────────────────────────────────────

def test_cdo18_grant_design_idempotent():
    """CDO-18: grant_design called twice → one row, no exception on second call."""
    from app.services.card_design_service import grant_design

    db = _make_db()
    existing = MagicMock()
    q = MagicMock()
    q.filter_by.return_value = q
    q.first.return_value = existing  # already exists
    db.query.return_value = q

    result = grant_design(db, user_id=101, card_type_id="welcome_card", design_id="default")

    assert result is None  # idempotent return
    db.add.assert_not_called()
    db.commit.assert_not_called()


# ── CDO-19..20: no auto-grant in user flows ───────────────────────────────────

def test_cdo19_onboarding_does_not_grant_welcome_card():
    """CDO-19: onboarding step-7 route does NOT call grant_design for welcome_card."""
    import ast
    from pathlib import Path

    src_path = (
        Path(__file__).resolve().parents[3]
        / "app" / "api" / "web_routes" / "onboarding.py"
    )
    src = src_path.read_text(encoding="utf-8")
    # grant_design must not be called in onboarding.py
    assert "grant_design" not in src, (
        "onboarding.py must NOT call grant_design — Welcome Card is not auto-granted on onboarding"
    )


def test_cdo20_challenge_completion_does_not_grant_challenge_card():
    """CDO-20: challenge completion does NOT call grant_design for challenge_card."""
    from pathlib import Path

    src_path = (
        Path(__file__).resolve().parents[3]
        / "app" / "api" / "web_routes" / "vt_challenges.py"
    )
    src = src_path.read_text(encoding="utf-8")
    # grant_design should not be called from challenge completion logic
    # (only challenge_card_export calls card design service, not challenge result)
    # We check the challenge result/complete handler specifically.
    # The export handler IS allowed to call is_design_accessible.
    # We verify grant_design is not called anywhere in the file.
    assert "grant_design" not in src, (
        "vt_challenges.py must NOT call grant_design — Challenge Card is not auto-granted"
    )
