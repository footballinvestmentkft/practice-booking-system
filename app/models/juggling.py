"""
Juggling POC — Video Intake + Quality Pipeline models.

Tables:
  juggling_consents       — per-user consent record (service + training + admin_review)
  juggling_videos         — per-video upload record with status state machine
  juggling_contact_events — per-event annotation records (PR-1)

State machine for juggling_videos.status:
  pending_upload → uploaded → processing → analyzed ─┐
                                        → rejected   ├──→ media_deleted (user: media files deleted,
                                        → failed     ┘              analysis/annotation data preserved)
                           → gdpr_deleted (P3: terminal; all data nulled — account/GDPR deletion only)
  media_deleted            → gdpr_deleted (during account deletion after media already removed)

Training eligibility dual-gate (JugglingContactEvent):
  Training use requires BOTH simultaneously:
    1. consent_snapshot->>'training_consent' == 'true'   (historical at creation)
    2. JugglingConsent.training_consent == true           (current live consent)
  The snapshot is an immutable audit trail; revocation is enforced at
  export/query time by joining the live consent, NOT by mutating the snapshot.
"""
from __future__ import annotations

import enum
import uuid as _uuid_mod
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

# SQLAlchemy import for audit log BigInteger autoincrement PK
from sqlalchemy import Sequence as _Sequence

from app.database import Base


class JugglingVideoStatus(str, enum.Enum):
    pending_upload = "pending_upload"
    uploaded       = "uploaded"
    processing     = "processing"
    analyzed       = "analyzed"
    rejected       = "rejected"
    failed         = "failed"
    media_deleted  = "media_deleted"  # user request: media files gone, analysis/annotation data preserved
    gdpr_deleted   = "gdpr_deleted"   # P3: terminal — all personal data nulled (account/GDPR deletion)


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


class JugglingTrainingVideoType(str, enum.Enum):
    juggling       = "juggling"
    gan_footvolley = "gan_footvolley"
    gan_foottennis = "gan_foottennis"


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
    training_video_type = Column(
        String(30), nullable=False,
        default=JugglingTrainingVideoType.juggling.value,
        server_default="juggling",
        comment="Training activity type: juggling | gan_footvolley | gan_foottennis",
    )

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

    # ── P3 Retention fields ───────────────────────────────────────────────────
    deleted_at               = Column(DateTime(timezone=True), nullable=True,
                                      comment="Timestamp when GDPR delete or retention expiry applied")
    deletion_reason          = Column(String(50), nullable=True,
                                      comment="gdpr_request | retention_expired | orphan_cleanup | admin_delete")
    retention_expires_at     = Column(DateTime(timezone=True), nullable=True,
                                      comment="When this record is eligible for retention cleanup")
    retention_last_checked_at = Column(DateTime(timezone=True), nullable=True,
                                       comment="Last time the retention scan evaluated this record")
    retention_error          = Column(String(255), nullable=True,
                                      comment="Last retention operation error; cleared on success")

    # ── PR-1 annotation tracking ──────────────────────────────────────────────
    # annotation_status NULL = not yet started (same as metadata_ready for legacy rows)
    annotation_status = Column(
        String(30), nullable=True,
        comment=(
            "metadata_ready | in_progress | human_review_pending | "
            "annotated | reviewed | rejected. "
            "NULL = not yet started."
        ),
    )
    annotation_finished_at = Column(
        DateTime(timezone=True), nullable=True,
        comment="Timestamp when POST /contacts/finish was successfully called.",
    )
    total_juggling_count = Column(
        Integer, nullable=True,
        comment=(
            "Computed count of non-excluded contact events after finish. "
            "NULL until annotation is finished."
        ),
    )

    # ── User rotation override ───────────────────────────────────────────────
    # Display rotation chosen by the user via the rotate button.
    # Not a transcode parameter — the processed file is never re-encoded.
    user_rotation_degrees = Column(
        SmallInteger, nullable=False, default=0, server_default="0",
        comment="User display rotation override (0/90/180/270). Not a transcode parameter.",
    )

    # ── Dense ball trajectory lifecycle (AN-3B2D-1) ────────────────────────
    ball_trajectory_status = Column(
        String(20), nullable=True, default=None,
        comment="pending / processing / complete / failed — dense ball tracking lifecycle",
    )

    # ── Timestamps ───────────────────────────────────────────────────────────
    created_at = Column(DateTime(timezone=True), nullable=False, index=True,
                        default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="juggling_videos")
    contact_events = relationship(
        "JugglingContactEvent",
        back_populates="video",
        foreign_keys="JugglingContactEvent.video_id",
        cascade="all, delete-orphan",
    )


