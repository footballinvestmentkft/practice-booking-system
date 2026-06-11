"""
Juggling POC — Video Intake + Quality Pipeline models.

Two tables:
  juggling_consents  — per-user consent record (service + training + admin_review)
  juggling_videos    — per-video upload record with status state machine

State machine for juggling_videos.status:
  pending_upload → uploaded → processing → analyzed
                                        → rejected  (quality/codec/duration gate)
                                        → failed    (ffprobe crash / timeout / corrupt)

Definitions:
  rejected = a deliberate quality or validation gate decision; file was readable.
  failed   = technical error; ffprobe could not process the file.
"""
from __future__ import annotations

import enum
import uuid as _uuid_mod
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.database import Base


class JugglingVideoStatus(str, enum.Enum):
    pending_upload = "pending_upload"
    uploaded       = "uploaded"
    processing     = "processing"
    analyzed       = "analyzed"
    rejected       = "rejected"
    failed         = "failed"


class JugglingVideoQualityStatus(str, enum.Enum):
    pending      = "pending"
    acceptable   = "acceptable"
    needs_review = "needs_review"
    rejected     = "rejected"


class JugglingTranscodeStatus(str, enum.Enum):
    pending    = "pending"
    processing = "processing"
    done       = "done"
    skipped    = "skipped"
    failed     = "failed"


class JugglingSourceType(str, enum.Enum):
    in_app_capture = "in_app_capture"
    uploaded_video = "uploaded_video"


class JugglingUploadSource(str, enum.Enum):
    camera  = "camera"
    gallery = "gallery"
    file    = "file"
    unknown = "unknown"


class JugglingConsent(Base):
    """
    Per-user consent record for the juggling POC pipeline.

    service_consent   — mandatory; gates upload-init (required=True before any upload).
    training_consent  — optional; user may toggle after initial grant.
    admin_review_consent — optional; user may toggle after initial grant.

    Revoke scope in this POC:
      training_consent + admin_review_consent are toggleable.
      service_consent revoke = V1.0 GDPR flow; not implemented in POC.
    """
    __tablename__ = "juggling_consents"

    id      = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, unique=True, index=True)

    service_consent      = Column(Boolean, nullable=False, default=False,
                                  comment="Mandatory gate — required before any video upload")
    training_consent     = Column(Boolean, nullable=False, default=False,
                                  comment="Consent to use footage for model training")
    admin_review_consent = Column(Boolean, nullable=False, default=False,
                                  comment="Consent for admin/coach manual review")

    consented_at  = Column(DateTime(timezone=True), nullable=True,
                           comment="Timestamp of most recent consent update")
    created_at    = Column(DateTime(timezone=True), nullable=False,
                           default=lambda: datetime.now(timezone.utc))
    updated_at    = Column(DateTime(timezone=True), nullable=False,
                           default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="juggling_consent")


