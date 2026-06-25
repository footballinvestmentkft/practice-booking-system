"""
Sprint 29 — app/background/scheduler.py
=========================================
Target: ≥80% statement, ≥65% branch

Covers:
  sync_all_users_job    — no issues (early return), issues found + synced, partial failure, exception
  _log_job_result       — writes JSON file
  job_listener          — event with/without exception
  system_events_purge_job — success + exception
  start_scheduler       — already running + fresh start
  stop_scheduler        — not running + running
  run_sync_job_now      — calls sync_all_users_job
  run_purge_now         — success + exception
  get_scheduler_status  — None + running
"""

import pytest
from unittest.mock import MagicMock, patch, call
from pathlib import Path
import json
import tempfile
import os


_BASE = "app.background.scheduler"


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_sync_service(issues=None, synced=2, failed=0):
    """Return a mock ProgressLicenseSyncService."""
    svc = MagicMock()
    svc.find_desync_issues.return_value = issues if issues is not None else []
    svc.auto_sync_all.return_value = {
        "synced_count": synced,
        "failed_count": failed,
        "results": [],
    }
    return svc


# ============================================================================
# sync_all_users_job
# ============================================================================

class TestSyncAllUsersJob:

    @patch(f"{_BASE}.SessionLocal")
    @patch(f"{_BASE}.ProgressLicenseSyncService")
    @patch(f"{_BASE}._log_job_result")
    def test_no_issues_early_return(self, mock_log, MockSync, MockSession):
        """SAJ-01: no desync issues → early return, log 'No desync issues'."""
        from app.background.scheduler import sync_all_users_job

        mock_db = MagicMock()
        MockSession.return_value = mock_db
        MockSync.return_value = _make_sync_service(issues=[])

        sync_all_users_job()

        MockSync.return_value.auto_sync_all.assert_not_called()
        mock_log.assert_called_once()
        log_result = mock_log.call_args[0][1]
        assert log_result["status"] == "success"
        assert log_result["issues_found"] == 0
        mock_db.close.assert_called_once()

    @patch(f"{_BASE}.SessionLocal")
    @patch(f"{_BASE}.ProgressLicenseSyncService")
    @patch(f"{_BASE}._log_job_result")
    def test_all_synced_success(self, mock_log, MockSync, MockSession):
        """SAJ-02: issues found, all synced → status 'success' in log."""
        from app.background.scheduler import sync_all_users_job

        mock_db = MagicMock()
        MockSession.return_value = mock_db
        issue = MagicMock()
        MockSync.return_value = _make_sync_service(issues=[issue], synced=1, failed=0)

        sync_all_users_job()

        MockSync.return_value.auto_sync_all.assert_called_once_with(
            sync_direction="progress_to_license", dry_run=False
        )
        mock_log.assert_called_once()
        log_result = mock_log.call_args[0][1]
        assert log_result["status"] == "success"
        assert log_result["synced_count"] == 1
        mock_db.close.assert_called_once()

    @patch(f"{_BASE}.SessionLocal")
    @patch(f"{_BASE}.ProgressLicenseSyncService")
    @patch(f"{_BASE}._log_job_result")
    def test_partial_failure_logged(self, mock_log, MockSync, MockSession):
        """SAJ-03: some failures → status 'partial_failure' in log."""
        from app.background.scheduler import sync_all_users_job

        mock_db = MagicMock()
        MockSession.return_value = mock_db
        issues = [MagicMock(), MagicMock()]
        MockSync.return_value = _make_sync_service(issues=issues, synced=1, failed=1)

        sync_all_users_job()

        mock_log.assert_called_once()
        log_result = mock_log.call_args[0][1]
        assert log_result["status"] == "partial_failure"
        assert log_result["failed_count"] == 1

    @patch(f"{_BASE}.SessionLocal")
    @patch(f"{_BASE}.ProgressLicenseSyncService")
    @patch(f"{_BASE}._log_job_result")
    def test_exception_logged_and_reraised(self, mock_log, MockSync, MockSession):
        """SAJ-04: exception in sync → logs error status, re-raises, db.close called."""
        from app.background.scheduler import sync_all_users_job

        mock_db = MagicMock()
        MockSession.return_value = mock_db
        MockSync.return_value.find_desync_issues.side_effect = RuntimeError("db gone")

        with pytest.raises(RuntimeError, match="db gone"):
            sync_all_users_job()

        mock_log.assert_called_once()
        log_result = mock_log.call_args[0][1]
        assert log_result["status"] == "error"
        assert "db gone" in log_result["error"]
        mock_db.close.assert_called_once()