# ── Juggling contact event enums ──────────────────────────────────────────────

class JugglingAnnotationSource(str, enum.Enum):
    manual_user      = "manual_user"
    model_prediction = "model_prediction"
    user_corrected   = "user_corrected"


class JugglingAnnotationConfidence(str, enum.Enum):
    certain   = "certain"
    probable  = "probable"
    uncertain = "uncertain"


class JugglingAnnotationReviewStatus(str, enum.Enum):
    pending   = "pending"
    confirmed = "confirmed"
    corrected = "corrected"
    rejected  = "rejected"


class JugglingTaxonomyReviewStatus(str, enum.Enum):
    not_applicable         = "not_applicable"
    pending_taxonomy_review = "pending_taxonomy_review"
    reclassified           = "reclassified"
    promotion_candidate    = "promotion_candidate"
    promoted               = "promoted"
    approved_unclassified  = "approved_unclassified"


class JugglingVideoAnnotationStatus(str, enum.Enum):
    metadata_ready        = "metadata_ready"
    in_progress           = "in_progress"
    human_review_pending  = "human_review_pending"
    annotated             = "annotated"
    reviewed              = "reviewed"
    rejected              = "rejected"


class JugglingContactEvent(Base):
    """
    Per-event annotation record for juggling contact annotation.

    FK policy:
      video_id FK CASCADE          — deleted when video is deleted
      created_by_user_id FK RESTRICT — never orphaned; safe because users →
        juggling_videos CASCADE → contact events CASCADE means the RESTRICT
        is never triggered in practice during GDPR user deletion.
      corrected_from_event_id FK SET NULL — preserves correction trail

    Training eligibility dual-gate: see module docstring.
    """
    __tablename__ = "juggling_contact_events"

    id                   = Column(UUID(as_uuid=True), primary_key=True,
                                  default=_uuid_mod.uuid4)
    video_id             = Column(UUID(as_uuid=True),
                                  ForeignKey("juggling_videos.id", ondelete="CASCADE"),
                                  nullable=False, index=True)
    created_by_user_id   = Column(Integer,
                                  ForeignKey("users.id", ondelete="RESTRICT"),
                                  nullable=False, index=True)
    device_event_id      = Column(UUID(as_uuid=True), nullable=False)

    timestamp_ms         = Column(BigInteger, nullable=False)
    contact_type         = Column(String(40), nullable=False)
    side                 = Column(String(20), nullable=True)
    annotation_confidence = Column(String(20), nullable=False)

    annotation_review_status = Column(
        String(20), nullable=False,
        default=JugglingAnnotationReviewStatus.pending.value,
        server_default="pending",
    )
    taxonomy_review_status = Column(
        String(40), nullable=False,
        default=JugglingTaxonomyReviewStatus.not_applicable.value,
        server_default="not_applicable",
    )
    annotation_source    = Column(String(30), nullable=False)

    excluded_from_training = Column(Boolean, nullable=False, default=True,
                                    server_default="true")
    excluded_from_count    = Column(Boolean, nullable=False, default=False,
                                    server_default="false")

    model_confidence         = Column(Float, nullable=True)
    user_confirmed           = Column(Boolean, nullable=True)
    corrected_from_event_id  = Column(
        UUID(as_uuid=True),
        ForeignKey("juggling_contact_events.id", ondelete="SET NULL"),
        nullable=True,
    )

    custom_label        = Column(String(40), nullable=True)
    custom_description  = Column(String(200), nullable=True)
    taxonomy_version    = Column(String(10), nullable=False, default="v1",
                                 server_default="v1")
    consent_snapshot    = Column(JSONB, nullable=True)
    note                = Column(String(500), nullable=True)
    ball_height_approx_px = Column(Integer, nullable=True)

    version    = Column(Integer, nullable=False, default=1, server_default="1")
    created_at = Column(DateTime(timezone=True), nullable=False, index=True,
                        default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("video_id", "device_event_id",
                         name="uq_juggling_contact_device_event"),
        CheckConstraint(
            "annotation_source IN ('manual_user','model_prediction','user_corrected')",
            name="ck_juggling_contact_annotation_source",
        ),
        CheckConstraint(
            "annotation_confidence IN ('certain','probable','uncertain')",
            name="ck_juggling_contact_annotation_confidence",
        ),
        CheckConstraint(
            "annotation_review_status IN ('pending','confirmed','corrected','rejected')",
            name="ck_juggling_contact_annotation_review_status",
        ),
        CheckConstraint(
            "taxonomy_review_status IN ("
            "'not_applicable','pending_taxonomy_review','reclassified',"
            "'promotion_candidate','promoted','approved_unclassified')",
            name="ck_juggling_contact_taxonomy_review_status",
        ),
        CheckConstraint(
            "timestamp_ms >= 0",
            name="ck_juggling_contact_timestamp_ms_nonneg",
        ),
        CheckConstraint(
            "version >= 1",
            name="ck_juggling_contact_version_positive",
        ),
        CheckConstraint(
            "model_confidence IS NULL OR "
            "(model_confidence >= 0.0 AND model_confidence <= 1.0)",
            name="ck_juggling_contact_model_confidence_range",
        ),
    )

    video          = relationship("JugglingVideo", back_populates="contact_events",
                                  foreign_keys=[video_id])
    created_by     = relationship("User", foreign_keys=[created_by_user_id])
    corrected_from = relationship("JugglingContactEvent",
                                  foreign_keys=[corrected_from_event_id],
                                  remote_side="JugglingContactEvent.id")


