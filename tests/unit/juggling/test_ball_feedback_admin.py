"""
Admin review queue + export endpoint tests — BFA-01..08 + BFX-01..05.

FastAPI TestClient + PostgreSQL savepoint. No Celery needed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sa_event
from sqlalchemy.orm import sessionmaker

from app.core.auth import create_access_token
from app.database import engine, get_db
from app.main import app
from app.models.juggling import (
    JugglingBallFeedback,
    JugglingConsent,
    JugglingFrameGroundTruth,
    JugglingVideo,
    JugglingVideoStatus,
)
from app.models.user import User, UserRole


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
def admin_user(db_session):
    u = User(
        email=f"bfa_admin_{uuid.uuid4().hex[:6]}@test.com",
        name="BFA Admin",
        password_hash="x",
        role=UserRole.ADMIN,
        is_active=True,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture()
def student_user(db_session):
    u = User(
        email=f"bfa_student_{uuid.uuid4().hex[:6]}@test.com",
        name="BFA Student",
        password_hash="x",
        role=UserRole.STUDENT,
        is_active=True,
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture()
def admin_token(admin_user):
    return create_access_token(data={"sub": admin_user.email})


@pytest.fixture()
def student_token(student_user):
    return create_access_token(data={"sub": student_user.email})


def _make_video(db, user: User) -> JugglingVideo:
    from sqlalchemy import select as _sel
    from app.models.juggling import JugglingConsent as _JC
    existing = db.execute(_sel(_JC).where(_JC.user_id == user.id)).scalar_one_or_none()
    if existing is None:
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


def _make_feedback(
    db, video_id, user_id: int, frame_ms: int,
    decision: str = "confirm",
    approval_state: str = "needs_review",
    spam_flags: list | None = None,
) -> JugglingBallFeedback:
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
        approval_state=approval_state,
        spam_flags=spam_flags or [],
    )
    db.add(row)
    db.flush()
    return row


def _make_gt(
    db, video_id, frame_ms: int,
    training_eligible: bool = True,
    exported_at=None,
    dataset_version: str | None = None,
    gt_decision: str = "ball_present",
) -> JugglingFrameGroundTruth:
    gt = JugglingFrameGroundTruth(
        video_id=video_id,
        frame_ms=frame_ms,
        gt_decision=gt_decision,
        confidence_score=0.9,
        agreement_rate=0.9,
        vote_count=3,
        yes_votes=3,
        no_votes=0,
        no_ball_votes=0,
        correction_count=0,
        training_eligible=training_eligible,
        exported_at=exported_at,
        dataset_version=dataset_version,
        is_gold_standard=False,
    )
    db.add(gt)
    db.flush()
    return gt


# ── BFA: Admin review queue tests ─────────────────────────────────────────────

def test_BFA_01_review_queue_returns_needs_review_by_default(client, admin_token, db_session, admin_user):
    """GET review-queue returns only needs_review rows by default."""
    video = _make_video(db_session, admin_user)
    _make_feedback(db_session, video.id, admin_user.id, 1000, approval_state="needs_review")
    _make_feedback(db_session, video.id, admin_user.id, 2000, approval_state="approved")
    _make_feedback(db_session, video.id, admin_user.id, 3000, approval_state="spam")
    db_session.commit()

    resp = client.get(
        "/api/v1/admin/juggling/ball-feedback/review-queue",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["approval_state"] == "needs_review"


def test_BFA_02_state_spam_filter(client, admin_token, db_session, admin_user):
    """?state=spam returns only spam rows."""
    video = _make_video(db_session, admin_user)
    _make_feedback(db_session, video.id, admin_user.id, 1000, approval_state="needs_review")
    _make_feedback(db_session, video.id, admin_user.id, 2000, approval_state="spam",
                   spam_flags=["velocity"])
    db_session.commit()

    resp = client.get(
        "/api/v1/admin/juggling/ball-feedback/review-queue?state=spam",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert "velocity" in data["items"][0]["spam_flags"]


def test_BFA_03_video_id_filter(client, admin_token, db_session, admin_user):
    """?video_id= filters results to that video."""
    video1 = _make_video(db_session, admin_user)
    video2 = _make_video(db_session, admin_user)
    _make_feedback(db_session, video1.id, admin_user.id, 1000, approval_state="needs_review")
    _make_feedback(db_session, video2.id, admin_user.id, 2000, approval_state="needs_review")
    db_session.commit()

    resp = client.get(
        f"/api/v1/admin/juggling/ball-feedback/review-queue?video_id={video1.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_BFA_04_non_admin_gets_403(client, student_token):
    """Student accessing review-queue → 403."""
    resp = client.get(
        "/api/v1/admin/juggling/ball-feedback/review-queue",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 403


def test_BFA_05_patch_approve(client, admin_token, db_session, admin_user):
    """PATCH approve → approval_state=approved, reviewed_at stamped."""
    video = _make_video(db_session, admin_user)
    fb = _make_feedback(db_session, video.id, admin_user.id, 1000,
                        approval_state="needs_review")
    db_session.commit()

    resp = client.patch(
        f"/api/v1/admin/juggling/ball-feedback/{fb.id}/review",
        json={"action": "approve"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["approval_state"] == "approved"
    assert data["reviewed_at"] is not None, "PATCH response must include reviewed_at (BallFeedbackAdminItem)"
    assert data["reviewed_by_user_id"] == admin_user.id, "PATCH response must include reviewer admin ID"

    db_session.refresh(fb)
    assert fb.reviewed_at is not None
    assert fb.reviewed_by_user_id == admin_user.id


def test_BFA_06_patch_reject(client, admin_token, db_session, admin_user):
    """PATCH reject → approval_state=rejected, timestamps set."""
    video = _make_video(db_session, admin_user)
    fb = _make_feedback(db_session, video.id, admin_user.id, 1000,
                        approval_state="needs_review")
    db_session.commit()

    resp = client.patch(
        f"/api/v1/admin/juggling/ball-feedback/{fb.id}/review",
        json={"action": "reject"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["approval_state"] == "rejected"
    assert data["reviewed_at"] is not None, "PATCH response must include reviewed_at (BallFeedbackAdminItem)"
    assert data["reviewed_by_user_id"] == admin_user.id, "PATCH response must include reviewer admin ID"
    db_session.refresh(fb)
    assert fb.reviewed_at is not None


def test_BFA_07_patch_escalate_clears_spam_flags(client, admin_token, db_session, admin_user):
    """PATCH escalate_to_review → clears spam_flags, sets needs_review."""
    video = _make_video(db_session, admin_user)
    fb = _make_feedback(db_session, video.id, admin_user.id, 1000,
                        approval_state="spam", spam_flags=["velocity"])
    db_session.commit()

    resp = client.patch(
        f"/api/v1/admin/juggling/ball-feedback/{fb.id}/review",
        json={"action": "escalate_to_review"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["approval_state"] == "needs_review"

    db_session.refresh(fb)
    assert fb.approval_state == "needs_review"
    assert fb.spam_flags == []


def test_BFA_08_double_approve_returns_409(client, admin_token, db_session, admin_user):
    """PATCH approve on already-approved row → 409."""
    video = _make_video(db_session, admin_user)
    fb = _make_feedback(db_session, video.id, admin_user.id, 1000,
                        approval_state="approved")
    db_session.commit()

    resp = client.patch(
        f"/api/v1/admin/juggling/ball-feedback/{fb.id}/review",
        json={"action": "approve"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 409


def test_BFA_09_queue_item_reviewed_by_is_null_for_pending(client, admin_token, db_session, admin_user):
    """GET review-queue pending items have reviewed_by_user_id=null (not yet reviewed).

    Also serves as backwards-compat check: reviewed_by_user_id is Optional[int]=None,
    so existing clients that ignore unknown fields or receive null are unaffected.
    """
    video = _make_video(db_session, admin_user)
    _make_feedback(db_session, video.id, admin_user.id, 1000, approval_state="needs_review")
    db_session.commit()

    resp = client.get(
        "/api/v1/admin/juggling/ball-feedback/review-queue",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    item = data["items"][0]
    assert item["reviewed_by_user_id"] is None, (
        "Pending queue items must have reviewed_by_user_id=null before any admin action"
    )
    assert "reviewed_by_user_id" in item, (
        "reviewed_by_user_id must be present in queue response (Optional field, null default)"
    )


# ── BFX: Export tests ──────────────────────────────────────────────────────────

def test_BFX_01_export_returns_only_eligible_not_exported(client, admin_token, db_session, admin_user):
    """Export returns only training_eligible=True AND exported_at IS NULL rows."""
    video = _make_video(db_session, admin_user)
    _make_gt(db_session, video.id, 1000, training_eligible=True,  exported_at=None)
    _make_gt(db_session, video.id, 2000, training_eligible=False, exported_at=None)
    _make_gt(db_session, video.id, 3000, training_eligible=True,
             exported_at=datetime.now(timezone.utc), dataset_version="v1_old")
    db_session.commit()

    resp = client.get(
        "/api/v1/admin/juggling/ball-feedback/training-export",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["frame_count"] == 1
    assert data["frames"][0]["frame_ms"] == 1000


def test_BFX_02_export_stamps_exported_at(client, admin_token, db_session, admin_user):
    """After export, exported_at and dataset_version are set on returned rows."""
    video = _make_video(db_session, admin_user)
    gt = _make_gt(db_session, video.id, 1000, training_eligible=True, exported_at=None)
    db_session.commit()

    resp = client.get(
        "/api/v1/admin/juggling/ball-feedback/training-export",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200

    db_session.refresh(gt)
    assert gt.exported_at is not None
    assert gt.dataset_version is not None


def test_BFX_03_reexport_with_version_is_idempotent(client, admin_token, db_session, admin_user):
    """Re-export with ?version= returns already-stamped rows without re-stamping."""
    video = _make_video(db_session, admin_user)
    fixed_time = datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc)
    _make_gt(db_session, video.id, 1000, training_eligible=True,
             exported_at=fixed_time, dataset_version="v1_2026-06-18_1200")
    db_session.commit()

    resp = client.get(
        "/api/v1/admin/juggling/ball-feedback/training-export?version=v1_2026-06-18_1200",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == "v1_2026-06-18_1200"
    assert data["frame_count"] == 1


def test_BFX_04_limit_param_respected(client, admin_token, db_session, admin_user):
    """?limit= restricts the number of exported frames."""
    video = _make_video(db_session, admin_user)
    for i in range(5):
        _make_gt(db_session, video.id, i * 1000, training_eligible=True, exported_at=None)
    db_session.commit()

    resp = client.get(
        "/api/v1/admin/juggling/ball-feedback/training-export?limit=2",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["frame_count"] == 2


def test_BFX_05_non_admin_gets_403(client, student_token):
    """Student accessing training-export → 403."""
    resp = client.get(
        "/api/v1/admin/juggling/ball-feedback/training-export",
        headers={"Authorization": f"Bearer {student_token}"},
    )
    assert resp.status_code == 403
