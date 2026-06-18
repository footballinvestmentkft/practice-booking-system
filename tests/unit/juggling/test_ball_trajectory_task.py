"""
Dense ball trajectory task tests — BT-13..BT-19.

Mock detector + extractor; no ONNX model or video files needed.
Uses the project's PostgreSQL savepoint pattern for DB tests.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import numpy as np
import pytest
from sqlalchemy import event as sa_event
from sqlalchemy.orm import sessionmaker

from app.database import engine
from app.models.juggling import (
    JugglingBallTrajectory,
    JugglingConsent,
    JugglingContactEvent,
    JugglingVideo,
    JugglingVideoStatus,
)
from app.tasks.juggling_trajectory_task import run_dense_ball_trajectory
import app.tasks.juggling_trajectory_task as task_module

# ── DB fixture (PostgreSQL savepoint) ─────────────────────────────────────────

@pytest.fixture()
def db():
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


@pytest.fixture(autouse=True)
def _enable_flags(monkeypatch):
    monkeypatch.setattr(task_module.settings, "BALL_TRAJECTORY_ENABLED", True)
    monkeypatch.setattr(task_module.settings, "BALL_DETECTION_MODEL_PATH",
                        "/tmp/fake_model.onnx")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_video(db, *, transcode_status="done", duration_sec=1.0, user_id=1):
    vid_id = uuid.uuid4()
    consent = db.query(JugglingConsent).filter(JugglingConsent.user_id == user_id).first()
    if consent is None:
        consent = JugglingConsent(
            user_id=user_id, service_consent=True,
            training_consent=True, admin_review_consent=True,
        )
        db.add(consent)
        db.flush()

    video = JugglingVideo(
        id=vid_id,
        user_id=user_id,
        source_type="uploaded_video",
        upload_source="gallery",
        training_video_type="juggling",
        storage_path="/tmp/test.mp4",
        status=JugglingVideoStatus.analyzed.value,
        transcode_status=transcode_status,
        server_detected_metadata={"duration_seconds": duration_sec},
    )
    db.add(video)
    db.commit()
    return str(vid_id)


def _mock_extract():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    def extract(path, ms):
        return frame, 100, 100
    return extract


def _mock_detector(detections: dict[int, tuple | None]):
    """detections: {frame_ms: (cx, cy, conf) or None}."""
    class FakeDetector:
        def __init__(self):
            self._call_count = 0

        def detect(self, frame, target_class_id=37, confidence_threshold=0.3):
            ms = self._call_count * 100
            self._call_count += 1
            return detections.get(ms)

    fake = FakeDetector()
    return lambda model_path: fake


# ── Tests ─────────────────────────────────────────────────────────────────────

# BT-13: happy path — 10 frames, 7 detected, 3 missed
def test_bt13_happy_path_mixed(db, monkeypatch):
    import pathlib
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: True)

    video_id = _make_video(db, duration_sec=0.9)
    detections = {
        0: (0.5, 0.5, 0.8), 100: (0.52, 0.52, 0.7), 200: (0.54, 0.54, 0.75),
        300: None, 400: None, 500: None,
        600: (0.6, 0.6, 0.9), 700: (0.62, 0.62, 0.85),
        800: (0.64, 0.64, 0.88), 900: (0.66, 0.66, 0.82),
    }

    result = run_dense_ball_trajectory(
        video_id, db,
        _extract_frame=_mock_extract(),
        _get_detector=_mock_detector(detections),
        sampling_interval_ms=100,
    )

    assert result["status"] == "complete"
    assert result["frames"] == 10
    assert result["detected"] == 7
    assert result["predicted"] + result["lost"] == 3

    rows = db.query(JugglingBallTrajectory).filter(
        JugglingBallTrajectory.video_id == uuid.UUID(video_id)
    ).count()
    assert rows == 10


# BT-14: all detected
def test_bt14_all_detected(db, monkeypatch):
    import pathlib
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: True)

    video_id = _make_video(db, duration_sec=0.4)
    detections = {i * 100: (0.5, 0.5, 0.9) for i in range(5)}

    result = run_dense_ball_trajectory(
        video_id, db,
        _extract_frame=_mock_extract(),
        _get_detector=_mock_detector(detections),
        sampling_interval_ms=100,
    )

    assert result["status"] == "complete"
    assert result["detected"] == 5
    assert result["predicted"] == 0
    assert result["lost"] == 0

    rows = db.query(JugglingBallTrajectory).filter(
        JugglingBallTrajectory.tracking_state == "detected"
    ).count()
    assert rows == 5


# BT-15: detector always None → lost after max_miss
def test_bt15_all_none_tracking_lost(db, monkeypatch):
    import pathlib
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: True)

    video_id = _make_video(db, duration_sec=0.9)

    result = run_dense_ball_trajectory(
        video_id, db,
        _extract_frame=_mock_extract(),
        _get_detector=_mock_detector({}),
        sampling_interval_ms=100,
        max_consecutive_miss=3,
    )

    assert result["status"] == "complete"
    assert result["detected"] == 0
    assert result["lost"] == 10


# BT-16: feature flag off → skipped
def test_bt16_feature_flag_off(db, monkeypatch):
    monkeypatch.setattr(task_module.settings, "BALL_TRAJECTORY_ENABLED", False)
    video_id = _make_video(db)
    result = run_dense_ball_trajectory(video_id, db)
    assert result["status"] == "skipped"
    assert "BALL_TRAJECTORY_ENABLED" in result["reason"]


# BT-17: video not found → failed
def test_bt17_video_not_found(db):
    fake_id = str(uuid.uuid4())
    result = run_dense_ball_trajectory(fake_id, db)
    assert result["status"] == "failed"
    assert "not found" in result["reason"]


# BT-18: transcode not done → skipped
def test_bt18_transcode_not_done(db):
    video_id = _make_video(db, transcode_status="pending")
    result = run_dense_ball_trajectory(
        video_id, db,
        _extract_frame=lambda *a: None,
        _get_detector=lambda p: None,
    )
    assert result["status"] == "skipped"
    assert "transcode" in result["reason"]


# BT-19: is_manual=TRUE points not overwritten
def test_bt19_manual_points_preserved(db, monkeypatch):
    import pathlib
    monkeypatch.setattr(pathlib.Path, "is_file", lambda self: True)

    video_id = _make_video(db, duration_sec=0.2)

    manual_point = JugglingBallTrajectory(
        video_id=uuid.UUID(video_id),
        frame_ms=100,
        ball_x=0.99,
        ball_y=0.99,
        confidence=None,
        is_manual=True,
        tracking_state="manual_seed",
    )
    db.add(manual_point)
    db.commit()

    detections = {0: (0.5, 0.5, 0.8), 100: (0.6, 0.6, 0.9), 200: (0.7, 0.7, 0.85)}

    result = run_dense_ball_trajectory(
        video_id, db,
        _extract_frame=_mock_extract(),
        _get_detector=_mock_detector(detections),
        sampling_interval_ms=100,
    )

    assert result["status"] == "complete"

    manual = db.query(JugglingBallTrajectory).filter(
        JugglingBallTrajectory.video_id == uuid.UUID(video_id),
        JugglingBallTrajectory.frame_ms == 100,
        JugglingBallTrajectory.is_manual.is_(True),
    ).first()
    assert manual is not None
    assert manual.ball_x == 0.99
    assert manual.ball_y == 0.99