class JugglingPoseSnapshot(Base):
    """
    Per-event pose snapshot captured by iOS Vision at annotation timestamp.

    Phase 2A: iOS-native only (Apple Vision VNHumanBodyPoseObservation, 19 joints).
    Backend ML fallback (MediaPipe) is Phase 2A-B, not implemented here.

    Keypoints JSONB format:
      {
        "schema_version": "1",
        "body": [{"name": "left_ankle", "x": 0.41, "y": 0.83, "confidence": 0.97}, ...],
        "left_hand": [],
        "right_hand": []
      }
      Coordinates: screen-normalized [0,1], origin top-left, y = 1 - vision_y.
      Only joints with confidence >= 0.3 are stored; others are omitted.

    Privacy: excluded_from_training is always implicit (Policy B).
    POSE_SNAPSHOT_ENABLED must be True for endpoints to accept uploads.
    """
    __tablename__ = "juggling_pose_snapshots"

    id                   = Column(UUID(as_uuid=True), primary_key=True,
                                  default=_uuid_mod.uuid4)
    contact_event_id     = Column(UUID(as_uuid=True),
                                  ForeignKey("juggling_contact_events.id",
                                             ondelete="CASCADE"),
                                  nullable=False)
    video_id             = Column(UUID(as_uuid=True),
                                  ForeignKey("juggling_videos.id",
                                             ondelete="CASCADE"),
                                  nullable=False)
    timestamp_ms         = Column(BigInteger, nullable=False)
    keypoints            = Column(JSONB, nullable=False)
    model_version        = Column(String(40), nullable=False)
    capture_source       = Column(String(20), nullable=False)
    inference_confidence = Column(Float, nullable=True)
    image_width_px       = Column(Integer, nullable=True)
    image_height_px      = Column(Integer, nullable=True)
    created_at           = Column(DateTime(timezone=True), nullable=False,
                                  default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        CheckConstraint(
            "capture_source IN ('ios_realtime', 'ios_retroactive', 'backend_task')",
            name="ck_juggling_pose_snapshots_capture_source",
        ),
        CheckConstraint(
            "inference_confidence IS NULL OR "
            "(inference_confidence >= 0.0 AND inference_confidence <= 1.0)",
            name="ck_juggling_pose_snapshots_inference_confidence",
        ),
        UniqueConstraint("contact_event_id",
                         name="ux_juggling_pose_snapshots_event"),
    )