# ============================================================================
# _log_job_result
# ============================================================================

class TestLogJobResult:

    def test_writes_valid_json(self):
        """LJR-01: writes valid JSON to provided path."""
        from app.background.scheduler import _log_job_result

        with tempfile.TemporaryDirectory() as tmpdir:
            log_file = Path(tmpdir) / "test_result.json"
            data = {"status": "success", "count": 3}
            _log_job_result(log_file, data)

            assert log_file.exists()
            with open(log_file) as f:
                loaded = json.load(f)
            assert loaded["status"] == "success"
            assert loaded["count"] == 3


# ============================================================================
# job_listener
# ============================================================================

class TestJobListener:

    def test_no_exception_logs_success(self, caplog):
        """JL-01: event without exception → logs 'executed successfully'."""
        import logging
        from app.background.scheduler import job_listener

        event = MagicMock()
        event.exception = None
        event.job_id = "test_job"

        with caplog.at_level(logging.INFO, logger="app.background.scheduler"):
            job_listener(event)

        assert any("executed successfully" in m for m in caplog.messages)

    def test_exception_logs_failure(self, caplog):
        """JL-02: event with exception → logs error."""
        import logging
        from app.background.scheduler import job_listener

        event = MagicMock()
        event.exception = ValueError("boom")
        event.job_id = "failing_job"

        with caplog.at_level(logging.ERROR, logger="app.background.scheduler"):
            job_listener(event)

        assert any("failed" in m or "Retry" in m for m in caplog.messages)


# ============================================================================
# system_events_purge_job
# ============================================================================

class TestSystemEventsPurgeJob:

    @patch(f"{_BASE}.SessionLocal")
    def test_success_commits_and_closes(self, MockSession):
        """SEP-01: purge succeeds → db.commit() + db.close()."""
        from app.background.scheduler import system_events_purge_job

        mock_db = MagicMock()
        MockSession.return_value = mock_db

        mock_svc = MagicMock()
        mock_svc.purge_old_events.return_value = 5

        with patch(f"{_BASE}.SystemEventService", create=True) as _cls:
            # system_events_purge_job does lazy import inside function body
            pass

        # Patch via sys.modules for the lazy import
        import sys
        mock_module = MagicMock()
        mock_module.SystemEventService.return_value = mock_svc

        with patch.dict("sys.modules", {"app.services.system_event_service": mock_module}):
            system_events_purge_job()

        mock_db.commit.assert_called_once()
        mock_db.close.assert_called_once()

    @patch(f"{_BASE}.SessionLocal")
    def test_exception_rolls_back_and_closes(self, MockSession):
        """SEP-02: purge raises → db.rollback() + db.close()."""
        from app.background.scheduler import system_events_purge_job

        mock_db = MagicMock()
        MockSession.return_value = mock_db

        mock_svc = MagicMock()
        mock_svc.purge_old_events.side_effect = RuntimeError("table missing")

        import sys
        mock_module = MagicMock()
        mock_module.SystemEventService.return_value = mock_svc

        with patch.dict("sys.modules", {"app.services.system_event_service": mock_module}):
            system_events_purge_job()  # should NOT raise

        mock_db.rollback.assert_called_once()
        mock_db.close.assert_called_once()


