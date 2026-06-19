"""
Juggling consent service — DB helpers for JugglingConsent.

Consent scope:
  service_consent      — mandatory gate for upload-init; POC: grant only.
  training_consent     — optional; togglable.
  admin_review_consent — optional; togglable.

Full revoke / GDPR data delete = V1.0 scope (not implemented in POC).

training_consent revoke produces a JUGGLING_TRAINING_CONSENT_REVOKED audit log
entry in the same commit (AN-3B2F PR-1A).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.juggling import JugglingConsent


def get_consent(user_id: int, db: Session) -> Optional[JugglingConsent]:
    return db.query(JugglingConsent).filter(JugglingConsent.user_id == user_id).first()


def upsert_consent(
    user_id: int,
    service_consent: bool,
    training_consent: bool,
    admin_review_consent: bool,
    db: Session,
) -> JugglingConsent:
    """
    Create or update the juggling consent record for user_id.
    Returns the persisted JugglingConsent row.

    If training_consent transitions True → False, appends a
    JUGGLING_TRAINING_CONSENT_REVOKED AuditLog row in the same commit.
    """
    now = datetime.now(timezone.utc)
    record = get_consent(user_id, db)

    was_training_consented = record is not None and record.training_consent
    is_revoking_training = was_training_consented and not training_consent

    if record is None:
        record = JugglingConsent(
            user_id=user_id,
            service_consent=service_consent,
            training_consent=training_consent,
            admin_review_consent=admin_review_consent,
            consented_at=now,
        )
        db.add(record)
    else:
        record.service_consent      = service_consent
        record.training_consent     = training_consent
        record.admin_review_consent = admin_review_consent
        record.consented_at         = now
        record.updated_at           = now

    if is_revoking_training:
        from app.models.audit_log import AuditLog, AuditAction
        db.add(AuditLog(
            user_id=user_id,
            action=AuditAction.JUGGLING_TRAINING_CONSENT_REVOKED,
            resource_type="juggling_consent",
            details={
                "training_consent_set_to": False,
                "revoked_at": now.isoformat(),
            },
        ))

    db.commit()
    db.refresh(record)
    return record


def has_service_consent(user_id: int, db: Session) -> bool:
    """Return True if the user has granted service_consent."""
    record = get_consent(user_id, db)
    return record is not None and record.service_consent