class JugglingBallDetection(Base):
    """
    Per-event ball position detected by ONNX model or manual override.

    detection_source: 'mobilenet_ssd_v2' (auto) | 'manual' (user override).
    Coordinates: screen-normalized [0,1], origin top-left.
    world_x_m / world_y_m: NULL until pitch_config is applied (AN-3B2B-2).
    excluded_from_training always True (Policy B).
    """
    __tablename__ = "juggling_ball_detections"

    id                   = Column(UUID(as_uuid=True), primary_key=True,
                                  default=_uuid_mod.uuid4)
    contact_event_id     = Column(UUID(as_uuid=True),
                                  ForeignKey("juggling_contact_events.id",
                                             ondelete="CASCADE"),
                                  nullable=False)
    video_id             = Column(UUID(as_uuid=True),
                                  ForeignKey("juggling_videos.id",
                                             ondelete="CASCADE"),
                                  nullable=False, index=True)
    detection_source     = Column(String(40), nullable=False)
    ball_x               = Column(Float, nullable=True)
    ball_y               = Column(Float, nullable=True)
    confidence           = Column(Float, nullable=True)
    world_x_m            = Column(Float, nullable=True)
    world_y_m            = Column(Float, nullable=True)
    model_version        = Column(String(60), nullable=True)
    image_width_px       = Column(Integer, nullable=True)
    image_height_px      = Column(Integer, nullable=True)
    no_ball_detected     = Column(Boolean, nullable=False, default=False,
                                  server_default="false")
    excluded_from_training = Column(Boolean, nullable=False, default=True,
                                    server_default="true")
    # AN-3B2C-1 Opció A: original automatic state frozen on first manual override.
    # Populated only when detection_source transitions automatic→manual for the first time.
    # NULL for manual-first events (auto pipeline never ran) and for pure automatic records.
    # auto_confidence is set by the before_insert ORM listener below (no task code change needed).
    auto_ball_x          = Column(Float, nullable=True)
    auto_ball_y          = Column(Float, nullable=True)
    auto_confidence      = Column(Float, nullable=True,
                                  comment="Model confidence at auto detection; frozen, never overwritten by manual override")
    created_at           = Column(DateTime(timezone=True), nullable=False,
                                  default=lambda: datetime.now(timezone.utc))
    updated_at           = Column(DateTime(timezone=True), nullable=False,
                                  default=lambda: datetime.now(timezone.utc),
                                  onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("contact_event_id",
                         name="ux_juggling_ball_detections_event"),
        CheckConstraint(
            "detection_source IN ('mobilenet_ssd_v1', 'manual')",
            name="ck_juggling_ball_detections_source",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_juggling_ball_detections_confidence",
        ),
        CheckConstraint(
            "(no_ball_detected = true AND ball_x IS NULL AND ball_y IS NULL) "
            "OR (no_ball_detected = false AND ball_x IS NOT NULL AND ball_y IS NOT NULL)",
            name="ck_juggling_ball_detections_coords",
        ),
    )


