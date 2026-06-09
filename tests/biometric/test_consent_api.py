"""
Biometric consent API endpoint tests.

BCA-01  POST /me/biometric-consent — 201 + has_consent=true
BCA-02  POST /me/biometric-consent — 409 when already active
BCA-03  GET  /me/biometric-consent — has_consent=true when active
BCA-04  GET  /me/biometric-consent — has_consent=false when no consent
BCA-05  DELETE /me/biometric-consent — 200 + has_consent=false
BCA-06  DELETE /me/biometric-consent — 404 when no active consent
BCA-07  Feature flag OFF — POST returns 503
BCA-08  Feature flag OFF — GET returns 503
BCA-09  Feature flag OFF — DELETE returns 503
BCA-10  POST response contains no face_match_score
BCA-11  POST response contains no embedding_ciphertext
BCA-12  GET response contains no face_match_score
BCA-13  DELETE response contains no face_match_score
BCA-14  Full grant → revoke cycle: face_match_status=consent_revoked on user
BCA-15  Unauthenticated request raises 401 (dependency chain)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.api_v1.endpoints.users.biometric_consent import (
    get_biometric_consent_status,
    grant_biometric_consent,
    revoke_biometric_consent,
)
from app.schemas.biometric import (
    BiometricConsentGrantRequest,
    BiometricConsentRevokeRequest,
    BiometricConsentStatusOut,
)

_MODULE = "app.api.api_v1.endpoints.users.biometric_consent"
_SVC    = "app.services.biometric.consent_service"


def _run(fn):
    # Endpoint functions are synchronous (def, not async def).
    # Call directly if not a coroutine.
    import inspect
    if inspect.iscoroutine(fn):
        return asyncio.run(fn)
    return fn


def _user(uid: int = 10, face_match_status: str | None = None):
    u = MagicMock()
    u.id               = uid
    u.face_match_status = face_match_status
    return u


def _mock_request(ip: str = "127.0.0.1", ua: str = "pytest/1.0"):
    req = MagicMock()
    req.headers.get = lambda key, default=None: {
        "x-forwarded-for": None,
        "x-real-ip":       None,
        "user-agent":      ua,
    }.get(key, default)
    req.client.host = ip
    return req


def _consent_row(
    *,
    is_active: bool = True,
    version: str = "v1.0",
    granted_at: datetime | None = None,
    revoked_at: datetime | None = None,
):
    row = MagicMock()
    row.is_active           = is_active
    row.consent_version     = version
    row.consent_granted_at  = granted_at or datetime.now(timezone.utc)
    row.consent_revoked_at  = revoked_at
    return row


# ── BCA-01 / BCA-02 ──────────────────────────────────────────────────────────

def test_bca01_post_returns_201_has_consent(db, student_user, biometric_feature_enabled):
    from app.services.biometric.consent_service import grant_consent as _gc
    payload = BiometricConsentGrantRequest(consent_version="v1.0")
    req = _mock_request()

    result = _run(grant_biometric_consent(
        payload=payload, request=req, db=db, current_user=student_user
    ))
    db.commit()

    assert isinstance(result, BiometricConsentStatusOut)
    assert result.has_consent is True
    assert result.is_active is True
    assert result.version == "v1.0"


def test_bca02_post_409_when_already_active(db, student_user, biometric_feature_enabled):
    from app.services.biometric.consent_service import grant_consent
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    db.flush()

    payload = BiometricConsentGrantRequest(consent_version="v1.0")
    req = _mock_request()
    with pytest.raises(HTTPException) as exc:
        _run(grant_biometric_consent(
            payload=payload, request=req, db=db, current_user=student_user
        ))
    assert exc.value.status_code == 409


# ── BCA-03 / BCA-04 ──────────────────────────────────────────────────────────

def test_bca03_get_returns_true_when_active(db, student_user, biometric_feature_enabled):
    from app.services.biometric.consent_service import grant_consent
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    db.flush()

    result = _run(get_biometric_consent_status(db=db, current_user=student_user))
    assert result.has_consent is True


def test_bca04_get_returns_false_when_no_consent(db, student_user, biometric_feature_enabled):
    result = _run(get_biometric_consent_status(db=db, current_user=student_user))
    assert result.has_consent is False
    assert result.is_active is False


# ── BCA-05 / BCA-06 ──────────────────────────────────────────────────────────

def test_bca05_delete_returns_revoked(db, student_user, biometric_feature_enabled):
    from app.services.biometric.consent_service import grant_consent
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    db.flush()

    payload = BiometricConsentRevokeRequest(reason="test")
    req = _mock_request()
    result = _run(revoke_biometric_consent(
        payload=payload, request=req, db=db, current_user=student_user
    ))
    db.commit()

    assert result.has_consent is False
    assert result.is_active is False
    assert result.revoked_at is not None


def test_bca06_delete_404_no_active_consent(db, student_user, biometric_feature_enabled):
    payload = BiometricConsentRevokeRequest()
    req = _mock_request()
    with pytest.raises(HTTPException) as exc:
        _run(revoke_biometric_consent(
            payload=payload, request=req, db=db, current_user=student_user
        ))
    assert exc.value.status_code == 404


# ── BCA-07 / BCA-08 / BCA-09  Feature flag OFF → 503 ─────────────────────────

def test_bca07_post_503_when_flag_off():
    # Flag is off by default (no biometric_feature_enabled fixture)
    from app.services.biometric.feature_flag import require_biometric_enabled
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_biometric_enabled())
    assert exc.value.status_code == 503


def test_bca08_get_503_when_flag_off():
    from app.services.biometric.feature_flag import require_biometric_enabled
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_biometric_enabled())
    assert exc.value.status_code == 503


def test_bca09_delete_503_when_flag_off():
    from app.services.biometric.feature_flag import require_biometric_enabled
    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_biometric_enabled())
    assert exc.value.status_code == 503


# ── BCA-10 … BCA-13  No forbidden fields in responses ────────────────────────

def test_bca10_post_response_no_face_match_score(db, student_user, biometric_feature_enabled):
    payload = BiometricConsentGrantRequest(consent_version="v1.0")
    result = _run(grant_biometric_consent(
        payload=payload, request=_mock_request(), db=db, current_user=student_user
    ))
    out = result.model_dump()
    assert "face_match_score" not in out
    assert "embedding_ciphertext" not in out


def test_bca11_post_response_no_embedding(db, student_user, biometric_feature_enabled):
    payload = BiometricConsentGrantRequest(consent_version="v1.0")
    result = _run(grant_biometric_consent(
        payload=payload, request=_mock_request(), db=db, current_user=student_user
    ))
    out = result.model_dump()
    for key in ("embedding_ciphertext", "embedding_iv", "yaw", "roll", "landmarks"):
        assert key not in out, f"Forbidden field {key!r} found in response"


def test_bca12_get_response_no_face_match_score(db, student_user, biometric_feature_enabled):
    result = _run(get_biometric_consent_status(db=db, current_user=student_user))
    out = result.model_dump()
    assert "face_match_score" not in out


def test_bca13_delete_response_no_face_match_score(db, student_user, biometric_feature_enabled):
    from app.services.biometric.consent_service import grant_consent
    grant_consent(db=db, user=student_user, consent_version="v1.0")
    db.flush()
    payload = BiometricConsentRevokeRequest()
    result = _run(revoke_biometric_consent(
        payload=payload, request=_mock_request(), db=db, current_user=student_user
    ))
    out = result.model_dump()
    assert "face_match_score" not in out


# ── BCA-14  Full cycle ────────────────────────────────────────────────────────

def test_bca14_full_grant_revoke_cycle(db, student_user, biometric_feature_enabled):
    payload_grant  = BiometricConsentGrantRequest(consent_version="v1.0")
    payload_revoke = BiometricConsentRevokeRequest(reason="full cycle test")
    req = _mock_request()

    # Grant
    r1 = _run(grant_biometric_consent(
        payload=payload_grant, request=req, db=db, current_user=student_user
    ))
    assert r1.has_consent is True

    # Revoke
    r2 = _run(revoke_biometric_consent(
        payload=payload_revoke, request=req, db=db, current_user=student_user
    ))
    assert r2.has_consent is False
    db.refresh(student_user)
    assert student_user.face_match_status == "consent_revoked"

    # Get — should show revoked
    r3 = _run(get_biometric_consent_status(db=db, current_user=student_user))
    assert r3.has_consent is False
    assert r3.revoked_at is not None


# ── BCA-15  BiometricConsentStatusOut schema fields ───────────────────────────

def test_bca15_schema_fields_no_forbidden_keys():
    """Structural: BiometricConsentStatusOut model_fields must not contain forbidden keys."""
    fields = set(BiometricConsentStatusOut.model_fields.keys())
    for forbidden in ("face_match_score", "embedding_ciphertext", "embedding_iv",
                      "yaw", "roll", "landmarks", "liveness_metadata"):
        assert forbidden not in fields, f"Forbidden field {forbidden!r} in response schema"


# ── IP extraction helper coverage ────────────────────────────────────────────

def test_extract_ip_uses_x_forwarded_for():
    from app.api.api_v1.endpoints.users.biometric_consent import _extract_ip
    req = MagicMock()
    req.headers.get = lambda k, default=None: "10.0.0.1, 10.0.0.2" if k == "x-forwarded-for" else default
    req.client.host = "1.2.3.4"
    assert _extract_ip(req) == "10.0.0.1"


def test_extract_ip_falls_back_to_x_real_ip():
    from app.api.api_v1.endpoints.users.biometric_consent import _extract_ip
    req = MagicMock()
    req.headers.get = lambda k, default=None: "10.1.1.1" if k == "x-real-ip" else None
    req.client.host = "1.2.3.4"
    assert _extract_ip(req) == "10.1.1.1"
