"""
Juggling upload endpoint tests — JU-01..JU-18.

Tests run with JUGGLING_POC_ENABLED=True (monkeypatched).
transcode_video_task.delay is mocked so no Celery worker is needed.
"""
from __future__ import annotations

import struct
import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.services.juggling import feature_flag as ff_module


# ── Helpers ──────────────────────────────────────────────────────────────────

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


def _grant_consent(client, token):
    client.post(
        "/api/v1/users/me/juggling-consent",
        json={"service_consent": True},
        headers=_auth(token),
    )


def _init_upload(client, token, source_type="uploaded_video"):
    return client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={"source_type": source_type, "upload_source": "gallery"},
        headers=_auth(token),
    )


def _upload_file(client, token, video_id, data=None, filename="video.mp4", ct="video/mp4"):
    if data is None:
        data = _valid_mp4()
    return client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/upload",
        files={"file": (filename, data, ct)},
        headers=_auth(token),
    )


# ── upload-init tests ─────────────────────────────────────────────────────────

def test_ju01_upload_init_in_app_capture_201(client, student_token):
    """JU-01: upload-init source_type=in_app_capture returns 201."""
    _grant_consent(client, student_token)
    r = _init_upload(client, student_token, "in_app_capture")
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["status"] == "pending_upload"
    assert "video_id" in data


def test_ju02_upload_init_uploaded_video_201(client, student_token):
    """JU-02: upload-init source_type=uploaded_video returns 201."""
    _grant_consent(client, student_token)
    r = _init_upload(client, student_token, "uploaded_video")
    assert r.status_code == 201, r.text


def test_ju03_upload_init_invalid_source_type_422(client, student_token):
    """JU-03: invalid source_type returns 422."""
    _grant_consent(client, student_token)
    r = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={"source_type": "webcam_recording"},
        headers=_auth(student_token),
    )
    assert r.status_code == 422, r.text


def test_ju04_upload_init_client_metadata_optional(client, student_token):
    """JU-04: client_reported_metadata is optional."""
    _grant_consent(client, student_token)
    r = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={"source_type": "uploaded_video"},
        headers=_auth(student_token),
    )
    assert r.status_code == 201


def test_ju05_upload_init_extra_metadata_keys_ignored(client, student_token):
    """JU-05: Unknown keys in client_reported_metadata are silently dropped."""
    _grant_consent(client, student_token)
    r = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={
            "source_type": "uploaded_video",
            "client_reported_metadata": {
                "fps": 60.0,
                "resolution": "1280x720",
                "SECRET_KEY": "should_be_dropped",
                "malicious_key": "also_dropped",
            },
        },
        headers=_auth(student_token),
    )
    assert r.status_code == 201


# ── file upload tests ─────────────────────────────────────────────────────────

def test_ju06_upload_mp4_ok(client, student_token, tmp_path):
    """JU-06: Upload valid .mp4 returns 200 with checksum."""
    _grant_consent(client, student_token)
    init = _init_upload(client, student_token)
    video_id = init.json()["video_id"]

    with patch("app.services.juggling.video_service.save_file") as mock_save:
        mock_save.return_value = tmp_path / "test.mp4"
        (tmp_path / "test.mp4").write_bytes(_valid_mp4())
        r = _upload_file(client, student_token, video_id)

    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "uploaded"
    assert "checksum_sha256" in data
    assert len(data["checksum_sha256"]) == 64


def test_ju07_upload_mov_ok(client, student_token, tmp_path):
    """JU-07: Upload valid .mov returns 200."""
    _grant_consent(client, student_token)
    init = _init_upload(client, student_token)
    video_id = init.json()["video_id"]

    with patch("app.services.juggling.video_service.save_file") as mock_save:
        mock_save.return_value = tmp_path / "test.mov"
        (tmp_path / "test.mov").write_bytes(_valid_mp4())
        r = _upload_file(client, student_token, video_id,
                         filename="clip.mov", ct="video/quicktime")

    assert r.status_code == 200


