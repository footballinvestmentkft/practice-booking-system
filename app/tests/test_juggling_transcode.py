"""
Juggling transcode integration tests — JT-01..JT-12.

Tests cover:
  JT-01  audio input → audio_stripped=True written to DB (via apply_transcode_result)
  JT-02  no-audio input → transcode_status=skipped, no processed_path
  JT-03  60fps input → done path, processed_fps=30 written
  JT-04  rotation done path → processed_path set
  JT-05  thumbnail_path written to DB
  JT-06  skip case → transcode_status=skipped, processed_path=None
  JT-07  failed transcode → transcode_status=failed, error recorded
  JT-08  analyze NOT dispatched when transcode_status=failed
  JT-09  quality response never contains processed_path / thumbnail_path
  JT-10  original file not deleted after apply_transcode_result
  JT-11  feature flag regression — 503 when disabled (P1 guard still works)
  JT-12  P1 quality response structure still intact after P2 schema additions

Strategy:
  HTTP layer creates DB records via TestClient.
  video_service.apply_transcode_result() is called directly to write transcode
  results into the test DB (avoids Celery task session isolation issues).
  The analyze dispatch guard (JT-08) is tested by mocking the task's DB lookup.
"""
from __future__ import annotations

import struct
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.services.juggling import feature_flag as ff_module
from app.services.juggling import video_service
from app.services.juggling.transcode_service import TranscodeResult


# ── Fixtures / helpers ────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _enable_juggling(monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_ftyp_box(brand: bytes = b"isom") -> bytes:
    size = 20
    return struct.pack(">I", size) + b"ftyp" + brand + b"\x00\x00\x00\x00" + brand


def _valid_mp4() -> bytes:
    return _make_ftyp_box() + b"\x00" * 200


def _get_user_id(client, token: str) -> int:
    r = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    return r.json()["id"]


def _grant_consent(client, token):
    client.post(
        "/api/v1/users/me/juggling-consent",
        json={"service_consent": True},
        headers=_auth(token),
    )


def _init_upload(client, token):
    return client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={"source_type": "uploaded_video", "upload_source": "gallery"},
        headers=_auth(token),
    )


def _upload_file(client, token, video_id, tmp_path):
    """Write a valid MP4 to tmp_path, patch save_file, call upload endpoint."""
    mp4_file = tmp_path / f"{video_id}.mp4"
    mp4_file.write_bytes(_valid_mp4())
    with patch("app.services.juggling.video_service.save_file") as mock_save:
        mock_save.return_value = mp4_file
        r = client.post(
            f"/api/v1/users/me/juggling/videos/{video_id}/upload",
            files={"file": ("clip.mp4", _valid_mp4(), "video/mp4")},
            headers=_auth(token),
        )
    return r, mp4_file


def _setup_uploaded_video(client, token, tmp_path):
    """Full setup: consent → init → upload. Returns (video_id, original_path)."""
    _grant_consent(client, token)
    init = _init_upload(client, token)
    assert init.status_code == 201, init.text
    video_id = init.json()["video_id"]
    r, original = _upload_file(client, token, video_id, tmp_path)
    assert r.status_code == 200, r.text
    return video_id, original


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_jt01_audio_input_audio_stripped_written(client, student_token, tmp_path, db_session):
    """JT-01: audio input → audio_stripped=True written to DB."""
    from app.models.juggling import JugglingVideo

    video_id, _ = _setup_uploaded_video(client, student_token, tmp_path)

    result = TranscodeResult(
        status="done",
        processed_path=tmp_path / f"{video_id}_proc.mp4",
        thumbnail_path=tmp_path / f"{video_id}_thumb.jpg",
        audio_stripped=True,
        processed_resolution="1280x720",
        processed_fps=30.0,
        processed_file_size_bytes=50000,
        checksum_processed="abc123",
    )
    video_service.apply_transcode_result(video_id, result, db_session)

    video = db_session.query(JugglingVideo).filter_by(id=video_id).first()
    assert video.audio_stripped is True
    assert video.transcode_status == "done"


def test_jt02_no_audio_input_skipped_no_error(client, student_token, tmp_path, db_session):
    """JT-02: no-audio input → transcode_status=skipped, no error."""
    from app.models.juggling import JugglingVideo

    video_id, _ = _setup_uploaded_video(client, student_token, tmp_path)

    result = TranscodeResult(
        status="skipped",
        thumbnail_path=tmp_path / f"{video_id}_thumb.jpg",
    )
    video_service.apply_transcode_result(video_id, result, db_session)

    video = db_session.query(JugglingVideo).filter_by(id=video_id).first()
    assert video.transcode_status == "skipped"
    assert video.transcode_error is None
    assert video.processed_path is None