# ============================================================================
# start_scheduler / stop_scheduler
# ============================================================================

class TestStartStopScheduler:

    def test_start_already_running_returns_early(self):
        """SSS-01: scheduler already set → returns without creating new one."""
        import app.background.scheduler as sched_mod

        original = sched_mod.scheduler
        try:
            fake_sched = MagicMock()
            sched_mod.scheduler = fake_sched

            result = sched_mod.start_scheduler()
            assert result is None  # early return

        finally:
            sched_mod.scheduler = original

    def test_start_creates_and_starts_scheduler(self):
        """SSS-02: fresh start → BackgroundScheduler created, jobs added, started."""
        import app.background.scheduler as sched_mod

        original = sched_mod.scheduler
        try:
            sched_mod.scheduler = None

            mock_sched = MagicMock()
            mock_sched.get_jobs.return_value = []

            with patch(f"{_BASE}.BackgroundScheduler", return_value=mock_sched):
                with patch(f"{_BASE}.IntervalTrigger"):
                    with patch(f"{_BASE}.CronTrigger"):
                        result = sched_mod.start_scheduler()

            mock_sched.start.assert_called_once()
            assert mock_sched.add_job.call_count == 5  # sync + health + purge + auto_checkin_open + mc1_stopping_timeout
            assert result is mock_sched

        finally:
            sched_mod.scheduler = original

    def test_stop_not_running_returns_early(self):
        """SSS-03: scheduler is None → returns early, no error."""
        import app.background.scheduler as sched_mod

        original = sched_mod.scheduler
        try:
            sched_mod.scheduler = None
            sched_mod.stop_scheduler()  # should not raise
        finally:
            sched_mod.scheduler = original

    def test_stop_running_shuts_down(self):
        """SSS-04: scheduler running → shutdown(wait=True), set to None."""
        import app.background.scheduler as sched_mod

        original = sched_mod.scheduler
        try:
            fake = MagicMock()
            sched_mod.scheduler = fake

            sched_mod.stop_scheduler()

            fake.shutdown.assert_called_once_with(wait=True)
            assert sched_mod.scheduler is None

        finally:
            sched_mod.scheduler = original


# ============================================================================
# auto_checkin_open_job
# ============================================================================


class TestAutoCheckinOpenJob:
    """ACO-01..02: auto_checkin_open_job transitions/skips based on checkin_opens_at."""

    @patch(f"{_BASE}.SessionLocal")
    @patch("app.api.api_v1.endpoints.tournaments.lifecycle.record_status_change")
    def test_ACO_01_past_checkin_triggers_transition(self, mock_record, MockSession):
        """ACO-01: tournament with past checkin_opens_at → CHECK_IN_OPEN, commit called."""
        from app.background.scheduler import auto_checkin_open_job

        mock_db = MagicMock()
        MockSession.return_value = mock_db

        mock_tournament = MagicMock()
        mock_tournament.id = 1
        mock_tournament.name = "Test Tournament"
        mock_tournament.tournament_status = "ENROLLMENT_CLOSED"

        mock_db.query.return_value.filter.return_value.all.return_value = [mock_tournament]

        auto_checkin_open_job()

        assert mock_tournament.tournament_status == "CHECK_IN_OPEN"
        mock_db.commit.assert_called_once()
        mock_db.close.assert_called_once()

    @patch(f"{_BASE}.SessionLocal")
    def test_ACO_02_no_ready_tournaments_no_commit(self, MockSession):
        """ACO-02: no tournaments with past checkin_opens_at → commit NOT called."""
        from app.background.scheduler import auto_checkin_open_job

        mock_db = MagicMock()
        MockSession.return_value = mock_db

        mock_db.query.return_value.filter.return_value.all.return_value = []

        auto_checkin_open_job()

        mock_db.commit.assert_not_called()
        mock_db.close.assert_called_once()


# ============================================================================
# run_sync_job_now / run_purge_now
# ============================================================================