def test_ju08_upload_too_large_413(client, student_token, monkeypatch):
    """JU-08: File exceeding size limit returns 413."""
    from app.services.juggling import security_service as ss
    monkeypatch.setattr(ss.settings, "JUGGLING_VIDEO_MAX_SIZE_MB", 0)  # 0 MB → everything too large

    _grant_consent(client, student_token)
    init = _init_upload(client, student_token)
    video_id = init.json()["video_id"]

    r = _upload_file(client, student_token, video_id, data=_valid_mp4())
    assert r.status_code == 413, r.text


def test_ju09_upload_empty_file_400(client, student_token):
    """JU-09: Empty file returns 400."""
    _grant_consent(client, student_token)
    init = _init_upload(client, student_token)
    video_id = init.json()["video_id"]
    r = _upload_file(client, student_token, video_id, data=b"")
    assert r.status_code == 400, r.text


def test_ju10_upload_unsupported_extension_415(client, student_token):
    """JU-10: .avi extension returns 415."""
    _grant_consent(client, student_token)
    init = _init_upload(client, student_token)
    video_id = init.json()["video_id"]
    r = _upload_file(client, student_token, video_id,
                     filename="video.avi", ct="video/x-msvideo",
                     data=_valid_mp4())
    assert r.status_code == 415, r.text


def test_ju11_upload_unsupported_mime_415(client, student_token):
    """JU-11: image/jpeg MIME returns 415."""
    _grant_consent(client, student_token)
    init = _init_upload(client, student_token)
    video_id = init.json()["video_id"]
    r = _upload_file(client, student_token, video_id,
                     filename="video.mp4", ct="image/jpeg",
                     data=_valid_mp4())
    assert r.status_code == 415, r.text


def test_ju12_upload_magic_bytes_mismatch_415(client, student_token):
    """JU-12: .mp4 extension + video/mp4 MIME but JPEG magic bytes → 415."""
    jpeg_magic = b"\xff\xd8\xff\xe0" + b"\x00" * 200
    _grant_consent(client, student_token)
    init = _init_upload(client, student_token)
    video_id = init.json()["video_id"]
    r = _upload_file(client, student_token, video_id,
                     filename="fake.mp4", ct="video/mp4",
                     data=jpeg_magic)
    assert r.status_code == 415, r.text


def test_ju13_server_generated_filename_not_client_name(client, student_token, tmp_path):
    """JU-13: Stored filename is server-generated UUID, never the client filename."""
    _grant_consent(client, student_token)
    init = _init_upload(client, student_token)
    video_id = init.json()["video_id"]

    saved_path = tmp_path / "uuid-generated.mp4"
    with patch("app.services.juggling.video_service.save_file") as mock_save:
        mock_save.return_value = saved_path
        saved_path.write_bytes(_valid_mp4())
        r = _upload_file(client, student_token, video_id,
                         filename="my_personal_video.mp4")

    assert r.status_code == 200
    # The saved filename should NOT contain the client's filename
    call_args = mock_save.call_args
    stored_fname = call_args[0][1]  # second positional arg = filename
    assert "my_personal_video" not in stored_fname
    assert ".." not in stored_fname


def test_ju14_checksum_sha256_stored(client, student_token, tmp_path, db_session):
    """JU-14: checksum_sha256 is stored in DB after upload."""
    from app.models.juggling import JugglingVideo
    from app.services.juggling.security_service import compute_sha256

    _grant_consent(client, student_token)
    init = _init_upload(client, student_token)
    video_id = init.json()["video_id"]
    mp4_data = _valid_mp4()
    expected_checksum = compute_sha256(mp4_data)

    with patch("app.services.juggling.video_service.save_file") as mock_save:
        mock_save.return_value = tmp_path / "test.mp4"
        (tmp_path / "test.mp4").write_bytes(mp4_data)
        _upload_file(client, student_token, video_id, data=mp4_data)

    record = db_session.query(JugglingVideo).filter_by(id=video_id).first()
    assert record is not None
    assert record.checksum_sha256 == expected_checksum


