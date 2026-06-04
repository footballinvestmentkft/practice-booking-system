"""VT-ELG-01..17 — VT Card eligibility service tests.

Verifies check_single_game_eligibility() and check_reward_eligibility() in
isolation using mock DB objects.  The actual JSONB query is not exercised
here (no PostgreSQL) — only the business-logic layer around the count.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.services.vt_card_eligibility import (
    REWARD_TIERS,
    check_reward_eligibility,
    check_single_game_eligibility,
)

_MODULE = "app.services.vt_card_eligibility"
_TODAY = date(2026, 6, 4)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_game(game_id: int = 1, max_daily: int = 5, is_active: bool = True) -> MagicMock:
    g = MagicMock()
    g.id = game_id
    g.is_active = is_active
    g.max_daily_attempts = max_daily
    return g


def _make_db_returning_game(game: MagicMock | None, standalone_count: int = 0) -> MagicMock:
    """Mock db where query().filter().first() returns game and query().filter().count() returns count."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = game
    db.query.return_value.filter.return_value.count.return_value = standalone_count
    return db


def _make_db_no_game() -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.count.return_value = 0
    return db


# ── VT-ELG-01..06: check_single_game_eligibility ─────────────────────────────

class TestSingleGameEligibility:
    def test_elg01_zero_attempts_not_eligible(self):
        game = _make_game(max_daily=5)
        with patch(f"{_MODULE}._standalone_count", return_value=0), \
             patch(f"{_MODULE}.VirtualTrainingGame"):
            db = _make_db_returning_game(game, 0)
            eligible, count, required = check_single_game_eligibility(db, 1, 1, _TODAY)
        assert eligible is False
        assert count == 0
        assert required == 5

    def test_elg02_partial_attempts_not_eligible(self):
        game = _make_game(max_daily=5)
        with patch(f"{_MODULE}._standalone_count", return_value=4), \
             patch(f"{_MODULE}.VirtualTrainingGame"):
            db = _make_db_returning_game(game, 4)
            eligible, count, required = check_single_game_eligibility(db, 1, 1, _TODAY)
        assert eligible is False
        assert count == 4
        assert required == 5

    def test_elg03_exactly_max_attempts_eligible(self):
        game = _make_game(max_daily=5)
        with patch(f"{_MODULE}._standalone_count", return_value=5), \
             patch(f"{_MODULE}.VirtualTrainingGame"):
            db = _make_db_returning_game(game, 5)
            eligible, count, required = check_single_game_eligibility(db, 1, 1, _TODAY)
        assert eligible is True
        assert count == 5
        assert required == 5

    def test_elg04_over_max_attempts_still_eligible(self):
        game = _make_game(max_daily=5)
        with patch(f"{_MODULE}._standalone_count", return_value=7), \
             patch(f"{_MODULE}.VirtualTrainingGame"):
            db = _make_db_returning_game(game, 7)
            eligible, count, required = check_single_game_eligibility(db, 1, 1, _TODAY)
        assert eligible is True
        assert count == 7

    def test_elg05_inactive_game_returns_false(self):
        db = _make_db_no_game()
        eligible, count, required = check_single_game_eligibility(db, 1, 999, _TODAY)
        assert eligible is False
        assert count == 0
        assert required == 0

    def test_elg06_required_count_matches_game_max_daily(self):
        game = _make_game(max_daily=3)
        with patch(f"{_MODULE}._standalone_count", return_value=3), \
             patch(f"{_MODULE}.VirtualTrainingGame"):
            db = _make_db_returning_game(game, 3)
            _, count, required = check_single_game_eligibility(db, 1, 1, _TODAY)
        assert required == 3
        assert count == 3

    def test_elg07_uses_today_when_date_not_provided(self):
        # check_single_game_eligibility with day=None should not raise
        db = _make_db_no_game()
        eligible, count, required = check_single_game_eligibility(db, 1, 1, day=None)
        assert eligible is False


# ── VT-ELG-08..14: check_reward_eligibility ──────────────────────────────────

