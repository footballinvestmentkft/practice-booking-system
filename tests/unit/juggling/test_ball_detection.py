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
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event
from sqlalchemy.orm import sessionmaker

from app.core.auth import create_access_token
from app.database import engine, get_db
from app.main import app
from app.models.juggling import (
    JugglingConsent,
    JugglingContactEvent,
    JugglingVideo,
    JugglingBallDetection,
)
from app.models.user import User, UserRole
from app.services.juggling import feature_flag as ff_module
from app.api.api_v1.endpoints.users import juggling_ball_detection as bd_module
from app.services.juggling.analysis_model_registry import (
    get_model_config,
    ANALYSIS_MODEL_REGISTRY,
)


# ── Local fixtures ────────────────────────────────────────────────────────────

@pytest.fixture()
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = TestSession()
    connection.begin_nested()

    @sa_event.listens_for(session, "after_transaction_end")
    def restart_savepoint(sess, txn):
        if txn.nested and not txn._parent.nested:
            sess.begin_nested()

    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def student_user(db_session):
    user = User(
        email=f"bdt_student+{uuid.uuid4().hex[:8]}@test.com",
        name="BDT Student",
        password_hash="hashed",
        role=UserRole.STUDENT,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def student_token(student_user):
    return create_access_token(
        data={"sub": student_user.email}, expires_delta=timedelta(hours=1)
    )


@pytest.fixture(autouse=True)
def _enable_flags(monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)
    monkeypatch.setattr(bd_module.settings, "BALL_DETECTION_ENABLED", True)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def student_user2(db_session):
    user = User(
        email=f"bdt_student2+{uuid.uuid4().hex[:8]}@test.com",
        name="BDT Student Two",
        password_hash="hashed",
        role=UserRole.STUDENT,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def student_token2(student_user2):
    return create_access_token(
        data={"sub": student_user2.email}, expires_delta=timedelta(hours=1)
    )


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
    assert cfg.detection_source == "mobilenet_ssd_v1"
    assert cfg.target_class_name == "sports_ball"


def test_bdt_d02_registry_footvolley():
    cfg = get_model_config("gan_footvolley")
    assert cfg.detection_source == "mobilenet_ssd_v1"


def test_bdt_d03_registry_foottennis():
    cfg = get_model_config("gan_foottennis")
    assert cfg.detection_source == "mobilenet_ssd_v1"


def test_bdt_d04_registry_unknown_fallback():
    cfg = get_model_config("unknown_future_sport")
    assert cfg.detection_source == "mobilenet_ssd_v1"
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


# ── BDT-A-01..BDT-A-05: Admin trigger endpoint tests ─────────────────────────

@pytest.fixture
def admin_token(db_session):
    admin = User(
        email=f"bdt_admin+{uuid.uuid4().hex[:8]}@test.com",
        name="BDT Admin",
        password_hash="hashed",
        role=UserRole.ADMIN,
        is_active=True,
    )
    db_session.add(admin)
    db_session.commit()
    db_session.refresh(admin)
    return create_access_token(
        data={"sub": admin.email}, expires_delta=timedelta(hours=1)
    )


@pytest.fixture
def _confirmed_video(db_session, student_user):
    """Video with one confirmed event for admin trigger tests."""
    video = JugglingVideo(
        id=uuid.uuid4(), user_id=student_user.id,
        source_type="uploaded_video", upload_source="gallery",
        training_video_type="juggling", status="analyzed",
    )
    db_session.add(video)
    db_session.flush()
    event = JugglingContactEvent(
        id=uuid.uuid4(), video_id=video.id,
        created_by_user_id=student_user.id,
        device_event_id=uuid.uuid4(),
        timestamp_ms=5000, contact_type="right_instep",
        annotation_confidence="certain",
        annotation_review_status="confirmed",
        annotation_source="manual_user",
        excluded_from_training=True,
        taxonomy_version="v1",
    )
    db_session.add(event)
    db_session.commit()
    return video, event


def test_bdt_a01_admin_trigger_queues_events(
    client, admin_token, _confirmed_video, monkeypatch,
):
    video, event = _confirmed_video
    dispatched = []
    monkeypatch.setattr(
        "app.tasks.juggling_analysis_task.detect_ball_for_event",
        type("MockTask", (), {"delay": lambda *a, **kw: dispatched.append((a, kw))})(),
    )
    r = client.post(
        f"/api/v1/admin/juggling/videos/{video.id}/trigger-ball-detection",
        headers=_auth(admin_token),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["events_queued"] == 1
    assert data["training_video_type"] == "juggling"
    assert data["model_used"] == "ssd_mobilenet_v1_12_onnx"
    assert len(dispatched) == 1


def test_bdt_a02_admin_trigger_no_confirmed_events(
    client, admin_token, _juggling_video, monkeypatch,
):
    video, event = _juggling_video
    dispatched = []
    monkeypatch.setattr(
        "app.tasks.juggling_analysis_task.detect_ball_for_event",
        type("MockTask", (), {"delay": lambda *a, **kw: dispatched.append((a, kw))})(),
    )
    r = client.post(
        f"/api/v1/admin/juggling/videos/{video.id}/trigger-ball-detection",
        headers=_auth(admin_token),
    )
    assert r.status_code == 200
    assert r.json()["events_queued"] == 0
    assert len(dispatched) == 0


def test_bdt_a03_admin_trigger_non_admin_403(
    client, student_token, _confirmed_video,
):
    video, _ = _confirmed_video
    r = client.post(
        f"/api/v1/admin/juggling/videos/{video.id}/trigger-ball-detection",
        headers=_auth(student_token),
    )
    assert r.status_code == 403


def test_bdt_a04_admin_trigger_invalid_video_404(client, admin_token):
    r = client.post(
        f"/api/v1/admin/juggling/videos/{uuid.uuid4()}/trigger-ball-detection",
        headers=_auth(admin_token),
    )
    assert r.status_code == 404


def test_bdt_a05_admin_trigger_disabled_503(
    client, admin_token, _confirmed_video, monkeypatch,
):
    from app.api.api_v1.endpoints import juggling_admin_ball_detection as admin_bd_module
    monkeypatch.setattr(admin_bd_module.settings, "BALL_DETECTION_ENABLED", False)
    video, _ = _confirmed_video
    r = client.post(
        f"/api/v1/admin/juggling/videos/{video.id}/trigger-ball-detection",
        headers=_auth(admin_token),
    )
    assert r.status_code == 503


# ── BDT-FR-01..02: Frame extractor unit tests ────────────────────────────────

def test_bdt_fr01_extract_frame_mock(monkeypatch):
    import numpy as np

    class MockCap:
        def isOpened(self): return True
        def set(self, prop, val): pass
        def read(self): return True, np.zeros((240, 320, 3), dtype=np.uint8)
        def release(self): pass

    import app.services.juggling.frame_extractor as fe_module
    monkeypatch.setattr(fe_module.cv2, "VideoCapture", lambda _: MockCap())
    monkeypatch.setattr(fe_module.cv2, "cvtColor", lambda f, _: f)
    frame, w, h = fe_module.extract_frame_at_ms("/fake/path.mp4", 5000)
    assert w == 320
    assert h == 240


def test_bdt_fr02_extract_frame_invalid_path():
    from app.services.juggling.frame_extractor import extract_frame_at_ms
    with pytest.raises(ValueError, match="Cannot open video"):
        extract_frame_at_ms("/nonexistent/video.mp4", 0)


# ── BDT-OD-01..03: ONNX detector unit tests ─────────────────────────────────

def test_bdt_od01_detect_mock_session(monkeypatch):
    import numpy as np
    from app.services.juggling.onnx_ball_detector import OnnxBallDetector

    mock_outputs = [
        np.array([1.0]),
        np.array([[[0.3, 0.4, 0.5, 0.6]]]),
        np.array([[0.85]]),
        np.array([[37.0]]),
    ]

    class MockSession:
        def run(self, _, inputs): return mock_outputs

    monkeypatch.setattr(
        "app.services.juggling.onnx_ball_detector.ort.InferenceSession",
        lambda *a, **kw: MockSession(),
    )
    monkeypatch.setattr(
        "app.services.juggling.onnx_ball_detector.Path.is_file",
        lambda _: True,
    )

    detector = OnnxBallDetector("/fake/model.onnx")
    frame = np.zeros((300, 300, 3), dtype=np.uint8)
    result = detector.detect(frame, target_class_id=37, confidence_threshold=0.3)
    assert result is not None
    cx, cy, conf = result
    assert 0.4 < cx < 0.6
    assert 0.3 < cy < 0.5
    assert conf == 0.85


def test_bdt_od02_detect_no_ball(monkeypatch):
    import numpy as np
    from app.services.juggling.onnx_ball_detector import OnnxBallDetector

    mock_outputs = [
        np.array([1.0]),
        np.array([[[0.1, 0.2, 0.3, 0.4]]]),
        np.array([[0.9]]),
        np.array([[1.0]]),  # class 1 = person, not sports_ball
    ]

    class MockSession:
        def run(self, _, inputs): return mock_outputs

    monkeypatch.setattr(
        "app.services.juggling.onnx_ball_detector.ort.InferenceSession",
        lambda *a, **kw: MockSession(),
    )
    monkeypatch.setattr(
        "app.services.juggling.onnx_ball_detector.Path.is_file",
        lambda _: True,
    )

    detector = OnnxBallDetector("/fake/model.onnx")
    frame = np.zeros((300, 300, 3), dtype=np.uint8)
    result = detector.detect(frame, target_class_id=37)
    assert result is None


def test_bdt_od03_detect_below_threshold(monkeypatch):
    import numpy as np
    from app.services.juggling.onnx_ball_detector import OnnxBallDetector

    mock_outputs = [
        np.array([1.0]),
        np.array([[[0.3, 0.4, 0.5, 0.6]]]),
        np.array([[0.15]]),  # below 0.3 threshold
        np.array([[37.0]]),
    ]

    class MockSession:
        def run(self, _, inputs): return mock_outputs

    monkeypatch.setattr(
        "app.services.juggling.onnx_ball_detector.ort.InferenceSession",
        lambda *a, **kw: MockSession(),
    )
    monkeypatch.setattr(
        "app.services.juggling.onnx_ball_detector.Path.is_file",
        lambda _: True,
    )

    detector = OnnxBallDetector("/fake/model.onnx")
    frame = np.zeros((300, 300, 3), dtype=np.uint8)
    result = detector.detect(frame, target_class_id=37, confidence_threshold=0.3)
    assert result is None


# ── BDT-OD-04: ONNX model file missing → FileNotFoundError ───────────────────

def test_bdt_od04_model_file_missing():
    from app.services.juggling.onnx_ball_detector import OnnxBallDetector
    with pytest.raises(FileNotFoundError, match="ONNX model not found"):
        OnnxBallDetector("/nonexistent/path/model.onnx")


# ── BDT-OD-05: get_detector cache ────────────────────────────────────────────

def test_bdt_od05_get_detector_cache(monkeypatch):
    import numpy as np
    from app.services.juggling import onnx_ball_detector as od_module

    class MockSession:
        def run(self, _, inputs): return [np.array([0.0]), np.array([[[]]]), np.array([[]]), np.array([[]])]

    monkeypatch.setattr(od_module.ort, "InferenceSession", lambda *a, **kw: MockSession())
    monkeypatch.setattr(od_module.Path, "is_file", lambda _: True)
    od_module._detector_cache.clear()

    d1 = od_module.get_detector("/fake/cached.onnx")
    d2 = od_module.get_detector("/fake/cached.onnx")
    assert d1 is d2
    od_module._detector_cache.clear()


# ── BDT-OD-06: zero detections (num_det=0) ───────────────────────────────────

def test_bdt_od06_zero_detections(monkeypatch):
    import numpy as np
    from app.services.juggling.onnx_ball_detector import OnnxBallDetector

    mock_outputs = [
        np.array([0.0]),
        np.array([[[]]]).reshape(1, 0, 4),
        np.array([[]]).reshape(1, 0),
        np.array([[]]).reshape(1, 0),
    ]

    class MockSession:
        def run(self, _, inputs): return mock_outputs

    monkeypatch.setattr("app.services.juggling.onnx_ball_detector.ort.InferenceSession", lambda *a, **kw: MockSession())
    monkeypatch.setattr("app.services.juggling.onnx_ball_detector.Path.is_file", lambda _: True)

    detector = OnnxBallDetector("/fake/model.onnx")
    frame = np.zeros((300, 300, 3), dtype=np.uint8)
    result = detector.detect(frame)
    assert result is None


# ── BDT-CORE-01..09: run_ball_detection_core direct tests ────────────────────

def test_bdt_core01_disabled_flag(db_session, monkeypatch):
    from app.tasks.juggling_analysis_task import run_ball_detection_core
    import app.tasks.juggling_analysis_task as task_mod
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_ENABLED", False)
    result = run_ball_detection_core(str(uuid.uuid4()), str(uuid.uuid4()), "juggling", db_session)
    assert result["status"] == "skipped"
    assert "BALL_DETECTION_ENABLED" in result["reason"]


def test_bdt_core02_model_missing(db_session, monkeypatch):
    from app.tasks.juggling_analysis_task import run_ball_detection_core
    import app.tasks.juggling_analysis_task as task_mod
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_ENABLED", True)
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_MODEL_PATH", "/nonexistent/model.onnx")
    result = run_ball_detection_core(str(uuid.uuid4()), str(uuid.uuid4()), "juggling", db_session)
    assert result["status"] == "failed"
    assert "model file missing" in result["reason"]


def test_bdt_core03_video_not_found(db_session, monkeypatch):
    from app.tasks.juggling_analysis_task import run_ball_detection_core
    import app.tasks.juggling_analysis_task as task_mod
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_ENABLED", True)
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_MODEL_PATH", __file__)
    result = run_ball_detection_core(str(uuid.uuid4()), str(uuid.uuid4()), "juggling", db_session)
    assert result["status"] == "failed"
    assert result["reason"] == "video not found"


def test_bdt_core04_event_not_found(db_session, _juggling_video, monkeypatch):
    from app.tasks.juggling_analysis_task import run_ball_detection_core
    import app.tasks.juggling_analysis_task as task_mod
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_ENABLED", True)
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_MODEL_PATH", __file__)
    video, _ = _juggling_video
    result = run_ball_detection_core(str(video.id), str(uuid.uuid4()), "juggling", db_session)
    assert result["status"] == "failed"
    assert result["reason"] == "event not found"


def test_bdt_core05_detection_already_exists(db_session, _juggling_video, monkeypatch):
    from app.tasks.juggling_analysis_task import run_ball_detection_core
    import app.tasks.juggling_analysis_task as task_mod
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_ENABLED", True)
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_MODEL_PATH", __file__)
    video, event = _juggling_video
    existing = JugglingBallDetection(
        contact_event_id=event.id, video_id=video.id,
        detection_source="manual", ball_x=0.5, ball_y=0.5,
        no_ball_detected=False, excluded_from_training=True,
    )
    db_session.add(existing)
    db_session.flush()
    result = run_ball_detection_core(str(video.id), str(event.id), "juggling", db_session)
    assert result["status"] == "skipped"
    assert "already exists" in result["reason"]


def test_bdt_core06_no_video_file(db_session, _juggling_video, monkeypatch):
    from app.tasks.juggling_analysis_task import run_ball_detection_core
    import app.tasks.juggling_analysis_task as task_mod
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_ENABLED", True)
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_MODEL_PATH", __file__)
    video, event = _juggling_video
    result = run_ball_detection_core(str(video.id), str(event.id), "juggling", db_session)
    assert result["status"] == "failed"
    assert "video file not found" in result["reason"]


def test_bdt_core07_ball_detected(db_session, _juggling_video, monkeypatch, tmp_path):
    import numpy as np
    from app.tasks.juggling_analysis_task import run_ball_detection_core
    import app.tasks.juggling_analysis_task as task_mod

    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_ENABLED", True)
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_MODEL_PATH", __file__)

    video, event = _juggling_video
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"fake")
    video.processed_path = str(fake_video)
    db_session.flush()

    mock_frame = np.zeros((240, 320, 3), dtype=np.uint8)

    class MockDetector:
        def detect(self, frame, target_class_id=37, confidence_threshold=0.3):
            return (0.45, 0.67, 0.91)

    result = run_ball_detection_core(
        str(video.id), str(event.id), "juggling", db_session,
        _extract_frame=lambda path, ms: (mock_frame, 320, 240),
        _get_detector=lambda path: MockDetector(),
    )
    assert result["status"] == "detected"
    assert result["ball_x"] == 0.45
    assert result["ball_y"] == 0.67
    assert result["confidence"] == 0.91


def test_bdt_core08_no_ball_detected(db_session, _juggling_video, monkeypatch, tmp_path):
    import numpy as np
    from app.tasks.juggling_analysis_task import run_ball_detection_core
    import app.tasks.juggling_analysis_task as task_mod

    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_ENABLED", True)
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_MODEL_PATH", __file__)

    video, event = _juggling_video
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"fake")
    video.processed_path = str(fake_video)
    db_session.flush()

    mock_frame = np.zeros((240, 320, 3), dtype=np.uint8)

    class MockDetector:
        def detect(self, frame, target_class_id=37, confidence_threshold=0.3):
            return None

    result = run_ball_detection_core(
        str(video.id), str(event.id), "juggling", db_session,
        _extract_frame=lambda path, ms: (mock_frame, 320, 240),
        _get_detector=lambda path: MockDetector(),
    )
    assert result["status"] == "not_detected"
    assert result["ball_x"] is None


def test_bdt_core09_type_aware_footvolley(db_session, _footvolley_video, monkeypatch, tmp_path):
    import numpy as np
    from app.tasks.juggling_analysis_task import run_ball_detection_core
    import app.tasks.juggling_analysis_task as task_mod

    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_ENABLED", True)
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_MODEL_PATH", __file__)

    video, event = _footvolley_video
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"fake")
    video.processed_path = str(fake_video)
    db_session.flush()

    mock_frame = np.zeros((240, 320, 3), dtype=np.uint8)

    class MockDetector:
        def detect(self, frame, target_class_id=37, confidence_threshold=0.3):
            return (0.3, 0.4, 0.8)

    result = run_ball_detection_core(
        str(video.id), str(event.id), "gan_footvolley", db_session,
        _extract_frame=lambda path, ms: (mock_frame, 320, 240),
        _get_detector=lambda path: MockDetector(),
    )
    assert result["status"] == "detected"


