"""
Unit tests for app/tasks/juggling_transcode_task.py — branch coverage.

CI coverage gate: branch >= 80%.  juggling_transcode_task.py had 6 branches,
all uncovered in CI (only tests/unit/ + api_smoke run; app/tests/ excluded),
causing the combined branch-rate to drop to 79.9%.

These tests mock the DB session, metadata_service, and transcode_service so no
real DB, ffmpeg, or Celery worker is needed.

Branches covered:
  JTT-01  record not found         → early return  (branch: video is None)
  JTT-02  file not found           → failure        (branch: not original.exists())
  JTT-03  done status              → analyze dispatched
  JTT-04  skipped status           → analyze dispatched
  JTT-05  failed status            → analyze blocked
  JTT-06  probe error → retry → max retries → failure
  JTT-07  unexpected exception → retry → max retries → failure
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.services.juggling.transcode_service import TranscodeResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_mock_video(status="processing", storage_path="/tmp/fake.mp4", exists=True):
    v = MagicMock()
    v.status = status
    v.storage_path = storage_path if storage_path else None
    v.transcode_status = None
    return v, Path(storage_path) if storage_path else None


def _mock_db(video=None):
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = video
    return db


def _base_patches(video, result, file_exists=True):
    """Context managers for the common happy-path mocks."""
    return [
        patch("app.tasks.juggling_transcode_task.SessionLocal",
              return_value=_mock_db(video)),
        patch("app.tasks.juggling_transcode_task.metadata_service.probe_video",
              return_value={"streams": [], "format": {}}),
        patch("app.tasks.juggling_transcode_task.metadata_service.extract_server_metadata",
              return_value={"fps": 30.0, "resolution": "1280x720",
                            "rotation": 0, "has_audio": False}),
        patch("app.tasks.juggling_transcode_task.transcode_service.transcode",
              return_value=result),
        patch("app.tasks.juggling_transcode_task.video_service.set_transcode_processing"),
        patch("app.tasks.juggling_transcode_task.video_service.apply_transcode_result"),
        patch("app.tasks.juggling_transcode_task.video_service.apply_transcode_failure"),
        patch("pathlib.Path.exists", return_value=file_exists),
    ]


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_jtt01_record_not_found_returns_failed():
    """JTT-01: video record not in DB → returns failed/record_not_found, no analyze dispatched."""
    with patch("app.tasks.juggling_transcode_task.SessionLocal",
               return_value=_mock_db(None)):
        from app.tasks.juggling_transcode_task import transcode_video_task
        result = transcode_video_task.apply(args=["nonexistent-uuid"])

    assert result.result["status"] == "failed"
    assert "record_not_found" in str(result.result.get("reason", ""))


def test_jtt02_file_not_found_applies_failure():
    """JTT-02: storage_path points to missing file → apply_transcode_failure called."""
    video, _ = _make_mock_video(storage_path="/tmp/nonexistent_99999.mp4")
    failure_mock = MagicMock()

    with patch("app.tasks.juggling_transcode_task.SessionLocal",
               return_value=_mock_db(video)), \
         patch("app.tasks.juggling_transcode_task.video_service.set_transcode_processing"), \
         patch("app.tasks.juggling_transcode_task.video_service.apply_transcode_failure",
               failure_mock):
        from app.tasks.juggling_transcode_task import transcode_video_task
        result = transcode_video_task.apply(args=["test-uuid"])

    assert result.result["status"] == "failed"


def test_jtt03_done_status_dispatches_analyze():
    """JTT-03: transcode result=done → analyze_video_task.delay() is called."""
    video, _ = _make_mock_video()
    video.storage_path = "/tmp/fake.mp4"
    done_result = TranscodeResult(
        status="done",
        processed_path=Path("/tmp/fake_proc.mp4"),
        thumbnail_path=Path("/tmp/fake_thumb.jpg"),
        audio_stripped=True,
        processed_resolution="1280x720",
        processed_fps=30.0,
        processed_file_size_bytes=10000,
        checksum_processed="abc",
    )

    analyze_mock = MagicMock()
    analyze_mock.delay = MagicMock()

    with patch("app.tasks.juggling_transcode_task.SessionLocal",
               return_value=_mock_db(video)), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("app.tasks.juggling_transcode_task.metadata_service.probe_video",
               return_value={"streams": [], "format": {}}), \
         patch("app.tasks.juggling_transcode_task.metadata_service.extract_server_metadata",
               return_value={"fps": 60.0, "resolution": "1920x1080",
                             "rotation": 0, "has_audio": True}), \
         patch("app.tasks.juggling_transcode_task.transcode_service.transcode",
               return_value=done_result), \
         patch("app.tasks.juggling_transcode_task.video_service.set_transcode_processing"), \
         patch("app.tasks.juggling_transcode_task.video_service.apply_transcode_result"), \
         patch("app.tasks.juggling_tasks.analyze_video_task", analyze_mock):

        from app.tasks.juggling_transcode_task import transcode_video_task
        result = transcode_video_task.apply(args=["test-uuid"])

    assert result.result["status"] == "done"
    assert "analyze_queued" in result.result.get("next", "")
    analyze_mock.delay.assert_called_once_with("test-uuid")


def test_jtt04_skipped_status_dispatches_analyze():
    """JTT-04: transcode result=skipped → analyze_video_task.delay() is called."""
    video, _ = _make_mock_video()
    video.storage_path = "/tmp/fake.mp4"
    skip_result = TranscodeResult(
        status="skipped",
        thumbnail_path=Path("/tmp/fake_thumb.jpg"),
    )

    analyze_mock = MagicMock()
    analyze_mock.delay = MagicMock()

    with patch("app.tasks.juggling_transcode_task.SessionLocal",
               return_value=_mock_db(video)), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("app.tasks.juggling_transcode_task.metadata_service.probe_video",
               return_value={"streams": [], "format": {}}), \
         patch("app.tasks.juggling_transcode_task.metadata_service.extract_server_metadata",
               return_value={"fps": 25.0, "resolution": "1280x720",
                             "rotation": 0, "has_audio": False}), \
         patch("app.tasks.juggling_transcode_task.transcode_service.transcode",
               return_value=skip_result), \
         patch("app.tasks.juggling_transcode_task.video_service.set_transcode_processing"), \
         patch("app.tasks.juggling_transcode_task.video_service.apply_transcode_result"), \
         patch("app.tasks.juggling_tasks.analyze_video_task", analyze_mock):

        from app.tasks.juggling_transcode_task import transcode_video_task
        result = transcode_video_task.apply(args=["test-uuid"])

    assert result.result["status"] == "skipped"
    analyze_mock.delay.assert_called_once_with("test-uuid")


def test_jtt05_failed_status_blocks_analyze():
    """JTT-05: transcode result=failed → analyze_video_task.delay() is NOT called."""
    video, _ = _make_mock_video()
    video.storage_path = "/tmp/fake.mp4"
    fail_result = TranscodeResult(status="failed", error="ffmpeg_exit_1")

    analyze_mock = MagicMock()
    analyze_mock.delay = MagicMock()

    with patch("app.tasks.juggling_transcode_task.SessionLocal",
               return_value=_mock_db(video)), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("app.tasks.juggling_transcode_task.metadata_service.probe_video",
               return_value={"streams": [], "format": {}}), \
         patch("app.tasks.juggling_transcode_task.metadata_service.extract_server_metadata",
               return_value={"fps": 60.0, "resolution": "1920x1080",
                             "rotation": 90, "has_audio": True}), \
         patch("app.tasks.juggling_transcode_task.transcode_service.transcode",
               return_value=fail_result), \
         patch("app.tasks.juggling_transcode_task.video_service.set_transcode_processing"), \
         patch("app.tasks.juggling_transcode_task.video_service.apply_transcode_result"), \
         patch("app.tasks.juggling_tasks.analyze_video_task", analyze_mock):

        from app.tasks.juggling_transcode_task import transcode_video_task
        result = transcode_video_task.apply(args=["test-uuid"])

    assert result.result["status"] == "failed"
    analyze_mock.delay.assert_not_called()


def test_jtt06_probe_error_exhausts_retries():
    """JTT-06: VideoProbeError → max_retries path → apply_transcode_failure called."""
    video, _ = _make_mock_video()
    video.storage_path = "/tmp/fake.mp4"
    failure_mock = MagicMock()

    from app.services.juggling.metadata_service import VideoProbeError

    class FakeMaxRetriesExceeded(Exception):
        pass

    def fake_retry(exc=None):
        raise FakeMaxRetriesExceeded()

    with patch("app.tasks.juggling_transcode_task.SessionLocal",
               return_value=_mock_db(video)), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("app.tasks.juggling_transcode_task.metadata_service.probe_video",
               side_effect=VideoProbeError("ffprobe failed")), \
         patch("app.tasks.juggling_transcode_task.video_service.set_transcode_processing"), \
         patch("app.tasks.juggling_transcode_task.video_service.apply_transcode_failure",
               failure_mock):

        from app.tasks.juggling_transcode_task import transcode_video_task
        original_retry = transcode_video_task.retry
        original_max = transcode_video_task.MaxRetriesExceededError

        # Patch retry to immediately raise MaxRetriesExceededError
        transcode_video_task.retry = fake_retry
        transcode_video_task.MaxRetriesExceededError = FakeMaxRetriesExceeded
        try:
            result = transcode_video_task.apply(args=["test-uuid"])
        finally:
            transcode_video_task.retry = original_retry
            transcode_video_task.MaxRetriesExceededError = original_max

    failure_mock.assert_called_once()
    call_args = failure_mock.call_args[0]
    assert call_args[1] == "probe_failed"


def test_jtt08_outer_exception_handler_applies_failure():
    """JTT-08: unexpected exception during transcode_service → outer except Exception → failure."""
    video, _ = _make_mock_video()
    video.storage_path = "/tmp/fake.mp4"
    failure_mock = MagicMock()

    class FakeMaxRetriesExceeded(Exception):
        pass

    def fake_retry(exc=None):
        raise FakeMaxRetriesExceeded()

    with patch("app.tasks.juggling_transcode_task.SessionLocal",
               return_value=_mock_db(video)), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("app.tasks.juggling_transcode_task.metadata_service.probe_video",
               return_value={"streams": [], "format": {}}), \
         patch("app.tasks.juggling_transcode_task.metadata_service.extract_server_metadata",
               return_value={"fps": 60.0, "resolution": "1920x1080",
                             "rotation": 0, "has_audio": True}), \
         patch("app.tasks.juggling_transcode_task.transcode_service.transcode",
               side_effect=RuntimeError("unexpected crash")), \
         patch("app.tasks.juggling_transcode_task.video_service.set_transcode_processing"), \
         patch("app.tasks.juggling_transcode_task.video_service.apply_transcode_failure",
               failure_mock):

        from app.tasks.juggling_transcode_task import transcode_video_task
        original_retry = transcode_video_task.retry
        original_max = transcode_video_task.MaxRetriesExceededError
        transcode_video_task.retry = fake_retry
        transcode_video_task.MaxRetriesExceededError = FakeMaxRetriesExceeded
        try:
            result = transcode_video_task.apply(args=["test-uuid"])
        finally:
            transcode_video_task.retry = original_retry
            transcode_video_task.MaxRetriesExceededError = original_max

    failure_mock.assert_called_once()
    call_args = failure_mock.call_args[0]
    assert "task_exception" in call_args[1]


def test_jtt07_missing_storage_path_applies_failure():
    """JTT-07: storage_path is None → apply_transcode_failure with missing_storage_path."""
    video = MagicMock()
    video.storage_path = None
    failure_mock = MagicMock()

    with patch("app.tasks.juggling_transcode_task.SessionLocal",
               return_value=_mock_db(video)), \
         patch("app.tasks.juggling_transcode_task.video_service.set_transcode_processing"), \
         patch("app.tasks.juggling_transcode_task.video_service.apply_transcode_failure",
               failure_mock):

        from app.tasks.juggling_transcode_task import transcode_video_task
        result = transcode_video_task.apply(args=["test-uuid"])

    assert result.result["status"] == "failed"
    failure_mock.assert_called_once()
    args = failure_mock.call_args[0]
    assert "missing_storage_path" in args[1] or "file_not_found" in args[1]