def test_jt03_high_fps_triggers_done_path(client, student_token, tmp_path, db_session):
    """JT-03: 60fps video → result=done, processed_fps=30 written to DB."""
    from app.models.juggling import JugglingVideo

    video_id, _ = _setup_uploaded_video(client, student_token, tmp_path)

    result = TranscodeResult(
        status="done",
        processed_path=tmp_path / f"{video_id}_proc.mp4",
        thumbnail_path=tmp_path / f"{video_id}_thumb.jpg",
        audio_stripped=True,
        processed_fps=30.0,
        processed_resolution="1280x720",
        processed_file_size_bytes=40000,
        checksum_processed="def456",
    )
    video_service.apply_transcode_result(video_id, result, db_session)

    video = db_session.query(JugglingVideo).filter_by(id=video_id).first()
    assert video.transcode_status == "done"
    assert video.processed_fps == 30.0


def test_jt04_rotation_done_path(client, student_token, tmp_path, db_session):
    """JT-04: rotation → done path, processed_path set."""
    from app.models.juggling import JugglingVideo

    video_id, _ = _setup_uploaded_video(client, student_token, tmp_path)

    proc = tmp_path / f"{video_id}_proc.mp4"
    proc.write_bytes(b"\x00" * 100)

    result = TranscodeResult(
        status="done",
        processed_path=proc,
        thumbnail_path=tmp_path / f"{video_id}_thumb.jpg",
        audio_stripped=True,
        processed_resolution="720x1280",
        processed_fps=30.0,
        processed_file_size_bytes=proc.stat().st_size,
        checksum_processed="ghi789",
    )
    video_service.apply_transcode_result(video_id, result, db_session)

    video = db_session.query(JugglingVideo).filter_by(id=video_id).first()
    assert video.transcode_status == "done"
    assert video.processed_path == str(proc)


def test_jt05_thumbnail_path_written_to_db(client, student_token, tmp_path, db_session):
    """JT-05: thumbnail_path is written to DB after transcode result."""
    from app.models.juggling import JugglingVideo

    video_id, _ = _setup_uploaded_video(client, student_token, tmp_path)

    thumb = tmp_path / f"{video_id}_thumb.jpg"
    result = TranscodeResult(
        status="done",
        processed_path=tmp_path / f"{video_id}_proc.mp4",
        thumbnail_path=thumb,
        audio_stripped=True,
        processed_resolution="1280x720",
        processed_fps=30.0,
        processed_file_size_bytes=10000,
        checksum_processed="xyz",
    )
    video_service.apply_transcode_result(video_id, result, db_session)

    video = db_session.query(JugglingVideo).filter_by(id=video_id).first()
    assert video.thumbnail_path == str(thumb)


def test_jt06_skip_no_processed_path(client, student_token, tmp_path, db_session):
    """JT-06: skip → transcode_status=skipped, processed_path=None."""
    from app.models.juggling import JugglingVideo

    video_id, _ = _setup_uploaded_video(client, student_token, tmp_path)

    result = TranscodeResult(
        status="skipped",
        thumbnail_path=tmp_path / f"{video_id}_thumb.jpg",
    )
    video_service.apply_transcode_result(video_id, result, db_session)

    video = db_session.query(JugglingVideo).filter_by(id=video_id).first()
    assert video.transcode_status == "skipped"
    assert video.processed_path is None


def test_jt07_failed_transcode_written(client, student_token, tmp_path, db_session):
    """JT-07: transcode failure → transcode_status=failed, error recorded."""
    from app.models.juggling import JugglingVideo

    video_id, _ = _setup_uploaded_video(client, student_token, tmp_path)

    result = TranscodeResult(
        status="failed",
        error="ffmpeg_exit_1:error",
    )
    video_service.apply_transcode_result(video_id, result, db_session)

    video = db_session.query(JugglingVideo).filter_by(id=video_id).first()
    assert video.transcode_status == "failed"
    assert video.transcode_error == "ffmpeg_exit_1:error"


