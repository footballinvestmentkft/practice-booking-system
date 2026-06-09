"""
BG-FIX — Unit tests for _run_bg_removal in lfa_player/mood_photos.py

Covers the critical fix: processor.remove() (correct) vs processor.remove_background() (was wrong).

BG-FIX-01  success path: processor.remove() called, processed_png_url written, apply_removal_result called
BG-FIX-02  processor exception → apply_removal_failure, original_url preserved
BG-FIX-03  orig_path missing → apply_removal_failure immediately, processor.remove() NOT called
BG-FIX-04  retry from failed status: remove-bg endpoint re-enqueues background task
BG-FIX-05  phase_a_complete=True when 6/6 Phase-A records exist (all statuses count)
BG-FIX-06  phase_a_complete=False when only 5/6 records exist
BG-FIX-07  phase_a_complete=True even when all slots have status="failed"
BG-FIX-08  processor.remove_background does NOT exist on NullProcessor (guards the fix)
BG-FIX-09  processor.remove_background does NOT exist on RembgProcessor (guards the fix)
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

_ENDPOINT = "app.api.api_v1.endpoints.lfa_player.mood_photos"
_DB_MOD    = "app.database"                      # SessionLocal lazy-imported inside _run_bg_removal
_PROC_MOD  = "app.services.background_removal"   # get_processor lazy-imported inside _run_bg_removal


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user(uid: int = 1):
    u = MagicMock()
    u.id = uid
    return u


def _db():
    db = MagicMock()
    return db


def _slot_record(status="uploaded", original_url="/static/uploads/mood_photos/1_mood_neutral.png"):
    r = MagicMock()
    r.status           = status
    r.original_url     = original_url
    r.processed_png_url = None
    return r


# ── BG-FIX-01 ─────────────────────────────────────────────────────────────────

def test_bgfix01_success_calls_processor_remove_and_apply_result(tmp_path):
    """_run_bg_removal calls processor.remove() (not remove_background) and saves result."""
    from app.api.api_v1.endpoints.lfa_player.mood_photos import _run_bg_removal

    orig_file = tmp_path / "1_mood_neutral.png"
    orig_file.write_bytes(b"FAKEPNG")
    original_url = f"/static/uploads/mood_photos/{orig_file.name}"

    mock_processor = MagicMock()
    mock_processor.remove.return_value = b"PROCESSED"

    with patch(f"{_DB_MOD}.SessionLocal", return_value=_db()), \
         patch(f"{_PROC_MOD}.get_processor", return_value=mock_processor), \
         patch(f"{_ENDPOINT}.MOOD_PHOTO_DIR", tmp_path), \
         patch(f"{_ENDPOINT}.apply_removal_result") as mock_result, \
         patch(f"{_ENDPOINT}.apply_removal_failure") as mock_fail:

        _run_bg_removal(user_id=1, slot="mood_intro_neutral", original_url=original_url)

    mock_processor.remove.assert_called_once_with(b"FAKEPNG")
    mock_result.assert_called_once()
    mock_fail.assert_not_called()


# ── BG-FIX-02 ─────────────────────────────────────────────────────────────────

def test_bgfix02_processor_exception_calls_apply_failure(tmp_path):
    """When processor.remove() raises, apply_removal_failure is called, original_url preserved."""
    from app.api.api_v1.endpoints.lfa_player.mood_photos import _run_bg_removal

    orig_file = tmp_path / "1_mood_neutral.png"
    orig_file.write_bytes(b"FAKEPNG")
    original_url = f"/static/uploads/mood_photos/{orig_file.name}"

    mock_processor = MagicMock()
    mock_processor.remove.side_effect = RuntimeError("rembg model failure")

    with patch(f"{_DB_MOD}.SessionLocal", return_value=_db()), \
         patch(f"{_PROC_MOD}.get_processor", return_value=mock_processor), \
         patch(f"{_ENDPOINT}.MOOD_PHOTO_DIR", tmp_path), \
         patch(f"{_ENDPOINT}.apply_removal_result") as mock_result, \
         patch(f"{_ENDPOINT}.apply_removal_failure") as mock_fail:

        _run_bg_removal(user_id=1, slot="mood_intro_neutral", original_url=original_url)

    mock_fail.assert_called_once()
    mock_result.assert_not_called()


# ── BG-FIX-03 ─────────────────────────────────────────────────────────────────

def test_bgfix03_missing_orig_file_calls_failure_immediately(tmp_path):
    """When original file is missing from disk, apply_removal_failure is called without touching processor."""
    from app.api.api_v1.endpoints.lfa_player.mood_photos import _run_bg_removal

    original_url = "/static/uploads/mood_photos/missing_file.png"

    mock_processor = MagicMock()

    with patch(f"{_DB_MOD}.SessionLocal", return_value=_db()), \
         patch(f"{_PROC_MOD}.get_processor", return_value=mock_processor), \
         patch(f"{_ENDPOINT}.MOOD_PHOTO_DIR", tmp_path), \
         patch(f"{_ENDPOINT}.apply_removal_result") as mock_result, \
         patch(f"{_ENDPOINT}.apply_removal_failure") as mock_fail:

        _run_bg_removal(user_id=1, slot="mood_intro_neutral", original_url=original_url)

    mock_fail.assert_called_once()
    mock_processor.remove.assert_not_called()
    mock_result.assert_not_called()


# ── BG-FIX-04 ─────────────────────────────────────────────────────────────────

def test_bgfix04_retry_from_failed_re_enqueues_background_task(tmp_path):
    """
    POST /mood-photos/{slot}/remove-bg on a failed record re-enqueues _run_bg_removal.
    This tests the Retry path (failed → processing → [task runs] → ready/failed).
    """
    from app.api.api_v1.endpoints.lfa_player.mood_photos import trigger_bg_removal

    orig_file = tmp_path / "1_mood_neutral_orig.png"
    orig_file.write_bytes(b"FAKEPNG")
    original_url = f"/static/uploads/mood_photos/{orig_file.name}"

    record = _slot_record(status="failed", original_url=original_url)

    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = record

    bg = MagicMock()

    with patch(f"{_ENDPOINT}.MOOD_PHOTO_DIR", tmp_path), \
         patch(f"{_ENDPOINT}.check_bg_removal_rate_limit", return_value=True), \
         patch(f"{_ENDPOINT}.set_status_processing") as mock_set, \
         patch(f"{_ENDPOINT}.apply_removal_failure") as mock_fail:

        trigger_bg_removal(
            slot="mood_intro_neutral",
            background_tasks=bg,
            db=db,
            current_user=_user(1),
        )

    mock_set.assert_called_once()
    bg.add_task.assert_called_once()
    mock_fail.assert_not_called()


# ── BG-FIX-05 ─────────────────────────────────────────────────────────────────

def test_bgfix05_phase_a_complete_true_when_6_records_exist():
    """phase_a_complete = True when all 6 Phase-A slots have a record (any status)."""
    from app.api.api_v1.endpoints.lfa_player.mood_photos import list_mood_photos

    _PHASE_A = [
        "mood_intro_neutral", "mood_happy_smile", "mood_celebration",
        "mood_sad_disappointed", "mood_angry_competitive", "mood_surprised_shocked",
    ]

    by_slot = {s: _slot_record(status="uploaded") for s in _PHASE_A}

    with patch(f"{_ENDPOINT}.get_mood_photos_for_user", return_value=by_slot):
        result = list_mood_photos(db=_db(), current_user=_user(1))

    assert result["phase_a_complete"] is True
    assert result["phase_a_uploaded_count"] == 6


# ── BG-FIX-06 ─────────────────────────────────────────────────────────────────

def test_bgfix06_phase_a_complete_false_when_only_5_records():
    """phase_a_complete = False when only 5/6 Phase-A slots have records."""
    from app.api.api_v1.endpoints.lfa_player.mood_photos import list_mood_photos

    _PHASE_A = [
        "mood_intro_neutral", "mood_happy_smile", "mood_celebration",
        "mood_sad_disappointed", "mood_angry_competitive",
        # mood_surprised_shocked missing
    ]

    by_slot = {s: _slot_record(status="uploaded") for s in _PHASE_A}

    with patch(f"{_ENDPOINT}.get_mood_photos_for_user", return_value=by_slot):
        result = list_mood_photos(db=_db(), current_user=_user(1))

    assert result["phase_a_complete"] is False
    assert result["phase_a_uploaded_count"] == 5


# ── BG-FIX-07 ─────────────────────────────────────────────────────────────────

def test_bgfix07_phase_a_complete_true_even_when_all_status_failed():
    """
    phase_a_complete = True even when all 6 slots have status='failed'.
    Completion is based on record existence (original_url uploaded), not BG removal success.
    """
    from app.api.api_v1.endpoints.lfa_player.mood_photos import list_mood_photos

    _PHASE_A = [
        "mood_intro_neutral", "mood_happy_smile", "mood_celebration",
        "mood_sad_disappointed", "mood_angry_competitive", "mood_surprised_shocked",
    ]

    by_slot = {s: _slot_record(status="failed") for s in _PHASE_A}

    with patch(f"{_ENDPOINT}.get_mood_photos_for_user", return_value=by_slot):
        result = list_mood_photos(db=_db(), current_user=_user(1))

    assert result["phase_a_complete"] is True, (
        "Completion must not depend on BG removal success. "
        "failed status with original_url still counts toward phase_a_complete."
    )


# ── BG-FIX-08 ─────────────────────────────────────────────────────────────────

def test_bgfix08_null_processor_has_no_remove_background_method():
    """
    NullProcessor does NOT have a remove_background method.
    This test guards against future regressions — if remove_background is ever
    accidentally added, this test will catch it and force a naming decision.
    """
    from app.services.background_removal.processor import NullProcessor
    assert not hasattr(NullProcessor(), "remove_background"), (
        "NullProcessor must not have remove_background — the correct method is remove()."
    )
    assert hasattr(NullProcessor(), "remove"), "NullProcessor must have a remove() method."


# ── BG-FIX-09 ─────────────────────────────────────────────────────────────────

def test_bgfix09_rembg_processor_has_no_remove_background_method():
    """
    RembgProcessor does NOT have a remove_background method.
    The correct method is remove(). This guards the fix permanently.
    """
    from app.services.background_removal.rembg_processor import RembgProcessor
    assert not hasattr(RembgProcessor(), "remove_background"), (
        "RembgProcessor must not have remove_background — the correct method is remove()."
    )
    assert hasattr(RembgProcessor(), "remove"), "RembgProcessor must have a remove() method."
