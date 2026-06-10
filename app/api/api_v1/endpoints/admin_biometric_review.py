"""
Admin Biometric Review endpoints — PR-7B.

GET  /admin/biometric/review-queue          — list users needing manual review
GET  /admin/biometric/{user_id}/history     — audit history for one user (no score)
POST /admin/biometric/{user_id}/override    — approve or reject a review case

All endpoints require:
  - UserRole.ADMIN (existing RBAC — get_current_admin_user dependency)
  - BIOMETRIC_FACE_MATCHING_ENABLED=True (503 otherwise)

face_match_score is NEVER returned in any response — internal DB only.
No frontend HTML/template in this PR (Admin Review Backend API, not UI).

Not production-ready. DPIA/DPO approval pending.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin_user
from app.models.user import User
from app.schemas.biometric import (
    AdminBiometricHistoryOut,
    AdminBiometricOverrideOut,
    AdminBiometricOverrideRequest,
    AdminBiometricReviewQueueOut,
)
from app.services.biometric.admin_review_service import (
    apply_admin_override,
    get_review_queue,
    get_user_biometric_history,
)
from app.services.biometric.feature_flag import require_biometric_enabled

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
    "/review-queue",
    status_code=200,
    response_model=AdminBiometricReviewQueueOut,
    dependencies=[Depends(require_biometric_enabled)],
    summary="List users requiring biometric manual review (admin only)",
)
def admin_get_review_queue(
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_user),
) -> Any:
    """
    Return all users with face_match_status='manual_review_required'.

    - Requires admin role (403 for non-admin).
    - Requires BIOMETRIC_FACE_MATCHING_ENABLED=true (503 otherwise).
    - No face_match_score in response.
    """
    return get_review_queue(db=db)


@router.get(
    "/{user_id}/history",
    status_code=200,
    response_model=AdminBiometricHistoryOut,
    dependencies=[Depends(require_biometric_enabled)],
    summary="Biometric audit history for a user (admin only, no score)",
)
def admin_get_user_history(
    user_id: int,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_user),
) -> Any:
    """
    Return biometric audit log events for a specific user.

    - Requires admin role (403 for non-admin).
    - Requires BIOMETRIC_FACE_MATCHING_ENABLED=true (503).
    - face_match_score is NOT returned — internal DB only.
    - Returns: event_type, event_result, threshold_used, model_version, created_at.
    """
    return get_user_biometric_history(db=db, user_id=user_id)


@router.post(
    "/{user_id}/override",
    status_code=200,
    response_model=AdminBiometricOverrideOut,
    dependencies=[Depends(require_biometric_enabled)],
    summary="Admin override for a biometric manual review case",
)
def admin_override_biometric(
    user_id: int,
    payload: AdminBiometricOverrideRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin_user),
) -> Any:
    """
    Approve or reject a manual_review_required biometric case.

    - Requires admin role (403 for non-admin).
    - Requires BIOMETRIC_FACE_MATCHING_ENABLED=true (503).
    - 403 self_override_forbidden if actor == target.
    - 404 if target user not found.
    - 409 override_not_applicable if target is not manual_review_required.
    - 403 if target lacks active consent or current disclosure.
    - Audit: EVT_ADMIN_OVERRIDE with actor_user_id (NOT NULL), no face_match_score.
    - approved → face_match_status="verified", manual_review_required=False.
    - rejected → face_match_status="rejected", manual_review_required=False.
    - No face_match_score in response.
    """
    result = apply_admin_override(
        db=db,
        target_user_id=user_id,
        actor_user_id=current_admin.id,
        decision=payload.decision,
        reason=payload.reason,
        actor_ip_address=_extract_ip(request),
    )
    db.commit()
    return result