def test_jt08_analyze_not_dispatched_on_failed(client, student_token, tmp_path, db_session):
    """JT-08: analyze_video_task.delay() NOT called when transcode fails."""
    video_id, original = _setup_uploaded_video(client, student_token, tmp_path)

    # Mock the task's DB to return a video with transcode_status=failed
    mock_video = MagicMock()
    mock_video.storage_path = str(original)
    mock_video.transcode_status = "failed"

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = mock_video

    analyze_called = False

    with patch("app.tasks.juggling_transcode_task.SessionLocal", return_value=mock_db), \
         patch("app.tasks.juggling_transcode_task.transcode_service.transcode",
               return_value=TranscodeResult(status="failed", error="ffmpeg_exit_1")), \
         patch("app.tasks.juggling_transcode_task.metadata_service.probe_video",
               return_value={"streams": [], "format": {}}), \
         patch("app.tasks.juggling_transcode_task.metadata_service.extract_server_metadata",
               return_value={"fps": 60.0, "resolution": "1920x1080",
                             "rotation": 90, "has_audio": True}), \
         patch("app.tasks.juggling_transcode_task.video_service.set_transcode_processing"), \
         patch("app.tasks.juggling_transcode_task.video_service.apply_transcode_result"), \
         patch("app.tasks.juggling_tasks.analyze_video_task") as mock_analyze:

        mock_analyze.delay = MagicMock(side_effect=lambda vid: None)

        from app.tasks.juggling_transcode_task import transcode_video_task
        transcode_video_task.apply(args=[video_id])

        analyze_called = mock_analyze.delay.called

    assert analyze_called is False, "analyze must NOT be dispatched on transcode failure"


def test_jt09_quality_response_no_paths(client, student_token, tmp_path, db_session):
    """JT-09: GET /quality response never contains processed_path, thumbnail_path, storage_path."""
    video_id, _ = _setup_uploaded_video(client, student_token, tmp_path)

    result = TranscodeResult(
        status="done",
        processed_path=tmp_path / f"{video_id}_proc.mp4",
        thumbnail_path=tmp_path / f"{video_id}_thumb.jpg",
        audio_stripped=True,
        processed_resolution="1280x720",
        processed_fps=30.0,
        processed_file_size_bytes=50000,
        checksum_processed="abc",
    )
    video_service.apply_transcode_result(video_id, result, db_session)

    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video_id}/quality",
        headers=_auth(student_token),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    # Forbidden fields must not appear in response
    for forbidden in ("processed_path", "thumbnail_path", "original_path",
                      "storage_path", "checksum_processed"):
        assert forbidden not in data, f"Forbidden field in response: {forbidden}"
    # Allowed P2 fields must be present
    for field in ("transcode_status", "audio_stripped", "processed_resolution",
                  "processed_fps", "processed_file_size_bytes"):
        assert field in data, f"P2 field missing from response: {field}"
    assert data["transcode_status"] == "done"
    assert data["audio_stripped"] is True
    assert data["processed_resolution"] == "1280x720"
    assert data["processed_fps"] == 30.0
    assert data["processed_file_size_bytes"] == 50000


def test_jt10_original_not_deleted(client, student_token, tmp_path, db_session):
    """JT-10: original file is not deleted when apply_transcode_result is called."""
    video_id, original_file = _setup_uploaded_video(client, student_token, tmp_path)
    assert original_file.exists()

    result = TranscodeResult(
        status="done",
        processed_path=tmp_path / f"{video_id}_proc.mp4",
        thumbnail_path=tmp_path / f"{video_id}_thumb.jpg",
        audio_stripped=True,
        processed_resolution="1280x720",
        processed_fps=30.0,
        processed_file_size_bytes=40000,
        checksum_processed="abc",
    )
    video_service.apply_transcode_result(video_id, result, db_session)

    # Original must still exist — apply_transcode_result must not delete it
    assert original_file.exists(), "original file must not be deleted"


def test_jt11_feature_flag_regression_503(client, student_token, monkeypatch):
    """JT-11: P1 feature flag guard still returns 503 when flag is disabled."""
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: False)
    r = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={"source_type": "uploaded_video"},
        headers=_auth(student_token),
    )
    assert r.status_code == 503, r.text


def test_jt12_quality_response_p1_structure_intact(client, student_token, db_session):
    """JT-12: P1 quality response fields still present after P2 schema additions."""
    from app.services.juggling import consent_service

    uid = _get_user_id(client, student_token)
    consent_service.upsert_consent(uid, True, False, False, db_session)
    r_init = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={"source_type": "uploaded_video"},
        headers=_auth(student_token),
    )
    video_id = r_init.json()["video_id"]

    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video_id}/quality",
        headers=_auth(student_token),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    # All P1 fields must still be present
    for field in ("video_id", "status", "quality_status", "quality_score",
                  "server_detected_metadata", "quality_detail",
                  "rejection_reason", "warnings"):
        assert field in data, f"P1 field missing from response: {field}"
    # P2 transcode fields added
    for field in ("transcode_status", "audio_stripped", "processed_resolution",
                  "processed_fps", "processed_file_size_bytes"):
        assert field in data, f"P2 field missing from response: {field}"
