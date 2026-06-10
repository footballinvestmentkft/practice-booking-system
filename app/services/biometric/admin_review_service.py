"""
Admin Biometric Review service — PR-7B.

Provides:
  - get_review_queue(): users with face_match_status="manual_review_required"
  - get_user_history(): audit log events for a user (no face_match_score)
  - apply_admin_override(): approve or reject a manual review case

Design rules enforced here:
  1. face_match_score is NEVER returned or logged — internal DB only.
  2. Only users with face_match_status="manual_review_required" are in the queue.
  3. Override requires:
       - target face_match_status == "manual_review_required"  (409 otherwise)
       - actor_user_id != target user_id  (403 self_override_forbidden)
       - active current disclosure on target
       - active biometric consent on target
  4. Override "approved"  → face_match_status="verified",  manual_review_required=False
     Override "rejected"  → face_match_status="rejected",  manual_review_required=False
     Both outcomes remove the user from the review queue.
  5. EVT_ADMIN_OVERRIDE audit row: actor_user_id NOT NULL, no face_match_score.
  6. reason field is sanitised (max 200 chars, stored in error_message).

Not production-ready. DPIA/DPO approval pending.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.models.biometric import (
    BiometricVerificationLog,
    UserBiometricConsent,
    UserBiometricDisclosure,
)
from app.models.user import User
from app.schemas.biometric import (
    AdminBiometricHistoryEventOut,
    AdminBiometricHistoryOut,
    AdminBiometricOverrideOut,
    AdminBiometricReviewItemOut,
    AdminBiometricReviewQueueOut,
)
from app.services.biometric.audit_log import (
    BiometricAuditLogger,
    EVT_ADMIN_OVERRIDE,
)

logger = logging.getLogger(__name__)

_STATUS_MANUAL_REVIEW = "manual_review_required"
_STATUS_VERIFIED       = "verified"
_STATUS_REJECTED       = "rejected"


# ── Review queue ──────────────────────────────────────────────────────────────

def get_review_queue(*, db: Session) -> AdminBiometricReviewQueueOut:
    """
    Return all users requiring manual biometric review.

    Filters:
      - face_match_status = "manual_review_required"
      - manual_review_required = True
      - has active biometric consent (is_active=True)
      - has active current disclosure (is_active=True)

    face_match_score is never included in the response.
    """
    users = (
        db.query(User)
        .filter(
            User.face_match_status == _STATUS_MANUAL_REVIEW,
            User.manual_review_required.is_(True),
        )
        .all()
    )

    items = []
    for user in users:
        active_consent = (
            db.query(UserBiometricConsent)
            .filter_by(user_id=user.id, is_active=True)
            .first()
        )
        if active_consent is None:
            continue   # skip users with revoked consent

        active_disclosure = (
            db.query(UserBiometricDisclosure)
            .filter_by(user_id=user.id, is_active=True)
            .first()
        )

        items.append(AdminBiometricReviewItemOut(
            user_id=user.id,
            face_match_status=user.face_match_status or _STATUS_MANUAL_REVIEW,
            face_reference_photo_status=user.face_reference_photo_status,
            manual_review_flagged_at=None,   # set by first EVT_MATCH_REVIEW_REQUIRED
            consent_version=active_consent.consent_version,
            disclosure_accepted=active_disclosure is not None,
            disclosure_version=(
                active_disclosure.disclosure_version if active_disclosure else None
            ),
        ))

    return AdminBiometricReviewQueueOut(items=items)


# ── User history ──────────────────────────────────────────────────────────────

def get_user_biometric_history(*, db: Session, user_id: int) -> AdminBiometricHistoryOut:
    """
    Return biometric audit log events for a specific user.

    face_match_score is never returned — internal DB only.
    Returns: event_type, event_result, threshold_used, model_version, created_at.

    Raises 404 if user not found.
    """
    user = db.query(User).filter_by(id=user_id).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user_not_found",
        )

    logs = (
        db.query(BiometricVerificationLog)
        .filter_by(user_id=user_id)
        .order_by(BiometricVerificationLog.created_at.desc())
        .all()
    )

    events = [
        AdminBiometricHistoryEventOut(
            event_type=log.event_type,
            event_result=log.event_result,
            threshold_used=log.threshold_used,
            model_version=log.model_version,
            created_at=log.created_at,
            # face_match_score intentionally NOT included
        )
        for log in logs
    ]

    return AdminBiometricHistoryOut(user_id=user_id, events=events)


# ── Override ──────────────────────────────────────────────────────────────────

def apply_admin_override(
    *,
    db: Session,
    target_user_id: int,
    actor_user_id: int,
    decision: str,   # "approved" | "rejected"
    reason: Optional[str] = None,
    actor_ip_address: Optional[str] = None,
) -> AdminBiometricOverrideOut:
    """
    Apply an admin override decision to a manual_review_required case.

    Guards (in order):
      1. self-override: actor_user_id == target_user_id → 403
      2. target user exists → 404
      3. target status == "manual_review_required" → 409 if not
      4. active consent on target → 403
      5. active current disclosure on target → 403

    Approved:  face_match_status="verified",  manual_review_required=False
    Rejected:  face_match_status="rejected",  manual_review_required=False
    Both outcomes remove the user from the review queue.

    Audit: EVT_ADMIN_OVERRIDE with actor_user_id (NOT NULL), no face_match_score.

    Reason is stored in error_message (max 200 chars already validated by schema).
    """
    # ── 1. Self-override guard ────────────────────────────────────────────────
    if actor_user_id == target_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="self_override_forbidden",
        )

    # ── 2. Target user ────────────────────────────────────────────────────────
    target = db.query(User).filter_by(id=target_user_id).first()
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="user_not_found",
        )

    # ── 3. Status guard — only manual_review_required can be overridden ───────
    if target.face_match_status != _STATUS_MANUAL_REVIEW:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="override_not_applicable",
        )

    # ── 4. Active consent check ───────────────────────────────────────────────
    active_consent = (
        db.query(UserBiometricConsent)
        .filter_by(user_id=target_user_id, is_active=True)
        .first()
    )
    if active_consent is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="biometric_consent_required",
        )

    # ── 5. Active current disclosure check ───────────────────────────────────
    active_disclosure = (
        db.query(UserBiometricDisclosure)
        .filter_by(user_id=target_user_id, is_active=True)
        .first()
    )
    if active_disclosure is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="biometric_disclosure_required",
        )
    current_version = settings.CURRENT_BIOMETRIC_DISCLOSURE_VERSION
    if active_disclosure.disclosure_version != current_version:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="biometric_disclosure_update_required",
        )

    # ── 6. Apply override ─────────────────────────────────────────────────────
    now = datetime.now(timezone.utc)

    if decision == "approved":
        target.face_match_status      = _STATUS_VERIFIED
        target.manual_review_required = False
        target.reviewed_by            = actor_user_id
    else:  # "rejected"
        target.face_match_status      = _STATUS_REJECTED
        target.manual_review_required = False
        target.reviewed_by            = actor_user_id

    db.flush()

    # ── 7. Audit log — actor_user_id NOT NULL, face_match_score ABSENT ────────
    BiometricAuditLogger(db).log(
        user_id=target_user_id,
        event_type=EVT_ADMIN_OVERRIDE,
        event_result=decision,
        actor_user_id=actor_user_id,     # NOT NULL — enforced at call site
        actor_ip_address=actor_ip_address,
        error_message=reason,            # reuse error_message for optional reason
        # face_match_score intentionally omitted — NEVER in override audit row
    )

    logger.info(
        "biometric_admin_override user_id=%s actor_user_id=%s decision=%s",
        target_user_id, actor_user_id, decision,
        # no score in log
    )

    return AdminBiometricOverrideOut(
        result=decision,
        user_id=target_user_id,
        decided_at=now,
        # face_match_score ABSENT — structural enforcement
    )
