"""
Juggling contact event service — AN-1.

Business logic for all contact annotation CRUD operations.

Service invariants (enforced here, never in endpoints):
  - annotation_source = "manual_user"  (always, not client-settable)
  - annotation_review_status = "pending"  (always on create)
  - taxonomy_review_status:
      stable contact type → "not_applicable"
      custom_other        → "pending_taxonomy_review"
  - excluded_from_training = True  (Policy B — always on create)
  - side for stable types is derived from taxonomy, not from client
  - custom_other requires explicit side + custom_label + custom_description

Idempotency:
  same (video_id, device_event_id): same payload → 200 + existing row
                                    different payload → 409 idempotency_conflict

Optimistic locking:
  PATCH supplies version; mismatch → 409 version_conflict

Post-finish guard:
  annotation_status == "human_review_pending" → 409 on all CRUD

State machine:
  metadata_ready (or NULL) + first contact → annotation_status = "in_progress"
  POST /contacts/finish → annotation_status = "human_review_pending"
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.juggling import (
    JugglingAnnotationReviewStatus,
    JugglingConsent,
    JugglingContactEvent,
    JugglingTaxonomyReviewStatus,
    JugglingVideo,
)
from app.models.user import User
from app.schemas.juggling import (
    ContactEventBatchItemResult,
    ContactEventBatchRequest,
    ContactEventBatchResult,
    ContactEventCreateRequest,
    ContactEventOut,
    ContactEventPatchRequest,
    FinishAnnotationRequest,
)
from app.services.juggling.taxonomy_service import (
    derive_side,
    is_stable,
    validate_contact_type,
)

# ── Internal helpers ──────────────────────────────────────────────────────────

_POST_FINISH_STATUSES = frozenset({"human_review_pending"})
_IN_PROGRESS_STATUSES = frozenset({"in_progress"})
_ANNOTATABLE_STATUSES = frozenset({None, "metadata_ready", "in_progress"})


def _get_video_owned(video_id: str, user_id: int, db: Session) -> JugglingVideo:
    """Return video or raise 404. Raises 403 if video belongs to another user."""
    try:
        vid_uuid = uuid.UUID(video_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Video not found")
    video = db.query(JugglingVideo).filter(JugglingVideo.id == vid_uuid).first()
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.user_id != user_id:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


def _guard_post_finish(video: JugglingVideo) -> None:
    if video.annotation_status in _POST_FINISH_STATUSES:
        raise HTTPException(
            status_code=409,
            detail="annotation_closed — annotation is locked in human_review_pending status. "
                   "No further CRUD is allowed until an admin re-opens it.",
        )


def _build_consent_snapshot(user_id: int, db: Session) -> dict:
    consent = db.query(JugglingConsent).filter(JugglingConsent.user_id == user_id).first()
    if consent is None:
        return {"service_consent": False, "training_consent": False, "admin_review_consent": False}
    return {
        "service_consent":      consent.service_consent,
        "training_consent":     consent.training_consent,
        "admin_review_consent": consent.admin_review_consent,
    }


def _validate_and_derive(req: ContactEventCreateRequest) -> tuple[str, Optional[str], str, str]:
    """
    Validate contact_type and derive server-side fields.

    Returns (contact_type, side, annotation_review_status, taxonomy_review_status).
    Raises HTTPException 422 on invalid input.
    """
    try:
        validate_contact_type(req.contact_type)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if is_stable(req.contact_type):
        derived_side = derive_side(req.contact_type)
        # If client sends side for stable type, must match derived
        if req.side is not None and req.side != derived_side:
            raise HTTPException(
                status_code=422,
                detail=f"side mismatch for stable contact_type {req.contact_type!r}: "
                       f"server derives {derived_side!r}, client sent {req.side!r}.",
            )
        taxonomy_status = JugglingTaxonomyReviewStatus.not_applicable.value
        return req.contact_type, derived_side, JugglingAnnotationReviewStatus.pending.value, taxonomy_status
    else:
        # custom_other
        if req.side is None:
            raise HTTPException(status_code=422, detail="side is required for custom_other contact type")
        if not req.custom_label:
            raise HTTPException(status_code=422, detail="custom_label is required for custom_other contact type")
        if not req.custom_description:
            raise HTTPException(
                status_code=422, detail="custom_description is required for custom_other contact type"
            )
        taxonomy_status = JugglingTaxonomyReviewStatus.pending_taxonomy_review.value
        return req.contact_type, req.side, JugglingAnnotationReviewStatus.pending.value, taxonomy_status


def _payloads_match(existing: JugglingContactEvent, req: ContactEventCreateRequest) -> bool:
    """True if the incoming request matches the stored event (idempotent duplicate)."""
    return (
        existing.timestamp_ms      == req.timestamp_ms
        and existing.contact_type  == req.contact_type
        and existing.annotation_confidence == req.annotation_confidence
        and existing.custom_label  == req.custom_label
        and existing.custom_description == req.custom_description
    )


def _ensure_in_progress(video: JugglingVideo, db: Session) -> None:
    """Transition annotation_status to in_progress on first event creation."""
    if video.annotation_status in (None, "metadata_ready"):
        video.annotation_status = "in_progress"
        db.add(video)


# ── Public API ────────────────────────────────────────────────────────────────

def list_contacts(video_id: str, current_user: User, db: Session) -> tuple[JugglingVideo, list[JugglingContactEvent]]:
    video = _get_video_owned(video_id, current_user.id, db)
    events = (
        db.query(JugglingContactEvent)
        .filter(
            JugglingContactEvent.video_id == video.id,
            JugglingContactEvent.deleted_at.is_(None),
        )
        .order_by(JugglingContactEvent.timestamp_ms)
        .all()
    )
    return video, events


@dataclass
class CreateResult:
    event: JugglingContactEvent
    http_status: Literal[200, 201, 409]
    conflict_detail: Optional[str] = None


def create_contact(
    video_id: str,
    req: ContactEventCreateRequest,
    current_user: User,
    db: Session,
) -> CreateResult:
    video = _get_video_owned(video_id, current_user.id, db)
    _guard_post_finish(video)

    # Check idempotency
    existing = (
        db.query(JugglingContactEvent)
        .filter(
            JugglingContactEvent.video_id    == video.id,
            JugglingContactEvent.device_event_id == req.device_event_id,
        )
        .first()
    )
    if existing is not None:
        if existing.deleted_at is not None:
            # Previously soft-deleted — treat as new creation with same device_event_id
            # Re-activate is out of scope; treat as conflict
            return CreateResult(
                event=existing,
                http_status=409,
                conflict_detail="device_event_id refers to a deleted event",
            )
        if _payloads_match(existing, req):
            return CreateResult(event=existing, http_status=200)
        return CreateResult(
            event=existing,
            http_status=409,
            conflict_detail="idempotency_conflict — same device_event_id, different payload",
        )

    contact_type, side, review_status, taxonomy_status = _validate_and_derive(req)
    consent_snapshot = _build_consent_snapshot(current_user.id, db)

    event = JugglingContactEvent(
        video_id              = video.id,
        created_by_user_id    = current_user.id,
        device_event_id       = req.device_event_id,
        timestamp_ms          = req.timestamp_ms,
        contact_type          = contact_type,
        side                  = side,
        annotation_confidence = req.annotation_confidence,
        annotation_source     = "manual_user",
        annotation_review_status  = review_status,
        taxonomy_review_status    = taxonomy_status,
        excluded_from_training    = True,
        excluded_from_count       = False,
        custom_label              = req.custom_label,
        custom_description        = req.custom_description,
        taxonomy_version          = "v1",
        consent_snapshot          = consent_snapshot,
    )
    _ensure_in_progress(video, db)
    db.add(event)
    db.commit()
    db.refresh(event)
    return CreateResult(event=event, http_status=201)


def create_contacts_batch(
    video_id: str,
    req: ContactEventBatchRequest,
    current_user: User,
    db: Session,
) -> ContactEventBatchResult:
    video = _get_video_owned(video_id, current_user.id, db)
    _guard_post_finish(video)

    results: list[ContactEventBatchItemResult] = []
    created = 0
    duplicate_skipped = 0
    conflict = 0

    for item in req.events:
        existing = (
            db.query(JugglingContactEvent)
            .filter(
                JugglingContactEvent.video_id        == video.id,
                JugglingContactEvent.device_event_id == item.device_event_id,
            )
            .first()
        )
        if existing is not None and existing.deleted_at is None:
            if _payloads_match(existing, item):
                results.append(ContactEventBatchItemResult(
                    device_event_id=item.device_event_id,
                    status="duplicate",
                    event_id=existing.id,
                ))
                duplicate_skipped += 1
            else:
                results.append(ContactEventBatchItemResult(
                    device_event_id=item.device_event_id,
                    status="conflict",
                    event_id=existing.id,
                    detail="idempotency_conflict — same device_event_id, different payload",
                ))
                conflict += 1
            continue

        try:
            contact_type, side, review_status, taxonomy_status = _validate_and_derive(item)
        except HTTPException as exc:
            results.append(ContactEventBatchItemResult(
                device_event_id=item.device_event_id,
                status="conflict",
                detail=str(exc.detail),
            ))
            conflict += 1
            continue

        consent_snapshot = _build_consent_snapshot(current_user.id, db)
        event = JugglingContactEvent(
            video_id                  = video.id,
            created_by_user_id        = current_user.id,
            device_event_id           = item.device_event_id,
            timestamp_ms              = item.timestamp_ms,
            contact_type              = contact_type,
            side                      = side,
            annotation_confidence     = item.annotation_confidence,
            annotation_source         = "manual_user",
            annotation_review_status  = review_status,
            taxonomy_review_status    = taxonomy_status,
            excluded_from_training    = True,
            excluded_from_count       = False,
            custom_label              = item.custom_label,
            custom_description        = item.custom_description,
            taxonomy_version          = "v1",
            consent_snapshot          = consent_snapshot,
        )
        db.add(event)
        results.append(ContactEventBatchItemResult(
            device_event_id=item.device_event_id,
            status="created",
            event_id=event.id,
        ))
        created += 1

    if created > 0:
        _ensure_in_progress(video, db)
    db.commit()
    # Refresh UUIDs generated by DB
    for r in results:
        if r.status == "created" and r.event_id is None:
            pass  # event_id already set above (pre-commit)

    return ContactEventBatchResult(
        created=created,
        duplicate_skipped=duplicate_skipped,
        conflict=conflict,
        results=results,
    )


def patch_contact(
    video_id: str,
    event_id: str,
    req: ContactEventPatchRequest,
    current_user: User,
    db: Session,
) -> JugglingContactEvent:
    video = _get_video_owned(video_id, current_user.id, db)
    _guard_post_finish(video)

    try:
        evt_uuid = uuid.UUID(event_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Event not found")

    event = (
        db.query(JugglingContactEvent)
        .filter(
            JugglingContactEvent.id       == evt_uuid,
            JugglingContactEvent.video_id == video.id,
            JugglingContactEvent.deleted_at.is_(None),
        )
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")
    if event.version != req.version:
        raise HTTPException(
            status_code=409,
            detail=f"version_conflict — expected version {event.version}, got {req.version}. "
                   "Fetch the current event and retry.",
        )

    if req.contact_type is not None:
        try:
            validate_contact_type(req.contact_type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        event.contact_type = req.contact_type
        if is_stable(req.contact_type):
            event.side = derive_side(req.contact_type)
            event.taxonomy_review_status = JugglingTaxonomyReviewStatus.not_applicable.value
        else:
            # custom_other
            if req.side is None:
                raise HTTPException(status_code=422, detail="side required when changing to custom_other")
            event.side = req.side
            event.taxonomy_review_status = JugglingTaxonomyReviewStatus.pending_taxonomy_review.value

    if req.annotation_confidence is not None:
        event.annotation_confidence = req.annotation_confidence

    if req.side is not None and req.contact_type is None:
        # Side change on existing custom_other
        if is_stable(event.contact_type):
            raise HTTPException(
                status_code=422,
                detail="Cannot set side for stable contact types — side is derived server-side.",
            )
        event.side = req.side

    if req.custom_label is not None:
        event.custom_label = req.custom_label
    if req.custom_description is not None:
        event.custom_description = req.custom_description

    event.version += 1
    event.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(event)
    return event


def delete_contact(
    video_id: str,
    event_id: str,
    current_user: User,
    db: Session,
) -> None:
    video = _get_video_owned(video_id, current_user.id, db)
    _guard_post_finish(video)

    try:
        evt_uuid = uuid.UUID(event_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=404, detail="Event not found")

    event = (
        db.query(JugglingContactEvent)
        .filter(
            JugglingContactEvent.id       == evt_uuid,
            JugglingContactEvent.video_id == video.id,
            JugglingContactEvent.deleted_at.is_(None),
        )
        .first()
    )
    if event is None:
        raise HTTPException(status_code=404, detail="Event not found")

    event.deleted_at = datetime.now(timezone.utc)
    db.commit()


def finish_annotation(
    video_id: str,
    req: FinishAnnotationRequest,
    current_user: User,
    db: Session,
) -> JugglingVideo:
    video = _get_video_owned(video_id, current_user.id, db)

    if video.annotation_status not in (None, "metadata_ready", "in_progress"):
        raise HTTPException(
            status_code=409,
            detail=f"annotation_closed — current status {video.annotation_status!r} "
                   "cannot be finished. Contact an admin to re-open.",
        )

    active_count = (
        db.query(JugglingContactEvent)
        .filter(
            JugglingContactEvent.video_id    == video.id,
            JugglingContactEvent.deleted_at.is_(None),
            JugglingContactEvent.excluded_from_count.is_(False),
        )
        .count()
    )

    if active_count == 0 and not req.confirm_zero_contacts:
        raise HTTPException(
            status_code=422,
            detail="zero_contact_not_confirmed — no active contact events. "
                   "If this video genuinely has zero contacts, resubmit with "
                   "confirm_zero_contacts=true.",
        )

    now = datetime.now(timezone.utc)
    video.annotation_status      = "human_review_pending"
    video.annotation_finished_at = now
    video.total_juggling_count   = active_count
    db.commit()
    db.refresh(video)
    return video