class JugglingBallTrajectory(Base):
    """
    Dense ball trajectory point — one row per (video, frame_ms).

    Populated by dense_ball_trajectory_task at 10 FPS (100ms intervals).
    tracking_state: detected (ONNX hit), predicted (Kalman extrapolation),
    lost (too many misses), manual_seed (user-placed).
    """
    __tablename__ = "juggling_ball_trajectories"

    id              = Column(UUID(as_uuid=True), primary_key=True,
                             server_default=text("gen_random_uuid()"))
    video_id        = Column(UUID(as_uuid=True),
                             ForeignKey("juggling_videos.id", ondelete="CASCADE"),
                             nullable=False)
    frame_ms        = Column(Integer, nullable=False)
    ball_x          = Column(Float, nullable=True)
    ball_y          = Column(Float, nullable=True)
    confidence      = Column(Float, nullable=True)
    is_manual       = Column(Boolean, nullable=False, default=False,
                             server_default="false")
    tracking_state  = Column(String(20), nullable=False, default="detected",
                             server_default=text("'detected'"))
    model_version   = Column(String(60), nullable=True)
    image_width_px  = Column(Integer, nullable=True)
    image_height_px = Column(Integer, nullable=True)
    created_at      = Column(DateTime(timezone=True), nullable=False,
                             server_default=text("now()"))

    __table_args__ = (
        UniqueConstraint("video_id", "frame_ms",
                         name="ux_ball_traj_video_frame"),
        CheckConstraint(
            "tracking_state IN ('detected', 'predicted', 'lost', 'manual_seed')",
            name="ck_ball_traj_tracking_state",
        ),
        CheckConstraint(
            "(tracking_state = 'lost' AND ball_x IS NULL AND ball_y IS NULL) "
            "OR (tracking_state != 'lost' AND ball_x IS NOT NULL AND ball_y IS NOT NULL)",
            name="ck_ball_traj_coords_state",
        ),
    )


class JugglingFileDeletionLog(Base):
    """
    Immutable audit trail for all file deletion and retention scan events.

    Invariants:
      file_path_hash = HMAC_SHA256(JUGGLING_AUDIT_HASH_SECRET, raw_path)
      user_pseudonym = HMAC_SHA256(JUGGLING_AUDIT_HASH_SECRET, str(user_id))
      Raw paths and raw user_id MUST NOT be stored here.
    """
    __tablename__ = "juggling_file_deletion_log"

    id             = Column(BigInteger, primary_key=True, autoincrement=True)
    video_id       = Column(UUID(as_uuid=True),
                            ForeignKey("juggling_videos.id", ondelete="SET NULL"),
                            nullable=True,
                            comment="SET NULL when the video record is hard-deleted")
    user_pseudonym = Column(String(64), nullable=True,
                            comment="HMAC_SHA256(secret, str(user_id)) — never raw user_id")
    event_type     = Column(String(50), nullable=False,
                            comment=(
                                "gdpr_delete | user_media_delete | retention_expire | "
                                "orphan_cleanup | missing_file_audit | temp_cleanup | "
                                "dry_run_would_delete | scan_started | scan_completed"
                            ))
    file_type      = Column(String(30), nullable=True,
                            comment="original | processed | thumbnail | temp | all")
    file_path_hash = Column(String(64), nullable=True,
                            comment="HMAC_SHA256(secret, raw_path) — never raw path")
    dry_run        = Column(Boolean, nullable=False, default=True)
    success        = Column(Boolean, nullable=True)
    error_message  = Column(String(255), nullable=True)
    task_run_id    = Column(String(36), nullable=True,
                            comment="Celery task ID for correlation")
    created_at     = Column(DateTime(timezone=True), nullable=False,
                            default=lambda: datetime.now(timezone.utc))


# ── ORM listener: auto_confidence (AN-3B2C-1 follow-up) ──────────────────────
# Set auto_confidence = confidence for new automatic detection rows at INSERT
# time. This avoids modifying juggling_analysis_task.py (restricted pipeline).
# Manual overrides update detection_source → "manual" via UPDATE, never INSERT,
# so this listener only fires for the initial auto row creation.