# ── BDT-SVC-01..02: ball_detection_service edge cases ────────────────────────

def test_bdt_svc01_invalid_video_uuid(client, student_token):
    r = client.post(
        "/api/v1/users/me/juggling/videos/not-a-uuid/contacts/not-a-uuid/ball-detection",
        headers=_auth(student_token),
        json={"ball_x": 0.5, "ball_y": 0.5},
    )
    assert r.status_code == 404


def test_bdt_svc02_invalid_event_uuid(client, student_token, _juggling_video):
    video, _ = _juggling_video
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/not-a-uuid/ball-detection",
        headers=_auth(student_token),
        json={"ball_x": 0.5, "ball_y": 0.5},
    )
    assert r.status_code == 404


# ── BDT-ADM-06: admin trigger with invalid UUID ──────────────────────────────

def test_bdt_adm06_admin_trigger_invalid_uuid(client, admin_token):
    r = client.post(
        "/api/v1/admin/juggling/videos/not-a-uuid/trigger-ball-detection",
        headers=_auth(admin_token),
    )
    assert r.status_code == 404


# ── BDT-VP-01: _video_path helper ────────────────────────────────────────────

def test_bdt_vp01_video_path_fallback(tmp_path):
    from app.tasks.juggling_analysis_task import _video_path
    from unittest.mock import MagicMock
    video = MagicMock()
    video.processed_path = "/nonexistent/processed.mp4"
    fallback = tmp_path / "original.mp4"
    fallback.write_bytes(b"data")
    video.storage_path = str(fallback)
    assert _video_path(video) == str(fallback)


