"""
Dense ball trajectory API tests — BT-20..BT-28.

Endpoint tests using FastAPI TestClient + PostgreSQL savepoint.
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
    JugglingBallTrajectory,
    JugglingConsent,
    JugglingVideo,
    JugglingVideoStatus,
)
from app.models.user import User, UserRole
from app.services.juggling import feature_flag as ff_module
import app.api.api_v1.endpoints.users.juggling_ball_trajectory as bt_module


# ── Fixtures ──────────────────────────────────────────────────────────────────

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
        email=f"bt_student+{uuid.uuid4().hex[:8]}@test.com",
        name="BT Student",
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


@pytest.fixture()
def student_user2(db_session):
    user = User(
        email=f"bt_student2+{uuid.uuid4().hex[:8]}@test.com",
        name="BT Student Two",
        password_hash="hashed",
        role=UserRole.STUDENT,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture()
def student_token2(student_user2):
    return create_access_token(
        data={"sub": student_user2.email}, expires_delta=timedelta(hours=1)
    )


@pytest.fixture(autouse=True)
def _enable_flags(monkeypatch):
    monkeypatch.setattr(ff_module, "is_juggling_enabled", lambda: True)
    monkeypatch.setattr(bt_module.settings, "BALL_TRAJECTORY_ENABLED", True)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def _video_with_trajectory(db_session, student_user):
    """Create a video with trajectory status=complete and some points."""
    consent = JugglingConsent(
        user_id=student_user.id, service_consent=True,
        training_consent=True, admin_review_consent=True,
    )
    db_session.add(consent)
    db_session.flush()

    vid_id = uuid.uuid4()
    video = JugglingVideo(
        id=vid_id, user_id=student_user.id,
        source_type="uploaded_video", upload_source="gallery",
        training_video_type="juggling",
        storage_path="/tmp/test.mp4",
        status=JugglingVideoStatus.analyzed.value,
        transcode_status="done",
        ball_trajectory_status="complete",
    )
    db_session.add(video)
    db_session.flush()

    for ms in range(0, 1000, 100):
        point = JugglingBallTrajectory(
            video_id=vid_id,
            frame_ms=ms,
            ball_x=0.5 + ms * 0.0001,
            ball_y=0.5 + ms * 0.0001,
            confidence=0.8,
            is_manual=False,
            tracking_state="detected",
        )
        db_session.add(point)

    db_session.commit()
    return str(vid_id)


@pytest.fixture()
def _video_no_trajectory(db_session, student_user):
    """Create a video without trajectory data (status=NULL)."""
    consent = db_session.query(JugglingConsent).filter(
        JugglingConsent.user_id == student_user.id
    ).first()
    if consent is None:
        consent = JugglingConsent(
            user_id=student_user.id, service_consent=True,
            training_consent=True, admin_review_consent=True,
        )
        db_session.add(consent)
        db_session.flush()

    vid_id = uuid.uuid4()
    video = JugglingVideo(
        id=vid_id, user_id=student_user.id,
        source_type="uploaded_video", upload_source="gallery",
        training_video_type="juggling",
        storage_path="/tmp/test.mp4",
        status=JugglingVideoStatus.analyzed.value,
        transcode_status="done",
        ball_trajectory_status=None,
    )
    db_session.add(video)
    db_session.commit()
    return str(vid_id)


# ── Tests ─────────────────────────────────────────────────────────────────────

# BT-20: GET /ball-trajectory → 503 when disabled
def test_bt20_get_503_when_disabled(client, student_token, _video_with_trajectory, monkeypatch):
    monkeypatch.setattr(bt_module.settings, "BALL_TRAJECTORY_ENABLED", False)
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{_video_with_trajectory}/ball-trajectory",
        headers=_auth(student_token),
    )
    assert r.status_code == 503


# BT-21: GET /ball-trajectory → 404 when no trajectory data
def test_bt21_get_404_no_trajectory(client, student_token, _video_no_trajectory):
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{_video_no_trajectory}/ball-trajectory",
        headers=_auth(student_token),
    )
    assert r.status_code == 404


# BT-22: GET /ball-trajectory → 200 with correct points
def test_bt22_get_200_with_points(client, student_token, _video_with_trajectory):
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{_video_with_trajectory}/ball-trajectory"
        "?from_ms=0&to_ms=500",
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "complete"
    assert len(data["points"]) == 6  # 0, 100, 200, 300, 400, 500
    assert data["points"][0]["frame_ms"] == 0
    assert data["points"][0]["tracking_state"] == "detected"


# BT-23: GET /ball-trajectory window too large → 422
def test_bt23_window_too_large(client, student_token, _video_with_trajectory):
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{_video_with_trajectory}/ball-trajectory"
        "?from_ms=0&to_ms=100000",
        headers=_auth(student_token),
    )
    assert r.status_code == 422


# BT-24: POST /manual-seed → 201 creates point
def test_bt24_post_seed_201(client, student_token, _video_with_trajectory):
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{_video_with_trajectory}/ball-trajectory/manual-seed",
        headers=_auth(student_token),
        json={"frame_ms": 5000, "ball_x": 0.42, "ball_y": 0.71},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["frame_ms"] == 5000
    assert data["ball_x"] == 0.42
    assert data["ball_y"] == 0.71
    assert data["tracking_state"] == "manual_seed"
    assert data["is_manual"] is True


# BT-25: POST /manual-seed → 200 upserts existing
def test_bt25_post_seed_200_upsert(client, student_token, _video_with_trajectory):
    r1 = client.post(
        f"/api/v1/users/me/juggling/videos/{_video_with_trajectory}/ball-trajectory/manual-seed",
        headers=_auth(student_token),
        json={"frame_ms": 100, "ball_x": 0.99, "ball_y": 0.99},
    )
    assert r1.status_code == 200
    data = r1.json()
    assert data["ball_x"] == 0.99
    assert data["is_manual"] is True


# BT-26: POST /manual-seed → 503 when disabled
def test_bt26_post_seed_503_disabled(client, student_token, _video_with_trajectory, monkeypatch):
    monkeypatch.setattr(bt_module.settings, "BALL_TRAJECTORY_ENABLED", False)
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{_video_with_trajectory}/ball-trajectory/manual-seed",
        headers=_auth(student_token),
        json={"frame_ms": 100, "ball_x": 0.5, "ball_y": 0.5},
    )
    assert r.status_code == 503


# BT-27: POST /manual-seed → 404 other user's video
def test_bt27_post_seed_404_other_user(client, student_token2, _video_with_trajectory):
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{_video_with_trajectory}/ball-trajectory/manual-seed",
        headers=_auth(student_token2),
        json={"frame_ms": 100, "ball_x": 0.5, "ball_y": 0.5},
    )
    assert r.status_code == 404


# BT-28: POST /manual-seed → 422 invalid ball_x
def test_bt28_post_seed_422_invalid(client, student_token, _video_with_trajectory):
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{_video_with_trajectory}/ball-trajectory/manual-seed",
        headers=_auth(student_token),
        json={"frame_ms": 100, "ball_x": 1.5, "ball_y": 0.5},
    )
    assert r.status_code == 422
