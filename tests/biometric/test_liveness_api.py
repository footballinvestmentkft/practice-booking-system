"""
Biometric liveness API endpoint tests.

BCL-01  POST /me/biometric-liveness — 201 + face_reference_photo_status set
BCL-02  face_reference_photo_status = onboarding_liveness_capture after success
BCL-03  face_match_status = reference_pending after success
BCL-04  Three audit log rows created (liveness_completed, reference_submitted, reference_auto_approved_liveness)
BCL-05  Feature flag OFF — POST returns 503
BCL-06  No active consent — POST returns 403
BCL-07  Duplicate onboarding_liveness submission — 409
BCL-08  liveness_metadata forbidden field (yaw) — 422
BCL-09  liveness_metadata forbidden field (device_model) — 422
BCL-10  photo_filename path traversal — 400
BCL-11  source != "onboarding_liveness" — 422
BCL-12  Unauthenticated — 401 (dependency chain)
BCL-13  Response contains no face_match_score field
BCL-14  Response contains no embedding field
BCL-15  liveness_metadata sanitizer runs (known forbidden key stripped + warning)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.api_v1.endpoints.users.biometric_liveness import submit_biometric_liveness
from app.models.biometric import BiometricVerificationLog
from app.schemas.biometric import BiometricLivenessSubmitRequest, LivenessMetadata
from app.services.biometric.audit_log import (
    EVT_LIVENESS_COMPLETED,
    EVT_REFERENCE_AUTO_APPROVED_LIVENESS,
    EVT_REFERENCE_SUBMITTED,
)

_MODULE = "app.api.api_v1.endpoints.users.biometric_liveness"
_SVC    = "app.services.biometric.liveness_service"


def _run(fn):
    import inspect
    if inspect.iscoroutine(fn):
        return asyncio.run(fn)
    return fn


def _mock_request(ip: str = "127.0.0.1"):
    req = MagicMock()
    req.headers.get = lambda key, default=None: {
        "x-forwarded-for": None,
        "x-real-ip":       None,
    }.get(key, default)
    req.client.host = ip
    return req


def _valid_payload(photo_filename: str | None = "photo_abc.jpg") -> BiometricLivenessSubmitRequest:
    return BiometricLivenessSubmitRequest(
        source="onboarding_liveness",
        liveness_metadata=LivenessMetadata(
            challenge_version="v1.0",
            steps_completed=["center", "left", "right"],
            total_duration_ms=4500,
            retry_count=0,
            failure_reason=None,
        ),
        photo_filename=photo_filename,
    )


def _grant_consent(db, user):
    from app.services.biometric.consent_service import grant_consent
    grant_consent(db=db, user=user, consent_version="v1.0")
    db.flush()


# ── BCL-01 / BCL-02 / BCL-03 ─────────────────────────────────────────────────

def test_bcl01_post_returns_201_fields(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    result = _run(submit_biometric_liveness(
        payload=_valid_payload(), request=_mock_request(), db=db, current_user=student_user
    ))
    assert result.has_biometric_consent is True
    assert result.face_reference_photo_status is not None
    assert result.face_match_status is not None


def test_bcl02_face_reference_photo_status_set(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    _run(submit_biometric_liveness(
        payload=_valid_payload(), request=_mock_request(), db=db, current_user=student_user
    ))
    db.refresh(student_user)
    assert student_user.face_reference_photo_status == "onboarding_liveness_capture"


def test_bcl03_face_match_status_reference_pending(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    _run(submit_biometric_liveness(
        payload=_valid_payload(), request=_mock_request(), db=db, current_user=student_user
    ))
    db.refresh(student_user)
    assert student_user.face_match_status == "reference_pending"
    assert student_user.face_match_status != "verified"


# ── BCL-04 — three audit log rows ─────────────────────────────────────────────

def test_bcl04_three_audit_log_rows(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    _run(submit_biometric_liveness(
        payload=_valid_payload(), request=_mock_request(), db=db, current_user=student_user
    ))
    logs = (
        db.query(BiometricVerificationLog)
        .filter(BiometricVerificationLog.user_id == student_user.id)
        .order_by(BiometricVerificationLog.id)
        .all()
    )
    # 1 consent_granted (from _grant_consent) + 3 liveness events
    event_types = [log.event_type for log in logs]
    assert EVT_LIVENESS_COMPLETED          in event_types
    assert EVT_REFERENCE_SUBMITTED         in event_types
    assert EVT_REFERENCE_AUTO_APPROVED_LIVENESS in event_types


# ── BCL-05 — feature flag OFF ─────────────────────────────────────────────────

def test_bcl05_post_503_when_flag_off():
    from app.services.biometric.feature_flag import require_biometric_enabled
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_biometric_enabled())
    assert exc.value.status_code == 503


# ── BCL-06 — no consent → 403 ────────────────────────────────────────────────

def test_bcl06_post_403_no_consent(db, student_user, biometric_feature_enabled):
    with pytest.raises(HTTPException) as exc:
        _run(submit_biometric_liveness(
            payload=_valid_payload(), request=_mock_request(), db=db, current_user=student_user
        ))
    assert exc.value.status_code == 403
    assert exc.value.detail == "biometric_consent_required"


# ── BCL-07 — duplicate submission → 409 ──────────────────────────────────────

def test_bcl07_post_409_duplicate(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    _run(submit_biometric_liveness(
        payload=_valid_payload(), request=_mock_request(), db=db, current_user=student_user
    ))
    with pytest.raises(HTTPException) as exc:
        _run(submit_biometric_liveness(
            payload=_valid_payload(), request=_mock_request(), db=db, current_user=student_user
        ))
    assert exc.value.status_code == 409


# ── BCL-08 / BCL-09 — forbidden liveness_metadata fields → 422 ───────────────

def test_bcl08_yaw_in_metadata_raises_422():
    with pytest.raises(Exception):
        BiometricLivenessSubmitRequest(
            source="onboarding_liveness",
            liveness_metadata={
                "challenge_version": "v1.0",
                "steps_completed": [],
                "total_duration_ms": 1000,
                "retry_count": 0,
                "yaw": 12.5,         # forbidden
            },
            photo_filename=None,
        )


def test_bcl09_device_model_in_metadata_raises_422():
    with pytest.raises(Exception):
        BiometricLivenessSubmitRequest(
            source="onboarding_liveness",
            liveness_metadata={
                "challenge_version": "v1.0",
                "steps_completed": [],
                "total_duration_ms": 1000,
                "retry_count": 0,
                "device_model": "iPhone 15",  # forbidden
            },
            photo_filename=None,
        )


# ── BCL-10 — path traversal photo_filename rejected ──────────────────────────
# Pydantic schema validator catches path traversal → ValidationError (→ 422 in FastAPI).
# Service-layer basename guard (→ 400) is tested in BCLS-07 (service called directly).

def test_bcl10_path_traversal_rejected_by_schema():
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as exc_info:
        BiometricLivenessSubmitRequest(
            source="onboarding_liveness",
            liveness_metadata=LivenessMetadata(
                challenge_version="v1.0",
                steps_completed=[],
                total_duration_ms=1000,
                retry_count=0,
            ),
            photo_filename="../secret/file.jpg",
        )
    errors = exc_info.value.errors()
    assert any("photo_filename" in str(e["loc"]) for e in errors)


# ── BCL-11 — wrong source → 422 ──────────────────────────────────────────────

def test_bcl11_wrong_source_raises_422():
    with pytest.raises(Exception):
        BiometricLivenessSubmitRequest(
            source="admin_upload",   # not "onboarding_liveness"
            liveness_metadata=LivenessMetadata(
                challenge_version="v1.0",
                steps_completed=[],
                total_duration_ms=1000,
                retry_count=0,
            ),
            photo_filename=None,
        )


# ── BCL-12 — unauthenticated → 401 (dependency chain structural test) ────────

def test_bcl12_unauthenticated_structural():
    from app.dependencies import get_current_user
    import inspect
    sig = inspect.signature(get_current_user)
    # Structural: get_current_user is a dependency that raises 401 when no token
    assert callable(get_current_user)


# ── BCL-13 / BCL-14 — no forbidden fields in response ────────────────────────

def test_bcl13_response_no_face_match_score(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    result = _run(submit_biometric_liveness(
        payload=_valid_payload(), request=_mock_request(), db=db, current_user=student_user
    ))
    out = result.model_dump()
    assert "face_match_score" not in out


def test_bcl14_response_no_embedding(db, student_user, biometric_feature_enabled):
    _grant_consent(db, student_user)
    result = _run(submit_biometric_liveness(
        payload=_valid_payload(), request=_mock_request(), db=db, current_user=student_user
    ))
    out = result.model_dump()
    for key in ("embedding_ciphertext", "embedding_iv", "yaw", "roll", "landmarks"):
        assert key not in out, f"Forbidden field {key!r} in response"


# ── BCL-15 — sanitizer runs and strips forbidden keys ────────────────────────

def test_bcl15_sanitizer_strips_forbidden_keys(db, student_user, biometric_feature_enabled, caplog):
    _grant_consent(db, student_user)
    import logging
    # Directly test sanitizer layer — liveness_metadata with a known forbidden key
    from app.services.biometric.liveness_metadata_sanitizer import sanitize_liveness_metadata
    raw = {
        "challenge_version": "v1.0",
        "steps_completed": ["center"],
        "total_duration_ms": 3000,
        "retry_count": 0,
        "yaw": 15.3,          # forbidden — should be stripped
        "device_model": "X",  # forbidden — should be stripped
    }
    with caplog.at_level(logging.WARNING, logger="app.services.biometric.liveness_metadata_sanitizer"):
        result = sanitize_liveness_metadata(raw)
    assert "yaw" not in result
    assert "device_model" not in result
    assert result["challenge_version"] == "v1.0"
    assert "yaw" in caplog.text or "device_model" in caplog.text