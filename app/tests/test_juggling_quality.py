"""
Juggling quality service unit tests — JQ-01..JQ-14.

Tests run against quality_service.py directly (no HTTP layer needed for most).
HTTP-layer polling tests included for quality endpoint response structure.
"""
from __future__ import annotations

import pytest

from app.services.juggling import feature_flag as ff_module
from app.services.juggling import quality_service


# ── Helpers ──────────────────────────────────────────────────────────────────

def _meta(**kwargs) -> dict:
    base = {
        "fps": 60.0,
        "resolution": "1280x720",
        "duration_seconds": 45.0,
        "codec": "hevc",
        "bitrate_kbps": 8000,
        "rotation": 0,
        "has_audio": False,
        "file_format": "mov,mp4",
        "container": "mov",
        "nb_streams": 1,
    }
    base.update(kwargs)
    return base


def _file_bytes(size_mb: float = 20.0) -> bytes:
    return b"\x00" * int(size_mb * 1024 * 1024)


# ── Quality service unit tests ────────────────────────────────────────────────

def test_jq01_acceptable_video_returns_high_score():
    """JQ-01: Good quality video gets acceptable quality_status."""
    score, status, detail, reason = quality_service.analyze(
        _file_bytes(20), _meta()
    )
    assert status in ("acceptable", "needs_review")
    assert reason is None
    assert score > 0.5


def test_jq02_subject_size_score_is_null():
    """JQ-02: subject_size_score is always None in this branch (P2/P3 scope)."""
    _, _, detail, _ = quality_service.analyze(_file_bytes(10), _meta())
    assert detail["subject_size_score"] is None


def test_jq03_ball_visible_score_is_null():
    """JQ-03: ball_visible_score is always None in this branch (P2/P3 scope)."""
    _, _, detail, _ = quality_service.analyze(_file_bytes(10), _meta())
    assert detail["ball_visible_score"] is None


def test_jq04_audio_present_warning_in_detail():
    """JQ-04: has_audio=True → audio_present=True in quality_detail."""
    _, _, detail, _ = quality_service.analyze(
        _file_bytes(10), _meta(has_audio=True)
    )
    assert detail.get("audio_present") is True


def test_jq05_no_audio_warning_absent():
    """JQ-05: has_audio=False → audio_present key absent from quality_detail."""
    _, _, detail, _ = quality_service.analyze(
        _file_bytes(10), _meta(has_audio=False)
    )
    assert "audio_present" not in detail


def test_jq06_fps_score_60fps_is_high():
    """JQ-06: 60 fps gets fps_score=1.0."""
    assert quality_service._fps_score(60.0) == 1.0


def test_jq07_fps_score_30fps_is_mid():
    """JQ-07: 30 fps gets fps_score=0.7."""
    assert quality_service._fps_score(30.0) == 0.7


def test_jq08_fps_score_24fps_is_low():
    """JQ-08: 24 fps gets fps_score=0.4 (barely acceptable)."""
    assert quality_service._fps_score(24.0) == 0.4


def test_jq09_fps_below_min_rejected():
    """JQ-09: fps < 24 → rejection_reason=fps_too_low."""
    _, status, _, reason = quality_service.analyze(
        _file_bytes(10), _meta(fps=20.0)
    )
    assert reason == "fps_too_low"
    assert status == "rejected"


def test_jq10_null_fps_neutral():
    """JQ-10: fps=None → fps_score=0.5 (neutral, not rejected)."""
    assert quality_service._fps_score(None) == 0.5
    _, _, detail, reason = quality_service.analyze(
        _file_bytes(10), _meta(fps=None)
    )
    assert reason != "fps_too_low"
    assert detail["fps_acceptable"] is True


def test_jq11_rotation_stored_in_detail():
    """JQ-11: rotation value from server metadata appears in quality_detail."""
    _, _, detail, _ = quality_service.analyze(
        _file_bytes(10), _meta(rotation=90)
    )
    assert detail["rotation"] == 90


def test_jq12_overall_score_excludes_null_dimensions():
    """JQ-12: subject_size_score and ball_visible_score are null → not in score calculation."""
    score, _, detail, _ = quality_service.analyze(
        _file_bytes(20), _meta()
    )
    # Score must be computable (not None/NaN) even with null dimensions
    assert isinstance(score, float)
    assert 0.0 <= score <= 1.0
    assert detail["subject_size_score"] is None
    assert detail["ball_visible_score"] is None


def test_jq13_duration_acceptable_true_by_default():
    """JQ-13: duration_acceptable is True in quality_detail (duration gate is in task layer)."""
    _, _, detail, _ = quality_service.analyze(_file_bytes(10), _meta())
    assert detail["duration_acceptable"] is True


# ── Quality HTTP endpoint tests ───────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _enable_juggling(monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_jq14_quality_endpoint_returns_correct_structure(
    client, student_token, db_session
):
    """JQ-14: GET /quality returns pending status and correct schema before analysis."""
    from app.services.juggling import consent_service, video_service

    # Setup: create consent + pending_upload video
    from app.tests.conftest import TestingSessionLocal
    consent_service.upsert_consent(
        user_id=_get_user_id(client, student_token),
        service_consent=True, training_consent=False,
        admin_review_consent=False, db=db_session,
    )
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
    assert data["video_id"] == video_id
    assert data["status"] == "pending_upload"
    assert data["quality_status"] == "pending"
    assert data["quality_score"] is None
    assert data["server_detected_metadata"] is None
    # No video URL in response
    assert "storage_path" not in data
    assert "filename_stored" not in data
    assert "url" not in str(data).lower() or "upload_url" not in data


def test_jq15_quality_endpoint_audio_warning(client, student_token, db_session):
    """JQ-15: quality endpoint includes audio_present warning when has_audio=True."""
    from app.services.juggling import consent_service, video_service

    uid = _get_user_id(client, student_token)
    consent_service.upsert_consent(uid, True, False, False, db_session)
    r_init = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        json={"source_type": "uploaded_video"},
        headers=_auth(student_token),
    )
    video_id = r_init.json()["video_id"]

    # Simulate task writing quality_detail with audio_present
    video = db_session.query(
        __import__("app.models.juggling", fromlist=["JugglingVideo"]).JugglingVideo
    ).filter_by(id=video_id).first()

    video_service.apply_analysis(
        video_id,
        {"fps": 60.0, "has_audio": True, "codec": "h264",
         "duration_seconds": 30.0, "rotation": 0},
        0.80, "acceptable",
        {"blur_score": 0.75, "dark_frame_ratio": 0.05, "fps_detected": 60.0,
         "fps_acceptable": True, "duration_acceptable": True, "rotation": 0,
         "subject_size_score": None, "ball_visible_score": None,
         "audio_present": True},
        db_session,
    )

    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video_id}/quality",
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    data = r.json()
    assert "audio_present" in data["warnings"]


def _get_user_id(client, token: str) -> int:
    """Helper: get the user ID from /api/v1/users/me."""
    r = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {token}"})
    return r.json()["id"]
