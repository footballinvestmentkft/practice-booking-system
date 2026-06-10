"""
Biometric disclosure endpoints — PR-7A.

GET    /me/biometric-disclosure  — query disclosure status
POST   /me/biometric-disclosure  — accept the biometric tájékoztató modal
DELETE /me/biometric-disclosure  — revoke disclosure (cascades to consent revoke)

Gated by BIOMETRIC_DISCLOSURE_ENABLED (separate from BIOMETRIC_FACE_MATCHING_ENABLED).
This allows the disclosure modal to be rolled out before face matching is active.

BCD-22 decision (documented): disclosure acceptance is allowed when
  BIOMETRIC_DISCLOSURE_ENABLED=True even if BIOMETRIC_FACE_MATCHING_ENABLED=False.
  Liveness/verify remain blocked by BIOMETRIC_FACE_MATCHING_ENABLED.

Not KYC. Not production-ready. DPIA / DPO approval pending.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.biometric import (
    BiometricDisclosureAcceptRequest,
    BiometricDisclosureStatusOut,
)
from app.services.biometric.feature_flag import require_disclosure_enabled
from app.services.biometric.disclosure_service import (
    accept_disclosure,
    get_disclosure_status,
    revoke_disclosure,
)

router = APIRouter()


def _extract_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    if request.client:
        return request.client.host
    return None


@router.get(
    "/me/biometric-disclosure",
    status_code=200,
    response_model=BiometricDisclosureStatusOut,
    dependencies=[Depends(require_disclosure_enabled)],
    summary="Get biometric disclosure acceptance status",
)
def get_biometric_disclosure(
    request: Request = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Return the user's current disclosure acceptance state.

    - Requires BIOMETRIC_DISCLOSURE_ENABLED=true (503 otherwise).
    - Rate limited: 30 / 60s per user (PR-8).
    - No face_match_score, embedding, or raw biometric data in response.
    """
    from app.services.biometric.rate_limiter import enforce_rate_limit, DISCLOSURE_GET
    _ip = _extract_ip(request) if request else None
    enforce_rate_limit(
        endpoint_group=DISCLOSURE_GET,
        user_id=current_user.id,
        ip=_ip,
        db=db,
        audit_user_id=current_user.id,
    )
    return get_disclosure_status(db=db, user=current_user)


@router.post(
    "/me/biometric-disclosure",
    status_code=201,
    response_model=BiometricDisclosureStatusOut,
    dependencies=[Depends(require_disclosure_enabled)],
    summary="Accept the biometric tájékoztató (disclosure modal)",
)
def accept_biometric_disclosure(
    payload: BiometricDisclosureAcceptRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Record the user's explicit acceptance of the biometric disclosure modal.

    - Requires BIOMETRIC_DISCLOSURE_ENABLED=true (503).
    - Raises 403 parental_consent_required if user is minor or age unknown.
    - Raises 422 if disclosure_version != CURRENT_BIOMETRIC_DISCLOSURE_VERSION.
    - Raises 409 if an active disclosure already exists.
    - No face_match_score, embedding in response.

    BCD-22: disclosure acceptance allowed even when BIOMETRIC_FACE_MATCHING_ENABLED=False.
    """
    from app.services.biometric.rate_limiter import enforce_rate_limit, DISCLOSURE_POST
    from app.services.biometric.metrics import biometric_metrics, M_DISCLOSURE_ACCEPT
    _ip = _extract_ip(request)
    enforce_rate_limit(
        endpoint_group=DISCLOSURE_POST,
        user_id=current_user.id,
        ip=_ip,
        db=db,
        audit_user_id=current_user.id,
    )
    result = accept_disclosure(
        db=db,
        user=current_user,
        disclosure_version=payload.disclosure_version,
        ip_address=_ip,
    )
    db.commit()
    biometric_metrics.increment(M_DISCLOSURE_ACCEPT)
    return result


@router.delete(
    "/me/biometric-disclosure",
    status_code=200,
    response_model=BiometricDisclosureStatusOut,
    dependencies=[Depends(require_disclosure_enabled)],
    summary="Revoke biometric disclosure (cascades to consent revoke)",
)
def revoke_biometric_disclosure(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Revoke the user's active biometric disclosure.

    - Raises 404 if no active disclosure exists.
    - Automatically revokes active biometric consent via the existing
      revoke_consent() service (cascades Celery embedding delete).
    - Audit: EVT_DISCLOSURE_REVOKED + EVT_CONSENT_REVOKED (if consent was active).
    - No face_match_score, embedding in response.
    """
    from app.services.biometric.rate_limiter import enforce_rate_limit, DISCLOSURE_DELETE
    from app.services.biometric.metrics import biometric_metrics, M_DISCLOSURE_REVOKE
    _ip = _extract_ip(request)
    enforce_rate_limit(
        endpoint_group=DISCLOSURE_DELETE,
        user_id=current_user.id,
        ip=_ip,
        db=db,
        audit_user_id=current_user.id,
    )
    result = revoke_disclosure(
        db=db,
        user=current_user,
        ip_address=_ip,
    )
    db.commit()
    biometric_metrics.increment(M_DISCLOSURE_REVOKE)
    return result