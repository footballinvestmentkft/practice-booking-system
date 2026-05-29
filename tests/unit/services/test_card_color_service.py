"""
CS — card_color_service unit tests (TS-1).

CS-01: get_colors_for_family("player_card") → 6 colors
CS-02: player_card palette contains all expected color IDs
CS-03: get_colors_for_family("unknown_family") → empty list (no KeyError)
CS-04: get_colors_for_family("welcome_card") → empty list (TS-2 scope)
CS-05: free color is_color_unlocked → True without DB query
CS-06: premium color is_color_unlocked → True when ownership row exists
CS-07: premium color is_color_unlocked → False when no ownership row
CS-08: welcome_card gold is_color_unlocked → False even if player_card gold owned
CS-09: get_owned_color_ids returns set of owned premium color ids for user
CS-10: get_owned_color_ids returns empty set when user has no owned colors
CS-11: unlock_color valid purchase → ownership row + credit deduction
CS-12: unlock_color already owned → already_owned=True, 0 CR
CS-13: unlock_color free color → already_owned=True, 0 CR, no DB write
CS-14: unlock_color unknown color → ValueError("color_not_found")
CS-15: unlock_color unsupported family → ValueError("unsupported_family")
CS-16: unlock_color insufficient credits → InsufficientCreditsError, no DB write
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

import pytest

from app.services.card_color_service import (
    FAMILY_COLORS,
    ColorDefinition,
    get_colors_for_family,
    get_owned_color_ids,
    is_color_unlocked,
    unlock_color,
    UnlockColorResult,
)
from app.services.credit_service import InsufficientCreditsError


# ── Helpers ───────────────────────────────────────────────────────────────────

def _db_with_no_ownership():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.all.return_value = []
    return db


def _db_with_ownership(user_id: int, card_type_id: str, color_id: str):
    """Return a mock DB that returns an ownership row for the given triplet."""
    db = MagicMock()
    row = MagicMock()
    row.__getitem__ = lambda s, i: color_id if i == 0 else None

    def _filter_side(*args, **kwargs):
        m = MagicMock()
        m.first.return_value = row
        m.all.return_value = [row]
        return m

    db.query.return_value.filter.side_effect = _filter_side
    return db


def _user(credit_balance: int = 1000) -> MagicMock:
    u = MagicMock()
    u.id = 42
    u.credit_balance = credit_balance
    return u


# ── CS-01..CS-04: get_colors_for_family ───────────────────────────────────────

class TestGetColorsForFamily:

    def test_cs_01_player_card_returns_six_colors(self):
        colors = get_colors_for_family("player_card")
        assert len(colors) == 6

    def test_cs_02_player_card_contains_all_ids(self):
        ids = {c.id for c in get_colors_for_family("player_card")}
        assert ids == {"default", "midnight", "arctic", "gold", "emerald", "crimson"}

    def test_cs_02b_player_card_ordered_by_sort_order(self):
        colors = get_colors_for_family("player_card")
        orders = [c.sort_order for c in colors]
        assert orders == sorted(orders)

    def test_cs_03_unknown_family_returns_empty_list(self):
        result = get_colors_for_family("unknown_family_xyz")
        assert result == []

    def test_cs_04_welcome_card_returns_empty_list(self):
        result = get_colors_for_family("welcome_card")
        assert result == []


# ── CS-05..CS-08: is_color_unlocked ───────────────────────────────────────────

class TestIsColorUnlocked:

    def test_cs_05_free_color_always_unlocked(self):
        db = MagicMock()
        assert is_color_unlocked(db, 42, "player_card", "default") is True
        assert is_color_unlocked(db, 42, "player_card", "midnight") is True
        assert is_color_unlocked(db, 42, "player_card", "arctic") is True
        # DB was never queried for free colors
        db.query.assert_not_called()

    def test_cs_06_premium_color_owned_returns_true(self):
        db = MagicMock()
        ownership_row = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = ownership_row
        assert is_color_unlocked(db, 42, "player_card", "gold") is True

    def test_cs_07_premium_color_not_owned_returns_false(self):
        db = _db_with_no_ownership()
        assert is_color_unlocked(db, 42, "player_card", "gold") is False

    def test_cs_08_different_family_not_owned(self):
        # welcome_card family has no colors defined in TS-1 → always False for premium
        db = MagicMock()
        assert is_color_unlocked(db, 42, "welcome_card", "gold") is False
        db.query.assert_not_called()  # unknown family exits early

    def test_cs_08b_unknown_color_returns_false(self):
        db = MagicMock()
        assert is_color_unlocked(db, 42, "player_card", "nonexistent") is False


# ── CS-09..CS-10: get_owned_color_ids ─────────────────────────────────────────

class TestGetOwnedColorIds:

    def test_cs_09_returns_set_of_owned_ids(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = [
            ("gold",), ("emerald",)
        ]
        result = get_owned_color_ids(db, 42, "player_card")
        assert result == {"gold", "emerald"}

    def test_cs_10_returns_empty_set_when_no_owned(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        result = get_owned_color_ids(db, 42, "player_card")
        assert result == set()


# ── CS-11..CS-16: unlock_color ────────────────────────────────────────────────

class TestUnlockColor:

    def _no_ownership_db(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None
        return db

    def test_cs_11_valid_purchase_creates_ownership_row(self):
        db = self._no_ownership_db()
        user = _user(credit_balance=1000)

        with patch("app.services.card_color_service.CreditService") as MockCS:
            MockCS.return_value.deduct.return_value = MagicMock()
            result = unlock_color(db, user, "player_card", "gold")

        assert result.ok is True
        assert result.already_owned is False
        assert result.credits_charged == 500
        assert result.color_id == "gold"
        assert result.card_type_id == "player_card"
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_cs_12_already_owned_returns_idempotent_result(self):
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = MagicMock()
        user = _user()

        with patch("app.services.card_color_service.CreditService") as MockCS:
            result = unlock_color(db, user, "player_card", "gold")

        assert result.ok is True
        assert result.already_owned is True
        assert result.credits_charged == 0
        MockCS.return_value.deduct.assert_not_called()
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_cs_13_free_color_returns_already_owned_no_db_write(self):
        db = MagicMock()
        user = _user()

        with patch("app.services.card_color_service.CreditService") as MockCS:
            result = unlock_color(db, user, "player_card", "default")

        assert result.ok is True
        assert result.already_owned is True
        assert result.credits_charged == 0
        MockCS.return_value.deduct.assert_not_called()
        # No ownership lookup for free colors
        db.add.assert_not_called()
        db.commit.assert_not_called()

    def test_cs_14_unknown_color_raises_value_error(self):
        db = self._no_ownership_db()
        user = _user()
        with pytest.raises(ValueError, match="color_not_found"):
            unlock_color(db, user, "player_card", "nonexistent_color")

    def test_cs_15_unsupported_family_raises_value_error(self):
        db = self._no_ownership_db()
        user = _user()
        with pytest.raises(ValueError, match="unsupported_family"):
            unlock_color(db, user, "welcome_card", "gold")

    def test_cs_16_insufficient_credits_raises_error_no_db_write(self):
        db = self._no_ownership_db()
        user = _user(credit_balance=100)  # less than 500 CR

        with patch("app.services.card_color_service.CreditService") as MockCS:
            MockCS.return_value.deduct.side_effect = InsufficientCreditsError(
                required=500, available=100
            )
            with pytest.raises(InsufficientCreditsError):
                unlock_color(db, user, "player_card", "gold")

        db.add.assert_not_called()
        db.commit.assert_not_called()
