"""
User rotation persistence tests — RP-01..RP-08.

PATCH /api/v1/users/me/juggling/videos/{video_id}/rotation

Tests run with JUGGLING_POC_ENABLED=True (monkeypatched).
No real video file, Celery task, or media needed.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.juggling import JugglingVideo
from app.services.juggling import feature_flag as ff_module


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _enable_juggling(monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def _video(db_session, student_user):
    """Minimal JugglingVideo in 'analyzed' state, user_rotation_degrees=0."""
    video = JugglingVideo(
        id=uuid.uuid4(),
        user_id=student_user.id,
        source_type="uploaded_video",
        upload_source="gallery",
        status="analyzed",
        storage_path="/tmp/fake.mp4",
        filename_stored="fake.mp4",
        file_size_bytes=1024,
        checksum_sha256="a" * 64,
        user_rotation_degrees=0,
    )
    db_session.add(video)
    db_session.commit()
    return video


@pytest.fixture
def _video_user2(db_session):
    """Second user + video for ownership test."""
    from app.models.user import User, UserRole
    from app.core.security import get_password_hash
    user2 = User(
        name="Other User", email="other_rp@test.com",
        password_hash=get_password_hash("otherpw"),
        role=UserRole.STUDENT, is_active=True,
    )
    db_session.add(user2)
    db_session.commit()
    db_session.refresh(user2)
    video = JugglingVideo(
        id=uuid.uuid4(),
        user_id=user2.id,
        source_type="uploaded_video",
        upload_source="gallery",
        status="analyzed",
        storage_path="/tmp/fake2.mp4",
        filename_stored="fake2.mp4",
        file_size_bytes=1024,
        checksum_sha256="b" * 64,
        user_rotation_degrees=0,
    )
    db_session.add(video)
    db_session.commit()
    return video


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_rp01_patch_rotation_90_succeeds(client, student_token, _video):
    """RP-01: PATCH rotation_degrees=90 → 200, user_rotation_degrees=90."""
    r = client.patch(
        f"/api/v1/users/me/juggling/videos/{_video.id}/rotation",
        json={"rotation_degrees": 90},
        headers=_auth(student_token),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["video_id"] == str(_video.id)
    assert data["user_rotation_degrees"] == 90


def test_rp02_patch_rotation_180_succeeds(client, student_token, _video):
    """RP-02: PATCH rotation_degrees=180 → 200, value persisted."""
    r = client.patch(
        f"/api/v1/users/me/juggling/videos/{_video.id}/rotation",
        json={"rotation_degrees": 180},
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    assert r.json()["user_rotation_degrees"] == 180


def test_rp03_patch_rotation_270_succeeds(client, student_token, _video):
    """RP-03: PATCH rotation_degrees=270 → 200."""
    r = client.patch(
        f"/api/v1/users/me/juggling/videos/{_video.id}/rotation",
        json={"rotation_degrees": 270},
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    assert r.json()["user_rotation_degrees"] == 270


def test_rp04_patch_rotation_0_idempotent(client, student_token, _video):
    """RP-04: PATCH rotation_degrees=0 when already 0 → 200 (idempotent)."""
    r = client.patch(
        f"/api/v1/users/me/juggling/videos/{_video.id}/rotation",
        json={"rotation_degrees": 0},
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    assert r.json()["user_rotation_degrees"] == 0


def test_rp05_patch_rotation_invalid_45_rejected(client, student_token, _video):
    """RP-05: PATCH rotation_degrees=45 → 422 (not in {0,90,180,270})."""
    r = client.patch(
        f"/api/v1/users/me/juggling/videos/{_video.id}/rotation",
        json={"rotation_degrees": 45},
        headers=_auth(student_token),
    )
    assert r.status_code == 422


def test_rp06_patch_rotation_invalid_negative_rejected(client, student_token, _video):
    """RP-06: PATCH rotation_degrees=-90 → 422."""
    r = client.patch(
        f"/api/v1/users/me/juggling/videos/{_video.id}/rotation",
        json={"rotation_degrees": -90},
        headers=_auth(student_token),
    )
    assert r.status_code == 422


def test_rp07_patch_rotation_ownership_other_user_404(client, student_token, _video_user2):
    """RP-07: User A cannot PATCH rotation for User B's video → 404."""
    r = client.patch(
        f"/api/v1/users/me/juggling/videos/{_video_user2.id}/rotation",
        json={"rotation_degrees": 90},
        headers=_auth(student_token),
    )
    assert r.status_code == 404


def test_rp08_patch_rotation_nonexistent_video_404(client, student_token):
    """RP-08: PATCH rotation for nonexistent video → 404."""
    fake_id = str(uuid.uuid4())
    r = client.patch(
        f"/api/v1/users/me/juggling/videos/{fake_id}/rotation",
        json={"rotation_degrees": 90},
        headers=_auth(student_token),
    )
    assert r.status_code == 404


def test_rp09_list_videos_includes_user_rotation_degrees(client, student_token, _video):
    """RP-09: GET /videos list returns user_rotation_degrees field."""
    r = client.get(
        "/api/v1/users/me/juggling/videos",
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    videos = r.json()["videos"]
    assert any(str(_video.id) == v["video_id"] for v in videos), "video not found in list"
    row = next(v for v in videos if str(_video.id) == v["video_id"])
    assert "user_rotation_degrees" in row
    assert row["user_rotation_degrees"] == 0


def test_rp10_patch_persists_to_list(client, student_token, _video):
    """RP-10: PATCH 180, then GET list → user_rotation_degrees=180."""
    client.patch(
        f"/api/v1/users/me/juggling/videos/{_video.id}/rotation",
        json={"rotation_degrees": 180},
        headers=_auth(student_token),
    )
    r = client.get("/api/v1/users/me/juggling/videos", headers=_auth(student_token))
    assert r.status_code == 200
    row = next(v for v in r.json()["videos"] if str(_video.id) == v["video_id"])
    assert row["user_rotation_degrees"] == 180