# ── complete endpoint tests ───────────────────────────────────────────────────

def test_ju15_complete_returns_processing(client, student_token, tmp_path):
    """JU-15: complete endpoint transitions to processing and returns 200."""
    _grant_consent(client, student_token)
    init = _init_upload(client, student_token)
    video_id = init.json()["video_id"]

    with patch("app.services.juggling.video_service.save_file") as mock_save:
        mock_save.return_value = tmp_path / "t.mp4"
        (tmp_path / "t.mp4").write_bytes(_valid_mp4())
        _upload_file(client, student_token, video_id)

    with patch(
        "app.api.api_v1.endpoints.users.juggling_videos.transcode_video_task"
    ) as mock_task:
        mock_task.delay = MagicMock()
        r = client.post(
            f"/api/v1/users/me/juggling/videos/{video_id}/complete",
            headers=_auth(student_token),
        )

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "processing"
    mock_task.delay.assert_called_once_with(video_id)


def test_ju16_complete_409_from_pending_upload(client, student_token):
    """JU-16: complete returns 409 if video is in pending_upload status."""
    _grant_consent(client, student_token)
    init = _init_upload(client, student_token)
    video_id = init.json()["video_id"]
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/complete",
        headers=_auth(student_token),
    )
    assert r.status_code == 409, r.text


def test_ju17_complete_409_double_submit(client, student_token, tmp_path):
    """JU-17: calling complete twice returns 409 on second call."""
    _grant_consent(client, student_token)
    init = _init_upload(client, student_token)
    video_id = init.json()["video_id"]

    with patch("app.services.juggling.video_service.save_file") as mock_save:
        mock_save.return_value = tmp_path / "t.mp4"
        (tmp_path / "t.mp4").write_bytes(_valid_mp4())
        _upload_file(client, student_token, video_id)

    with patch("app.api.api_v1.endpoints.users.juggling_videos.transcode_video_task") as mock_task:
        mock_task.delay = MagicMock()
        client.post(
            f"/api/v1/users/me/juggling/videos/{video_id}/complete",
            headers=_auth(student_token),
        )
        r = client.post(
            f"/api/v1/users/me/juggling/videos/{video_id}/complete",
            headers=_auth(student_token),
        )
    assert r.status_code == 409, r.text


def test_ju18_server_metadata_overrides_client_metadata(
    client, student_token, tmp_path, db_session
):
    """JU-18: server_detected_metadata is written by task; client metadata is not authoritative."""
    from app.models.juggling import JugglingVideo
    from app.services.juggling import video_service

    _grant_consent(client, student_token)
    # Init with client metadata claiming fps=24
    r_init = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={
            "source_type": "in_app_capture",
            "client_reported_metadata": {"fps": 24.0, "resolution": "640x480"},
        },
        headers=_auth(student_token),
    )
    video_id = r_init.json()["video_id"]

    with patch("app.services.juggling.video_service.save_file") as mock_save:
        mock_save.return_value = tmp_path / "t.mp4"
        (tmp_path / "t.mp4").write_bytes(_valid_mp4())
        _upload_file(client, student_token, video_id)

    # Simulate task writing server metadata with different fps
    server_meta = {"fps": 59.94, "resolution": "1280x720", "codec": "h264",
                   "has_audio": False, "duration_seconds": 30.0}
    video_service.apply_analysis(
        video_id, server_meta, 0.85, "acceptable",
        {"blur_score": 0.8, "dark_frame_ratio": 0.05, "fps_detected": 59.94,
         "fps_acceptable": True, "duration_acceptable": True, "rotation": 0,
         "subject_size_score": None, "ball_visible_score": None},
        db_session,
    )

    r_q = client.get(
        f"/api/v1/users/me/juggling/videos/{video_id}/quality",
        headers=_auth(student_token),
    )
    assert r_q.status_code == 200
    server = r_q.json()["server_detected_metadata"]
    assert server["fps"] == 59.94       # server wins
    assert server["resolution"] == "1280x720"  # server wins
    assert "SECRET_KEY" not in server
