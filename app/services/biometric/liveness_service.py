"""
Biometric liveness reference service — PR-3.

Accepts the result of a completed onboarding liveness challenge and
records the reference photo status for later embedding generation (PR-4+).

Design rules:
  - Caller must have active biometric consent (enforced before this service).
  - liveness_metadata is sanitized unconditionally before DB write (layer 3).
  - face_match_score is never read, written, or returned by this service.
  - embedding_ciphertext / embedding_iv are never touched here (PR-4+).
  - Celery embedding task is intentionally a placeholder log only (PR-4+).
  - Not KYC, not production-ready — DPIA / DPO approval pending.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.biometric import UserBiometricConsent
from app.models.user import User
from app.schemas.biometric import BiometricVerificationStatusOut
from app.services.biometric.audit_log import (
    BiometricAuditLogger,
    EVT_LIVENESS_COMPLETED,
    EVT_REFERENCE_AUTO_APPROVED_LIVENESS,
    EVT_REFERENCE_SUBMITTED,
)
from app.services.biometric.liveness_metadata_sanitizer import sanitize_liveness_metadata

logger = logging.getLogger(__name__)

# Status values written to users table
_STATUS_REFERENCE_PENDING          = "reference_pending"
_STATUS_ONBOARDING_LIVENESS_CAPTURE = "onboarding_liveness_capture"


def submit_liveness_result(
    *,
    db: Session,
    user: User,
    liveness_metadata: dict,
    source: str,
    photo_filename: Optional[str],
    ip_address: Optional[str] = None,
) -> BiometricVerificationStatusOut:
    """
    Record a completed onboarding liveness challenge on the backend.

    Steps:
      1. Verify active biometric consent exists.
      2. Guard against duplicate onboarding_liveness submission.
      3. Sanitize liveness_metadata (layer 3 — mandatory even after Pydantic).
      4. Validate photo_filename basename safety.
      5. Update user status columns.
      6. Write three audit log rows.
      7. Log embedding-generation placeholder (Celery task in PR-4).
      8. Return BiometricVerificationStatusOut (no face_match_score).

    Raises:
      403 — no active biometric consent
      409 — duplicate onboarding_liveness submission
      400 — unsafe photo_filename (path traversal)
    """
    # ── 1. Consent check ──────────────────────────────────────────────────────
    active_consent = (
        db.query(UserBiometricConsent)
        .filter(
            UserBiometricConsent.user_id == user.id,
            UserBiometricConsent.is_active.is_(True),
        )
        .first()
    )
    if not active_consent:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="biometric_consent_required",
        )

    # ── 2. Duplicate guard ────────────────────────────────────────────────────
    if user.face_reference_photo_status == _STATUS_ONBOARDING_LIVENESS_CAPTURE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="onboarding_liveness_already_submitted",
        )

    # ── 3. Sanitize liveness_metadata (unconditional — layer 3) ──────────────
    safe_metadata = sanitize_liveness_metadata(liveness_metadata)

    # ── 4. photo_filename basename guard ──────────────────────────────────────
    if photo_filename is not None:
        if os.path.basename(photo_filename) != photo_filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="photo_filename_path_traversal",
            )

    # ── 5. Update user status columns ─────────────────────────────────────────
    user.face_reference_photo_status = _STATUS_ONBOARDING_LIVENESS_CAPTURE
    user.face_match_status           = _STATUS_REFERENCE_PENDING
    db.flush()

    # ── 6. Audit log — three events ───────────────────────────────────────────
    audit = BiometricAuditLogger(db)

    audit.log(
        user_id=user.id,
        event_type=EVT_LIVENESS_COMPLETED,
        event_result="accepted",
        liveness_metadata=safe_metadata,
        actor_ip_address=ip_address,
    )

    audit.log(
        user_id=user.id,
        event_type=EVT_REFERENCE_SUBMITTED,
        event_result="pending",
        photo_filename=photo_filename,
        actor_ip_address=ip_address,
    )

    audit.log(
        user_id=user.id,
        event_type=EVT_REFERENCE_AUTO_APPROVED_LIVENESS,
        event_result="auto_approved_liveness",
        actor_ip_address=ip_address,
    )

    # ── 7. Dispatch embedding generation Celery task ──────────────────────────
    from app.tasks.biometric_tasks import biometric_generate_embedding_task
    biometric_generate_embedding_task.apply_async(
        args=[user.id, photo_filename],
        countdown=5,
    )
    logger.info(
        "biometric_generate_embedding_task dispatched user_id=%s source=%s",
        user.id, source,
    )

    # ── 8. Return status (no face_match_score) ────────────────────────────────
    return BiometricVerificationStatusOut(
        face_match_status=user.face_match_status,
        face_reference_photo_status=user.face_reference_photo_status,
        has_biometric_consent=True,
        manual_review_required=bool(user.manual_review_required),
    )