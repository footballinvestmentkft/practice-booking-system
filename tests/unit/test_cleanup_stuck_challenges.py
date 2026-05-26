"""
CLEAN-01  dry-run (default) — does NOT call db.delete()
CLEAN-02  --apply flag — deletes challenge id=1 when all safety assertions pass
CLEAN-03  --apply aborts (SystemExit 2) if challenger_attempt_id is set
CLEAN-04  --apply aborts (SystemExit 2) if challenge_config_snapshot is not NULL
CLEAN-05  script refuses to run (SystemExit 1) outside development/dev environment
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

_SCRIPT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../scripts/dev_cleanup_stuck_challenges.py")
)
_APP_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))


def _load_script(argv=None):
    """Load the cleanup script as a module with optional sys.argv override."""
    original_argv = sys.argv[:]
    if argv is not None:
        sys.argv = argv
    try:
        spec = importlib.util.spec_from_file_location("_cleanup_mod", _SCRIPT_PATH)
        mod = importlib.util.module_from_spec(spec)
        # Ensure app is importable from run()
        if _APP_ROOT not in sys.path:
            sys.path.insert(0, _APP_ROOT)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.argv = original_argv


def _mock_challenge(
    challenger_attempt_id=None,
    challenged_attempt_id=None,
    challenge_config_snapshot=None,
    challenger_id=3,
    challenged_id=3617,
    game_id=6,
):
    from app.models.vt_challenge import ChallengeStatus

    ch = MagicMock()
    ch.status = ChallengeStatus.ACCEPTED
    ch.challenger_attempt_id = challenger_attempt_id
    ch.challenged_attempt_id = challenged_attempt_id
    ch.challenge_config_snapshot = challenge_config_snapshot
    ch.challenger_id = challenger_id
    ch.challenged_id = challenged_id
    ch.game_id = game_id
    return ch


def _make_mock_db(challenge_obj):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = challenge_obj
    return db


# ══════════════════════════════════════════════════════════════════════════════
# CLEAN-01  dry-run does not delete
# ══════════════════════════════════════════════════════════════════════════════

class TestClean01DryRunNoDelete:

    def test_clean01_dryrun_does_not_call_delete(self):
        mod = _load_script(argv=["script.py"])  # no --apply → DRY_RUN=True
        assert mod.DRY_RUN is True

        ch = _mock_challenge()
        mock_db = _make_mock_db(ch)

        with patch("app.database.SessionLocal", return_value=mock_db):
            mod.run()

        mock_db.delete.assert_not_called()
        mock_db.commit.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# CLEAN-02  --apply deletes the stuck challenge
# ══════════════════════════════════════════════════════════════════════════════

class TestClean02ApplyDeletes:

    def test_clean02_apply_deletes_challenge(self):
        mod = _load_script(argv=["script.py", "--apply"])
        assert mod.DRY_RUN is False

        ch = _mock_challenge()
        mock_db = _make_mock_db(ch)

        with patch("app.database.SessionLocal", return_value=mock_db):
            mod.run()

        mock_db.delete.assert_called_once_with(ch)
        mock_db.commit.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════════
# CLEAN-03  aborts if challenger_attempt_id is set
# ══════════════════════════════════════════════════════════════════════════════

class TestClean03LinkedAttemptAbort:

    def test_clean03_abort_if_challenger_attempt_linked(self):
        mod = _load_script(argv=["script.py", "--apply"])

        ch = _mock_challenge(challenger_attempt_id=99)
        mock_db = _make_mock_db(ch)

        with patch("app.database.SessionLocal", return_value=mock_db):
            with pytest.raises(SystemExit) as exc:
                mod.run()

        assert exc.value.code == 2
        mock_db.delete.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# CLEAN-04  aborts if snapshot is not NULL
# ══════════════════════════════════════════════════════════════════════════════

class TestClean04SnapshotNotNullAbort:

    def test_clean04_abort_if_snapshot_present(self):
        mod = _load_script(argv=["script.py", "--apply"])

        ch = _mock_challenge(challenge_config_snapshot={"game": "memory_sequence"})
        mock_db = _make_mock_db(ch)

        with patch("app.database.SessionLocal", return_value=mock_db):
            with pytest.raises(SystemExit) as exc:
                mod.run()

        assert exc.value.code == 2
        mock_db.delete.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# CLEAN-05  refuses to run outside development env
# ══════════════════════════════════════════════════════════════════════════════

class TestClean05NonDevRefused:

    def test_clean05_refuses_in_production(self):
        env = {**os.environ, "ENVIRONMENT": "production"}
        result = subprocess.run(
            [sys.executable, _SCRIPT_PATH],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "ABORT" in result.stdout

    def test_clean05_refuses_in_staging(self):
        env = {**os.environ, "ENVIRONMENT": "staging"}
        result = subprocess.run(
            [sys.executable, _SCRIPT_PATH],
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert "ABORT" in result.stdout
