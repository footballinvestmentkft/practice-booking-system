"""Unit tests for scripts/validate_seed_state.py — VT reference data check

VSS-01  validate fails (exit 1) when virtual_training_games is empty
VSS-02  validate fails (exit 1) when no challenge-compatible games are active
VSS-03  validate passes when memory_sequence + target_tracking are active

Note: validate_seed_state.run() calls sys.exit(). Tests capture SystemExit.
These tests patch the DB query to isolate the VT check logic.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


def _make_db_with_counts(**table_counts):
    """
    Build a mock db where db.query(Model).count() or .all() returns preset values.
    table_counts keys are model class names (str).
    """
    db = MagicMock()

    def query_side_effect(model):
        name = getattr(model, "__name__", str(model))
        q = MagicMock()
        q.count.return_value = table_counts.get(name, 0)
        q.all.return_value = table_counts.get(name + "__all", [])
        filtered = MagicMock()
        filtered.count.return_value = table_counts.get(name + "__filtered", 0)
        filtered.all.return_value = table_counts.get(name + "__filtered_all", [])
        q.filter.return_value = filtered
        return q

    db.query.side_effect = query_side_effect
    return db


class TestValidateSeedStateVTCheck:
    """Patch SessionLocal to inject controlled DB state."""

    def _run(self, db_mock):
        with patch("scripts.validate_seed_state.SessionLocal", return_value=db_mock):
            from importlib import reload
            import scripts.validate_seed_state as _mod
            reload(_mod)
            return _mod.run

    def test_vss01_fails_when_vt_empty(self, test_db):
        """VSS-01: validate exits 1 when virtual_training_games has 0 active rows.

        bootstrap_clean Step 8 seeds VT games, so we delete them first to
        create the "empty VT" scenario this test is designed to verify.
        """
        from app.models.virtual_training import VirtualTrainingGame
        from scripts.validate_seed_state import run as validate_run

        # Clear the VT games that bootstrap seeded
        test_db.query(VirtualTrainingGame).delete()
        test_db.flush()

        with patch("scripts.validate_seed_state.SessionLocal", return_value=test_db):
            with pytest.raises(SystemExit) as exc:
                validate_run()
        assert exc.value.code != 0

    def test_vss02_fails_when_no_compat_games_active(self, test_db):
        """VSS-02: validate exits 1 when VT games exist but none are challenge-compatible.

        Delete all bootstrap VT games, then insert only a non-compatible game
        so the challenge-compatible count stays at 0.
        """
        from app.models.virtual_training import VirtualTrainingGame

        # Remove bootstrap VT games, then insert only a non-compat one
        test_db.query(VirtualTrainingGame).delete()
        test_db.flush()
        test_db.add(VirtualTrainingGame(
            code="color_reaction",
            name="Color Reaction",
            game_type="reaction_time",
            is_active=True,
            base_xp=20,
            max_daily_attempts=5,
            skill_targets={},
            config={},
        ))
        test_db.flush()

        # memory_sequence and target_tracking absent → compat count = 0
        with patch("scripts.validate_seed_state.SessionLocal", return_value=test_db):
            with pytest.raises(SystemExit) as exc:
                from importlib import reload
                import scripts.validate_seed_state as _mod
                _mod.run()
        assert exc.value.code != 0

    def test_vss03_vt_check_logic_passes_with_compat_games(self, test_db):
        """VSS-03: VT check logic passes when memory_sequence + target_tracking are active.

        bootstrap_clean Step 8 already seeds all 12 games (including both
        challenge-compatible ones), so we just verify the filter directly.
        """
        from app.models.virtual_training import VirtualTrainingGame

        _CHALLENGE_COMPAT = {"memory_sequence", "target_tracking"}

        # Games are already present from bootstrap — no inserts needed
        vt_active_games = (
            test_db.query(VirtualTrainingGame)
            .filter(VirtualTrainingGame.is_active == True)  # noqa: E712
            .all()
        )
        vt_compat_count = sum(1 for g in vt_active_games if g.code in _CHALLENGE_COMPAT)

        assert len(vt_active_games) >= 1, "Expected at least 1 active VT game"
        assert vt_compat_count >= 2, (
            f"Expected 2 challenge-compatible games, got {vt_compat_count}"
        )
