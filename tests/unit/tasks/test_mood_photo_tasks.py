"""
MP-T01..MP-T09 — unit tests for mood_photo_tasks (background removal Celery task).

All tests use MagicMock / patch — no real Celery worker, no real DB,
no rembg/onnxruntime model download required.

Phase 1 invariants tested (MP-T01..T06):
  - NullProcessor returns bytes unchanged
  - get_processor() returns NullProcessor when BG_REMOVAL_PROCESSOR="null"
  - Task writes processed file and calls apply_removal_result on success
  - Task calls apply_removal_failure when original file is missing (no retry)
  - Task calls apply_removal_failure when max retries are exhausted
  - apply_removal_failure is NOT called on a successful run

Phase 2 invariants tested (MP-T07..T09):
  - get_processor() returns RembgProcessor when BG_REMOVAL_PROCESSOR="rembg"
  - RembgProcessor.remove() calls rembg.remove(..., model="u2netp")
  - rembg.remove() exception causes the Celery task to call apply_removal_failure
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

_TASK_MOD = "app.tasks.mood_photo_tasks"


# ── MP-T01 ── NullProcessor.remove() returns input unchanged ─────────────────

def test_mp_t01_null_processor_passthrough():
    from app.services.background_removal.processor import NullProcessor

    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    assert NullProcessor().remove(data) == data


# ── MP-T02 ── get_processor() with null config → NullProcessor ────────────────

def test_mp_t02_get_processor_null_mode():
    from app.services.background_removal.processor import NullProcessor

    with patch("app.services.background_removal.settings") as mock_cfg:
        mock_cfg.BG_REMOVAL_PROCESSOR = "null"
        from app.services.background_removal import get_processor
        proc = get_processor()

    assert isinstance(proc, NullProcessor)


# ── MP-T03 ── Task success: proc file written, apply_removal_result called ────

def test_mp_t03_task_success_writes_proc_file(tmp_path):
    orig_file = tmp_path / "42_mood_happy_smile_orig_1.png"
    orig_file.write_bytes(b"FAKEDATA")
    original_url = f"/static/uploads/mood_photos/{orig_file.name}"

    fake_proc = MagicMock()
    fake_proc.remove.return_value = b"PROCESSED"

    with patch(f"{_TASK_MOD}.MOOD_PHOTO_DIR", new=tmp_path), \
         patch(f"{_TASK_MOD}.get_processor", return_value=fake_proc), \
         patch(f"{_TASK_MOD}.apply_removal_result") as mock_ok, \
         patch(f"{_TASK_MOD}.apply_removal_failure") as mock_fail, \
         patch(f"{_TASK_MOD}.SessionLocal", return_value=MagicMock()):

        from app.tasks.mood_photo_tasks import remove_background_task
        result = remove_background_task.run(
            user_id=42, slot="mood_happy_smile", original_url=original_url
        )

    assert result["status"] == "ready"
    mock_ok.assert_called_once()
    mock_fail.assert_not_called()

    # Slot names start with "mood_" and the filename template adds "_mood_" prefix,
    # so the actual pattern is: 42_mood_{slot}_proc_*.png = 42_mood_mood_happy_smile_proc_*.png
    proc_files = list(tmp_path.glob(f"42_mood_mood_happy_smile_proc_*.png"))
    assert len(proc_files) == 1
    assert proc_files[0].read_bytes() == b"PROCESSED"


# ── MP-T04 ── Task: max retries exhausted → apply_removal_failure called ──────

def test_mp_t04_task_max_retries_calls_failure(tmp_path):
    orig_file = tmp_path / "42_mood_happy_smile_orig_1.png"
    orig_file.write_bytes(b"FAKEDATA")
    original_url = f"/static/uploads/mood_photos/{orig_file.name}"

    failing_proc = MagicMock()
    failing_proc.remove.side_effect = RuntimeError("boom")

    with patch(f"{_TASK_MOD}.MOOD_PHOTO_DIR", new=tmp_path), \
         patch(f"{_TASK_MOD}.get_processor", return_value=failing_proc), \
         patch(f"{_TASK_MOD}.apply_removal_result") as mock_ok, \
         patch(f"{_TASK_MOD}.apply_removal_failure") as mock_fail, \
         patch(f"{_TASK_MOD}.SessionLocal", return_value=MagicMock()):

        from app.tasks.mood_photo_tasks import remove_background_task

        # Simulate max retries exhausted: task.retry raises MaxRetriesExceededError
        mock_self = MagicMock()
        mock_self.MaxRetriesExceededError = RuntimeError
        mock_self.retry.side_effect = RuntimeError("max retries exceeded")

        # Invoke the underlying function directly with a mock self
        result = remove_background_task.run.__func__(
            mock_self,
            user_id=42,
            slot="mood_happy_smile",
            original_url=original_url,
        )

    assert result["status"] == "failed"
    mock_fail.assert_called_once()
    mock_ok.assert_not_called()


# ── MP-T05 ── Task: missing original file → failed immediately, no retry ───────

def test_mp_t05_task_missing_file_immediate_failure(tmp_path):
    # original_url points to a file that does NOT exist
    original_url = "/static/uploads/mood_photos/nonexistent.png"

    with patch(f"{_TASK_MOD}.MOOD_PHOTO_DIR", new=tmp_path), \
         patch(f"{_TASK_MOD}.apply_removal_failure") as mock_fail, \
         patch(f"{_TASK_MOD}.apply_removal_result") as mock_ok, \
         patch(f"{_TASK_MOD}.SessionLocal", return_value=MagicMock()):

        from app.tasks.mood_photo_tasks import remove_background_task
        result = remove_background_task.run(
            user_id=42, slot="mood_happy_smile", original_url=original_url
        )

    assert result["status"] == "failed"
    assert result.get("reason") == "missing_file"
    mock_fail.assert_called_once()
    mock_ok.assert_not_called()


# ── MP-T06 ── apply_removal_failure NOT called on success ────────────────────

def test_mp_t06_no_failure_call_on_success(tmp_path):
    orig_file = tmp_path / "99_mood_celebration_orig_1.png"
    orig_file.write_bytes(b"DATA")
    original_url = f"/static/uploads/mood_photos/{orig_file.name}"

    fake_proc = MagicMock()
    fake_proc.remove.return_value = b"OUTPUT"

    with patch(f"{_TASK_MOD}.MOOD_PHOTO_DIR", new=tmp_path), \
         patch(f"{_TASK_MOD}.get_processor", return_value=fake_proc), \
         patch(f"{_TASK_MOD}.apply_removal_result"), \
         patch(f"{_TASK_MOD}.apply_removal_failure") as mock_fail, \
         patch(f"{_TASK_MOD}.SessionLocal", return_value=MagicMock()):

        from app.tasks.mood_photo_tasks import remove_background_task
        remove_background_task.run(
            user_id=99, slot="mood_celebration", original_url=original_url
        )

    mock_fail.assert_not_called()


# ── MP-T07 ── get_processor() with rembg config → RembgProcessor ─────────────

def test_mp_t07_get_processor_rembg_mode():
    from app.services.background_removal.rembg_processor import RembgProcessor

    with patch("app.services.background_removal.settings") as mock_cfg:
        mock_cfg.BG_REMOVAL_PROCESSOR = "rembg"
        from app.services.background_removal import get_processor
        proc = get_processor()

    assert isinstance(proc, RembgProcessor)


# ── MP-T08 ── RembgProcessor.remove() calls rembg.remove(model="u2netp") ─────
# rembg is imported inside remove() (deferred), so we patch via sys.modules.

def test_mp_t08_rembg_processor_calls_rembg_remove():
    mock_rembg = MagicMock()
    mock_rembg.remove.return_value = b"TRANSPARENT_PNG"

    with patch.dict(sys.modules, {"rembg": mock_rembg}):
        from app.services.background_removal.rembg_processor import RembgProcessor
        result = RembgProcessor().remove(b"INPUT_PNG")

    assert result == b"TRANSPARENT_PNG"
    mock_rembg.remove.assert_called_once_with(b"INPUT_PNG", model="u2netp")


# ── MP-T09 ── rembg.remove() exception → task calls apply_removal_failure ─────

def test_mp_t09_rembg_exception_causes_task_failure(tmp_path):
    orig_file = tmp_path / "42_mood_happy_smile_orig_1.png"
    orig_file.write_bytes(b"FAKEDATA")
    original_url = f"/static/uploads/mood_photos/{orig_file.name}"

    mock_rembg = MagicMock()
    mock_rembg.remove.side_effect = RuntimeError("ONNX session failed")

    with patch.dict(sys.modules, {"rembg": mock_rembg}):
        from app.services.background_removal.rembg_processor import RembgProcessor
        rembg_proc = RembgProcessor()

    with patch(f"{_TASK_MOD}.MOOD_PHOTO_DIR", new=tmp_path), \
         patch(f"{_TASK_MOD}.get_processor", return_value=rembg_proc), \
         patch(f"{_TASK_MOD}.apply_removal_result") as mock_ok, \
         patch(f"{_TASK_MOD}.apply_removal_failure") as mock_fail, \
         patch(f"{_TASK_MOD}.SessionLocal", return_value=MagicMock()):

        from app.tasks.mood_photo_tasks import remove_background_task

        mock_self = MagicMock()
        mock_self.MaxRetriesExceededError = RuntimeError
        mock_self.retry.side_effect = RuntimeError("max retries exceeded")

        with patch.dict(sys.modules, {"rembg": mock_rembg}):
            result = remove_background_task.run.__func__(
                mock_self,
                user_id=42,
                slot="mood_happy_smile",
                original_url=original_url,
            )

    assert result["status"] == "failed"
    mock_fail.assert_called_once()
    mock_ok.assert_not_called()
