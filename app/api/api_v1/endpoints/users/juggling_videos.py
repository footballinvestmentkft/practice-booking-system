"""
Juggling video intake endpoints.

GET  /api/v1/users/me/juggling/videos                    — list own videos (P5)
POST /api/v1/users/me/juggling/videos/upload-init      — create pending record
POST /api/v1/users/me/juggling/videos/{video_id}/upload  — upload file
POST /api/v1/users/me/juggling/videos/{video_id}/complete — enqueue analysis
GET  /api/v1/users/me/juggling/videos/{video_id}/quality  — poll result
GET  /api/v1/users/me/juggling/videos/{video_id}/thumbnail — auth-gated JPEG thumbnail
GET  /api/v1/users/me/juggling/videos/{video_id}/media     — auth-gated processed video stream

All endpoints gated by require_juggling_enabled() → 503 when flag off.
service_consent is required before upload-init → 403 if missing.

Storage: files written to JUGGLING_UPLOAD_DIR (outside app/static/).
         DB stores storage_path only — never a public URL.
Quality endpoint returns metadata + scores only; no video URL in response.
Media endpoints stream thumbnail_path / processed_path only — no raw path in response.

Security pipeline (pre-save, all in upload endpoint):
  1. Extension allowlist: .mp4 .mov .m4v
  2. MIME allowlist: video/mp4 video/quicktime video/x-m4v
  3. File magic bytes: ftyp box (ISO Base Media container)
  4. Empty file reject
  5. File size limit (JUGGLING_VIDEO_MAX_SIZE_MB from config)
  6. Server-generated filename; client name never propagated to filesystem
  7. checksum_sha256 stored
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, get_current_user_media
from app.models.juggling import JugglingVideo, JugglingVideoStatus, JugglingTranscodeStatus
from app.models.user import User
from app.schemas.juggling import (
    JugglingCompleteOut,
    JugglingQualityOut,
    JugglingRotationPatchOut,
    JugglingRotationPatchRequest,
    JugglingUploadFileOut,
    JugglingUploadInitRequest,
    JugglingUploadInitOut,
    JugglingVideoItemOut,
    JugglingVideoListOut,
)
from app.services.juggling.consent_service import has_service_consent
from app.services.juggling.feature_flag import require_juggling_enabled
from app.services.juggling.security_service import (
    VideoSecurityError,
    run_all_pre_save_checks,
)
from app.services.juggling import video_service

import logging
logger = logging.getLogger(__name__)
from app.services.juggling.media_service import (
    MediaMissingError,
    MediaNotReadyError,
    PathSafetyError,
    ThumbnailMissingError,
    ThumbnailNotReadyError,
    resolve_media_path,
    resolve_thumbnail_path,
)
from app.tasks.juggling_transcode_task import transcode_video_task

router = APIRouter()

# ── P5 list helpers ───────────────────────────────────────────────────────────

# Statuses where a thumbnail cannot exist yet — mirrors media_service._THUMBNAIL_NOT_READY_STATUSES
_THUMBNAIL_NOT_READY = frozenset({
    JugglingVideoStatus.pending_upload.value,
    JugglingVideoStatus.uploaded.value,
    JugglingVideoStatus.processing.value,
})


def _has_thumbnail(video: JugglingVideo) -> bool:
    return video.thumbnail_path is not None and video.status not in _THUMBNAIL_NOT_READY


def _has_media(video: JugglingVideo) -> bool:
    return (
        video.status == JugglingVideoStatus.analyzed.value
        and video.transcode_status == JugglingTranscodeStatus.done.value
        and video.processed_path is not None
    )


def _duration_seconds(video: JugglingVideo) -> float | None:
    detail = video.quality_detail or {}
    val = detail.get("duration_seconds")
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ── Statuses from which complete() is blocked ─────────────────────────────────
# (gdpr_deleted is also caught by _get_video_or_404 → 410)
_COMPLETE_BLOCKED = {
    JugglingVideoStatus.pending_upload.value,
    JugglingVideoStatus.processing.value,
    JugglingVideoStatus.analyzed.value,
    JugglingVideoStatus.rejected.value,
    JugglingVideoStatus.gdpr_deleted.value,
}


def _get_video_or_404(video_id: str, user_id: int, db: Session) -> JugglingVideo:
    video = (
        db.query(JugglingVideo)
        .filter(JugglingVideo.id == video_id, JugglingVideo.user_id == user_id)
        .first()
    )
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found.")
    if video.status == JugglingVideoStatus.gdpr_deleted.value:
        raise HTTPException(status_code=410, detail="Video has been permanently deleted.")
    return video


@router.get(
    "/me/juggling/videos",
    response_model=JugglingVideoListOut,
    dependencies=[Depends(require_juggling_enabled)],
    summary="List own juggling videos",
    tags=["juggling"],
)
def list_videos(
    limit:  int = Query(default=50, ge=1, le=100, description="Max items to return (1–100)"),
    offset: int = Query(default=0,  ge=0,          description="Number of items to skip"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JugglingVideoListOut:
    base_q = (
        db.query(JugglingVideo)
        .filter(
            JugglingVideo.user_id == current_user.id,
            JugglingVideo.status != JugglingVideoStatus.gdpr_deleted.value,
            JugglingVideo.status != JugglingVideoStatus.media_deleted.value,
        )
    )
    total = base_q.count()
    rows = (
        base_q
        .order_by(JugglingVideo.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    items = [
        JugglingVideoItemOut(
            video_id=str(v.id),
            status=v.status,
            transcode_status=v.transcode_status,
            quality_status=v.quality_status,
            quality_score=float(v.quality_score) if v.quality_score is not None else None,
            created_at=v.created_at,
            updated_at=v.updated_at,
            duration_seconds=_duration_seconds(v),
            processed_resolution=v.processed_resolution,
            processed_fps=v.processed_fps,
            processed_file_size_bytes=v.processed_file_size_bytes,
            has_thumbnail=_has_thumbnail(v),
            has_media=_has_media(v),
            upload_source=v.upload_source,
            source_type=v.source_type,
            annotation_status=v.annotation_status,
            user_rotation_degrees=v.user_rotation_degrees if v.user_rotation_degrees is not None else 0,
        )
        for v in rows
    ]
    return JugglingVideoListOut(videos=items, total=total, limit=limit, offset=offset)


@router.post(
    "/me/juggling/videos/upload-init",
    response_model=JugglingUploadInitOut,
    status_code=201,
    dependencies=[Depends(require_juggling_enabled)],
    summary="Initialise a juggling video upload",
    tags=["juggling"],
)
def upload_init(
    body: JugglingUploadInitRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JugglingUploadInitOut:
    if not has_service_consent(current_user.id, db):
        raise HTTPException(
            status_code=403,
            detail="service_consent required before uploading juggling videos.",
        )

    video = video_service.create_pending(
        user_id=current_user.id,
        source_type=body.source_type,
        upload_source=body.upload_source,
        client_reported_metadata=body.client_reported_metadata,
        db=db,
    )

    video_id = str(video.id)
    base_url = str(request.base_url).rstrip("/")
    upload_url = f"{base_url}/api/v1/users/me/juggling/videos/{video_id}/upload"

    return JugglingUploadInitOut(
        video_id=video_id,
        status=video.status,
        upload_url=upload_url,
    )


@router.post(
    "/me/juggling/videos/{video_id}/upload",
    response_model=JugglingUploadFileOut,
    dependencies=[Depends(require_juggling_enabled)],
    summary="Upload juggling video file",
    tags=["juggling"],
)
async def upload_file(
    video_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JugglingUploadFileOut:
    video = _get_video_or_404(video_id, current_user.id, db)

    if video.status != JugglingVideoStatus.pending_upload.value:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot upload: video is in status={video.status!r}. "
                   f"Only pending_upload videos accept file uploads.",
        )

    file_bytes = await file.read()
    client_filename = file.filename or "upload.mp4"
    content_type = file.content_type or "application/octet-stream"

    try:
        server_filename, checksum = run_all_pre_save_checks(
            client_filename=client_filename,
            content_type=content_type,
            file_bytes=file_bytes,
        )
    except VideoSecurityError as exc:
        reason = str(exc)
        if "file_too_large" in reason:
            raise HTTPException(status_code=413, detail=reason)
        elif "empty_file" in reason:
            raise HTTPException(status_code=400, detail=reason)
        else:
            raise HTTPException(status_code=415, detail=reason)

    file_path = video_service.save_file(file_bytes, server_filename)

    video_service.set_uploaded_with_original(
        video=video,
        storage_path=str(file_path),
        filename_stored=server_filename,
        file_size_bytes=len(file_bytes),
        checksum_sha256=checksum,
        db=db,
    )

    return JugglingUploadFileOut(
        video_id=video_id,
        status=JugglingVideoStatus.uploaded.value,
        file_size_bytes=len(file_bytes),
        checksum_sha256=checksum,
    )


@router.post(
    "/me/juggling/videos/{video_id}/complete",
    response_model=JugglingCompleteOut,
    dependencies=[Depends(require_juggling_enabled)],
    summary="Trigger quality analysis (Celery async)",
    tags=["juggling"],
)
def complete(
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JugglingCompleteOut:
    video = _get_video_or_404(video_id, current_user.id, db)

    if video.status in _COMPLETE_BLOCKED:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot call complete from status={video.status!r}. "
                   f"Only 'uploaded' may proceed.",
        )

    # failed is the only status not in _COMPLETE_BLOCKED — but it can only
    # proceed after reset_processing() restores it to 'uploaded'.
    if video.status == JugglingVideoStatus.failed.value:
        raise HTTPException(
            status_code=409,
            detail="Video is in 'failed' status. Reset to 'uploaded' via admin "
                   "reset_processing before retrying.",
        )

    # Transition to processing BEFORE enqueue (mood_photo pattern)
    # P2: transcode_video_task runs first; it dispatches analyze_video_task
    # only when transcode_status=done or skipped.
    video_service.set_processing(video_id, db)
    transcode_video_task.delay(video_id)

    return JugglingCompleteOut(
        video_id=video_id,
        status=JugglingVideoStatus.processing.value,
    )


@router.get(
    "/me/juggling/videos/{video_id}/quality",
    response_model=JugglingQualityOut,
    dependencies=[Depends(require_juggling_enabled)],
    summary="Poll quality analysis result",
    tags=["juggling"],
)
def get_quality(
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JugglingQualityOut:
    video = _get_video_or_404(video_id, current_user.id, db)

    quality_score_float = None
    if video.quality_score is not None:
        try:
            quality_score_float = float(video.quality_score)
        except (ValueError, TypeError):
            pass

    warnings = []
    detail = video.quality_detail or {}
    if detail.get("audio_present"):
        warnings.append("audio_present")

    return JugglingQualityOut(
        video_id=video_id,
        status=video.status,
        quality_status=video.quality_status,
        quality_score=quality_score_float,
        server_detected_metadata=video.server_detected_metadata,
        quality_detail=detail if detail else None,
        rejection_reason=video.rejection_reason,
        warnings=warnings,
        # P2 transcode metadata — paths are never included
        transcode_status=video.transcode_status,
        audio_stripped=video.audio_stripped,
        processed_resolution=video.processed_resolution,
        processed_fps=video.processed_fps,
        processed_file_size_bytes=video.processed_file_size_bytes,
    )


_MEDIA_HEADERS = {"Cache-Control": "private, no-store"}


@router.get(
    "/me/juggling/videos/{video_id}/thumbnail",
    dependencies=[Depends(require_juggling_enabled)],
    summary="Serve auth-gated JPEG thumbnail",
    tags=["juggling"],
    response_class=FileResponse,
)
def get_thumbnail(
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_media),
) -> FileResponse:
    video = _get_video_or_404(video_id, current_user.id, db)
    if video.status == JugglingVideoStatus.media_deleted.value:
        raise HTTPException(status_code=410, detail="Media has been deleted.")
    try:
        path = resolve_thumbnail_path(video)
    except ThumbnailNotReadyError as exc:
        raise HTTPException(status_code=409, detail=exc.reason)
    except ThumbnailMissingError:
        raise HTTPException(status_code=404, detail="Thumbnail not available.")
    except PathSafetyError:
        raise HTTPException(status_code=404, detail="Thumbnail not available.")
    return FileResponse(path=str(path), media_type="image/jpeg", headers=_MEDIA_HEADERS)


@router.get(
    "/me/juggling/videos/{video_id}/media",
    dependencies=[Depends(require_juggling_enabled)],
    summary="Serve auth-gated processed video stream (Range supported)",
    tags=["juggling"],
    response_class=FileResponse,
)
def get_media(
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_media),
) -> FileResponse:
    video = _get_video_or_404(video_id, current_user.id, db)
    if video.status == JugglingVideoStatus.media_deleted.value:
        raise HTTPException(status_code=410, detail="Media has been deleted.")
    try:
        path = resolve_media_path(video)
    except MediaNotReadyError as exc:
        raise HTTPException(status_code=409, detail=exc.reason)
    except MediaMissingError:
        raise HTTPException(status_code=404, detail="Media file not available.")
    except PathSafetyError:
        raise HTTPException(status_code=404, detail="Media file not available.")
    return FileResponse(path=str(path), media_type="video/mp4", headers=_MEDIA_HEADERS)


@router.delete(
    "/me/juggling/videos/{video_id}",
    status_code=204,
    dependencies=[Depends(require_juggling_enabled)],
    summary="Delete juggling video media (storage release)",
    description=(
        "Deletes the physical media files for the video and marks it as media_deleted. "
        "Analysis results, quality data, contact events and annotation data are preserved. "
        "Idempotent: calling DELETE on an already media_deleted video returns 204. "
        "This is NOT a GDPR erasure — account/profile data deletion is a separate flow."
    ),
    tags=["juggling"],
)
def delete_video(
    video_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    logger.info("delete_video_request", extra={"video_id": video_id, "user_id": current_user.id})
    result = video_service.delete_media(video_id, current_user.id, db)
    logger.info("delete_video_result", extra={"video_id": video_id, "status": result.get("status"), "reason": result.get("reason")})
    status = result["status"]

    if status in ("deleted", "skipped"):
        return Response(status_code=204)

    reason = result.get("reason", "")
    if status == "error":
        if reason == "gdpr_deleted":
            raise HTTPException(status_code=410, detail="Video has been permanently deleted.")
        raise HTTPException(status_code=404, detail="Video not found.")

    raise HTTPException(
        status_code=500,
        detail=f"Media deletion failed: {reason}",
    )


@router.patch(
    "/me/juggling/videos/{video_id}/rotation",
    response_model=JugglingRotationPatchOut,
    dependencies=[Depends(require_juggling_enabled)],
    summary="Persist user display rotation override",
    tags=["juggling"],
)
def patch_rotation(
    video_id: str,
    body: JugglingRotationPatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JugglingRotationPatchOut:
    """
    Stores the user-chosen display rotation (0/90/180/270 degrees) for a video.
    This is a display-only override — the processed file is never re-encoded.
    Idempotent: calling PATCH with the same value is a no-op.
    """
    video = _get_video_or_404(video_id, current_user.id, db)
    video.user_rotation_degrees = body.rotation_degrees
    video.updated_at = datetime.now(timezone.utc)
    db.commit()
    return JugglingRotationPatchOut(
        video_id=video_id,
        user_rotation_degrees=video.user_rotation_degrees,
    )