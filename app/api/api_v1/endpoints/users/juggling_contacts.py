"""
Juggling contact annotation endpoints — AN-1.

GET  /api/v1/users/me/juggling/videos/{video_id}/contacts
POST /api/v1/users/me/juggling/videos/{video_id}/contacts
POST /api/v1/users/me/juggling/videos/{video_id}/contacts/batch
PATCH /api/v1/users/me/juggling/videos/{video_id}/contacts/{event_id}
DELETE /api/v1/users/me/juggling/videos/{video_id}/contacts/{event_id}
POST /api/v1/users/me/juggling/videos/{video_id}/contacts/finish

All endpoints:
  - require_juggling_enabled (503 when flag off)
  - get_current_user (401 when unauthenticated)
  - video ownership checked in service layer (404 for not-found or wrong owner)
  - post-finish CRUD blocked in service layer (409)

Service invariants enforced by contact_service (never by endpoints):
  annotation_source, annotation_review_status, taxonomy_review_status,
  excluded_from_training, side (stable types) — all server-set, client-blind.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models.user import User
from app.schemas.juggling import (
    ContactEventBatchRequest,
    ContactEventBatchResult,
    ContactEventCreateRequest,
    ContactEventListOut,
    ContactEventOut,
    ContactEventPatchRequest,
    FinishAnnotationOut,
    FinishAnnotationRequest,
)
from app.services.juggling import contact_service
from app.services.juggling.feature_flag import require_juggling_enabled

router = APIRouter()

_TAG = "juggling"
_PREFIX = "/me/juggling/videos/{video_id}/contacts"


# ── GET /contacts ─────────────────────────────────────────────────────────────

@router.get(
    _PREFIX,
    response_model=ContactEventListOut,
    dependencies=[Depends(require_juggling_enabled)],
    summary="List contact events for a video",
    tags=[_TAG],
)
def list_contacts(
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContactEventListOut:
    """
    Returns all active (non-deleted) contact events for the video, ordered by timestamp_ms.
    Used by iOS to resume an in-progress annotation session.
    """
    video, events = contact_service.list_contacts(video_id, current_user, db)
    return ContactEventListOut(
        video_id=str(video.id),
        annotation_status=video.annotation_status,
        events=[ContactEventOut.model_validate(e) for e in events],
    )


# ── POST /contacts ────────────────────────────────────────────────────────────

@router.post(
    _PREFIX,
    dependencies=[Depends(require_juggling_enabled)],
    summary="Create a single contact event",
    tags=[_TAG],
)
def create_contact(
    video_id: str,
    body: ContactEventCreateRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContactEventOut:
    """
    Idempotent single-event creation.

    - 201: new event created
    - 200: exact duplicate (same device_event_id + same payload) — returns existing
    - 409: same device_event_id, different payload (idempotency_conflict)
    """
    result = contact_service.create_contact(video_id, body, current_user, db)
    if result.http_status == 409:
        from fastapi import HTTPException
        raise HTTPException(status_code=409, detail=result.conflict_detail)
    response.status_code = result.http_status
    return ContactEventOut.model_validate(result.event)


# ── POST /contacts/batch ──────────────────────────────────────────────────────

@router.post(
    f"{_PREFIX}/batch",
    response_model=ContactEventBatchResult,
    status_code=207,
    dependencies=[Depends(require_juggling_enabled)],
    summary="Batch-submit multiple contact events",
    tags=[_TAG],
)
def create_contacts_batch(
    video_id: str,
    body: ContactEventBatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContactEventBatchResult:
    """
    Submit up to 200 contact events in a single request.
    Per-item status: created (201) / duplicate (200) / conflict (409).
    Always returns 207 — inspect per-item status field for outcome.
    """
    return contact_service.create_contacts_batch(video_id, body, current_user, db)


# ── PATCH /contacts/{event_id} ────────────────────────────────────────────────

@router.patch(
    f"{_PREFIX}/{{event_id}}",
    response_model=ContactEventOut,
    dependencies=[Depends(require_juggling_enabled)],
    summary="Edit a contact event",
    tags=[_TAG],
)
def patch_contact(
    video_id:  str,
    event_id:  str,
    body: ContactEventPatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ContactEventOut:
    """
    Partial update. Supply only the fields to change.
    version is required for optimistic locking (409 on mismatch).
    Blocked after annotation_status = human_review_pending (409).
    """
    event = contact_service.patch_contact(video_id, event_id, body, current_user, db)
    return ContactEventOut.model_validate(event)


# ── DELETE /contacts/{event_id} ───────────────────────────────────────────────

@router.delete(
    f"{_PREFIX}/{{event_id}}",
    status_code=204,
    dependencies=[Depends(require_juggling_enabled)],
    summary="Delete a contact event (soft delete)",
    tags=[_TAG],
)
def delete_contact(
    video_id:  str,
    event_id:  str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """
    Soft-deletes the event (sets deleted_at). Row is preserved for audit.
    Blocked after annotation_status = human_review_pending (409).
    Returns 204 on success.
    """
    contact_service.delete_contact(video_id, event_id, current_user, db)


# ── POST /contacts/finish ─────────────────────────────────────────────────────

@router.post(
    f"{_PREFIX}/finish",
    response_model=FinishAnnotationOut,
    dependencies=[Depends(require_juggling_enabled)],
    summary="Finish annotation — transition to human_review_pending",
    tags=[_TAG],
)
def finish_annotation(
    video_id: str,
    body: FinishAnnotationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FinishAnnotationOut:
    """
    Closes the annotation session. State transition: in_progress → human_review_pending.

    - 0 events + confirm_zero_contacts=false → 422
    - 0 events + confirm_zero_contacts=true  → 200 (audit log written)
    - Already human_review_pending → 409
    """
    video = contact_service.finish_annotation(video_id, body, current_user, db)
    active_count = int(video.total_juggling_count or 0)
    return FinishAnnotationOut(
        video_id=str(video.id),
        annotation_status=video.annotation_status,
        total_juggling_count=active_count,
        contact_event_count=active_count,
        annotation_finished_at=video.annotation_finished_at,
    )
