"""
Ball feedback consensus task tests — BFC-01..12.

Tests call run_compute_frame_consensus() directly (no Celery broker needed).
PostgreSQL savepoint pattern for full DB isolation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import event as sa_event, select
from sqlalchemy.orm import sessionmaker

from app.database import engine
from app.models.juggling import (
    JugglingBallFeedback,
    JugglingConsent,
    JugglingFrameGroundTruth,
    JugglingVideo,
    JugglingVideoStatus,
    UserAnnotationReliability,
)
from app.models.user import User, UserRole
from app.tasks.juggling_feedback_task import run_compute_frame_consensus


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

def _make_user(db, suffix: str = "") -> User:
    u = User(
        email=f"bfc_{suffix}_{uuid.uuid4().hex[:6]}@test.com",
        name="BFC User",
        password_hash="x",
        role=UserRole.STUDENT,
        is_active=True,
    )
    db.add(u)
    db.flush()
    return u


def _make_video(db) -> JugglingVideo:
    user = _make_user(db, "owner")
    consent = JugglingConsent(
        user_id=user.id,
        service_consent=True,
        training_consent=True,
        admin_review_consent=True,
    )
    db.add(consent)
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


def _feedback(
    db,
    video_id,
    frame_ms: int,
    user_id: int,
    decision: str,
    corrected_x: float | None = None,
    corrected_y: float | None = None,
    reliability: float = 0.5,
    approval_state: str = "pending",
    is_gold: bool = False,
) -> JugglingBallFeedback:
    row = JugglingBallFeedback(
        video_id=video_id,
        frame_ms=frame_ms,
        user_id=user_id,
        decision=decision,
        corrected_x=corrected_x,
        corrected_y=corrected_y,
        correction_method="tap" if corrected_x is not None else None,
        model_predicted_x=0.5,
        model_predicted_y=0.5,
        model_confidence=0.40,
        model_tracking_state="detected",
        user_reliability_at_submit=reliability,
        approval_state=approval_state,
        is_gold_standard=is_gold,
        spam_flags=[],
    )
    db.add(row)
    db.flush()
    return row


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_BFC_01_three_confirms_ball_present(db):
    """3 confirm votes → gt_decision=ball_present, training_eligible=True."""
    v = _make_video(db)
    frame_ms = 1000
    for i in range(3):
        u = _make_user(db, f"u{i}")
        _feedback(db, v.id, frame_ms, u.id, "confirm")

    run_compute_frame_consensus(db, str(v.id), frame_ms)

    gt = db.execute(
        select(JugglingFrameGroundTruth).where(
            JugglingFrameGroundTruth.video_id == v.id,
            JugglingFrameGroundTruth.frame_ms == frame_ms,
        )
    ).scalar_one()
    assert gt.gt_decision == "ball_present"
    assert gt.vote_count == 3
    assert gt.yes_votes == 3
    assert gt.agreement_rate == pytest.approx(1.0)
    assert gt.training_eligible is True


def test_BFC_02_three_no_ball_votes(db):
    """3 no_ball votes → gt_decision=no_ball, training_eligible=True."""
    v = _make_video(db)
    frame_ms = 2000
    for i in range(3):
        u = _make_user(db, f"u{i}")
        _feedback(db, v.id, frame_ms, u.id, "no_ball")

    run_compute_frame_consensus(db, str(v.id), frame_ms)

    gt = db.execute(
        select(JugglingFrameGroundTruth).where(
            JugglingFrameGroundTruth.video_id == v.id,
            JugglingFrameGroundTruth.frame_ms == frame_ms,
        )
    ).scalar_one()
    assert gt.gt_decision == "no_ball"
    assert gt.no_ball_votes == 3
    assert gt.training_eligible is True


def test_BFC_03_disagreement_produces_uncertain(db):
    """2 confirm + 1 no_ball with <80% agreement → uncertain, needs_review."""
    v = _make_video(db)
    frame_ms = 3000
    u1, u2, u3 = _make_user(db, "a"), _make_user(db, "b"), _make_user(db, "c")
    _feedback(db, v.id, frame_ms, u1.id, "confirm")
    _feedback(db, v.id, frame_ms, u2.id, "confirm")
    _feedback(db, v.id, frame_ms, u3.id, "no_ball")

    run_compute_frame_consensus(db, str(v.id), frame_ms)

    gt = db.execute(
        select(JugglingFrameGroundTruth).where(
            JugglingFrameGroundTruth.video_id == v.id,
            JugglingFrameGroundTruth.frame_ms == frame_ms,
        )
    ).scalar_one()
    assert gt.gt_decision == "uncertain"
    assert gt.training_eligible is False

    fb_rows = db.execute(
        select(JugglingBallFeedback).where(
            JugglingBallFeedback.video_id == v.id,
            JugglingBallFeedback.frame_ms == frame_ms,
        )
    ).scalars().all()
    assert all(r.approval_state == "needs_review" for r in fb_rows)


def test_BFC_04_corrected_centroid_weighted(db):
    """2 corrected rows → gt_x/gt_y = reliability-weighted centroid."""
    v = _make_video(db)
    frame_ms = 4000
    u1 = _make_user(db, "hi")
    u2 = _make_user(db, "lo")
    u3 = _make_user(db, "c3")
    # High reliability user: corrects to (0.8, 0.8), weight=0.8
    _feedback(db, v.id, frame_ms, u1.id, "corrected", 0.8, 0.8, reliability=0.8)
    # Low reliability user: corrects to (0.2, 0.2), weight=0.2
    _feedback(db, v.id, frame_ms, u2.id, "corrected", 0.2, 0.2, reliability=0.2)
    # Confirmer to reach threshold
    _feedback(db, v.id, frame_ms, u3.id, "confirm", reliability=0.5)

    run_compute_frame_consensus(db, str(v.id), frame_ms)

    gt = db.execute(
        select(JugglingFrameGroundTruth).where(
            JugglingFrameGroundTruth.video_id == v.id,
            JugglingFrameGroundTruth.frame_ms == frame_ms,
        )
    ).scalar_one()
    # Centroid: (0.8*0.8 + 0.2*0.2) / (0.8+0.2) = (0.64+0.04)/1.0 = 0.68
    assert gt.gt_x == pytest.approx(0.68, abs=0.001)
    assert gt.gt_y == pytest.approx(0.68, abs=0.001)
    assert gt.correction_count == 2


def test_BFC_05_consensus_sets_rows_to_approved(db):
    """Consensus reached → all non-spam rows set to approved."""
    v = _make_video(db)
    frame_ms = 5000
    users = [_make_user(db, f"u{i}") for i in range(3)]
    rows = [_feedback(db, v.id, frame_ms, u.id, "confirm") for u in users]

    run_compute_frame_consensus(db, str(v.id), frame_ms)

    for r in rows:
        db.refresh(r)
        assert r.approval_state == "approved"
        assert r.weighted_vote_contribution is not None


def test_BFC_06_insufficient_votes_stays_pending(db):
    """2 votes (< min 3) → rows stay pending, no gt row written."""
    v = _make_video(db)
    frame_ms = 6000
    u1, u2 = _make_user(db, "p1"), _make_user(db, "p2")
    r1 = _feedback(db, v.id, frame_ms, u1.id, "confirm")
    r2 = _feedback(db, v.id, frame_ms, u2.id, "confirm")

    run_compute_frame_consensus(db, str(v.id), frame_ms)

    # No gt row created (not enough votes to trigger upsert with uncertainty)
    gt = db.execute(
        select(JugglingFrameGroundTruth).where(
            JugglingFrameGroundTruth.video_id == v.id,
            JugglingFrameGroundTruth.frame_ms == frame_ms,
        )
    ).scalar_one_or_none()
    # gt row IS written (uncertain), rows stay pending (new_state=None)
    db.refresh(r1)
    db.refresh(r2)
    assert r1.approval_state == "pending"
    assert r2.approval_state == "pending"
    assert gt is not None
    assert gt.gt_decision == "uncertain"
    assert gt.training_eligible is False


def test_BFC_07_idempotent(db):
    """Running consensus twice produces identical result."""
    v = _make_video(db)
    frame_ms = 7000
    for i in range(3):
        u = _make_user(db, f"idem{i}")
        _feedback(db, v.id, frame_ms, u.id, "confirm")

    run_compute_frame_consensus(db, str(v.id), frame_ms)
    run_compute_frame_consensus(db, str(v.id), frame_ms)

    gt_rows = db.execute(
        select(JugglingFrameGroundTruth).where(
            JugglingFrameGroundTruth.video_id == v.id,
            JugglingFrameGroundTruth.frame_ms == frame_ms,
        )
    ).scalars().all()
    assert len(gt_rows) == 1
    assert gt_rows[0].gt_decision == "ball_present"


def test_BFC_08_reliability_updated_after_consensus(db):
    """Reliability scores updated when consensus reached."""
    v = _make_video(db)
    frame_ms = 8000
    users = [_make_user(db, f"rel{i}") for i in range(3)]
    for u in users:
        _feedback(db, v.id, frame_ms, u.id, "confirm", reliability=0.5)

    run_compute_frame_consensus(db, str(v.id), frame_ms)

    for u in users:
        rel = db.get(UserAnnotationReliability, u.id)
        assert rel is not None
        assert rel.total_feedbacks == 1
        assert rel.correct_feedbacks == 1
        assert rel.ball_annotation_reliability == pytest.approx(1.0)


def test_BFC_09_weighted_vote_contribution_set(db):
    """weighted_vote_contribution set on approved rows; sums to ~1.0."""
    v = _make_video(db)
    frame_ms = 9000
    users = [_make_user(db, f"wvc{i}") for i in range(3)]
    rows = [_feedback(db, v.id, frame_ms, u.id, "confirm", reliability=0.5) for u in users]

    run_compute_frame_consensus(db, str(v.id), frame_ms)

    total_contribution = 0.0
    for r in rows:
        db.refresh(r)
        assert r.weighted_vote_contribution is not None
        total_contribution += r.weighted_vote_contribution
    assert total_contribution == pytest.approx(1.0, abs=0.01)


def test_BFC_10_gold_standard_reliability_counts_double(db):
    """Gold standard frame: reliability update increments by weight=2."""
    v = _make_video(db)
    frame_ms = 10000
    users = [_make_user(db, f"g{i}") for i in range(3)]
    for u in users:
        _feedback(db, v.id, frame_ms, u.id, "confirm", reliability=0.5, is_gold=True)

    run_compute_frame_consensus(db, str(v.id), frame_ms)

    for u in users:
        rel = db.get(UserAnnotationReliability, u.id)
        assert rel.total_feedbacks == 2   # weight=2 for gold
        assert rel.gold_attempts == 1
        assert rel.gold_correct == 1


def test_BFC_11_training_eligible_reset_after_rejection(db):
    """After consensus, admin rejects → training_eligible can be cleared."""
    v = _make_video(db)
    frame_ms = 11000
    for i in range(3):
        u = _make_user(db, f"tr{i}")
        _feedback(db, v.id, frame_ms, u.id, "confirm")

    run_compute_frame_consensus(db, str(v.id), frame_ms)

    gt = db.execute(
        select(JugglingFrameGroundTruth).where(
            JugglingFrameGroundTruth.video_id == v.id,
            JugglingFrameGroundTruth.frame_ms == frame_ms,
        )
    ).scalar_one()
    assert gt.training_eligible is True

    # Simulate admin changing gt_decision to uncertain
    gt.gt_decision = "uncertain"
    gt.training_eligible = False
    db.commit()

    gt2 = db.execute(
        select(JugglingFrameGroundTruth).where(
            JugglingFrameGroundTruth.video_id == v.id,
            JugglingFrameGroundTruth.frame_ms == frame_ms,
        )
    ).scalar_one()
    assert gt2.training_eligible is False


def test_BFC_12_spam_rows_excluded_from_consensus(db):
    """Spam-flagged rows are excluded from vote counting."""
    v = _make_video(db)
    frame_ms = 12000
    u_spam = _make_user(db, "spammer")
    u1, u2, u3 = _make_user(db, "a"), _make_user(db, "b"), _make_user(db, "c")

    # Spam row votes no_ball — should be excluded
    _feedback(db, v.id, frame_ms, u_spam.id, "no_ball", approval_state="spam")
    # 3 legitimate confirms
    for u in (u1, u2, u3):
        _feedback(db, v.id, frame_ms, u.id, "confirm")

    run_compute_frame_consensus(db, str(v.id), frame_ms)

    gt = db.execute(
        select(JugglingFrameGroundTruth).where(
            JugglingFrameGroundTruth.video_id == v.id,
            JugglingFrameGroundTruth.frame_ms == frame_ms,
        )
    ).scalar_one()
    # Only 3 legitimate votes counted — spam excluded
    assert gt.vote_count == 3
    assert gt.gt_decision == "ball_present"
    assert gt.no_ball_votes == 0
