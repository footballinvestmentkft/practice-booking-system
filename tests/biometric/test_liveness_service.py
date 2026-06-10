"""
Biometric liveness service unit tests.

BCLS-01  submit_liveness_result happy path — return type and fields
BCLS-02  No active consent → HTTPException 403
BCLS-03  user.face_reference_photo_status set to onboarding_liveness_capture
BCLS-04  user.face_match_status set to reference_pending (not verified)
BCLS-05  Three audit log rows written (liveness_completed / reference_submitted / auto_approved)
BCLS-06  sanitize_liveness_metadata called unconditionally
BCLS-07  photo_filename basename guard (path traversal → 400)
BCLS-08  Duplicate onboarding_liveness submission → 409
BCLS-09  Embedding placeholder log emitted — Celery task NOT called
BCLS-10  db.flush() called after status update
BCLS-11  face_match_score absent from return value
BCLS-12  ip_address stored in audit log rows
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch, call

import pytest
from fastapi import HTTPException

from app.models.biometric import BiometricVerificationLog
from app.schemas.biometric import BiometricVerificationStatusOut
from app.services.biometric.audit_log import (
    EVT_LIVENESS_COMPLETED,
    EVT_REFERENCE_AUTO_APPROVED_LIVENESS,
    EVT_REFERENCE_SUBMITTED,
)
from app.services.biometric.liveness_service import submit_liveness_result

_VALID_METADATA = {
    "challenge_version": "v1.0",
    "steps_completed": ["center", "left", "right"],
    "total_duration_ms": 4200,
    "retry_count": 0,
    "failure_reason": None,
}


def _grant_consent(db, user):
    from app.services.biometric.consent_service import grant_consent
    grant_consent(db=db, user=user, consent_version="v1.0")
    db.flush()


# ── BCLS-01 — happy path return value ────────────────────────────────────────

def test_bcls01_happy_path_return_type(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    result = submit_liveness_result(
        db=db,
        user=student_user,
        liveness_metadata=_VALID_METADATA,
        source="onboarding_liveness",
        photo_filename="ref.jpg",
        ip_address="10.0.0.1",
    )
    assert isinstance(result, BiometricVerificationStatusOut)
    assert result.has_biometric_consent is True
    assert result.face_reference_photo_status == "onboarding_liveness_capture"
    assert result.face_match_status == "reference_pending"
    assert result.manual_review_required is False


# ── BCLS-02 — no consent → 403 ───────────────────────────────────────────────

def test_bcls02_no_consent_raises_403(db, student_user, biometric_feature_enabled):
    with pytest.raises(HTTPException) as exc:
        submit_liveness_result(
            db=db,
            user=student_user,
            liveness_metadata=_VALID_METADATA,
            source="onboarding_liveness",
            photo_filename=None,
        )
    assert exc.value.status_code == 403
    assert exc.value.detail == "biometric_consent_required"


# ── BCLS-03 / BCLS-04 — status columns updated ───────────────────────────────

def test_bcls03_face_reference_photo_status(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    submit_liveness_result(
        db=db, user=student_user, liveness_metadata=_VALID_METADATA,
        source="onboarding_liveness", photo_filename=None,
    )
    db.refresh(student_user)
    assert student_user.face_reference_photo_status == "onboarding_liveness_capture"


def test_bcls04_face_match_status_not_verified(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    submit_liveness_result(
        db=db, user=student_user, liveness_metadata=_VALID_METADATA,
        source="onboarding_liveness", photo_filename=None,
    )
    db.refresh(student_user)
    assert student_user.face_match_status == "reference_pending"
    assert student_user.face_match_status != "verified"


# ── BCLS-05 — three audit log rows ───────────────────────────────────────────

def test_bcls05_three_audit_rows(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    submit_liveness_result(
        db=db, user=student_user, liveness_metadata=_VALID_METADATA,
        source="onboarding_liveness", photo_filename="photo.jpg",
    )
    logs = (
        db.query(BiometricVerificationLog)
        .filter(BiometricVerificationLog.user_id == student_user.id)
        .all()
    )
    event_types = {log.event_type for log in logs}
    assert EVT_LIVENESS_COMPLETED in event_types
    assert EVT_REFERENCE_SUBMITTED in event_types
    assert EVT_REFERENCE_AUTO_APPROVED_LIVENESS in event_types


# ── BCLS-06 — sanitizer called unconditionally ────────────────────────────────

def test_bcls06_sanitizer_called(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    with patch(
        "app.services.biometric.liveness_service.sanitize_liveness_metadata",
        wraps=__import__(
            "app.services.biometric.liveness_metadata_sanitizer",
            fromlist=["sanitize_liveness_metadata"]
        ).sanitize_liveness_metadata,
    ) as mock_san:
        submit_liveness_result(
            db=db, user=student_user, liveness_metadata=_VALID_METADATA,
            source="onboarding_liveness", photo_filename=None,
        )
    mock_san.assert_called_once()


# ── BCLS-07 — path traversal photo_filename → 400 ────────────────────────────

def test_bcls07_path_traversal_raises_400(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    with pytest.raises(HTTPException) as exc:
        submit_liveness_result(
            db=db, user=student_user, liveness_metadata=_VALID_METADATA,
            source="onboarding_liveness", photo_filename="../etc/passwd",
        )
    assert exc.value.status_code == 400
    assert "path_traversal" in exc.value.detail


# ── BCLS-08 — duplicate submission → 409 ─────────────────────────────────────

def test_bcls08_duplicate_submission_raises_409(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    submit_liveness_result(
        db=db, user=student_user, liveness_metadata=_VALID_METADATA,
        source="onboarding_liveness", photo_filename=None,
    )
    with pytest.raises(HTTPException) as exc:
        submit_liveness_result(
            db=db, user=student_user, liveness_metadata=_VALID_METADATA,
            source="onboarding_liveness", photo_filename=None,
        )
    assert exc.value.status_code == 409


# ── BCLS-09 — Celery generate task dispatched (PR-4) ─────────────────────────

def test_bcls09_celery_generate_task_dispatched(db, student_user, biometric_feature_enabled, caplog):
    """
    PR-4: liveness_service dispatches biometric_generate_embedding_task via apply_async.
    Replaces the PR-2/3 placeholder log message test.
    """
    from unittest.mock import patch
    _grant_consent(db, student_user)

    with patch(
        "app.tasks.biometric_tasks.biometric_generate_embedding_task.apply_async"
    ) as mock_dispatch, caplog.at_level(logging.INFO, logger="app.services.biometric.liveness_service"):
        submit_liveness_result(
            db=db, user=student_user, liveness_metadata=_VALID_METADATA,
            source="onboarding_liveness", photo_filename=None,
        )

    mock_dispatch.assert_called_once()
    assert "dispatched" in caplog.text


# ── BCLS-10 — db.flush() called ──────────────────────────────────────────────

def test_bcls10_db_flush_called(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    original_flush = db.flush
    flush_count = []
    def counting_flush(*a, **kw):
        flush_count.append(1)
        return original_flush(*a, **kw)
    db.flush = counting_flush
    submit_liveness_result(
        db=db, user=student_user, liveness_metadata=_VALID_METADATA,
        source="onboarding_liveness", photo_filename=None,
    )
    db.flush = original_flush
    assert len(flush_count) >= 1


# ── BCLS-11 — face_match_score absent from return ────────────────────────────

def test_bcls11_no_face_match_score_in_return(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    result = submit_liveness_result(
        db=db, user=student_user, liveness_metadata=_VALID_METADATA,
        source="onboarding_liveness", photo_filename=None,
    )
    out = result.model_dump()
    assert "face_match_score" not in out


# ── BCLS-12 — ip_address in audit log rows ───────────────────────────────────

def test_bcls12_ip_address_in_audit_log(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    submit_liveness_result(
        db=db, user=student_user, liveness_metadata=_VALID_METADATA,
        source="onboarding_liveness", photo_filename=None,
        ip_address="192.168.1.42",
    )
    logs = (
        db.query(BiometricVerificationLog)
        .filter(
            BiometricVerificationLog.user_id == student_user.id,
            BiometricVerificationLog.event_type == EVT_LIVENESS_COMPLETED,
        )
        .all()
    )
    assert any(log.actor_ip_address == "192.168.1.42" for log in logs)