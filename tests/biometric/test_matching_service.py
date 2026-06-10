"""
Face matching service tests — PR-6.

BCM-01  compute_cosine_similarity — identical unit vectors → 1.0
BCM-02  compute_cosine_similarity — orthogonal vectors → 0.0
BCM-03  compute_cosine_similarity — anti-parallel vectors → -1.0
BCM-04  compute_cosine_similarity — zero-magnitude vector → 0.0
BCM-05  classify_match_outcome — score 0.80 → "verified"
BCM-06  classify_match_outcome — score at threshold (0.75) → "verified"
BCM-07  classify_match_outcome — score 0.74 → "manual_review_required"
BCM-08  classify_match_outcome — score at review_lower (0.55) → "manual_review_required"
BCM-09  classify_match_outcome — score 0.54 → "rejected"
BCM-10  run_face_match — happy path: user state updated, audit log written, outcome returned
"""
from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

from app.services.biometric.matching_service import (
    MATCH_THRESHOLD,
    REVIEW_LOWER,
    classify_match_outcome,
    compute_cosine_similarity,
    run_face_match,
)


# ── BCM-01 — identical unit vectors ───────────────────────────────────────────

def test_bcm01_cosine_identical_unit_vectors():
    a = [1.0, 0.0, 0.0]
    assert abs(compute_cosine_similarity(a, a) - 1.0) < 1e-9


# ── BCM-02 — orthogonal vectors ───────────────────────────────────────────────

def test_bcm02_cosine_orthogonal():
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    assert abs(compute_cosine_similarity(a, b)) < 1e-9


# ── BCM-03 — anti-parallel ────────────────────────────────────────────────────

def test_bcm03_cosine_anti_parallel():
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert abs(compute_cosine_similarity(a, b) - (-1.0)) < 1e-9


# ── BCM-04 — zero vector → 0.0 ───────────────────────────────────────────────

def test_bcm04_cosine_zero_vector():
    a = [0.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    assert compute_cosine_similarity(a, b) == 0.0
    assert compute_cosine_similarity(a, a) == 0.0


# ── BCM-05 — high score → verified ───────────────────────────────────────────

def test_bcm05_classify_verified_above_threshold():
    assert classify_match_outcome(0.80) == "verified"


# ── BCM-06 — at threshold boundary → verified ────────────────────────────────

def test_bcm06_classify_at_threshold():
    assert classify_match_outcome(MATCH_THRESHOLD) == "verified"


# ── BCM-07 — just below threshold → manual_review_required ───────────────────

def test_bcm07_classify_just_below_threshold():
    # 0.74 is between REVIEW_LOWER and MATCH_THRESHOLD
    assert classify_match_outcome(0.74) == "manual_review_required"


# ── BCM-08 — at review_lower boundary → manual_review_required ───────────────

def test_bcm08_classify_at_review_lower():
    assert classify_match_outcome(REVIEW_LOWER) == "manual_review_required"


# ── BCM-09 — below review_lower → rejected ───────────────────────────────────

def test_bcm09_classify_below_review_lower():
    assert classify_match_outcome(0.54) == "rejected"
    assert classify_match_outcome(0.0)  == "rejected"


# ── BCM-10 — run_face_match happy path ───────────────────────────────────────

def test_bcm10_run_face_match_happy_path(
    db, student_user, biometric_feature_enabled, encryption_test_key, allow_test_key
):
    """
    run_face_match with same seed for reference and live → cosine = 1.0 → verified.
    Verifies: user.face_match_status updated, audit row written, MatchOutcome returned.
    """
    from app.services.biometric.audit_log import EVT_MATCH_SUCCESS
    from app.services.biometric.embedding_service import FakeEmbeddingProvider, store_embedding
    from app.models.biometric import BiometricVerificationLog, UserFaceEmbedding

    # Store reference embedding using the same seed that the verify will use
    seed = b"live_photo.jpg"
    provider  = FakeEmbeddingProvider()
    embedding = provider.generate(seed)
    row = store_embedding(db=db, user_id=student_user.id, embedding=embedding, model_version="fake_v1")
    row.is_active = True
    db.flush()

    outcome = run_face_match(
        db=db,
        user=student_user,
        live_image_seed=seed,   # identical seed → cosine ≈ 1.0 → verified
    )

    assert outcome == "verified"
    assert student_user.face_match_status == "verified"
    assert not student_user.manual_review_required

    logs = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student_user.id,
        BiometricVerificationLog.event_type == EVT_MATCH_SUCCESS,
    ).all()
    assert logs, "EVT_MATCH_SUCCESS audit row expected"
    assert logs[0].threshold_used == MATCH_THRESHOLD
    # face_match_score stored internally — never None for a successful match
    assert logs[0].face_match_score is not None