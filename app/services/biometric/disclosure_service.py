"""
Biometric disclosure service — PR-7A.

Manages the user_biometric_disclosures table: acceptance, status query,
and revocation of the biometric tájékoztató modal.

Design rules:
  1. Disclosure is DISTINCT from GDPR consent (UserBiometricConsent).
     disclosure accepted  = user acknowledged the informational modal
     consent granted      = GDPR Art. 9(2)(a) biometric consent
  2. Revoke disclosure → automatically revoke active consent via the
     existing revoke_consent() service (no duplicate Celery logic).
  3. face_match_score, embedding, raw liveness data never appear here.
  4. Age gate: user.age is None OR user.age < 18 → raise 403.
     No bypass for unknown age — conservative protection of minors.
  5. Only one active disclosure per user at any time
     (partial unique index on DB side; idempotency guard in service).
  6. Version check: accepted version must equal
     settings.CURRENT_BIOMETRIC_DISCLOSURE_VERSION; stale → 403.

Not production-ready. DPIA/DPO approval pending.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.biometric import UserBiometricConsent, UserBiometricDisclosure
from app.models.user import User
from app.schemas.biometric import BiometricDisclosureStatusOut
from app.services.biometric.audit_log import (
    BiometricAuditLogger,
    EVT_DISCLOSURE_ACCEPTED,
    EVT_DISCLOSURE_REVOKED,
    EVT_DISCLOSURE_STALE_ATTEMPT,
)

logger = logging.getLogger(__name__)


# ── Age / minor guard ──────────────────────────────────────────────────────────

def _assert_not_minor(user: User) -> None:
    """
    Raise 403 if the user is a minor (age < 18) or has no date_of_birth.

    user.age returns None when date_of_birth is NULL.
    Conservative policy: unknown age is treated as potentially minor — no bypass.
    Parental consent flow is out-of-scope (legal blocker); this gate prevents
    any biometric disclosure acceptance until that flow is implemented.
    """
    age = user.age  # None when date_of_birth is NULL
    if age is None or age < 18:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="parental_consent_required",
        )


# ── Status query ───────────────────────────────────────────────────────────────

def get_disclosure_status(*, db: Session, user: User) -> BiometricDisclosureStatusOut:
    """
    Return the user's current biometric disclosure state.
    Returns has_disclosure=False if no row exists.
    """
    row = _active_disclosure(db, user.id)
    if row is None:
        return BiometricDisclosureStatusOut(has_disclosure=False, is_active=False)
    return BiometricDisclosureStatusOut(
        has_disclosure=True,
        is_active=True,
        accepted_version=row.disclosure_version,
        accepted_at=row.accepted_at,
        revoked_at=None,
    )


# ── Accept disclosure ─────────────────────────────────────────────────────────

def accept_disclosure(
    *,
    db: Session,
    user: User,
    disclosure_version: str,
    ip_address: Optional[str] = None,
) -> BiometricDisclosureStatusOut:
    """
    Record the user's explicit acceptance of the biometric disclosure modal.

    Steps:
      1. Age guard — 403 if minor or unknown age.
      2. Version guard — 422 if version != CURRENT_BIOMETRIC_DISCLOSURE_VERSION.
      3. Duplicate guard — 409 if active disclosure for this version already exists.
      4. INSERT row (is_active=True).
      5. Audit EVT_DISCLOSURE_ACCEPTED.

    Returns BiometricDisclosureStatusOut (no score, no embedding).
    """
    _assert_not_minor(user)

    current = settings.CURRENT_BIOMETRIC_DISCLOSURE_VERSION
    if disclosure_version != current:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"disclosure_version_mismatch: expected {current}",
        )

    existing = _active_disclosure(db, user.id)
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="disclosure_already_accepted",
        )

    now = datetime.now(timezone.utc)
    row = UserBiometricDisclosure(
        user_id=user.id,
        disclosure_version=disclosure_version,
        accepted_at=now,
        acceptance_ip=ip_address,
        is_active=True,
        created_at=now,
    )
    db.add(row)
    db.flush()

    BiometricAuditLogger(db).log(
        user_id=user.id,
        event_type=EVT_DISCLOSURE_ACCEPTED,
        event_result="accepted",
        actor_ip_address=ip_address,
    )

    logger.info(
        "biometric_disclosure_accepted user_id=%s version=%s",
        user.id, disclosure_version,
    )
    return BiometricDisclosureStatusOut(
        has_disclosure=True,
        is_active=True,
        accepted_version=disclosure_version,
        accepted_at=now,
        revoked_at=None,
    )


# ── Revoke disclosure ─────────────────────────────────────────────────────────

def revoke_disclosure(
    *,
    db: Session,
    user: User,
    reason: Optional[str] = None,
    ip_address: Optional[str] = None,
) -> BiometricDisclosureStatusOut:
    """
    Revoke the user's active biometric disclosure.

    Steps:
      1. Guard — 404 if no active disclosure.
      2. Soft-delete: is_active=False, revoked_at=now.
      3. Audit EVT_DISCLOSURE_REVOKED.
      4. If an active biometric consent exists, call the existing
         revoke_consent() service — reuses the Celery embedding delete flow,
         does NOT duplicate any deletion logic.
         Audit EVT_CONSENT_REVOKED is written inside revoke_consent().

    Returns BiometricDisclosureStatusOut with is_active=False.
    """
    row = _active_disclosure(db, user.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="biometric_disclosure_not_found",
        )

    now = datetime.now(timezone.utc)
    row.is_active        = False
    row.revoked_at       = now
    row.revocation_reason = reason
    db.flush()

    BiometricAuditLogger(db).log(
        user_id=user.id,
        event_type=EVT_DISCLOSURE_REVOKED,
        event_result="revoked",
        actor_ip_address=ip_address,
    )

    # Cascade revoke to biometric consent — reuse existing service, no new logic
    active_consent = (
        db.query(UserBiometricConsent)
        .filter_by(user_id=user.id, is_active=True)
        .first()
    )
    if active_consent:
        from app.services.biometric.consent_service import revoke_consent
        revoke_consent(db=db, user=user, reason="disclosure_revoked")
        logger.info(
            "biometric_consent_revoked_via_disclosure user_id=%s", user.id
        )

    logger.info("biometric_disclosure_revoked user_id=%s", user.id)
    return BiometricDisclosureStatusOut(
        has_disclosure=True,
        is_active=False,
        accepted_version=row.disclosure_version,
        accepted_at=row.accepted_at,
        revoked_at=now,
    )


# ── Liveness / verify guard ───────────────────────────────────────────────────

def assert_disclosure_current(*, db: Session, user_id: int) -> None:
    """
    Raise 403 if the user has no active disclosure or the accepted version
    is stale (not equal to CURRENT_BIOMETRIC_DISCLOSURE_VERSION).

    Called by liveness_service and biometric_verify before proceeding.
    """
    row = _active_disclosure(db, user_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="biometric_disclosure_required",
        )
    current = settings.CURRENT_BIOMETRIC_DISCLOSURE_VERSION
    if row.disclosure_version != current:
        try:
            BiometricAuditLogger(db).log(
                user_id=user_id,
                event_type=EVT_DISCLOSURE_STALE_ATTEMPT,
                event_result="forbidden",
                error_message=f"accepted={row.disclosure_version} current={current}",
            )
        except Exception:
            pass  # best-effort; may not persist if transaction rolls back
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="biometric_disclosure_update_required",
        )


# ── Internal helpers ──────────────────────────────────────────────────────────

def _active_disclosure(
    db: Session, user_id: int
) -> Optional[UserBiometricDisclosure]:
    return (
        db.query(UserBiometricDisclosure)
        .filter_by(user_id=user_id, is_active=True)
        .first()
    )