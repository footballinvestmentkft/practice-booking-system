"""
Biometric consent service tests.

BCS-01  grant_consent inserts a UserBiometricConsent row
BCS-02  grant_consent sets is_active=True
BCS-03  grant_consent stores consent_version
BCS-04  grant_consent stores ip_address
BCS-05  grant_consent raises 409 when active consent already exists
BCS-06  grant_consent reactivates a previously revoked row (no duplicate INSERT)
BCS-07  grant_consent logs EVT_CONSENT_GRANTED audit row
BCS-08  get_consent_status returns None when no row exists
BCS-09  get_consent_status returns active row
BCS-10  get_consent_status returns revoked row (most recent)
BCS-11  revoke_consent raises 404 when no active consent
BCS-12  revoke_consent sets is_active=False
BCS-13  revoke_consent sets consent_revoked_at
BCS-14  revoke_consent sets user.face_match_status="consent_revoked"
BCS-15  revoke_consent deactivates user_face_embeddings (is_active=False)
BCS-16  revoke_consent logs EVT_CONSENT_REVOKED audit row
BCS-17  revoke_consent logs EVT_EMBEDDING_DELETED placeholder
BCS-18  revoke_consent with no embedding — no error, no orphan rows
BCS-19  revoke reason stored on consent row
BCS-20  response contains no face_match_score, embedding, or liveness raw data
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.models.biometric import (
    BiometricVerificationLog,
    UserBiometricConsent,
    UserFaceEmbedding,
)
from app.services.biometric.audit_log import (
    EVT_CONSENT_GRANTED,
    EVT_CONSENT_REVOKED,
    EVT_EMBEDDING_DELETED,
)
from app.services.biometric.consent_service import (
    get_consent_status,
    grant_consent,
    revoke_consent,
)


# ── BCS-01 … BCS-07  grant_consent ───────────────────────────────────────────

def test_bcs01_grant_inserts_row(db, student_user):
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    rows = db.query(UserBiometricConsent).filter_by(user_id=student_user.id).all()
    assert len(rows) == 1


def test_bcs02_grant_sets_is_active(db, student_user):
    consent = grant_consent(db=db, user=student_user, consent_version="v1.0")
    assert consent.is_active is True


def test_bcs03_grant_stores_version(db, student_user):
    consent = grant_consent(db=db, user=student_user, consent_version="v1.0")
    assert consent.consent_version == "v1.0"


def test_bcs04_grant_stores_ip(db, student_user):
    consent = grant_consent(
        db=db, user=student_user, consent_version="v1.0", ip_address="1.2.3.4"
    )
    assert consent.consent_ip_address == "1.2.3.4"


def test_bcs05_grant_409_when_already_active(db, student_user):
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    with pytest.raises(HTTPException) as exc:
        grant_consent(db=db, user=student_user, consent_version="v1.0")
    assert exc.value.status_code == 409


def test_bcs06_grant_reactivates_revoked_row(db, student_user):
    # Grant → revoke → re-grant — should reuse the existing row, not INSERT new
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    revoke_consent(db=db, user=student_user)
    grant_consent(db=db, user=student_user, consent_version="v1.1")
    rows = db.query(UserBiometricConsent).filter_by(user_id=student_user.id).all()
    assert len(rows) == 1
    assert rows[0].consent_version == "v1.1"
    assert rows[0].is_active is True


def test_bcs07_grant_logs_audit_row(db, student_user):
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    row = db.query(BiometricVerificationLog).filter_by(
        user_id=student_user.id, event_type=EVT_CONSENT_GRANTED
    ).first()
    assert row is not None
    assert row.event_result == "accepted"


# ── BCS-08 … BCS-10  get_consent_status ──────────────────────────────────────

def test_bcs08_get_returns_none_when_no_row(db, student_user):
    assert get_consent_status(db=db, user_id=student_user.id) is None


def test_bcs09_get_returns_active_row(db, student_user):
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    row = get_consent_status(db=db, user_id=student_user.id)
    assert row is not None
    assert row.is_active is True


def test_bcs10_get_returns_revoked_row(db, student_user):
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    revoke_consent(db=db, user=student_user)
    row = get_consent_status(db=db, user_id=student_user.id)
    assert row is not None
    assert row.is_active is False
    assert row.consent_revoked_at is not None


# ── BCS-11 … BCS-19  revoke_consent ──────────────────────────────────────────

def test_bcs11_revoke_404_no_active_consent(db, student_user):
    with pytest.raises(HTTPException) as exc:
        revoke_consent(db=db, user=student_user)
    assert exc.value.status_code == 404


def test_bcs12_revoke_sets_is_active_false(db, student_user):
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    revoke_consent(db=db, user=student_user)
    row = db.query(UserBiometricConsent).filter_by(user_id=student_user.id).first()
    assert row.is_active is False


def test_bcs13_revoke_sets_revoked_at(db, student_user):
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    revoke_consent(db=db, user=student_user)
    row = db.query(UserBiometricConsent).filter_by(user_id=student_user.id).first()
    assert row.consent_revoked_at is not None


def test_bcs14_revoke_sets_face_match_status(db, student_user):
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    revoke_consent(db=db, user=student_user)
    db.refresh(student_user)
    assert student_user.face_match_status == "consent_revoked"


def test_bcs15_revoke_deactivates_embedding(db, student_user):
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    # Insert an active embedding
    emb = UserFaceEmbedding(user_id=student_user.id, is_active=True)
    db.add(emb)
    db.flush()
    revoke_consent(db=db, user=student_user)
    db.refresh(emb)
    assert emb.is_active is False


def test_bcs16_revoke_logs_consent_revoked(db, student_user):
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    revoke_consent(db=db, user=student_user)
    row = db.query(BiometricVerificationLog).filter_by(
        user_id=student_user.id, event_type=EVT_CONSENT_REVOKED
    ).first()
    assert row is not None


def test_bcs17_revoke_dispatches_celery_delete_task(db, student_user):
    """
    PR-4: revoke_consent dispatches biometric_delete_embedding_task via Celery apply_async
    (replaces the PR-2 placeholder that wrote an EVT_EMBEDDING_DELETED(pending) log row).
    """
    from unittest.mock import patch
    grant_consent(db=db, user=student_user, consent_version="v1.0")

    with patch(
        "app.tasks.biometric_tasks.biometric_delete_embedding_task.apply_async"
    ) as mock_dispatch:
        revoke_consent(db=db, user=student_user)

    mock_dispatch.assert_called_once()
    kwargs = mock_dispatch.call_args.kwargs
    assert kwargs["args"] == [student_user.id]
    assert "eta" in kwargs, "delete task must be scheduled with ETA (30-day delayed deletion)"


def test_bcs18_revoke_no_embedding_no_error(db, student_user):
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    # No embedding row — should not raise
    revoke_consent(db=db, user=student_user)
    assert student_user.face_match_status == "consent_revoked"


def test_bcs19_revoke_reason_stored(db, student_user):
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    revoke_consent(db=db, user=student_user, reason="User requested data deletion")
    row = db.query(UserBiometricConsent).filter_by(user_id=student_user.id).first()
    assert row.revocation_reason == "User requested data deletion"


# ── BCS-20  No forbidden data in service return values ───────────────────────

def test_bcs20_consent_row_has_no_score_or_embedding(db, student_user):
    consent = grant_consent(db=db, user=student_user, consent_version="v1.0")
    # UserBiometricConsent model must not carry biometric data
    assert not hasattr(consent, "face_match_score")
    assert not hasattr(consent, "embedding_ciphertext")
    assert not hasattr(consent, "yaw")
    assert not hasattr(consent, "roll")
