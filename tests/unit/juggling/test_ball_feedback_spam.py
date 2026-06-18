"""
Ball feedback spam detection tests — BFS-01..04.

Tests verify that spam signals fire correctly and that spam rows are
excluded from consensus without deleting data.
PostgreSQL savepoint pattern.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event as sa_event, select
from sqlalchemy.orm import sessionmaker

from app.database import engine
from app.models.juggling import (
    JugglingBallFeedback,
    JugglingConsent,
    JugglingVideo,
    JugglingVideoStatus,
)
from app.models.user import User, UserRole
from app.services.juggling import ball_feedback_service as svc
from app.schemas.juggling import BallFeedbackRequest


# ── DB fixture ────────────────────────────────────────────────────────────────

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_user(db) -> User:
    u = User(
        email=f"bfs_{uuid.uuid4().hex[:8]}@test.com",
        name="BFS User",
        password_hash="x",
        role=UserRole.STUDENT,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_video(db, user: User) -> JugglingVideo:
    db.add(JugglingConsent(
        user_id=user.id,
        service_consent=True,
        training_consent=True,
        admin_review_consent=True,
    ))
    db.flush()
    v = JugglingVideo(
        user_id=user.id,
        status=JugglingVideoStatus.analyzed,
        upload_source="gallery",
        source_type="uploaded_video",
    )
    db.add(v)
    db.flush()
    return v


def _insert_feedback_row(
    db,
    video_id,
    user_id: int,
    frame_ms: int,
    decision: str = "confirm",
    created_at: datetime | None = None,
) -> JugglingBallFeedback:
    """Insert directly (bypasses spam detection) for fixture setup."""
    row = JugglingBallFeedback(
        video_id=video_id,
        frame_ms=frame_ms,
        user_id=user_id,
        decision=decision,
        model_predicted_x=0.5,
        model_predicted_y=0.5,
        model_confidence=0.4,
        model_tracking_state="detected",
        user_reliability_at_submit=0.5,
        approval_state="pending",
        spam_flags=[],
    )
    if created_at is not None:
        row.created_at = created_at
    db.add(row)
    db.flush()
    return row


def _make_request(frame_ms: int, decision: str = "confirm") -> BallFeedbackRequest:
    return BallFeedbackRequest(
        frame_ms=frame_ms,
        decision=decision,
        model_predicted_x=0.5,
        model_predicted_y=0.5,
        model_confidence=0.4,
        model_tracking_state="detected",
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_BFS_01_velocity_signal_fires(db):
    """
    >10 submissions within 60s for same user+video → spam_flags=['velocity'].
    approval_state set to 'spam'. Data row NOT deleted.
    """
    user = _make_user(db)
    video = _make_video(db, user)
    now = datetime.now(timezone.utc)

    # Pre-insert 10 recent rows (within 60s window) — total becomes 11 after submit
    for i in range(10):
        _insert_feedback_row(
            db, video.id, user.id, frame_ms=i * 100,
            created_at=now - timedelta(seconds=5),
        )

    # The 11th submission triggers velocity check
    req = _make_request(frame_ms=9999)
    record = svc.submit_feedback(db, str(video.id), user.id, req)

    assert "velocity" in record.spam_flags
    assert record.approval_state == "spam"
    # Row is persisted, not deleted
    assert record.id is not None


def test_BFS_02_uniform_rate_signal_fires(db):
    """
    >90% same decision across >20 submissions for same user+video → uniform_rate spam.
    """
    user = _make_user(db)
    video = _make_video(db, user)

    # Insert 20 rows all with decision="confirm" (via direct insert, not spam path)
    for i in range(20):
        _insert_feedback_row(db, video.id, user.id, frame_ms=i * 100, decision="confirm")

    # 21st submission with "confirm" — should trigger uniform_rate
    req = _make_request(frame_ms=99000, decision="confirm")
    record = svc.submit_feedback(db, str(video.id), user.id, req)

    assert "uniform_rate" in record.spam_flags
    assert record.approval_state == "spam"


def test_BFS_03_no_spam_for_varied_decisions(db):
    """Varied decisions and low volume → no spam flags."""
    user = _make_user(db)
    video = _make_video(db, user)

    # 5 rows with mixed decisions — well below thresholds
    decisions = ["confirm", "no_ball", "confirm", "no_ball", "confirm"]
    for i, dec in enumerate(decisions):
        _insert_feedback_row(db, video.id, user.id, frame_ms=i * 100, decision=dec)

    req = _make_request(frame_ms=9000, decision="confirm")
    record = svc.submit_feedback(db, str(video.id), user.id, req)

    assert record.spam_flags == []
    assert record.approval_state == "pending"


def test_BFS_04_spam_flag_does_not_delete_data(db):
    """Spam-flagged row can be queried; admin can read spam_flags."""
    user = _make_user(db)
    video = _make_video(db, user)
    now = datetime.now(timezone.utc)

    for i in range(10):
        _insert_feedback_row(
            db, video.id, user.id, frame_ms=i * 100,
            created_at=now - timedelta(seconds=5),
        )

    req = _make_request(frame_ms=9999)
    record = svc.submit_feedback(db, str(video.id), user.id, req)

    # Row is still in DB
    fetched = db.execute(
        select(JugglingBallFeedback).where(JugglingBallFeedback.id == record.id)
    ).scalar_one()
    assert fetched is not None
    assert fetched.approval_state == "spam"
    assert "velocity" in fetched.spam_flags
