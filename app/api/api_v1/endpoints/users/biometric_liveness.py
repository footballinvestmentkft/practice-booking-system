"""
Biometric liveness reference endpoint — PR-3.

POST /me/biometric-liveness
  Accepts a completed onboarding liveness challenge result.
  Feature-flag gated (BIOMETRIC_FACE_MATCHING_ENABLED).
  Requires active biometric consent.

Not KYC. Not production-ready. DPIA / DPO approval pending.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.api_v1.endpoints.users.biometric_consent import _extract_ip
from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.biometric import (
    BiometricLivenessSubmitRequest,
    BiometricVerificationStatusOut,
)
from app.services.biometric.feature_flag import require_biometric_enabled
from app.services.biometric.liveness_service import submit_liveness_result

router = APIRouter()


@router.post(
    "/me/biometric-liveness",
    status_code=201,
    response_model=BiometricVerificationStatusOut,
    dependencies=[Depends(require_biometric_enabled)],
    summary="Submit onboarding liveness challenge result",
)
def submit_biometric_liveness(
    payload: BiometricLivenessSubmitRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Record a completed onboarding liveness challenge on the backend.

    - Requires BIOMETRIC_FACE_MATCHING_ENABLED=true (feature flag).
    - Requires active biometric consent (403 otherwise).
    - Accepts source=onboarding_liveness only.
    - liveness_metadata is validated by schema (extra fields → 422)
      and sanitized again by the service layer before DB write.
    - Returns face_reference_photo_status and face_match_status.
    - face_match_score is never returned.
    - Embedding generation is a placeholder (PR-4).
    """
    from app.services.biometric.rate_limiter import enforce_rate_limit, LIVENESS_SUBMIT
    from app.services.biometric.metrics import biometric_metrics, M_LIVENESS_SUBMIT
    _ip = _extract_ip(request)
    enforce_rate_limit(
        endpoint_group=LIVENESS_SUBMIT,
        user_id=current_user.id,
        ip=_ip,
        db=db,
        audit_user_id=current_user.id,
    )
    result = submit_liveness_result(
        db=db,
        user=current_user,
        liveness_metadata=payload.liveness_metadata.model_dump(),
        source=payload.source,
        photo_filename=payload.photo_filename,
        ip_address=_ip,
    )
    db.commit()
    biometric_metrics.increment(M_LIVENESS_SUBMIT, status="completed")
    return result