def test_bdt_vp02_video_path_none():
    from app.tasks.juggling_analysis_task import _video_path
    from unittest.mock import MagicMock
    video = MagicMock()
    video.processed_path = None
    video.storage_path = None
    assert _video_path(video) is None


def test_bdt_core10_deferred_import_paths(db_session, _juggling_video, monkeypatch, tmp_path):
    """Cover the _extract_frame=None / _get_detector=None deferred import branches."""
    import numpy as np
    from app.tasks.juggling_analysis_task import run_ball_detection_core
    import app.tasks.juggling_analysis_task as task_mod
    import app.services.juggling.frame_extractor as fe_mod
    import app.services.juggling.onnx_ball_detector as od_mod

    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_ENABLED", True)
    monkeypatch.setattr(task_mod.settings, "BALL_DETECTION_MODEL_PATH", __file__)

    video, event = _juggling_video
    fake_video = tmp_path / "fake.mp4"
    fake_video.write_bytes(b"fake")
    video.processed_path = str(fake_video)
    db_session.flush()

    mock_frame = np.zeros((240, 320, 3), dtype=np.uint8)
    monkeypatch.setattr(fe_mod, "extract_frame_at_ms", lambda path, ms: (mock_frame, 320, 240))

    class MockDetector:
        def detect(self, frame, target_class_id=37, confidence_threshold=0.3):
            return (0.5, 0.5, 0.7)

    monkeypatch.setattr(od_mod, "get_detector", lambda path: MockDetector())

    result = run_ball_detection_core(
        str(video.id), str(event.id), "juggling", db_session,
    )
    assert result["status"] == "detected"


