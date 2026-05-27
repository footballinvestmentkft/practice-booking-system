"""Unit tests for scripts/seed_virtual_training_games.py

SVT-01  _GAMES defines exactly 12 game presets
SVT-02  All game codes are unique in _GAMES
SVT-03  memory_sequence is defined with is_active=True
SVT-04  target_tracking is defined with is_active=True
SVT-05  color_reaction is defined with is_active=True
SVT-06  stroop_challenge is defined with is_active=False
SVT-07  _UPDATE_FIELDS includes is_active (seed overrides admin toggles intentionally)
SVT-08  _UPDATE_FIELDS includes config, name, skill_targets, base_xp, description
SVT-09  All _CHALLENGE_COMPATIBLE codes exist in _GAMES and are active
SVT-10  DB seed: first run inserts all 12 games
SVT-11  DB seed: second run (idempotent) — count unchanged, no duplicates
SVT-12  DB seed: idempotent UPDATE preserves existing row data with new values from _GAMES
"""
from __future__ import annotations

from unittest.mock import MagicMock


# ── Data-definition tests (no DB) ────────────────────────────────────────────

class TestSeedGameDefinitions:

    def setup_method(self):
        from scripts.seed_virtual_training_games import _GAMES, _UPDATE_FIELDS
        self._games = _GAMES
        self._update_fields = _UPDATE_FIELDS

    def test_svt01_game_count(self):
        """SVT-01: 12 game definitions in _GAMES."""
        assert len(self._games) == 12

    def test_svt02_codes_unique(self):
        """SVT-02: All game codes are unique."""
        codes = [g["code"] for g in self._games]
        assert len(codes) == len(set(codes))

    def test_svt03_memory_sequence_active(self):
        """SVT-03: memory_sequence is_active=True."""
        game = next(g for g in self._games if g["code"] == "memory_sequence")
        assert game["is_active"] is True

    def test_svt04_target_tracking_active(self):
        """SVT-04: target_tracking is_active=True."""
        game = next(g for g in self._games if g["code"] == "target_tracking")
        assert game["is_active"] is True

    def test_svt05_color_reaction_active(self):
        """SVT-05: color_reaction is_active=True."""
        game = next(g for g in self._games if g["code"] == "color_reaction")
        assert game["is_active"] is True

    def test_svt06_stroop_inactive(self):
        """SVT-06: stroop_challenge is_active=False."""
        game = next(g for g in self._games if g["code"] == "stroop_challenge")
        assert game["is_active"] is False

    def test_svt07_update_fields_includes_is_active(self):
        """SVT-07: _UPDATE_FIELDS includes is_active."""
        assert "is_active" in self._update_fields

    def test_svt08_update_fields_complete(self):
        """SVT-08: _UPDATE_FIELDS covers all mutable fields."""
        for field in ("config", "name", "skill_targets", "base_xp", "description"):
            assert field in self._update_fields, f"Missing field in _UPDATE_FIELDS: {field}"

    def test_svt09_challenge_compatible_games_all_active(self):
        """SVT-09: CHALLENGE_COMPATIBLE_GAMES codes exist in _GAMES and are active."""
        from app.models.vt_challenge import CHALLENGE_COMPATIBLE_GAMES
        game_map = {g["code"]: g for g in self._games}
        for code in CHALLENGE_COMPATIBLE_GAMES:
            assert code in game_map, f"Challenge-compatible game '{code}' missing from _GAMES"
            assert game_map[code]["is_active"] is True, f"Game '{code}' must be active"


# ── DB logic tests (test_db fixture + mocked SessionLocal) ───────────────────

def _wrap_no_close(test_db):
    """Wrap test_db so .close() is a no-op (preserves SAVEPOINT isolation)."""
    wrapper = MagicMock(wraps=test_db)
    wrapper.close = MagicMock()
    return wrapper


class TestSeedVTGamesDB:

    def _run_seed(self, db_wrapper):
        with __import__("unittest.mock", fromlist=["patch"]).patch(
            "scripts.seed_virtual_training_games.SessionLocal",
            return_value=db_wrapper,
        ):
            from scripts.seed_virtual_training_games import seed_virtual_training_games
            seed_virtual_training_games()

    def test_svt10_first_run_inserts_all_games(self, test_db):
        """SVT-10: First seed inserts all 12 game rows."""
        from app.models.virtual_training import VirtualTrainingGame
        from scripts.seed_virtual_training_games import _GAMES

        self._run_seed(_wrap_no_close(test_db))

        count = test_db.query(VirtualTrainingGame).count()
        assert count == len(_GAMES)

    def test_svt11_second_run_idempotent(self, test_db):
        """SVT-11: Second seed call produces same count, no duplicates."""
        from app.models.virtual_training import VirtualTrainingGame

        self._run_seed(_wrap_no_close(test_db))
        count_after_first = test_db.query(VirtualTrainingGame).count()

        self._run_seed(_wrap_no_close(test_db))
        count_after_second = test_db.query(VirtualTrainingGame).count()

        assert count_after_first == count_after_second

    def test_svt12_seed_updates_existing_name(self, test_db):
        """SVT-12: Re-running seed overwrites name field of existing rows."""
        from app.models.virtual_training import VirtualTrainingGame

        # First seed
        self._run_seed(_wrap_no_close(test_db))

        # Manually corrupt the name
        game = test_db.query(VirtualTrainingGame).filter_by(code="memory_sequence").first()
        game.name = "CORRUPTED_NAME"
        test_db.flush()

        # Second seed — should restore correct name
        self._run_seed(_wrap_no_close(test_db))
        game = test_db.query(VirtualTrainingGame).filter_by(code="memory_sequence").first()
        assert game.name != "CORRUPTED_NAME"
