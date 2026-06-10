"""
Biometric consent service — PR-2.

Manages UserBiometricConsent lifecycle:
  grant   → INSERT (or reactivate revoked) consent row
  get     → read current consent state for a user
  revoke  → soft-delete: consent_revoked_at + is_active=False
            + users.face_match_status = "consent_revoked"
            + user_face_embeddings.is_active = False (if exists)
            + schedule_embedding_deletion() placeholder (real Celery task in PR-4)

All writes go through BiometricAuditLogger so every consent event has an
immutable audit trail in biometric_verification_logs.

Design rules:
  - face_match_score is never read or written by this service.
  - embedding_ciphertext / embedding_iv are never touched here (PR-4+).
  - schedule_embedding_deletion() logs intent now; actual deletion in PR-4.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.biometric import UserBiometricConsent, UserFaceEmbedding
from app.models.user import User
from app.services.biometric.audit_log import (
    BiometricAuditLogger,
    EVT_CONSENT_GRANTED,
    EVT_CONSENT_REVOKED,
)

logger = logging.getLogger(__name__)

# Retention: physical embedding deletion is scheduled this many days after revocation.
# Actual Celery task implemented in PR-4; this constant is used for the log record.
EMBEDDING_DELETION_DELAY_DAYS = 30


def grant_consent(
    *,
    db: Session,
    user: User,
    consent_version: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> UserBiometricConsent:
    """
    Grant biometric consent for a user.

    - Returns the new/reactivated consent row.
    - Raises 409 if an active consent already exists (idempotency guard).
    - Reactivates a previously revoked row rather than inserting a duplicate
      when the user re-consents after revocation.
    """
    existing = (
        db.query(UserBiometricConsent)
        .filter_by(user_id=user.id, is_active=True)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Biometric consent is already active. Use DELETE to revoke first.",
        )

    now = datetime.now(timezone.utc)

    # Reactivate most-recently-revoked row if one exists (avoids duplicate rows)
    revoked = (
        db.query(UserBiometricConsent)
        .filter_by(user_id=user.id, is_active=False)
        .order_by(UserBiometricConsent.id.desc())
        .first()
    )
    if revoked:
        revoked.consent_granted_at  = now
        revoked.consent_version     = consent_version
        revoked.consent_ip_address  = ip_address
        revoked.consent_user_agent  = user_agent
        revoked.consent_revoked_at  = None
        revoked.revocation_reason   = None
        revoked.is_active           = True
        consent = revoked
    else:
        consent = UserBiometricConsent(
            user_id            = user.id,
            consent_granted_at = now,
            consent_version    = consent_version,
            consent_ip_address = ip_address,
            consent_user_agent = user_agent,
            is_active          = True,
        )
        db.add(consent)

    db.flush()

    BiometricAuditLogger(db).log(
        user_id      = user.id,
        event_type   = EVT_CONSENT_GRANTED,
        event_result = "accepted",
        actor_ip_address = ip_address,
    )

    return consent


def get_consent_status(*, db: Session, user_id: int) -> Optional[UserBiometricConsent]:
    """Return the most recent consent row (active or revoked), or None."""
    return (
        db.query(UserBiometricConsent)
        .filter_by(user_id=user_id)
        .order_by(UserBiometricConsent.id.desc())
        .first()
    )


def revoke_consent(
    *,
    db: Session,
    user: User,
    reason: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> UserBiometricConsent:
    """
    Revoke biometric consent.

    Effects (all within the same transaction):
      1. consent.is_active = False, consent_revoked_at = now
      2. user.face_match_status = "consent_revoked"
      3. user_face_embeddings.is_active = False (soft-delete, if row exists)
      4. schedule_embedding_deletion() placeholder — logs intent for PR-4
      5. Audit log: EVT_CONSENT_REVOKED

    Raises 404 if no active consent exists.
    """
    consent = (
        db.query(UserBiometricConsent)
        .filter_by(user_id=user.id, is_active=True)
        .first()
    )
    if not consent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active biometric consent found. Nothing to revoke.",
        )

    now = datetime.now(timezone.utc)

    # 1. Deactivate consent
    consent.consent_revoked_at = now
    consent.revocation_reason  = reason
    consent.is_active          = False

    # 2. Mark face_match_status on user
    user.face_match_status = "consent_revoked"

    # 3. Soft-delete embedding if present
    embedding = (
        db.query(UserFaceEmbedding)
        .filter_by(user_id=user.id, is_active=True)
        .first()
    )
    if embedding:
        embedding.is_active = False
        db.flush()

    db.flush()

    # 4. Schedule physical deletion via Celery ETA task (PR-4)
    scheduled_deletion = now + timedelta(days=EMBEDDING_DELETION_DELAY_DAYS)
    from app.tasks.biometric_tasks import biometric_delete_embedding_task
    biometric_delete_embedding_task.apply_async(
        args=[user.id],
        eta=scheduled_deletion,
    )
    logger.info(
        "biometric_embedding_deletion_scheduled user_id=%d delete_after=%s",
        user.id, scheduled_deletion.isoformat(),
    )

    # 5. Audit log
    BiometricAuditLogger(db).log(
        user_id          = user.id,
        event_type       = EVT_CONSENT_REVOKED,
        event_result     = "accepted",
        actor_ip_address = ip_address,
    )

    return consent