def test_bdt_adm07_admin_trigger_skips_existing_detection(
    client, admin_token, _confirmed_video, db_session, monkeypatch,
):
    """Cover admin_ball_detection.py:89-90 — skip existing detection."""
    video, event = _confirmed_video
    existing = JugglingBallDetection(
        contact_event_id=event.id, video_id=video.id,
        detection_source="manual", ball_x=0.5, ball_y=0.5,
        no_ball_detected=False, excluded_from_training=True,
    )
    db_session.add(existing)
    db_session.commit()
    dispatched = []
    monkeypatch.setattr(
        "app.tasks.juggling_analysis_task.detect_ball_for_event",
        type("MockTask", (), {"delay": lambda *a, **kw: dispatched.append(1)})(),
    )
    r = client.post(
        f"/api/v1/admin/juggling/videos/{video.id}/trigger-ball-detection",
        headers=_auth(admin_token),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["events_queued"] == 0
    assert any("already exists" in s for s in data["skipped_reasons"])


def test_bdt_adm08_admin_trigger_mixed_confirmed_pending(
    client, admin_token, db_session, student_user, monkeypatch,
):
    """Cover admin_ball_detection.py:98-106 — not_confirmed count in response."""
    video = JugglingVideo(
        id=uuid.uuid4(), user_id=student_user.id,
        source_type="uploaded_video", upload_source="gallery",
        training_video_type="juggling", status="analyzed",
    )
    db_session.add(video)
    db_session.flush()
    confirmed = JugglingContactEvent(
        id=uuid.uuid4(), video_id=video.id,
        created_by_user_id=student_user.id, device_event_id=uuid.uuid4(),
        timestamp_ms=1000, contact_type="right_instep",
        annotation_confidence="certain",
        annotation_review_status="confirmed",
        annotation_source="manual_user",
        excluded_from_training=True, taxonomy_version="v1",
    )
    pending = JugglingContactEvent(
        id=uuid.uuid4(), video_id=video.id,
        created_by_user_id=student_user.id, device_event_id=uuid.uuid4(),
        timestamp_ms=2000, contact_type="head",
        annotation_confidence="probable",
        annotation_review_status="pending",
        annotation_source="manual_user",
        excluded_from_training=True, taxonomy_version="v1",
    )
    db_session.add_all([confirmed, pending])
    db_session.commit()
    dispatched = []
    monkeypatch.setattr(
        "app.tasks.juggling_analysis_task.detect_ball_for_event",
        type("MockTask", (), {"delay": lambda *a, **kw: dispatched.append(1)})(),
    )
    r = client.post(
        f"/api/v1/admin/juggling/videos/{video.id}/trigger-ball-detection",
        headers=_auth(admin_token),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["events_queued"] == 1
    assert data["events_skipped"] >= 1
    assert any("not in 'confirmed' status" in s for s in data["skipped_reasons"])


def test_bdt_svc03_get_event_invalid_uuid(client, student_token, _juggling_video):
    """Cover ball_detection_service.py:39-40 — event UUID parse error in GET."""
    video, _ = _juggling_video
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/not-a-uuid/ball-detection",
        headers=_auth(student_token),
    )
    assert r.status_code == 404


def test_bdt_svc04_get_event_not_found(client, student_token, _juggling_video):
    """Cover ball_detection_service.py:51 — event exists but belongs to different video."""
    video, _ = _juggling_video
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video.id}/contacts/{uuid.uuid4()}/ball-detection",
        headers=_auth(student_token),
    )
    assert r.status_code == 404