class JugglingVideo(Base):
    """
    Per-video record tracking the full intake pipeline:
      upload → ffprobe metadata detection → quality gate → result.

    Storage: files live in JUGGLING_UPLOAD_DIR (outside app/static/).
    DB stores storage_path (filesystem path), never a public URL.
    Quality endpoint returns metadata only — no direct video URL.
    """
    __tablename__ = "juggling_videos"

    id      = Column(UUID(as_uuid=True), primary_key=True, default=_uuid_mod.uuid4)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)

    # ── Intake classification ────────────────────────────────────────────────
    source_type   = Column(String(30), nullable=False,
                           comment="in_app_capture | uploaded_video")
    upload_source = Column(String(30), nullable=False, default="unknown",
                           comment="camera | gallery | file | unknown")

    # ── State machine ────────────────────────────────────────────────────────
    status = Column(String(30), nullable=False,
                    default=JugglingVideoStatus.pending_upload.value,
                    index=True)

    # ── File storage ─────────────────────────────────────────────────────────
    # storage_path: filesystem path under JUGGLING_UPLOAD_DIR.
    # NOT a public URL — the quality endpoint never returns this to clients.
    storage_path     = Column(String(512), nullable=True,
                              comment="Filesystem path under JUGGLING_UPLOAD_DIR; not public")
    filename_stored  = Column(String(255), nullable=True,
                              comment="Server-generated UUID filename; client name is discarded")
    file_size_bytes  = Column(BigInteger, nullable=True)
    checksum_sha256  = Column(String(64), nullable=True)

    # ── P2 transcode fields ───────────────────────────────────────────────────
    # original_path: mirrors storage_path; set at upload time so the original
    # is tracked even if storage_path semantics ever change.
    original_path             = Column(String(512), nullable=True,
                                       comment="Original upload path; mirrors storage_path at upload")
    processed_path            = Column(String(512), nullable=True,
                                       comment="ffmpeg output path; null when transcode_status=skipped/failed")
    thumbnail_path            = Column(String(512), nullable=True,
                                       comment="First-frame JPEG path; populated after transcode task")
    transcode_status          = Column(String(20), nullable=True,
                                       default=JugglingTranscodeStatus.pending.value,
                                       comment="pending|processing|done|skipped|failed")
    transcode_error           = Column(String(512), nullable=True,
                                       comment="Error message when transcode_status=failed")
    audio_stripped            = Column(Boolean, nullable=True,
                                       comment="True once audio removed from processed file")
    processed_resolution      = Column(String(20), nullable=True,
                                       comment="WxH of processed file; null if skipped")
    processed_fps             = Column(Float, nullable=True,
                                       comment="FPS of processed file; null if skipped")
    processed_file_size_bytes = Column(BigInteger, nullable=True,
                                       comment="Byte size of processed file; null if skipped")
    checksum_processed        = Column(String(64), nullable=True,
                                       comment="SHA-256 hex of processed file; null if skipped")

    # ── Client-reported metadata (not authoritative) ─────────────────────────
    # Allowed keys: fps, resolution, duration_seconds, codec,
    #               device, os_version, app_version
    client_reported_metadata = Column(
        JSONB, nullable=True,
        comment=(
            "Client-supplied metadata. Not authoritative. "
            "server_detected_metadata overrides on conflict. "
            "Allowed: fps, resolution, duration_seconds, codec, "
            "device, os_version, app_version."
        ),
    )

    # ── Server-detected metadata (authoritative, ffprobe) ────────────────────
    # Keys: fps, resolution, duration_seconds, codec, bitrate_kbps,
    #       rotation, has_audio, file_format, container, nb_streams
    server_detected_metadata = Column(
        JSONB, nullable=True,
        comment=(
            "ffprobe-derived metadata. Authoritative. "
            "Populated by Celery analyze task after complete endpoint."
        ),
    )

    # ── Quality analysis results ─────────────────────────────────────────────
    quality_score  = Column(
        # Float, nullable until analyze task runs.
        String(10), nullable=True,
        comment="Stored as string to avoid float precision noise; e.g. '0.76'"
    )
    quality_status = Column(String(30), nullable=True,
                            default=JugglingVideoQualityStatus.pending.value)
    quality_detail = Column(
        JSONB, nullable=True,
        comment=(
            "Per-dimension scores. Keys: blur_score, dark_frame_ratio, "
            "fps_detected, fps_acceptable, duration_seconds, duration_acceptable, "
            "rotation, subject_size_score (null — P2/P3), "
            "ball_visible_score (null — P2/P3), audio_present (warning only)."
        ),
    )
    rejection_reason = Column(
        String(255), nullable=True,
        comment=(
            "Machine-readable reason code when status=rejected or quality_status=rejected. "
            "E.g.: unsupported_codec, too_long, too_dark, fps_too_low, "
            "corrupt_video, analysis_timeout."
        ),
    )

    # ── Timestamps ───────────────────────────────────────────────────────────
    created_at = Column(DateTime(timezone=True), nullable=False, index=True,
                        default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="juggling_videos")
