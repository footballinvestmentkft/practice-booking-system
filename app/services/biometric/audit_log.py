"""
Biometric audit logger.

Single entry point for all biometric event logging. Uses
BiometricVerificationLog (dedicated table, separate from the generic
AuditLog) to provide a tamper-evident, insert-only audit trail.

Event type constants are defined here so every caller imports from
one canonical location — preventing string drift across PRs.

Design rules enforced here:
  1. face_match_score is accepted as an argument for storage but is
     NEVER returned to callers and NEVER written to any response dict.
  2. liveness_metadata is sanitized via liveness_metadata_sanitizer
     before every INSERT — forbidden fields cannot reach the DB.
  3. Logging failures are caught and re-raised as logged warnings;
     a failed audit write must not crash the calling request.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.biometric import BiometricVerificationLog
from app.services.biometric.liveness_metadata_sanitizer import sanitize_liveness_metadata

logger = logging.getLogger(__name__)

# ── Event type constants ──────────────────────────────────────────────────────

# Consent lifecycle
EVT_CONSENT_GRANTED = "consent_granted"
EVT_CONSENT_REVOKED = "consent_revoked"

# Reference photo lifecycle
EVT_REFERENCE_SUBMITTED             = "reference_submitted"
EVT_REFERENCE_APPROVED              = "reference_approved"
EVT_REFERENCE_REJECTED              = "reference_rejected"
EVT_REFERENCE_REPLACED              = "reference_replaced"
EVT_REFERENCE_AUTO_APPROVED_LIVENESS = "reference_auto_approved_liveness"

# Liveness challenge events
EVT_LIVENESS_STARTED    = "liveness_started"
EVT_LIVENESS_STEP_PASSED = "liveness_step_passed"
EVT_LIVENESS_FAILED     = "liveness_failed"
EVT_LIVENESS_COMPLETED  = "liveness_completed"

# Face matching events
EVT_MATCH_SUCCESS          = "match_success"
EVT_MATCH_FAILED           = "match_failed"
EVT_MATCH_REVIEW_REQUIRED  = "match_review_required"

# Admin / system actions
EVT_ADMIN_OVERRIDE   = "admin_override"
EVT_EMBEDDING_DELETED = "embedding_deleted"
EVT_GDPR_DELETE      = "gdpr_delete_requested"

# Upload validation
EVT_UPLOAD_NO_FACE        = "upload_rejected_no_face"
EVT_UPLOAD_MULTIPLE_FACES = "upload_rejected_multiple_faces"

# Complete set — used by test_audit_completeness to ensure coverage
ALL_EVENT_TYPES: frozenset[str] = frozenset({
    EVT_CONSENT_GRANTED,
    EVT_CONSENT_REVOKED,
    EVT_REFERENCE_SUBMITTED,
    EVT_REFERENCE_APPROVED,
    EVT_REFERENCE_REJECTED,
    EVT_REFERENCE_REPLACED,
    EVT_REFERENCE_AUTO_APPROVED_LIVENESS,
    EVT_LIVENESS_STARTED,
    EVT_LIVENESS_STEP_PASSED,
    EVT_LIVENESS_FAILED,
    EVT_LIVENESS_COMPLETED,
    EVT_MATCH_SUCCESS,
    EVT_MATCH_FAILED,
    EVT_MATCH_REVIEW_REQUIRED,
    EVT_ADMIN_OVERRIDE,
    EVT_EMBEDDING_DELETED,
    EVT_GDPR_DELETE,
    EVT_UPLOAD_NO_FACE,
    EVT_UPLOAD_MULTIPLE_FACES,
})

# ── BiometricAuditLogger ──────────────────────────────────────────────────────


class BiometricAuditLogger:
    """
    Writes a row to biometric_verification_logs.

    face_match_score is accepted and stored for internal admin use but
    is deliberately excluded from every return value and response schema.
    """

    def __init__(self, db: Session) -> None:
        self._db = db

    def log(
        self,
        *,
        user_id: int,
        event_type: str,
        event_result: str | None = None,
        face_match_score: float | None = None,   # stored, NEVER returned
        model_version: str | None = None,
        threshold_used: float | None = None,
        liveness_metadata: dict[str, Any] | None = None,
        actor_user_id: int | None = None,
        actor_ip_address: str | None = None,
        photo_filename: str | None = None,
        error_message: str | None = None,
    ) -> BiometricVerificationLog:
        """
        Insert a biometric audit log row.

        liveness_metadata is sanitized before INSERT — forbidden fields
        (device_model, ios_version, yaw, roll, landmarks, frames, etc.)
        are stripped silently and a WARNING is emitted for each one.

        Raises RuntimeError if the INSERT fails, so callers can decide
        whether to abort the parent transaction or continue.
        """
        safe_metadata = sanitize_liveness_metadata(liveness_metadata)

        entry = BiometricVerificationLog(
            user_id=user_id,
            event_type=event_type,
            event_result=event_result,
            face_match_score=face_match_score,  # internal only
            model_version=model_version,
            threshold_used=threshold_used,
            liveness_metadata=safe_metadata,
            actor_user_id=actor_user_id,
            actor_ip_address=actor_ip_address,
            photo_filename=photo_filename,
            error_message=error_message,
            created_at=datetime.now(timezone.utc),
        )
        try:
            self._db.add(entry)
            self._db.flush()
        except Exception as exc:
            logger.warning(
                "biometric_audit_log_write_failed user_id=%s event_type=%s error=%s",
                user_id, event_type, exc,
            )
            raise RuntimeError(f"Audit log write failed: {exc}") from exc

        return entry