def test_bdt_fr03_frame_bgr_none(monkeypatch):
    """Cover frame_extractor.py:34 — cv2 read returns None frame."""
    import app.services.juggling.frame_extractor as fe_mod

    class MockCap:
        def isOpened(self): return True
        def set(self, prop, val): pass
        def read(self): return True, None
        def release(self): pass

    monkeypatch.setattr(fe_mod.cv2, "VideoCapture", lambda _: MockCap())
    with pytest.raises(ValueError, match="Frame extraction failed"):
        fe_mod.extract_frame_at_ms("/fake/path.mp4", 5000)


def test_bdt_fr04_read_returns_false(monkeypatch):
    """Cover frame_extractor.py:34 — cv2 read returns ret=False."""
    import numpy as np
    import app.services.juggling.frame_extractor as fe_mod

    class MockCap:
        def isOpened(self): return True
        def set(self, prop, val): pass
        def read(self): return False, np.zeros((1, 1, 3), dtype=np.uint8)
        def release(self): pass

    monkeypatch.setattr(fe_mod.cv2, "VideoCapture", lambda _: MockCap())
    with pytest.raises(ValueError, match="Frame extraction failed"):
        fe_mod.extract_frame_at_ms("/fake/path.mp4", 5000)


def test_bdt_od07_second_detection_lower_score(monkeypatch):
    """Cover onnx_ball_detector.py:60->58 — second detection not > best_score."""
    import numpy as np
    from app.services.juggling.onnx_ball_detector import OnnxBallDetector

    mock_outputs = [
        np.array([2.0]),
        np.array([[[0.3, 0.4, 0.5, 0.6], [0.1, 0.2, 0.3, 0.4]]]),
        np.array([[0.85, 0.60]]),
        np.array([[37.0, 37.0]]),
    ]

    class MockSession:
        def run(self, _, inputs): return mock_outputs

    monkeypatch.setattr("app.services.juggling.onnx_ball_detector.ort.InferenceSession", lambda *a, **kw: MockSession())
    monkeypatch.setattr("app.services.juggling.onnx_ball_detector.Path.is_file", lambda _: True)

    detector = OnnxBallDetector("/fake/model.onnx")
    frame = np.zeros((300, 300, 3), dtype=np.uint8)
    result = detector.detect(frame, target_class_id=37, confidence_threshold=0.3)
    assert result is not None
    _, _, conf = result
    assert conf == 0.85


def test_bdt_vp03_video_path_prefers_processed(tmp_path):
    from app.tasks.juggling_analysis_task import _video_path
    from unittest.mock import MagicMock
    processed = tmp_path / "processed.mp4"
    processed.write_bytes(b"data")
    video = MagicMock()
    video.processed_path = str(processed)
    video.storage_path = "/other/path.mp4"
    assert _video_path(video) == str(processed)
