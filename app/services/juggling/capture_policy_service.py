"""Canonical server-side policy for Player SOLO_VERIFIED capture provenance."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.juggling import (
    JugglingCaptureAttempt,
    JugglingCaptureAttemptStatus,
    JugglingCaptureMode,
    JugglingVideo,
    JugglingVideoStatus,
)


class CapturePolicyError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def issue_solo_attempt(
    db: Session, *, user_id: int, request_idempotency_key: str,
) -> JugglingCaptureAttempt:
    existing = db.query(JugglingCaptureAttempt).filter_by(
        user_id=user_id,
        request_idempotency_key=request_idempotency_key,
    ).one_or_none()
    if existing is not None:
        return existing

    attempt = JugglingCaptureAttempt(
        id=uuid4(),
        user_id=user_id,
        request_idempotency_key=request_idempotency_key,
        capture_mode=JugglingCaptureMode.solo_verified.value,
        camera_position="front",
        status=JugglingCaptureAttemptStatus.issued.value,
    )
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(JugglingCaptureAttempt).filter_by(
            user_id=user_id,
            request_idempotency_key=request_idempotency_key,
        ).one_or_none()
        if existing is None:
            raise
        return existing
    db.refresh(attempt)
    return attempt


def bind_solo_upload(
    db: Session,
    *,
    user_id: int,
    capture_attempt_id: UUID,
    upload_idempotency_key: str,
    client_reported_metadata: dict,
) -> JugglingVideo:
    """Bind one server-issued front-camera attempt to exactly one video."""
    attempt = (
        db.query(JugglingCaptureAttempt)
        .filter(
            JugglingCaptureAttempt.id == capture_attempt_id,
            JugglingCaptureAttempt.user_id == user_id,
        )
        .with_for_update()
        .one_or_none()
    )
    if attempt is None:
        raise CapturePolicyError("CAPTURE_ATTEMPT_NOT_FOUND")
    if (
        attempt.capture_mode != JugglingCaptureMode.solo_verified.value
        or attempt.camera_position != "front"
    ):
        raise CapturePolicyError("CAPTURE_ATTEMPT_POLICY_MISMATCH")

    reported_camera = client_reported_metadata.get("camera_position")
    if reported_camera != attempt.camera_position:
        raise CapturePolicyError("CAPTURE_CAMERA_POSITION_MISMATCH")

    existing = db.query(JugglingVideo).filter_by(
        capture_attempt_id=attempt.id,
    ).one_or_none()
    if existing is not None:
        if (
            existing.user_id != user_id
            or existing.upload_idempotency_key != upload_idempotency_key
            or (existing.client_reported_metadata or {}) != client_reported_metadata
        ):
            raise CapturePolicyError("CAPTURE_ATTEMPT_ALREADY_BOUND")
        return existing

    if attempt.status != JugglingCaptureAttemptStatus.issued.value:
        raise CapturePolicyError("CAPTURE_ATTEMPT_ALREADY_BOUND")

    video = JugglingVideo(
        id=uuid4(),
        user_id=user_id,
        source_type="in_app_capture",
        upload_source="camera",
        upload_idempotency_key=upload_idempotency_key,
        capture_attempt_id=attempt.id,
        capture_mode=attempt.capture_mode,
        authoritative_camera_position=attempt.camera_position,
        training_video_type="juggling",
        status=JugglingVideoStatus.pending_upload.value,
        client_reported_metadata=client_reported_metadata,
    )
    db.add(video)
    attempt.status = JugglingCaptureAttemptStatus.bound.value
    attempt.bound_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        replay = db.query(JugglingVideo).filter_by(
            capture_attempt_id=capture_attempt_id,
        ).one_or_none()
        if replay is None:
            raise
        if (
            replay.user_id != user_id
            or replay.upload_idempotency_key != upload_idempotency_key
            or (replay.client_reported_metadata or {}) != client_reported_metadata
        ):
            raise CapturePolicyError("CAPTURE_ATTEMPT_ALREADY_BOUND")
        return replay
    db.refresh(video)
    return video