class TestRewardEligibility:
    def _make_db_with_games(self, games: list[MagicMock], active_count: int | None = None) -> MagicMock:
        db = MagicMock()
        # query().filter().all() → games list
        # query().filter().count() → active_count (for tier-10 gate)
        db.query.return_value.filter.return_value.all.return_value = games
        db.query.return_value.filter.return_value.count.return_value = (
            active_count if active_count is not None else len(games)
        )
        return db

    def test_elg08_zero_completed_not_eligible_for_tier3(self):
        games = [_make_game(i, max_daily=5) for i in range(1, 6)]
        db = self._make_db_with_games(games)
        with patch(f"{_MODULE}._standalone_count", return_value=0):
            eligible, completed = check_reward_eligibility(db, 1, 3, _TODAY)
        assert eligible is False
        assert completed == 0

    def test_elg09_three_completed_eligible_for_tier3(self):
        games = [_make_game(i, max_daily=5) for i in range(1, 6)]
        db = self._make_db_with_games(games)
        # First 3 games completed (count=5), last 2 not (count=2)
        counts = {g.id: (5 if i < 3 else 2) for i, g in enumerate(games)}
        with patch(f"{_MODULE}._standalone_count", side_effect=lambda db, uid, gid, day: counts[gid]):
            eligible, completed = check_reward_eligibility(db, 1, 3, _TODAY)
        assert eligible is True
        assert completed == 3

    def test_elg10_three_completed_not_eligible_for_tier5(self):
        games = [_make_game(i, max_daily=5) for i in range(1, 6)]
        db = self._make_db_with_games(games)
        counts = {g.id: (5 if i < 3 else 2) for i, g in enumerate(games)}
        with patch(f"{_MODULE}._standalone_count", side_effect=lambda db, uid, gid, day: counts[gid]):
            eligible, completed = check_reward_eligibility(db, 1, 5, _TODAY)
        assert eligible is False
        assert completed == 3

    def test_elg11_five_completed_eligible_for_tier5(self):
        games = [_make_game(i, max_daily=5) for i in range(1, 6)]
        db = self._make_db_with_games(games)
        with patch(f"{_MODULE}._standalone_count", return_value=5):
            eligible, completed = check_reward_eligibility(db, 1, 5, _TODAY)
        assert eligible is True
        assert completed == 5

    def test_elg12_tier10_disabled_when_fewer_than_10_active_games(self):
        # Only 5 active games → tier 10 always ineligible
        games = [_make_game(i) for i in range(1, 6)]
        db = self._make_db_with_games(games, active_count=5)
        with patch(f"{_MODULE}._standalone_count", return_value=5):
            eligible, completed = check_reward_eligibility(db, 1, 10, _TODAY)
        assert eligible is False
        assert completed == 0  # early return, no game scan

    def test_elg13_tier10_enabled_when_10_or_more_active_games(self):
        games = [_make_game(i) for i in range(1, 11)]
        db = self._make_db_with_games(games, active_count=10)
        with patch(f"{_MODULE}._standalone_count", return_value=5):
            eligible, completed = check_reward_eligibility(db, 1, 10, _TODAY)
        assert eligible is True
        assert completed == 10

    def test_elg14_invalid_tier_raises_value_error(self):
        db = MagicMock()
        with pytest.raises(ValueError, match="Unknown reward tier"):
            check_reward_eligibility(db, 1, 7, _TODAY)

    def test_elg15_uses_today_when_date_not_provided(self):
        games = [_make_game(1)]
        db = self._make_db_with_games(games, active_count=1)
        with patch(f"{_MODULE}._standalone_count", return_value=5):
            eligible, _ = check_reward_eligibility(db, 1, 3, day=None)
        # 1 completed game < tier 3 → not eligible
        assert eligible is False


# ── VT-ELG-16..17: REWARD_TIERS constant ─────────────────────────────────────

def test_elg16_reward_tiers_contains_3_5_10():
    assert set(REWARD_TIERS) == {3, 5, 10}


def test_elg17_reward_tiers_is_ordered():
    assert list(REWARD_TIERS) == sorted(REWARD_TIERS)
