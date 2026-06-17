"""
Juggling Phase 2A — Pose Snapshot endpoint tests — PS-01..PS-11.

Tests run with:
  JUGGLING_POC_ENABLED=True  (monkeypatched)
  POSE_SNAPSHOT_ENABLED=True (monkeypatched)

No real video file, Celery task, or ML inference needed.
A minimal JugglingVideo + JugglingContactEvent is inserted directly via ORM
to exercise the pose snapshot endpoints in isolation.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.juggling import JugglingConsent, JugglingContactEvent, JugglingVideo
from app.services.juggling import feature_flag as ff_module
from app.api.api_v1.endpoints.users import juggling_pose_snapshots as ps_module


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _enable_flags(monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)
    monkeypatch.setattr(ps_module.settings, "POSE_SNAPSHOT_ENABLED", True)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _valid_keypoints() -> dict:
    return {
        "schema_version": "1",
        "body": [
            {"name": "left_ankle",  "x": 0.412, "y": 0.834, "confidence": 0.971},
            {"name": "right_ankle", "x": 0.588, "y": 0.831, "confidence": 0.968},
            {"name": "root",        "x": 0.511, "y": 0.532, "confidence": 0.994},
        ],
        "left_hand": [],
        "right_hand": [],
    }


def _valid_payload(**overrides) -> dict:
    base = {
        "keypoints": _valid_keypoints(),
        "model_version": "apple_vision_v1",
        "capture_source": "ios_realtime",
        "captured_at_ms": 5000,
        "image_width_px": 1280,
        "image_height_px": 720,
        "inference_confidence": 0.91,
    }
    base.update(overrides)
    return base


@pytest.fixture
def student_user2(db_session):
    from app.models.user import User, UserRole
    from app.core.security import get_password_hash
    user = User(
        name="Student Two", email="student2@test.com",
        password_hash=get_password_hash("student2pw"),
        role=UserRole.STUDENT, is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def student_token2(client, student_user2):
    r = client.post("/api/v1/auth/login",
                    json={"email": "student2@test.com", "password": "student2pw"})
    return r.json()["access_token"]


@pytest.fixture
def _juggling_video(db_session, student_user):
    """Insert a minimal JugglingVideo in 'analyzed' state with an annotation contact event."""
    video = JugglingVideo(
        id=uuid.uuid4(),
        user_id=student_user.id,
        source_type="uploaded_video",
        upload_source="gallery",
        status="analyzed",
        annotation_status="in_progress",
        storage_path="/tmp/fake.mp4",
        filename_stored="fake.mp4",
        file_size_bytes=1024,
        checksum_sha256="a" * 64,
    )
    db_session.add(video)
    db_session.flush()

    event = JugglingContactEvent(
        id=uuid.uuid4(),
        video_id=video.id,
        created_by_user_id=student_user.id,
        device_event_id=uuid.uuid4(),
        timestamp_ms=5000,
        contact_type="instep_kick",
        side="right",
        annotation_confidence="certain",
        annotation_source="manual_user",
        annotation_review_status="pending",
        taxonomy_review_status="not_applicable",
        excluded_from_training=True,
        excluded_from_count=False,
        taxonomy_version="v1",
        consent_snapshot={},
    )
    db_session.add(event)
    db_session.commit()
    return video, event


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_ps01_post_pose_snapshot_creates_201(client, student_token, _juggling_video):
    """PS-01: POST /pose-snapshot → 201, snapshot saved."""
    video, event = _juggling_video
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/pose-snapshot",
        json=_valid_payload(),
        headers=_auth(student_token),
    )
    assert r.status_code == 201, r.text
    data = r.json()
    assert data["contact_event_id"] == str(event.id)
    assert data["video_id"] == str(video.id)
    assert data["timestamp_ms"] == 5000
    assert data["model_version"] == "apple_vision_v1"
    assert data["capture_source"] == "ios_realtime"
    assert data["inference_confidence"] == pytest.approx(0.91, abs=1e-6)
    assert data["keypoints"]["schema_version"] == "1"
    assert len(data["keypoints"]["body"]) == 3


def test_ps02_post_pose_snapshot_duplicate_returns_200(client, student_token, _juggling_video):
    """PS-02: Duplicate POST for same event_id → 200 (upsert, updated keypoints)."""
    video, event = _juggling_video
    url = f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/pose-snapshot"

    r1 = client.post(url, json=_valid_payload(inference_confidence=0.80), headers=_auth(student_token))
    assert r1.status_code == 201

    updated_keypoints = {**_valid_keypoints(), "body": [
        {"name": "root", "x": 0.5, "y": 0.5, "confidence": 0.999},
    ]}
    r2 = client.post(url, json=_valid_payload(
        keypoints=updated_keypoints,
        inference_confidence=0.95,
    ), headers=_auth(student_token))
    assert r2.status_code == 200, r2.text
    data = r2.json()
    assert data["inference_confidence"] == pytest.approx(0.95, abs=1e-6)
    assert len(data["keypoints"]["body"]) == 1


def test_ps03_post_pose_snapshot_wrong_user_404(client, student_token2, _juggling_video):
    """PS-03: POST pose-snapshot for video owned by another user → 404."""
    video, event = _juggling_video
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/pose-snapshot",
        json=_valid_payload(),
        headers=_auth(student_token2),
    )
    assert r.status_code == 404, r.text


def test_ps04_post_pose_snapshot_wrong_video_404(client, student_token, _juggling_video):
    """PS-04: POST pose-snapshot with nonexistent video_id → 404."""
    _, event = _juggling_video
    fake_video_id = str(uuid.uuid4())
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{fake_video_id}/contacts/{event.id}/pose-snapshot",
        json=_valid_payload(),
        headers=_auth(student_token),
    )
    assert r.status_code == 404, r.text


def test_ps05_post_pose_snapshot_wrong_event_404(client, student_token, _juggling_video):
    """PS-05: POST pose-snapshot with nonexistent event_id → 404."""
    video, _ = _juggling_video
    fake_event_id = str(uuid.uuid4())
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{fake_event_id}/pose-snapshot",
        json=_valid_payload(),
        headers=_auth(student_token),
    )
    assert r.status_code == 404, r.text


def test_ps06_post_pose_snapshot_missing_body_key_422(client, student_token, _juggling_video):
    """PS-06: POST pose-snapshot with keypoints missing 'body' key → 422."""
    video, event = _juggling_video
    bad_payload = _valid_payload(keypoints={"schema_version": "1", "left_hand": []})
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/pose-snapshot",
        json=bad_payload,
        headers=_auth(student_token),
    )
    assert r.status_code == 422, r.text


def test_ps07_post_pose_snapshot_empty_body_allowed(client, student_token, _juggling_video):
    """PS-07: POST pose-snapshot with keypoints.body=[] → 201 (empty list is valid)."""
    video, event = _juggling_video
    empty_kp = {"schema_version": "1", "body": [], "left_hand": [], "right_hand": []}
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/pose-snapshot",
        json=_valid_payload(keypoints=empty_kp),
        headers=_auth(student_token),
    )
    assert r.status_code == 201, r.text
    assert r.json()["keypoints"]["body"] == []


def test_ps08_post_pose_snapshot_confidence_out_of_range_422(client, student_token, _juggling_video):
    """PS-08: POST pose-snapshot with inference_confidence > 1.0 → 422."""
    video, event = _juggling_video
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/pose-snapshot",
        json=_valid_payload(inference_confidence=1.5),
        headers=_auth(student_token),
    )
    assert r.status_code == 422, r.text


def test_ps09_get_pose_snapshots_returns_ordered_list(client, student_token, db_session, student_user):
    """PS-09: GET /pose-snapshots returns all snapshots ordered by timestamp_ms."""
    video = JugglingVideo(
        id=uuid.uuid4(), user_id=student_user.id, source_type="uploaded_video",
        upload_source="gallery", status="analyzed", annotation_status="in_progress",
        storage_path="/tmp/fake2.mp4", filename_stored="fake2.mp4",
        file_size_bytes=1024, checksum_sha256="b" * 64,
    )
    db_session.add(video)
    db_session.flush()

    events = []
    for ms in [9000, 3000, 6000]:
        e = JugglingContactEvent(
            id=uuid.uuid4(), video_id=video.id,
            created_by_user_id=student_user.id,
            device_event_id=uuid.uuid4(),
            timestamp_ms=ms, contact_type="instep_kick", side="right",
            annotation_confidence="certain", annotation_source="manual_user",
            annotation_review_status="pending",
            taxonomy_review_status="not_applicable",
            excluded_from_training=True, excluded_from_count=False,
            taxonomy_version="v1", consent_snapshot={},
        )
        db_session.add(e)
        events.append((ms, e))
    db_session.commit()

    for ms, e in events:
        client.post(
            f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{e.id}/pose-snapshot",
            json=_valid_payload(captured_at_ms=ms),
            headers=_auth(student_token),
        )

    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/pose-snapshots",
        headers=_auth(student_token),
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 3
    timestamps = [s["timestamp_ms"] for s in data]
    assert timestamps == sorted(timestamps), "Snapshots must be ordered by timestamp_ms"


def test_ps10_get_pose_snapshots_empty_returns_200(client, student_token, _juggling_video):
    """PS-10: GET /pose-snapshots with no snapshots uploaded → 200 empty list."""
    video, _ = _juggling_video
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/pose-snapshots",
        headers=_auth(student_token),
    )
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_ps11_get_pose_snapshots_wrong_user_404(client, student_token2, _juggling_video):
    """PS-11: GET /pose-snapshots for video owned by another user → 404."""
    video, _ = _juggling_video
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/pose-snapshots",
        headers=_auth(student_token2),
    )
    assert r.status_code == 404, r.text


def test_ps12_retroactive_capture_source_accepted(client, student_token, _juggling_video):
    """PS-12: POST with capture_source='ios_retroactive' → 201 (Phase 2A patch: retroactive generation)."""
    video, event = _juggling_video
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/pose-snapshot",
        json=_valid_payload(capture_source="ios_retroactive"),
        headers=_auth(student_token),
    )
    assert r.status_code == 201, r.text
    assert r.json()["capture_source"] == "ios_retroactive"


def test_ps13_invalid_capture_source_rejected(client, student_token, _juggling_video):
    """PS-13: POST with unknown capture_source → 422 (Literal validation enforced)."""
    video, event = _juggling_video
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/pose-snapshot",
        json=_valid_payload(capture_source="unknown_source"),
        headers=_auth(student_token),
    )
    assert r.status_code == 422, r.text
