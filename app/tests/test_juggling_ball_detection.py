"""
Ball Detection endpoint + type-aware tests — BDT-01..BDT-14, TVT-01..TVT-08, BDT-D-01..BDT-D-04.

Tests run with:
  JUGGLING_POC_ENABLED=True  (monkeypatched)
  BALL_DETECTION_ENABLED=True (monkeypatched)

No real video file, Celery task, or ML inference needed.
A minimal JugglingVideo + JugglingContactEvent is inserted directly via ORM
to exercise the ball detection endpoints in isolation.
"""
from __future__ import annotations

import uuid

import pytest

from app.models.juggling import (
    JugglingConsent,
    JugglingContactEvent,
    JugglingVideo,
    JugglingBallDetection,
)
from app.services.juggling import feature_flag as ff_module
from app.api.api_v1.endpoints.users import juggling_ball_detection as bd_module
from app.services.juggling.analysis_model_registry import (
    get_model_config,
    ANALYSIS_MODEL_REGISTRY,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _enable_flags(monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)
    monkeypatch.setattr(bd_module.settings, "BALL_DETECTION_ENABLED", True)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def student_user2(db_session):
    from app.models.user import User, UserRole
    from app.core.security import get_password_hash
    user = User(
        name="BDT Student Two", email="bdt_student2@test.com",
        password_hash=get_password_hash("bdt_student2pw"),
        role=UserRole.STUDENT, is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def student_token2(client, student_user2):
    r = client.post("/api/v1/auth/login",
                    json={"email": "bdt_student2@test.com", "password": "bdt_student2pw"})
    return r.json()["access_token"]


@pytest.fixture
def _juggling_video(db_session, student_user):
    consent = JugglingConsent(
        user_id=student_user.id, service_consent=True,
        training_consent=True, admin_review_consent=True,
    )
    db_session.add(consent)
    db_session.flush()
    video = JugglingVideo(
        id=uuid.uuid4(), user_id=student_user.id,
        source_type="uploaded_video", upload_source="gallery",
        training_video_type="juggling",
        status="analyzed",
    )
    db_session.add(video)
    db_session.flush()
    event = JugglingContactEvent(
        id=uuid.uuid4(), video_id=video.id,
        created_by_user_id=student_user.id,
        device_event_id=uuid.uuid4(),
        timestamp_ms=5000, contact_type="right_instep",
        annotation_confidence="certain",
        annotation_review_status="pending",
        annotation_source="manual_user",
        excluded_from_training=True,
        taxonomy_version="v1",
    )
    db_session.add(event)
    db_session.commit()
    return video, event


@pytest.fixture
def _footvolley_video(db_session, student_user):
    """Video with training_video_type=gan_footvolley for type-aware tests."""
    video = JugglingVideo(
        id=uuid.uuid4(), user_id=student_user.id,
        source_type="uploaded_video", upload_source="gallery",
        training_video_type="gan_footvolley",
        status="analyzed",
    )
    db_session.add(video)
    db_session.flush()
    event = JugglingContactEvent(
        id=uuid.uuid4(), video_id=video.id,
        created_by_user_id=student_user.id,
        device_event_id=uuid.uuid4(),
        timestamp_ms=3000, contact_type="head",
        annotation_confidence="probable",
        annotation_review_status="pending",
        annotation_source="manual_user",
        excluded_from_training=True,
        taxonomy_version="v1",
    )
    db_session.add(event)
    db_session.commit()
    return video, event


# ── BDT-01..BDT-02: Feature flag guard ───────────────────────────────────────

def test_bdt01_get_503_when_disabled(client, student_token, monkeypatch):
    monkeypatch.setattr(bd_module.settings, "BALL_DETECTION_ENABLED", False)
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{uuid.uuid4()}/contacts/{uuid.uuid4()}/ball-detection",
        headers=_auth(student_token),
    )
    assert r.status_code == 503


def test_bdt02_post_503_when_disabled(client, student_token, monkeypatch):
    monkeypatch.setattr(bd_module.settings, "BALL_DETECTION_ENABLED", False)
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{uuid.uuid4()}/contacts/{uuid.uuid4()}/ball-detection",
        headers=_auth(student_token),
        json={"ball_x": 0.5, "ball_y": 0.5},
    )
    assert r.status_code == 503


