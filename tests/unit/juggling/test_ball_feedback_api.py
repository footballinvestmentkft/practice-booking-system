"""
Ball feedback API tests — BFB-01..18.

POST /api/v1/users/me/juggling/videos/{video_id}/ball-feedback
GET  /api/v1/users/me/juggling/videos/{video_id}/ball-feedback/queue

FastAPI TestClient + PostgreSQL savepoint (no permanent DB writes).
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
    JugglingBallFeedback,
    JugglingBallTrajectory,
    JugglingConsent,
    JugglingVideo,
    JugglingVideoStatus,
    UserAnnotationReliability,
)
from app.models.user import User, UserRole
from app.services.juggling import feature_flag as ff_module
import app.api.api_v1.endpoints.users.juggling_ball_feedback as fb_module


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
        email=f"bfb_student+{uuid.uuid4().hex[:8]}@test.com",
        name="BFB Student",
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
        email=f"bfb_student2+{uuid.uuid4().hex[:8]}@test.com",
        name="BFB Student Two",
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
    monkeypatch.setattr(fb_module.settings, "BALL_FEEDBACK_ENABLED", True)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _make_consent(db, user_id: int) -> None:
    existing = db.query(JugglingConsent).filter(JugglingConsent.user_id == user_id).first()
    if existing is None:
        db.add(JugglingConsent(
            user_id=user_id,
            service_consent=True,
            training_consent=True,
            admin_review_consent=True,
        ))
        db.flush()


def _make_video(db, user_id: int, *, gdpr_deleted: bool = False) -> str:
    _make_consent(db, user_id)
    vid_id = uuid.uuid4()
    status = "gdpr_deleted" if gdpr_deleted else JugglingVideoStatus.analyzed.value
    video = JugglingVideo(
        id=vid_id,
        user_id=user_id,
        source_type="uploaded_video",
        upload_source="gallery",
        training_video_type="juggling",
        storage_path="/tmp/test.mp4",
        status=status,
        transcode_status="done",
    )
    db.add(video)
    db.flush()
    return str(vid_id)


def _add_trajectory_point(
    db,
    video_id: str,
    frame_ms: int,
    *,
    tracking_state: str = "detected",
    confidence: float | None = 0.8,
) -> None:
    pt = JugglingBallTrajectory(
        video_id=video_id,
        frame_ms=frame_ms,
        ball_x=0.5 if tracking_state != "lost" else None,
        ball_y=0.5 if tracking_state != "lost" else None,
        confidence=confidence if tracking_state != "lost" else None,
        is_manual=False,
        tracking_state=tracking_state,
    )
    db.add(pt)
    db.flush()


_CONFIRM_BODY = {"frame_ms": 100, "decision": "confirm"}


# ── Tests ─────────────────────────────────────────────────────────────────────

# BFB-01: POST → 503 when BALL_FEEDBACK_ENABLED=False
def test_bfb01_post_503_when_disabled(client, student_token, db_session, student_user, monkeypatch):
    monkeypatch.setattr(fb_module.settings, "BALL_FEEDBACK_ENABLED", False)
    video_id = _make_video(db_session, student_user.id)
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback",
        json=_CONFIRM_BODY,
        headers=_auth(student_token),
    )
    assert r.status_code == 503


# BFB-02: POST decision=confirm → 201 + BallFeedbackOut fields
def test_bfb02_post_confirm_201(client, student_token, db_session, student_user):
    video_id = _make_video(db_session, student_user.id)
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback",
        json={"frame_ms": 100, "decision": "confirm"},
        headers=_auth(student_token),
    )
    assert r.status_code == 201
    data = r.json()
    assert data["decision"] == "confirm"
    assert data["approval_state"] == "pending"
    assert "id" in data
    assert "created_at" in data


# BFB-03: POST decision=no_ball → 201
def test_bfb03_post_no_ball_201(client, student_token, db_session, student_user):
    video_id = _make_video(db_session, student_user.id)
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback",
        json={"frame_ms": 200, "decision": "no_ball"},
        headers=_auth(student_token),
    )
    assert r.status_code == 201
    assert r.json()["decision"] == "no_ball"


# BFB-04: POST decision=corrected + coords → 201 + coords stored
def test_bfb04_post_corrected_with_coords(client, student_token, db_session, student_user):
    video_id = _make_video(db_session, student_user.id)
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback",
        json={
            "frame_ms": 300,
            "decision": "corrected",
            "corrected_x": 0.4,
            "corrected_y": 0.6,
            "correction_method": "drag",
        },
        headers=_auth(student_token),
    )
    assert r.status_code == 201
    assert r.json()["decision"] == "corrected"


# BFB-05: POST decision=corrected without coords → 422
def test_bfb05_post_corrected_without_coords_422(client, student_token, db_session, student_user):
    video_id = _make_video(db_session, student_user.id)
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback",
        json={"frame_ms": 400, "decision": "corrected"},
        headers=_auth(student_token),
    )
    assert r.status_code == 422


# BFB-06: POST duplicate feedback for same user+video+frame → 409
def test_bfb06_post_duplicate_409(client, student_token, db_session, student_user):
    video_id = _make_video(db_session, student_user.id)
    body = {"frame_ms": 500, "decision": "confirm"}
    r1 = client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback",
        json=body,
        headers=_auth(student_token),
    )
    assert r1.status_code == 201
    r2 = client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback",
        json=body,
        headers=_auth(student_token),
    )
    assert r2.status_code == 409


# BFB-07: POST unknown video → 404
def test_bfb07_post_unknown_video_404(client, student_token):
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{uuid.uuid4()}/ball-feedback",
        json=_CONFIRM_BODY,
        headers=_auth(student_token),
    )
    assert r.status_code == 404


# BFB-08: POST gdpr_deleted video → 404
def test_bfb08_post_gdpr_deleted_404(client, student_token, db_session, student_user):
    video_id = _make_video(db_session, student_user.id, gdpr_deleted=True)
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback",
        json=_CONFIRM_BODY,
        headers=_auth(student_token),
    )
    assert r.status_code == 404


# BFB-09: POST → lazy creates UserAnnotationReliability at 0.5
def test_bfb09_post_lazy_creates_reliability(client, student_token, db_session, student_user):
    video_id = _make_video(db_session, student_user.id)
    rel_before = db_session.get(UserAnnotationReliability, student_user.id)
    assert rel_before is None
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback",
        json={"frame_ms": 600, "decision": "reject"},
        headers=_auth(student_token),
    )
    assert r.status_code == 201
    db_session.expire_all()
    rel_after = db_session.get(UserAnnotationReliability, student_user.id)
    assert rel_after is not None
    assert rel_after.ball_annotation_reliability == pytest.approx(0.5)


# BFB-10: POST corrected stores corrected_x/corrected_y on the DB record
def test_bfb10_corrected_coords_stored(client, student_token, db_session, student_user):
    video_id = _make_video(db_session, student_user.id)
    r = client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback",
        json={
            "frame_ms": 700,
            "decision": "corrected",
            "corrected_x": 0.33,
            "corrected_y": 0.77,
        },
        headers=_auth(student_token),
    )
    assert r.status_code == 201
    row = (
        db_session.query(JugglingBallFeedback)
        .filter(
            JugglingBallFeedback.video_id == video_id,
            JugglingBallFeedback.frame_ms == 700,
        )
        .first()
    )
    assert row is not None
    assert row.corrected_x == pytest.approx(0.33)
    assert row.corrected_y == pytest.approx(0.77)


# BFB-11: GET queue → 503 when disabled
def test_bfb11_queue_503_when_disabled(client, student_token, db_session, student_user, monkeypatch):
    monkeypatch.setattr(fb_module.settings, "BALL_FEEDBACK_ENABLED", False)
    video_id = _make_video(db_session, student_user.id)
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback/queue",
        headers=_auth(student_token),
    )
    assert r.status_code == 503


# BFB-12: GET queue → 200 empty list when no trajectory points
def test_bfb12_queue_empty_no_trajectory(client, student_token, db_session, student_user):
    video_id = _make_video(db_session, student_user.id)
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback/queue",
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["queue_items"] == []
    assert data["total"] == 0
    assert data["max_per_session"] == 3


# BFB-13: GET queue → 200 items sorted by priority_score DESC
def test_bfb13_queue_sorted_by_priority(client, student_token, db_session, student_user):
    video_id = _make_video(db_session, student_user.id)
    # High uncertainty (low confidence) frame should rank first
    _add_trajectory_point(db_session, video_id, 100, confidence=0.95)  # low priority
    _add_trajectory_point(db_session, video_id, 200, confidence=0.10)  # high priority
    _add_trajectory_point(db_session, video_id, 300, confidence=0.50)  # mid
    db_session.commit()

    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback/queue",
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    items = r.json()["queue_items"]
    assert len(items) == 3
    scores = [i["priority_score"] for i in items]
    assert scores == sorted(scores, reverse=True)
    assert items[0]["frame_ms"] == 200  # lowest confidence = highest priority


# BFB-14: GET queue → excludes frames already reviewed by this user
def test_bfb14_queue_excludes_reviewed(client, student_token, db_session, student_user):
    video_id = _make_video(db_session, student_user.id)
    _add_trajectory_point(db_session, video_id, 100)
    _add_trajectory_point(db_session, video_id, 200)
    db_session.commit()

    # User submits feedback for frame 100
    client.post(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback",
        json={"frame_ms": 100, "decision": "confirm"},
        headers=_auth(student_token),
    )

    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback/queue",
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    frame_mss = [i["frame_ms"] for i in r.json()["queue_items"]]
    assert 100 not in frame_mss
    assert 200 in frame_mss


# BFB-15: GET queue → excludes frames with ≥3 total feedbacks
def test_bfb15_queue_excludes_saturated_frames(
    client, student_token, student_token2, db_session, student_user, student_user2
):
    video_id = _make_video(db_session, student_user.id)
    _add_trajectory_point(db_session, video_id, 100)
    _add_trajectory_point(db_session, video_id, 200)
    db_session.commit()

    # Frame 100 gets 3 feedbacks from DB inserts directly (3 different users is complex; use
    # direct DB inserts for user2 and two manual rows to saturate frame 100)
    for i in range(3):
        extra_user = User(
            email=f"bfb_extra{i}+{uuid.uuid4().hex[:8]}@test.com",
            name=f"Extra {i}",
            password_hash="hashed",
            role=UserRole.STUDENT,
            is_active=True,
        )
        db_session.add(extra_user)
        db_session.flush()
        db_session.add(JugglingBallFeedback(
            video_id=video_id,
            frame_ms=100,
            user_id=extra_user.id,
            decision="confirm",
            approval_state="pending",
        ))
    db_session.commit()

    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback/queue",
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    frame_mss = [i["frame_ms"] for i in r.json()["queue_items"]]
    assert 100 not in frame_mss
    assert 200 in frame_mss


# BFB-16: GET queue → limit param is respected
def test_bfb16_queue_limit_respected(client, student_token, db_session, student_user):
    video_id = _make_video(db_session, student_user.id)
    for ms in range(0, 1000, 100):
        _add_trajectory_point(db_session, video_id, ms)
    db_session.commit()

    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback/queue?limit=3",
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    assert len(r.json()["queue_items"]) == 3


# BFB-17: GET queue → lost frames have higher priority than high-confidence detected frames
def test_bfb17_queue_lost_frames_high_priority(client, student_token, db_session, student_user):
    video_id = _make_video(db_session, student_user.id)
    _add_trajectory_point(db_session, video_id, 100, tracking_state="detected", confidence=0.99)
    _add_trajectory_point(db_session, video_id, 200, tracking_state="lost", confidence=None)
    db_session.commit()

    r = client.get(
        f"/api/v1/users/me/juggling/videos/{video_id}/ball-feedback/queue",
        headers=_auth(student_token),
    )
    assert r.status_code == 200
    items = r.json()["queue_items"]
    assert len(items) == 2
    assert items[0]["frame_ms"] == 200  # lost frame ranked first
    assert items[0]["model_tracking_state"] == "lost"


# BFB-18: GET queue → 404 for unknown video
def test_bfb18_queue_unknown_video_404(client, student_token):
    r = client.get(
        f"/api/v1/users/me/juggling/videos/{uuid.uuid4()}/ball-feedback/queue",
        headers=_auth(student_token),
    )
    assert r.status_code == 404