from sqlalchemy import event as _sa_event  # noqa: E402


@_sa_event.listens_for(JugglingBallDetection, "before_insert")
def _freeze_auto_confidence_on_insert(mapper, connection, target):  # noqa: ANN001
    if target.detection_source != "manual" and target.auto_confidence is None:
        target.auto_confidence = target.confidence


# ── AN-3B2D-B0: User-assisted ball model training ─────────────────────────────


class BallFeedbackDecision(str, enum.Enum):
    confirm   = "confirm"
    reject    = "reject"
    no_ball   = "no_ball"
    corrected = "corrected"


class BallFeedbackApprovalState(str, enum.Enum):
    pending      = "pending"
    approved     = "approved"
    needs_review = "needs_review"
    rejected     = "rejected"
    spam         = "spam"


class JugglingBallFeedback(Base):
    """Per-user per-frame ball detection feedback. One row per (user, video, frame_ms)."""

    __tablename__ = "juggling_ball_feedback"

    id                    = Column(UUID(as_uuid=True), primary_key=True,
                                   default=_uuid_mod.uuid4)
    video_id              = Column(UUID(as_uuid=True),
                                   ForeignKey("juggling_videos.id", ondelete="CASCADE"),
                                   nullable=False, index=True)
    frame_ms              = Column(Integer, nullable=False)
    trajectory_point_id   = Column(UUID(as_uuid=True),
                                   ForeignKey("juggling_ball_trajectories.id",
                                              ondelete="SET NULL"),
                                   nullable=True)
    user_id               = Column(Integer,
                                   ForeignKey("users.id", ondelete="CASCADE"),
                                   nullable=False, index=True)
    # Decision
    decision              = Column(String(20), nullable=False)
    corrected_x           = Column(Float, nullable=True)
    corrected_y           = Column(Float, nullable=True)
    correction_method     = Column(String(20), nullable=True)
    # Model context snapshot at submit time
    model_predicted_x     = Column(Float, nullable=True)
    model_predicted_y     = Column(Float, nullable=True)
    model_confidence      = Column(Float, nullable=True)
    model_tracking_state  = Column(String(20), nullable=True)
    # Reliability (score at submit; not updated in B0)
    user_reliability_at_submit  = Column(Float, nullable=True, default=0.5)
    weighted_vote_contribution  = Column(Float, nullable=True)
    # State
    approval_state    = Column(String(20), nullable=False, default="pending")
    is_gold_standard  = Column(Boolean, nullable=False, default=False)
    is_control_sample = Column(Boolean, nullable=False, default=False)
    spam_flags        = Column(JSONB, nullable=False, default=list)
    # Timestamps
    created_at          = Column(DateTime(timezone=True), nullable=False,
                                 default=lambda: datetime.now(timezone.utc))
    reviewed_at         = Column(DateTime(timezone=True), nullable=True)
    reviewed_by_user_id = Column(Integer,
                                  ForeignKey("users.id", ondelete="SET NULL"),
                                  nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", "video_id", "frame_ms",
                         name="uq_ball_feedback_user_video_frame"),
        CheckConstraint(
            "decision IN ('confirm','reject','no_ball','corrected')",
            name="ck_ball_feedback_decision",
        ),
        CheckConstraint(
            "decision != 'corrected' OR "
            "(corrected_x IS NOT NULL AND corrected_y IS NOT NULL)",
            name="ck_ball_feedback_corrected_coords",
        ),
    )


class FrameGroundTruthDecision(str, enum.Enum):
    ball_present = "ball_present"
    no_ball      = "no_ball"
    uncertain    = "uncertain"


