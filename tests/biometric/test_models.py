"""
Biometric model tests.

BMM-01  UserBiometricConsent can be created and flushed
BMM-02  UserBiometricConsent.is_active defaults to True
BMM-03  UserFaceEmbedding can be created with is_active=False (default)
BMM-04  BiometricVerificationLog.face_match_score stored in DB
BMM-05  BiometricVerificationLog.liveness_metadata JSONB stored and retrieved
BMM-06  User.face_match_status column exists and is nullable
BMM-07  User.face_match_score column exists and is nullable (internal only)
BMM-08  User.face_reference_photo_status column exists
BMM-09  User.manual_review_required defaults to False
BMM-10  User.biometric_consents relationship accessible
BMM-11  User.face_embedding relationship accessible (None when no embedding)
BMM-12  BiometricVerificationLog inserted without liveness_metadata (null ok)
"""
from __future__ import annotations

import pytest

from app.models.biometric import (
    BiometricVerificationLog,
    UserBiometricConsent,
    UserFaceEmbedding,
)
from app.models.user import User


# ── BMM-01 / BMM-02 ──────────────────────────────────────────────────────────

def test_bmm01_consent_created(db, student_user):
    from datetime import datetime, timezone
    consent = UserBiometricConsent(
        user_id=student_user.id,
        consent_granted_at=datetime.now(timezone.utc),
        consent_version="v1.0",
    )
    db.add(consent)
    db.flush()
    assert consent.id is not None


def test_bmm02_consent_is_active_default(db, student_user):
    from datetime import datetime, timezone
    consent = UserBiometricConsent(
        user_id=student_user.id,
        consent_granted_at=datetime.now(timezone.utc),
        consent_version="v1.0",
    )
    db.add(consent)
    db.flush()
    assert consent.is_active is True


# ── BMM-03  UserFaceEmbedding ─────────────────────────────────────────────────

def test_bmm03_embedding_is_active_false_default(db, student_user):
    emb = UserFaceEmbedding(user_id=student_user.id)
    db.add(emb)
    db.flush()
    assert emb.is_active is False
    assert emb.embedding_ciphertext is None   # not populated until PR-4


# ── BMM-04 / BMM-05  BiometricVerificationLog ─────────────────────────────────

def test_bmm04_log_face_match_score_stored(db, student_user):
    row = BiometricVerificationLog(
        user_id=student_user.id,
        event_type="match_success",
        face_match_score=0.82,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.face_match_score == pytest.approx(0.82)


def test_bmm05_log_liveness_metadata_jsonb(db, student_user):
    metadata = {
        "challenge_version": "v1.0",
        "steps_completed": ["centered", "head_left"],
        "total_duration_ms": 9500,
        "retry_count": 0,
    }
    row = BiometricVerificationLog(
        user_id=student_user.id,
        event_type="liveness_completed",
        liveness_metadata=metadata,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    assert row.liveness_metadata["challenge_version"] == "v1.0"
    assert row.liveness_metadata["steps_completed"] == ["centered", "head_left"]


# ── BMM-06 … BMM-09  User columns ────────────────────────────────────────────

def test_bmm06_user_face_match_status_nullable(db, student_user):
    assert student_user.face_match_status is None


def test_bmm07_user_face_match_score_nullable(db, student_user):
    assert student_user.face_match_score is None


def test_bmm08_user_face_reference_photo_status_nullable(db, student_user):
    assert student_user.face_reference_photo_status is None


def test_bmm09_user_manual_review_required_defaults_false(db, student_user):
    assert student_user.manual_review_required is False


# ── BMM-10 / BMM-11  Relationships ───────────────────────────────────────────

def test_bmm10_user_biometric_consents_relationship(db, student_user):
    assert student_user.biometric_consents == []


def test_bmm11_user_face_embedding_relationship_none(db, student_user):
    assert student_user.face_embedding is None


# ── BMM-12  Log without liveness_metadata ────────────────────────────────────

def test_bmm12_log_without_metadata_ok(db, student_user):
    row = BiometricVerificationLog(
        user_id=student_user.id,
        event_type="consent_granted",
    )
    db.add(row)
    db.flush()
    assert row.liveness_metadata is None