"""
Biometric consent endpoints — PR-2.

POST   /api/v1/users/me/biometric-consent   — grant consent
GET    /api/v1/users/me/biometric-consent   — get consent status
DELETE /api/v1/users/me/biometric-consent   — revoke consent

All endpoints are gated by require_biometric_enabled().
When BIOMETRIC_FACE_MATCHING_ENABLED=false the endpoints return HTTP 503.

Response rules (enforced structurally):
  - face_match_score is absent from every response
  - embedding_ciphertext / embedding_iv are absent from every response
  - raw liveness data (yaw, roll, landmarks) is absent from every response
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.biometric import (
    BiometricConsentGrantRequest,
    BiometricConsentRevokeRequest,
    BiometricConsentStatusOut,
)
from app.services.biometric.consent_service import (
    get_consent_status,
    grant_consent,
    revoke_consent,
)
from app.services.biometric.feature_flag import require_biometric_enabled

router = APIRouter()


def _extract_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    return getattr(request.client, "host", None)


@router.post(
    "/me/biometric-consent",
    status_code=201,
    response_model=BiometricConsentStatusOut,
    dependencies=[Depends(require_biometric_enabled)],
    summary="Grant biometric consent",
)
def grant_biometric_consent(
    payload: BiometricConsentGrantRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Grant GDPR Art. 9 explicit biometric consent.

    Returns 409 if active consent already exists.
    Logs EVT_CONSENT_GRANTED to biometric_verification_logs.
    """
    consent = grant_consent(
        db              = db,
        user            = current_user,
        consent_version = payload.consent_version,
        ip_address      = _extract_ip(request),
        user_agent      = request.headers.get("user-agent"),
    )
    db.commit()
    return _consent_to_out(current_user.id, consent, db)


@router.get(
    "/me/biometric-consent",
    response_model=BiometricConsentStatusOut,
    dependencies=[Depends(require_biometric_enabled)],
    summary="Get biometric consent status",
)
def get_biometric_consent_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Return the current biometric consent state for the authenticated user.

    has_consent=false when no consent row exists or when it has been revoked.
    face_match_score and embedding data are never returned.
    """
    row = get_consent_status(db=db, user_id=current_user.id)
    return _consent_to_out(current_user.id, row, db)


@router.delete(
    "/me/biometric-consent",
    response_model=BiometricConsentStatusOut,
    dependencies=[Depends(require_biometric_enabled)],
    summary="Revoke biometric consent",
)
def revoke_biometric_consent(
    payload: BiometricConsentRevokeRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Revoke biometric consent (GDPR Art. 7(3) right to withdraw).

    Effects:
      - consent.is_active = False
      - user.face_match_status = "consent_revoked"
      - user_face_embeddings.is_active = False (if present)
      - Physical embedding deletion scheduled (Celery task in PR-4)

    Returns 404 if no active consent exists.
    Logs EVT_CONSENT_REVOKED + EVT_EMBEDDING_DELETED placeholder.
    """
    consent = revoke_consent(
        db         = db,
        user       = current_user,
        reason     = payload.reason,
        ip_address = _extract_ip(request),
    )
    db.commit()
    return _consent_to_out(current_user.id, consent, db)


def _consent_to_out(
    user_id: int,
    row: "UserBiometricConsent | None",
    db: Session,
) -> BiometricConsentStatusOut:
    if row is None:
        return BiometricConsentStatusOut(
            has_consent = False,
            is_active   = False,
        )
    return BiometricConsentStatusOut(
        has_consent = row.is_active,
        granted_at  = row.consent_granted_at,
        version     = row.consent_version,
        revoked_at  = row.consent_revoked_at,
        is_active   = row.is_active,
    )
