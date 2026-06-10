"""
Biometric face verification endpoint — PR-6.

POST /me/biometric-verify
  Compare a live-capture photo against the user's stored reference embedding.
  Feature-flag gated (BIOMETRIC_FACE_MATCHING_ENABLED).
  Requires active biometric consent and an active reference embedding.

Not KYC. Not production-ready. DPIA / DPO approval pending.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.biometric import UserBiometricConsent, UserFaceEmbedding
from app.models.user import User
from app.schemas.biometric import BiometricVerifyRequest, BiometricVerifyResponse
from app.services.biometric.feature_flag import require_biometric_enabled
from app.services.biometric.matching_service import run_face_match

router = APIRouter()


@router.post(
    "/me/biometric-verify",
    status_code=200,
    response_model=BiometricVerifyResponse,
    dependencies=[Depends(require_biometric_enabled)],
    summary="Verify live face against stored reference embedding",
)
def verify_biometric(
    payload: BiometricVerifyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """
    Compare a live-capture photo against the user's stored reference embedding.

    - Requires BIOMETRIC_FACE_MATCHING_ENABLED=true (feature flag).
    - Requires active biometric consent (403 otherwise).
    - Requires an active reference embedding (404 otherwise).
    - photo_filename path-traversal guard enforced by schema + service.
    - Returns result: verified | manual_review_required | rejected.
    - face_match_score is NEVER returned — stored internally in audit log only.
    """
    # ── Consent guard ─────────────────────────────────────────────────────────
    active_consent = (
        db.query(UserBiometricConsent)
        .filter(
            UserBiometricConsent.user_id == current_user.id,
            UserBiometricConsent.is_active.is_(True),
        )
        .first()
    )
    if not active_consent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="biometric_consent_required",
        )

    # ── Active embedding guard ────────────────────────────────────────────────
    active_embedding = (
        db.query(UserFaceEmbedding)
        .filter_by(user_id=current_user.id, is_active=True)
        .first()
    )
    if active_embedding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="biometric_reference_not_found",
        )

    # ── Build image seed ──────────────────────────────────────────────────────
    # FakeEmbeddingProvider uses bytes as a deterministic seed.
    # Real ONNX provider (PR-5+) would load the actual file bytes here.
    photo_filename = payload.photo_filename
    if photo_filename:
        # Extra basename guard (schema already validated, belt-and-suspenders)
        if os.path.basename(photo_filename) != photo_filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="photo_filename_path_traversal",
            )
        live_image_seed = photo_filename.encode("utf-8")
    else:
        live_image_seed = f"verify_user_{current_user.id}".encode("utf-8")

    # ── Run face matching pipeline ────────────────────────────────────────────
    outcome = run_face_match(
        db=db,
        user=current_user,
        live_image_seed=live_image_seed,
    )
    db.commit()

    # face_match_score is NOT in BiometricVerifyResponse (structural enforcement)
    return BiometricVerifyResponse(result=outcome)