# ── BDT-03..BDT-04: Manual override POST ─────────────────────────────────────

def test_bdt03_post_manual_creates_201(client, student_token, _juggling_video):
    video, event = _juggling_video
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/ball-detection",
        headers=_auth(student_token),
        json={"ball_x": 0.45, "ball_y": 0.78, "confidence": 0.9},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["detection_source"] == "manual"
    assert data["ball_x"] == 0.45
    assert data["ball_y"] == 0.78
    assert data["excluded_from_training"] is True


def test_bdt04_post_upsert_returns_200(client, student_token, _juggling_video):
    video, event = _juggling_video
    r1 = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/ball-detection",
        headers=_auth(student_token),
        json={"ball_x": 0.45, "ball_y": 0.78},
    )
    assert r1.status_code == 201

    r2 = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/ball-detection",
        headers=_auth(student_token),
        json={"ball_x": 0.55, "ball_y": 0.65},
    )
    assert r2.status_code == 200
    assert r2.json()["ball_x"] == 0.55


# ── BDT-05..BDT-06: Ownership guards ─────────────────────────────────────────

def test_bdt05_post_invalid_video_404(client, student_token):
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{uuid.uuid4()}/contacts/{uuid.uuid4()}/ball-detection",
        headers=_auth(student_token),
        json={"ball_x": 0.5, "ball_y": 0.5},
    )
    assert r.status_code == 404


def test_bdt06_post_other_user_video_404(
    client, student_token2, _juggling_video,
):
    video, event = _juggling_video
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/ball-detection",
        headers=_auth(student_token2),
        json={"ball_x": 0.5, "ball_y": 0.5},
    )
    assert r.status_code == 404


# ── BDT-07..BDT-08: Validation ───────────────────────────────────────────────

def test_bdt07_post_ball_x_above_1_422(client, student_token, _juggling_video):
    video, event = _juggling_video
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/ball-detection",
        headers=_auth(student_token),
        json={"ball_x": 1.5, "ball_y": 0.5},
    )
    assert r.status_code == 422


def test_bdt08_post_ball_x_below_0_422(client, student_token, _juggling_video):
    video, event = _juggling_video
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/ball-detection",
        headers=_auth(student_token),
        json={"ball_x": -0.1, "ball_y": 0.5},
    )
    assert r.status_code == 422


# ── BDT-09..BDT-11: GET ──────────────────────────────────────────────────────

def test_bdt09_get_returns_200(client, student_token, _juggling_video):
    video, event = _juggling_video
    client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/ball-detection",
        headers=_auth(student_token),
        json={"ball_x": 0.3, "ball_y": 0.7},
    )
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/ball-detection",
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ball_x"] == 0.3
    assert data["no_ball_detected"] is False


def test_bdt10_get_no_detection_404(client, student_token, _juggling_video):
    video, event = _juggling_video
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/ball-detection",
        headers=_auth(student_token),
    )
    assert r.status_code == 404


def test_bdt11_get_other_user_404(client, student_token2, _juggling_video):
    video, event = _juggling_video
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/ball-detection",
        headers=_auth(student_token2),
    )
    assert r.status_code == 404


# ── BDT-12..BDT-14: Policy B + constraints ───────────────────────────────────

def test_bdt12_excluded_from_training_always_true(client, student_token, _juggling_video):
    video, event = _juggling_video
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/ball-detection",
        headers=_auth(student_token),
        json={"ball_x": 0.5, "ball_y": 0.5},
    )
    assert r.json()["excluded_from_training"] is True