class TestConvenienceFunctions:

    @patch(f"{_BASE}.sync_all_users_job")
    def test_run_sync_job_now_calls_sync(self, mock_sync):
        """RSJ-01: run_sync_job_now calls sync_all_users_job."""
        from app.background.scheduler import run_sync_job_now
        run_sync_job_now()
        mock_sync.assert_called_once()

    @patch(f"{_BASE}.SessionLocal")
    def test_run_purge_now_success_returns_count(self, MockSession):
        """RPN-01: purge succeeds → returns deleted count."""
        from app.background.scheduler import run_purge_now

        mock_db = MagicMock()
        MockSession.return_value = mock_db

        mock_svc = MagicMock()
        mock_svc.purge_old_events.return_value = 7

        import sys
        mock_module = MagicMock()
        mock_module.SystemEventService.return_value = mock_svc

        with patch.dict("sys.modules", {"app.services.system_event_service": mock_module}):
            deleted = run_purge_now()

        assert deleted == 7
        mock_db.commit.assert_called_once()
        mock_db.close.assert_called_once()

    @patch(f"{_BASE}.SessionLocal")
    def test_run_purge_now_exception_returns_zero(self, MockSession):
        """RPN-02: purge raises → rollback, returns 0."""
        from app.background.scheduler import run_purge_now

        mock_db = MagicMock()
        MockSession.return_value = mock_db

        mock_svc = MagicMock()
        mock_svc.purge_old_events.side_effect = RuntimeError("gone")

        import sys
        mock_module = MagicMock()
        mock_module.SystemEventService.return_value = mock_svc

        with patch.dict("sys.modules", {"app.services.system_event_service": mock_module}):
            deleted = run_purge_now()

        assert deleted == 0
        mock_db.rollback.assert_called_once()
        mock_db.close.assert_called_once()


# ============================================================================
# get_scheduler_status
# ============================================================================

class TestGetSchedulerStatus:

    def test_not_running_returns_false(self):
        """GSS-01: scheduler is None → running=False, jobs=[]."""
        import app.background.scheduler as sched_mod

        original = sched_mod.scheduler
        try:
            sched_mod.scheduler = None
            result = sched_mod.get_scheduler_status()
            assert result == {"running": False, "jobs": []}
        finally:
            sched_mod.scheduler = original

    def test_running_returns_job_list(self):
        """GSS-02: scheduler running → running=True, jobs populated."""
        import app.background.scheduler as sched_mod

        original = sched_mod.scheduler
        try:
            mock_job = MagicMock()
            mock_job.id = "sync_job"
            mock_job.name = "Sync"
            mock_job.next_run_time = None
            mock_job.misfire_grace_time = 300

            fake_sched = MagicMock()
            fake_sched.get_jobs.return_value = [mock_job]
            sched_mod.scheduler = fake_sched

            result = sched_mod.get_scheduler_status()
            assert result["running"] is True
            assert len(result["jobs"]) == 1
            assert result["jobs"][0]["id"] == "sync_job"
            assert result["jobs"][0]["next_run_utc"] is None

        finally:
            sched_mod.scheduler = original

    def test_running_with_next_run_time(self):
        """GSS-03: job has next_run_time → isoformat() called."""
        import app.background.scheduler as sched_mod
        from datetime import datetime

        original = sched_mod.scheduler
        try:
            t = datetime(2026, 3, 10, 2, 0, 0)
            mock_job = MagicMock()
            mock_job.id = "purge"
            mock_job.name = "Purge"
            mock_job.next_run_time = t
            mock_job.misfire_grace_time = 3600

            fake_sched = MagicMock()
            fake_sched.get_jobs.return_value = [mock_job]
            sched_mod.scheduler = fake_sched

            result = sched_mod.get_scheduler_status()
            assert "2026-03-10" in result["jobs"][0]["next_run_utc"]

        finally:
            sched_mod.scheduler = original
