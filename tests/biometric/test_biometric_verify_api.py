"""
Biometric face verify API endpoint tests — PR-6.

BCM-11  POST /me/biometric-verify — feature flag OFF → 503
BCM-12  POST /me/biometric-verify — no active consent → 403
BCM-13  POST /me/biometric-verify — no active embedding → 404
BCM-14  POST /me/biometric-verify — match → 200 {"result": "verified"}
BCM-15  POST /me/biometric-verify — response contains no face_match_score
BCM-16  POST /me/biometric-verify — mocked review outcome → {"result": "manual_review_required"}
BCM-17  POST /me/biometric-verify — mocked rejected outcome → {"result": "rejected"}
BCM-18  POST /me/biometric-verify — audit EVT_MATCH_SUCCESS written when verified
BCM-19  POST /me/biometric-verify — audit EVT_MATCH_FAILED written when rejected
BCM-20  POST /me/biometric-verify — audit EVT_MATCH_REVIEW_REQUIRED when review band
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.api_v1.endpoints.users.biometric_verify import verify_biometric
from app.models.biometric import BiometricVerificationLog
from app.schemas.biometric import BiometricVerifyRequest, BiometricVerifyResponse
from app.services.biometric.audit_log import (
    EVT_MATCH_FAILED,
    EVT_MATCH_REVIEW_REQUIRED,
    EVT_MATCH_SUCCESS,
)

_MODULE  = "app.api.api_v1.endpoints.users.biometric_verify"
_MATCHER = "app.api.api_v1.endpoints.users.biometric_verify.run_face_match"


def _run(fn):
    import inspect
    if inspect.iscoroutine(fn):
        return asyncio.run(fn)
    return fn


def _valid_payload(photo_filename: str | None = "verify_photo.jpg") -> BiometricVerifyRequest:
    return BiometricVerifyRequest(photo_filename=photo_filename)


def _mock_user(uid: int = 42, face_match_status: str | None = None):
    u = MagicMock()
    u.id                    = uid
    u.face_match_status     = face_match_status
    u.manual_review_required = False
    return u


# ── BCM-11 — feature flag OFF → 503 ──────────────────────────────────────────

def test_bcm11_feature_flag_off_returns_503(db, student_user):
    """require_biometric_enabled dependency raises 503 when flag is off."""
    from app.services.biometric.feature_flag import require_biometric_enabled

    async def _call():
        await require_biometric_enabled()

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_call())
    assert exc_info.value.status_code == 503


# ── BCM-12 — no active consent → 403 ─────────────────────────────────────────

def test_bcm12_no_consent_returns_403(db, student_user, biometric_feature_enabled, allow_test_key):
    # No consent row → 403
    with pytest.raises(HTTPException) as exc_info:
        _run(verify_biometric(
            payload=_valid_payload(),
            db=db,
            current_user=student_user,
        ))
    assert exc_info.value.status_code == 403
    assert "consent" in exc_info.value.detail


# ── BCM-13 — no active embedding → 404 ───────────────────────────────────────

def test_bcm13_no_active_embedding_returns_404(
    db, student_user, biometric_feature_enabled, encryption_test_key
):
    from app.services.biometric.consent_service import grant_consent
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    db.flush()

    # Consent exists but no embedding → 404
    with pytest.raises(HTTPException) as exc_info:
        _run(verify_biometric(
            payload=_valid_payload(),
            db=db,
            current_user=student_user,
        ))
    assert exc_info.value.status_code == 404
    assert "reference_not_found" in exc_info.value.detail


# ── BCM-14 — match → 200 {"result": "verified"} ──────────────────────────────

def test_bcm14_match_returns_verified(
    db, student_user, biometric_feature_enabled, encryption_test_key, allow_test_key
):
    from app.services.biometric.consent_service import grant_consent
    from app.services.biometric.embedding_service import FakeEmbeddingProvider, store_embedding

    grant_consent(db=db, user=student_user, consent_version="v1.0")
    seed = b"verify_photo.jpg"
    emb  = FakeEmbeddingProvider().generate(seed)
    row  = store_embedding(db=db, user_id=student_user.id, embedding=emb, model_version="fake_v1")
    row.is_active = True
    db.flush()

    # Same photo_filename → same seed → cosine = 1.0 → verified
    response = _run(verify_biometric(
        payload=_valid_payload("verify_photo.jpg"),
        db=db,
        current_user=student_user,
    ))

    assert isinstance(response, BiometricVerifyResponse)
    assert response.result == "verified"


# ── BCM-15 — response has no face_match_score ─────────────────────────────────

def test_bcm15_response_has_no_face_match_score(
    db, student_user, biometric_feature_enabled, encryption_test_key, allow_test_key
):
    from app.services.biometric.consent_service import grant_consent
    from app.services.biometric.embedding_service import FakeEmbeddingProvider, store_embedding

    grant_consent(db=db, user=student_user, consent_version="v1.0")
    seed = b"verify_photo.jpg"
    emb  = FakeEmbeddingProvider().generate(seed)
    row  = store_embedding(db=db, user_id=student_user.id, embedding=emb, model_version="fake_v1")
    row.is_active = True
    db.flush()

    response = _run(verify_biometric(
        payload=_valid_payload("verify_photo.jpg"),
        db=db,
        current_user=student_user,
    ))

    assert not hasattr(response, "face_match_score"), "face_match_score must never be in response"
    assert not hasattr(response, "embedding"), "embedding must never be in response"
    response_dict = response.model_dump()
    assert "face_match_score" not in response_dict
    assert "embedding" not in response_dict


# ── BCM-16 — mocked review outcome → manual_review_required ──────────────────

def test_bcm16_review_band_outcome(
    db, student_user, biometric_feature_enabled, encryption_test_key, allow_test_key
):
    from app.services.biometric.consent_service import grant_consent
    from app.services.biometric.embedding_service import FakeEmbeddingProvider, store_embedding

    grant_consent(db=db, user=student_user, consent_version="v1.0")
    emb = FakeEmbeddingProvider().generate(b"seed")
    row = store_embedding(db=db, user_id=student_user.id, embedding=emb, model_version="fake_v1")
    row.is_active = True
    db.flush()

    with patch(_MATCHER, return_value="manual_review_required"):
        response = _run(verify_biometric(
            payload=_valid_payload(),
            db=db,
            current_user=student_user,
        ))

    assert response.result == "manual_review_required"


# ── BCM-17 — mocked rejected outcome ─────────────────────────────────────────

def test_bcm17_rejected_outcome(
    db, student_user, biometric_feature_enabled, encryption_test_key, allow_test_key
):
    from app.services.biometric.consent_service import grant_consent
    from app.services.biometric.embedding_service import FakeEmbeddingProvider, store_embedding

    grant_consent(db=db, user=student_user, consent_version="v1.0")
    emb = FakeEmbeddingProvider().generate(b"seed")
    row = store_embedding(db=db, user_id=student_user.id, embedding=emb, model_version="fake_v1")
    row.is_active = True
    db.flush()

    with patch(_MATCHER, return_value="rejected"):
        response = _run(verify_biometric(
            payload=_valid_payload(),
            db=db,
            current_user=student_user,
        ))

    assert response.result == "rejected"


# ── BCM-18 — EVT_MATCH_SUCCESS written on verified ────────────────────────────

def test_bcm18_audit_match_success(
    db, student_user, biometric_feature_enabled, encryption_test_key, allow_test_key
):
    from app.services.biometric.consent_service import grant_consent
    from app.services.biometric.embedding_service import FakeEmbeddingProvider, store_embedding

    grant_consent(db=db, user=student_user, consent_version="v1.0")
    seed = b"verify_photo.jpg"
    emb  = FakeEmbeddingProvider().generate(seed)
    row  = store_embedding(db=db, user_id=student_user.id, embedding=emb, model_version="fake_v1")
    row.is_active = True
    db.flush()

    _run(verify_biometric(
        payload=_valid_payload("verify_photo.jpg"),
        db=db,
        current_user=student_user,
    ))

    logs = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student_user.id,
        BiometricVerificationLog.event_type == EVT_MATCH_SUCCESS,
    ).all()
    assert logs, "EVT_MATCH_SUCCESS must be written to audit log"


# ── BCM-19 — EVT_MATCH_FAILED written on rejected ────────────────────────────

def test_bcm19_audit_match_failed(
    db, student_user, biometric_feature_enabled, encryption_test_key, allow_test_key
):
    from app.services.biometric.consent_service import grant_consent
    from app.services.biometric.embedding_service import FakeEmbeddingProvider, store_embedding

    grant_consent(db=db, user=student_user, consent_version="v1.0")
    emb = FakeEmbeddingProvider().generate(b"ref_seed")
    row = store_embedding(db=db, user_id=student_user.id, embedding=emb, model_version="fake_v1")
    row.is_active = True
    db.flush()

    # Different seed → different embedding → low similarity
    # We patch classify to guarantee "rejected" rather than relying on exact score
    with patch(
        "app.services.biometric.matching_service.classify_match_outcome",
        return_value="rejected",
    ):
        _run(verify_biometric(
            payload=_valid_payload("different_photo.jpg"),
            db=db,
            current_user=student_user,
        ))

    logs = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student_user.id,
        BiometricVerificationLog.event_type == EVT_MATCH_FAILED,
    ).all()
    assert logs, "EVT_MATCH_FAILED must be written to audit log"


# ── BCM-20 — EVT_MATCH_REVIEW_REQUIRED written on review band ────────────────

def test_bcm20_audit_match_review_required(
    db, student_user, biometric_feature_enabled, encryption_test_key, allow_test_key
):
    from app.services.biometric.consent_service import grant_consent
    from app.services.biometric.embedding_service import FakeEmbeddingProvider, store_embedding

    grant_consent(db=db, user=student_user, consent_version="v1.0")
    emb = FakeEmbeddingProvider().generate(b"ref_seed")
    row = store_embedding(db=db, user_id=student_user.id, embedding=emb, model_version="fake_v1")
    row.is_active = True
    db.flush()

    with patch(
        "app.services.biometric.matching_service.classify_match_outcome",
        return_value="manual_review_required",
    ):
        _run(verify_biometric(
            payload=_valid_payload("live_photo.jpg"),
            db=db,
            current_user=student_user,
        ))

    logs = db.query(BiometricVerificationLog).filter(
        BiometricVerificationLog.user_id == student_user.id,
        BiometricVerificationLog.event_type == EVT_MATCH_REVIEW_REQUIRED,
    ).all()
    assert logs, "EVT_MATCH_REVIEW_REQUIRED must be written to audit log"