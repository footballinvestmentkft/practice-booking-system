"""
Juggling consent endpoints.

POST /api/v1/users/me/juggling-consent  — create or update consent
GET  /api/v1/users/me/juggling-consent  — get current consent state

All endpoints gated by require_juggling_enabled() → 503 when flag off.

Consent scope (POC):
  service_consent      — mandatory gate before any video upload.
  training_consent     — optional; togglable in this request.
  admin_review_consent — optional; togglable in this request.

Full revoke / GDPR data delete = V1.0 scope (not implemented here).
Note: uploaded videos may contain audio until P2 audio stripping is active.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.juggling import JugglingConsentGrantRequest, JugglingConsentOut
from app.services.juggling.consent_service import get_consent, upsert_consent
from app.services.juggling.feature_flag import require_juggling_enabled

router = APIRouter()


@router.post(
    "/me/juggling-consent",
    response_model=JugglingConsentOut,
    status_code=200,
    dependencies=[Depends(require_juggling_enabled)],
    summary="Create or update juggling consent",
    tags=["juggling"],
)
def post_juggling_consent(
    body: JugglingConsentGrantRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JugglingConsentOut:
    record = upsert_consent(
        user_id=current_user.id,
        service_consent=body.service_consent,
        training_consent=body.training_consent,
        admin_review_consent=body.admin_review_consent,
        db=db,
    )
    return JugglingConsentOut.model_validate(record)


@router.get(
    "/me/juggling-consent",
    response_model=JugglingConsentOut,
    dependencies=[Depends(require_juggling_enabled)],
    summary="Get current juggling consent state",
    tags=["juggling"],
)
def get_juggling_consent(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JugglingConsentOut:
    record = get_consent(current_user.id, db)
    if record is None:
        raise HTTPException(status_code=404, detail="No juggling consent record found.")
    return JugglingConsentOut.model_validate(record)