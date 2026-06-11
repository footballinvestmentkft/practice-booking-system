"""
Juggling P3 Retention — HTTP integration tests — RET-I-01..04.

Verifies that all juggling video endpoints correctly handle gdpr_deleted records:
  RET-I-01: GET /quality → 410 Gone
  RET-I-02: POST /upload → 410 Gone
  RET-I-03: POST /complete → 410 Gone
  RET-I-04: Another user's gdpr_deleted video still returns 404 (not 410)

Videos are inserted directly into db_session at gdpr_deleted status.
JUGGLING_POC_ENABLED is monkeypatched to True.
"""
from __future__ import annotations

import struct
import uuid
from datetime import datetime, timezone

import pytest

from app.models.juggling import JugglingVideo, JugglingVideoStatus
from app.models.user import User, UserRole
from app.services.juggling import feature_flag as ff_module


@pytest.fixture(autouse=True)
def _enable_juggling(monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _valid_mp4() -> bytes:
    size = 20
    return struct.pack(">I", size) + b"ftyp" + b"isom" + b"\x00\x00\x00\x00" + b"isom" + b"\x00" * 200


def _make_gdpr_deleted_video(db_session, user_id: int) -> JugglingVideo:
    video = JugglingVideo(
        id=uuid.uuid4(),
        user_id=user_id,
        source_type="uploaded_video",
        upload_source="gallery",
        status=JugglingVideoStatus.gdpr_deleted.value,
        deleted_at=datetime.now(timezone.utc),
        deletion_reason="gdpr_request",
    )
    db_session.add(video)
    db_session.commit()
    db_session.refresh(video)
    return video


def test_ret_i01_quality_returns_410_for_gdpr_deleted(
    client, student_token, student_user, db_session
):
    """RET-I-01: GET /quality returns 410 Gone for a gdpr_deleted video."""
    video = _make_gdpr_deleted_video(db_session, student_user.id)
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/quality",
        headers=_auth(student_token),
    )
    assert r.status_code == 410, r.text
    body = r.json()
    msg = body.get("detail") or body.get("error", {}).get("message", "")
    assert "deleted" in msg.lower()


def test_ret_i02_upload_returns_410_for_gdpr_deleted(
    client, student_token, student_user, db_session
):
    """RET-I-02: POST /upload returns 410 Gone for a gdpr_deleted video."""
    video = _make_gdpr_deleted_video(db_session, student_user.id)
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/upload",
        files={"file": ("clip.mp4", _valid_mp4(), "video/mp4")},
        headers=_auth(student_token),
    )
    assert r.status_code == 410, r.text


def test_ret_i03_complete_returns_410_for_gdpr_deleted(
    client, student_token, student_user, db_session
):
    """RET-I-03: POST /complete returns 410 Gone for a gdpr_deleted video."""
    video = _make_gdpr_deleted_video(db_session, student_user.id)
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/complete",
        headers=_auth(student_token),
    )
    assert r.status_code == 410, r.text


def test_ret_i04_other_users_gdpr_deleted_video_is_404(
    client, student_token, db_session
):
    """RET-I-04: gdpr_deleted video owned by another user returns 404, not 410."""
    other = User(
        name="Other Retention User",
        email=f"other+{uuid.uuid4().hex[:8]}@test.com",
        password_hash="x",
        role=UserRole.STUDENT,
    )
    db_session.add(other)
    db_session.flush()
    video = _make_gdpr_deleted_video(db_session, other.id)

    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/quality",
        headers=_auth(student_token),
    )
    assert r.status_code == 404, r.text