def test_bdt13_no_ball_detected_false_with_coords(client, student_token, _juggling_video):
    video, event = _juggling_video
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/ball-detection",
        headers=_auth(student_token),
        json={"ball_x": 0.5, "ball_y": 0.5},
    )
    data = r.json()
    assert data["no_ball_detected"] is False
    assert data["ball_x"] is not None


def test_bdt14_world_coords_null(client, student_token, _juggling_video):
    video, event = _juggling_video
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/ball-detection",
        headers=_auth(student_token),
        json={"ball_x": 0.5, "ball_y": 0.5},
    )
    data = r.json()
    assert data["world_x_m"] is None
    assert data["world_y_m"] is None


# ── TVT-01..TVT-06: training_video_type regression tests ─────────────────────

def test_tvt01_upload_init_without_type_defaults_juggling(client, student_token, _juggling_video):
    r = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        headers=_auth(student_token),
        json={"source_type": "uploaded_video", "upload_source": "gallery"},
    )
    assert r.status_code == 201
    vid = r.json()["video_id"]
    videos_r = client.get(
        "/api/v1/users/me/juggling/videos",
        headers=_auth(student_token),
    )
    items = videos_r.json()["videos"]
    match = [v for v in items if v["video_id"] == vid]
    assert len(match) == 1
    assert match[0]["training_video_type"] == "juggling"


def test_tvt02_upload_init_explicit_juggling(client, student_token, _juggling_video):
    r = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        headers=_auth(student_token),
        json={"source_type": "uploaded_video", "training_video_type": "juggling"},
    )
    assert r.status_code == 201


def test_tvt03_upload_init_footvolley(client, student_token, _juggling_video):
    r = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        headers=_auth(student_token),
        json={"source_type": "uploaded_video", "training_video_type": "gan_footvolley"},
    )
    assert r.status_code == 201


def test_tvt04_upload_init_foottennis(client, student_token, _juggling_video):
    r = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        headers=_auth(student_token),
        json={"source_type": "uploaded_video", "training_video_type": "gan_foottennis"},
    )
    assert r.status_code == 201


def test_tvt05_upload_init_unknown_type_422(client, student_token, _juggling_video):
    r = client.post(
        "/api/v1/users/me/juggling/videos/upload-init",
        headers=_auth(student_token),
        json={"source_type": "uploaded_video", "training_video_type": "unknown_sport"},
    )
    assert r.status_code == 422


def test_tvt06_video_list_has_training_type(client, student_token, _juggling_video):
    r = client.get(
        "/api/v1/users/me/juggling/videos",
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    for v in r.json()["videos"]:
        assert "training_video_type" in v


# ── BDT-D-01..BDT-D-04: Model registry dispatch tests ────────────────────────

def test_bdt_d01_registry_juggling():
    cfg = get_model_config("juggling")
    assert cfg.detection_source == "mobilenet_ssd_v2"
    assert cfg.target_class_name == "sports_ball"


def test_bdt_d02_registry_footvolley():
    cfg = get_model_config("gan_footvolley")
    assert cfg.detection_source == "mobilenet_ssd_v2"


def test_bdt_d03_registry_foottennis():
    cfg = get_model_config("gan_foottennis")
    assert cfg.detection_source == "mobilenet_ssd_v2"


def test_bdt_d04_registry_unknown_fallback():
    cfg = get_model_config("unknown_future_sport")
    assert cfg.detection_source == "mobilenet_ssd_v2"
    assert cfg == get_model_config("juggling")


# ── BDT-D-05: Ball detection on footvolley video ─────────────────────────────

def test_bdt_d05_manual_detection_on_footvolley(
    client, student_token, _footvolley_video,
):
    video, event = _footvolley_video
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{event.id}/ball-detection",
        headers=_auth(student_token),
        json={"ball_x": 0.6, "ball_y": 0.4},
    )
    assert r.status_code == 201
    assert r.json()["detection_source"] == "manual"