class JugglingFrameGroundTruth(Base):
    """Aggregated majority-vote ground truth per (video, frame_ms). Populated by B2+."""

    __tablename__ = "juggling_frame_ground_truth"

    id       = Column(UUID(as_uuid=True), primary_key=True, default=_uuid_mod.uuid4)
    video_id = Column(UUID(as_uuid=True),
                      ForeignKey("juggling_videos.id", ondelete="CASCADE"),
                      nullable=False)
    frame_ms = Column(Integer, nullable=False)
    # Ground truth (populated B2)
    gt_decision    = Column(String(20), nullable=False, default="uncertain")
    gt_x           = Column(Float, nullable=True)
    gt_y           = Column(Float, nullable=True)
    gt_bbox_width  = Column(Float, nullable=True)
    gt_bbox_height = Column(Float, nullable=True)
    # Vote aggregates (populated B2)
    confidence_score = Column(Float,   nullable=False, default=0.0)
    agreement_rate   = Column(Float,   nullable=False, default=0.0)
    vote_count       = Column(Integer, nullable=False, default=0)
    yes_votes        = Column(Integer, nullable=False, default=0)
    no_votes         = Column(Integer, nullable=False, default=0)
    no_ball_votes    = Column(Integer, nullable=False, default=0)
    correction_count = Column(Integer, nullable=False, default=0)
    # Training eligibility (never True in B0)
    training_eligible = Column(Boolean,              nullable=False, default=False)
    dataset_version   = Column(String(20),           nullable=True)
    exported_at       = Column(DateTime(timezone=True), nullable=True)
    # Metadata
    is_gold_standard = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("video_id", "frame_ms",
                         name="uq_frame_ground_truth_video_frame"),
    )


class UserAnnotationReliability(Base):
    """Per-user annotation quality tracking. Populated by B2+."""

    __tablename__ = "user_annotation_reliability"

    user_id = Column(Integer,
                     ForeignKey("users.id", ondelete="CASCADE"),
                     primary_key=True)
    ball_annotation_reliability = Column(Float,   nullable=False, default=0.5)
    total_feedbacks             = Column(Integer, nullable=False, default=0)
    correct_feedbacks           = Column(Integer, nullable=False, default=0)
    gold_attempts               = Column(Integer, nullable=False, default=0)
    gold_correct                = Column(Integer, nullable=False, default=0)
    spam_flags_count            = Column(Integer, nullable=False, default=0)
    last_updated = Column(DateTime(timezone=True), nullable=False,
                          default=lambda: datetime.now(timezone.utc))


class BallTrainingAssignment(Base):
    """
    Short-lived task assignment for the Global Ball Training Hub (AN-3B2F).

    Privacy invariant: the client receives only `id` (opaque UUID4).
    `video_id` and `frame_ms` are NEVER returned to the client — they live
    only in this DB row. The client cannot derive video_id from the assignment_id
    because UUID4 is randomly generated with no relation to any payload.

    Lifecycle:
      consumed_at IS NULL + expires_at > now()  → pending (usable)
      consumed_at IS NOT NULL                   → consumed (submitted or swept-expired)
      consumed_at IS NULL + expires_at <= now() → expired-pending (swept at next queue request)

    Partial unique index uix_bta_active_per_user_video_frame enforces at most one
    active (consumed_at IS NULL) assignment per (user, video, frame) combination.
    The queue service sweeps expired-pending rows before creating new ones.

    display_mode: NULL in PR-1A; set by frame-serving endpoint in PR-1B.
    """
    __tablename__ = "ball_training_assignments"

    id          = Column(UUID(as_uuid=True), primary_key=True, default=_uuid_mod.uuid4)
    user_id     = Column(Integer, ForeignKey("users.id",         ondelete="CASCADE"),
                         nullable=False, index=True)
    video_id    = Column(UUID(as_uuid=True),
                         ForeignKey("juggling_videos.id",        ondelete="CASCADE"),
                         nullable=False)
    frame_ms    = Column(Integer, nullable=False)
    issued_at   = Column(DateTime(timezone=True), nullable=False,
                         default=lambda: datetime.now(timezone.utc))
    expires_at  = Column(DateTime(timezone=True), nullable=False)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    display_mode = Column(String(20), nullable=True)
