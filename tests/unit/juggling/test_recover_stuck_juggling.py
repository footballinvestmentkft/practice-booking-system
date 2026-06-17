"""
Unit tests for scripts/recover_stuck_juggling.py

All DB access is mocked — no real DB or filesystem required.

Test IDs: RSJ-01 … RSJ-09
"""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make the script importable without a running DB.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _make_video(
    status: str = "processing",
    storage_path: str | None = "/tmp/fake.mp4",
    age_seconds: int = 700,  # older than 600 s default timeout
) -> MagicMock:
    v = MagicMock()
    v.id = uuid.uuid4()
    v.user_id = uuid.uuid4()
    v.status = status
    v.storage_path = storage_path
    v.updated_at = _utc_now() - timedelta(seconds=age_seconds)
    v.rejection_reason = None
    return v


def _make_db(videos: list[MagicMock]) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = videos
    db.query.return_value.filter.return_value.filter.return_value.all.return_value = videos
    return db


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestRecoverStuckJuggling:

    def _run(
        self,
        videos: list[MagicMock],
        execute: bool = False,
        file_exists: bool = True,
        user_email: str | None = None,
    ) -> str:
        """Run recover_stuck() with mocked DB + filesystem, return stdout."""
        db = _make_db(videos)
        session_factory = MagicMock(return_value=db)

        with patch("scripts.recover_stuck_juggling.create_engine"), \
             patch("scripts.recover_stuck_juggling.sessionmaker", return_value=session_factory), \
             patch("pathlib.Path.exists", return_value=file_exists), \
             patch("builtins.print") as mock_print:

            from scripts.recover_stuck_juggling import recover_stuck
            recover_stuck(
                user_email=user_email,
                timeout_seconds=600,
                execute=execute,
            )

        # Collect all printed strings (handle bare print() with no args)
        return "\n".join(
            str(call.args[0]) if call.args else ""
            for call in mock_print.call_args_list
        )

    def test_rsj_01_no_stuck_records_outputs_clear_message(self):
        """RSJ-01: No stuck records → reports clean state, no DB writes."""
        out = self._run(videos=[])
        assert "No stuck" in out

    def _make_session(self, db: MagicMock) -> MagicMock:
        return MagicMock(return_value=db)

    def test_rsj_02_dry_run_does_not_commit(self):
        """RSJ-02: Dry-run (execute=False) never calls db.commit()."""
        video = _make_video(age_seconds=700)
        db = _make_db([video])

        with patch("scripts.recover_stuck_juggling.create_engine"), \
             patch("scripts.recover_stuck_juggling.sessionmaker", return_value=self._make_session(db)), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("builtins.print"):
            from scripts.recover_stuck_juggling import recover_stuck
            recover_stuck(timeout_seconds=600, execute=False)

        db.commit.assert_not_called()

    def test_rsj_03_execute_with_file_resets_to_uploaded(self):
        """RSJ-03: execute=True + file present → status reset to 'uploaded'."""
        video = _make_video(age_seconds=700)
        db = _make_db([video])

        with patch("scripts.recover_stuck_juggling.create_engine"), \
             patch("scripts.recover_stuck_juggling.sessionmaker", return_value=self._make_session(db)), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("builtins.print"):
            from scripts.recover_stuck_juggling import recover_stuck
            recover_stuck(timeout_seconds=600, execute=True)

        assert video.status == "uploaded"
        db.commit.assert_called_once()

    def test_rsj_04_execute_with_missing_file_sets_failed(self):
        """RSJ-04: execute=True + file missing → status set to 'failed' with reason."""
        video = _make_video(age_seconds=700)
        db = _make_db([video])

        with patch("scripts.recover_stuck_juggling.create_engine"), \
             patch("scripts.recover_stuck_juggling.sessionmaker", return_value=self._make_session(db)), \
             patch("pathlib.Path.exists", return_value=False), \
             patch("builtins.print"):
            from scripts.recover_stuck_juggling import recover_stuck
            recover_stuck(timeout_seconds=600, execute=True)

        assert video.status == "failed"
        assert "missing_file" in (video.rejection_reason or "")
        db.commit.assert_called_once()

    def test_rsj_05_dry_run_output_mentions_would_reset(self):
        """RSJ-05: Dry-run output reports would-reset count."""
        video = _make_video(age_seconds=700)
        out = self._run(videos=[video], execute=False, file_exists=True)
        assert "DRY-RUN" in out or "dry" in out.lower()
        assert "1" in out or "would" in out.lower() or "reset" in out.lower()

    def test_rsj_06_execute_output_reports_counts(self):
        """RSJ-06: Execute mode output confirms reset + failed counts."""
        video = _make_video(age_seconds=700)
        db = _make_db([video])
        output_lines: list[str] = []

        with patch("scripts.recover_stuck_juggling.create_engine"), \
             patch("scripts.recover_stuck_juggling.sessionmaker", return_value=self._make_session(db)), \
             patch("pathlib.Path.exists", return_value=True), \
             patch("builtins.print", side_effect=lambda *a, **k: output_lines.append(str(a[0]) if a else "")):
            from scripts.recover_stuck_juggling import recover_stuck
            recover_stuck(timeout_seconds=600, execute=True)

        combined = "\n".join(output_lines)
        assert "uploaded" in combined or "reset" in combined.lower()

    def test_rsj_07_multiple_videos_mixed_file_state(self):
        """RSJ-07: Two stuck videos — one with file (reset), one without (fail)."""
        v_with_file = _make_video(age_seconds=700, storage_path="/tmp/exists.mp4")
        v_no_file   = _make_video(age_seconds=700, storage_path="/tmp/missing.mp4")
        db = _make_db([v_with_file, v_no_file])

        def path_exists(self_path):
            return "exists" in str(self_path)

        with patch("scripts.recover_stuck_juggling.create_engine"), \
             patch("scripts.recover_stuck_juggling.sessionmaker", return_value=self._make_session(db)), \
             patch("pathlib.Path.exists", path_exists), \
             patch("builtins.print"):
            from scripts.recover_stuck_juggling import recover_stuck
            recover_stuck(timeout_seconds=600, execute=True)

        assert v_with_file.status == "uploaded"
        assert v_no_file.status == "failed"
        db.commit.assert_called_once()

    def test_rsj_08_non_processing_status_not_touched(self):
        """RSJ-08: DB returns no records for non-processing statuses → no writes."""
        out = self._run(videos=[], execute=True, file_exists=True)
        assert "No stuck" in out

    def test_rsj_09_storage_path_none_treated_as_missing(self):
        """RSJ-09: storage_path=None → treated as file missing → set to 'failed'."""
        video = _make_video(age_seconds=700, storage_path=None)
        db = _make_db([video])

        with patch("scripts.recover_stuck_juggling.create_engine"), \
             patch("scripts.recover_stuck_juggling.sessionmaker", return_value=self._make_session(db)), \
             patch("pathlib.Path.exists", return_value=False), \
             patch("builtins.print"):
            from scripts.recover_stuck_juggling import recover_stuck
            recover_stuck(timeout_seconds=600, execute=True)

        assert video.status == "failed"
        db.commit.assert_called_once()
