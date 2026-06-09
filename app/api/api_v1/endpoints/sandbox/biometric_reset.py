"""
Biometric QA reset endpoint — dev/test only.

POST /api/v1/sandbox/biometric-reset

Hard-deletes all biometric state for a given user_id so that a fresh
end-to-end liveness → verify test can be run from a clean slate.

Safety gates (ALL must pass before any DB write):
  1. ENVIRONMENT must not be "production" — hard abort.
  2. BIOMETRIC_DISCLOSURE_ENABLED or BIOMETRIC_FACE_MATCHING_ENABLED must
     be True — confirms this is an active dev/test instance.
  3. Requires admin auth (get_current_admin_user).
  4. Target user_id must exist.

What is reset (physical DELETE or NULL-out, never soft-delete):
  - UserFaceEmbedding row (physical DELETE)
  - UserBiometricConsent rows (physical DELETE — dev/test only, GDPR retention
    rules apply only in production)
  - UserBiometricDisclosure rows (physical DELETE — same caveat)
  - User.face_match_status → None
  - User.face_reference_photo_status → None
  - User.manual_review_required → False
  - Redis rate-limit keys for that user_id (all biometric endpoint groups)

What is NOT reset:
  - BiometricVerificationLog rows — audit trail is immutable even in dev/test.
  - Any non-biometric user state.

Production note:
  This endpoint MUST NOT be registered in production. The ENVIRONMENT guard
  below is a defence-in-depth check; route registration should also be
  conditionally excluded at startup (see api.py note).

Not KYC. Dev/test QA helper only.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user
from app.config import settings
from app.database import get_db
from app.models.biometric import (
    UserBiometricConsent,
    UserBiometricDisclosure,
    UserFaceEmbedding,
)
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()

_BIOMETRIC_RL_GROUPS = (
    "disclosure_post",
    "disclosure_delete",
    "disclosure_get",
    "liveness_submit",
    "verify",
)


class BiometricResetRequest(BaseModel):
    user_id: int = Field(..., description="Target user ID to reset biometric state for")


class BiometricResetResponse(BaseModel):
    user_id: int
    deleted_embedding:   bool
    deleted_consents:    int
    deleted_disclosures: int
    user_columns_reset:  bool
    redis_keys_cleared:  int
    audit_logs_retained: int


@router.post(
    "/sandbox/biometric-reset",
    response_model=BiometricResetResponse,
    status_code=200,
    summary="[DEV/TEST ONLY] Hard-reset all biometric state for a user",
    tags=["sandbox", "biometric"],
)
def biometric_reset(
    payload: BiometricResetRequest,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_admin_user),
) -> BiometricResetResponse:
    """
    Hard-reset all biometric state for a given user so that a fresh
    liveness → verify QA cycle can begin from a clean slate.

    Blocked unconditionally when ENVIRONMENT=production.
    Requires admin auth.
    BiometricVerificationLog rows are NOT deleted (immutable audit trail).
    """
    # ── 1. Production guard ───────────────────────────────────────────────────
    if settings.ENVIRONMENT == "production":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="biometric_reset_not_allowed_in_production",
        )

    # ── 2. Feature-flag plausibility guard ────────────────────────────────────
    # At least one biometric flag must be True — confirms this is a live dev/test
    # instance and not an accidental call against a misconfigured server.
    if not (settings.BIOMETRIC_DISCLOSURE_ENABLED or settings.BIOMETRIC_FACE_MATCHING_ENABLED):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="biometric_feature_disabled",
        )

    # ── 3. Target user must exist ─────────────────────────────────────────────
    user = db.query(User).filter_by(id=payload.user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user_not_found",
        )

    # ── 4. Delete UserFaceEmbedding ───────────────────────────────────────────
    emb_row = db.query(UserFaceEmbedding).filter_by(user_id=payload.user_id).first()
    deleted_embedding = emb_row is not None
    if emb_row is not None:
        db.delete(emb_row)

    # ── 5. Physical DELETE UserBiometricConsent rows ──────────────────────────
    consent_rows = (
        db.query(UserBiometricConsent)
        .filter(UserBiometricConsent.user_id == payload.user_id)
        .all()
    )
    deleted_consents = len(consent_rows)
    for row in consent_rows:
        db.delete(row)

    # ── 6. Physical DELETE UserBiometricDisclosure rows ───────────────────────
    disclosure_rows = (
        db.query(UserBiometricDisclosure)
        .filter(UserBiometricDisclosure.user_id == payload.user_id)
        .all()
    )
    deleted_disclosures = len(disclosure_rows)
    for row in disclosure_rows:
        db.delete(row)

    # ── 7. Reset User biometric status columns ────────────────────────────────
    user.face_match_status           = None
    user.face_reference_photo_status = None
    user.manual_review_required      = False
    db.flush()

    # ── 8. Clear Redis rate-limit keys for this user ──────────────────────────
    redis_cleared = _clear_redis_rate_limit_keys(payload.user_id)

    db.commit()

    # ── 9. Count retained audit log rows (informational — NOT deleted) ────────
    from app.models.biometric import BiometricVerificationLog
    retained_logs = (
        db.query(BiometricVerificationLog)
        .filter(BiometricVerificationLog.user_id == payload.user_id)
        .count()
    )

    logger.warning(
        "biometric_reset: admin=%s reset user_id=%s "
        "embedding=%s consents=%d disclosures=%d redis_keys=%d audit_logs_retained=%d",
        _admin.id, payload.user_id,
        deleted_embedding, deleted_consents, deleted_disclosures,
        redis_cleared, retained_logs,
    )

    return BiometricResetResponse(
        user_id=payload.user_id,
        deleted_embedding=deleted_embedding,
        deleted_consents=deleted_consents,
        deleted_disclosures=deleted_disclosures,
        user_columns_reset=True,
        redis_keys_cleared=redis_cleared,
        audit_logs_retained=retained_logs,
    )


def _clear_redis_rate_limit_keys(user_id: int) -> int:
    """
    Delete Redis rate-limit keys for the given user_id across all biometric
    endpoint groups. Returns the number of keys deleted.

    Key pattern: biometric_rl:user:{endpoint_group}:{user_id}

    Silently no-ops if Redis is unavailable (dev/test may run without Redis).
    """
    try:
        import redis as _redis
        client = _redis.Redis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=True,
        )
        keys = [
            f"biometric_rl:user:{group}:{user_id}"
            for group in _BIOMETRIC_RL_GROUPS
        ]
        deleted = client.delete(*keys)
        return int(deleted)
    except Exception as exc:
        logger.warning("biometric_reset: Redis unavailable, rate-limit keys not cleared: %s", exc)
        return 0
