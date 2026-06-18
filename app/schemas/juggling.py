"""
Juggling POC — Pydantic schemas for request/response validation.

Response rules (enforced structurally):
  - storage_path is NEVER returned in any response.
  - filename_stored is NEVER returned in any response.
  - quality endpoint returns metadata and scores only; no direct video URL.

AN-1 additions:
  - ContactEventCreateRequest / ContactEventOut (single event CRUD)
  - ContactEventBatchRequest / ContactEventBatchResult (batch submit)
  - ContactEventPatchRequest (edit with optimistic locking)
  - FinishAnnotationRequest / FinishAnnotationOut (state machine)
  - JugglingVideoItemOut extended with annotation_status

Service invariants enforced in schemas (client CANNOT override):
  annotation_source, annotation_review_status, taxonomy_review_status,
  excluded_from_training are all set server-side and absent from requests.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

# ── Consent schemas ──────────────────────────────────────────────────────────

_CLIENT_METADATA_ALLOWED_KEYS = frozenset({
    "fps", "resolution", "duration_seconds", "codec",
    "device", "os_version", "app_version",
})


class JugglingConsentGrantRequest(BaseModel):
    service_consent:      bool = False
    training_consent:     bool = False
    admin_review_consent: bool = False


class JugglingConsentOut(BaseModel):
    service_consent:      bool
    training_consent:     bool
    admin_review_consent: bool
    consented_at:         Optional[datetime] = None

    model_config = {"from_attributes": True}


# ── Upload-init schemas ──────────────────────────────────────────────────────

_VALID_TRAINING_VIDEO_TYPES = frozenset({"juggling", "gan_footvolley", "gan_foottennis"})


class JugglingUploadInitRequest(BaseModel):
    source_type:          str = Field(..., description="in_app_capture | uploaded_video")
    upload_source:        str = Field(default="unknown",
                                      description="camera | gallery | file | unknown")
    training_video_type:  str = Field(default="juggling",
                                      description="juggling | gan_footvolley | gan_foottennis")
    client_reported_metadata: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def _validate_enums(self) -> "JugglingUploadInitRequest":
        valid_source_types = {"in_app_capture", "uploaded_video"}
        if self.source_type not in valid_source_types:
            raise ValueError(
                f"source_type must be one of {sorted(valid_source_types)}, "
                f"got {self.source_type!r}"
            )
        valid_upload_sources = {"camera", "gallery", "file", "unknown"}
        if self.upload_source not in valid_upload_sources:
            raise ValueError(
                f"upload_source must be one of {sorted(valid_upload_sources)}, "
                f"got {self.upload_source!r}"
            )
        if self.training_video_type not in _VALID_TRAINING_VIDEO_TYPES:
            raise ValueError(
                f"training_video_type must be one of {sorted(_VALID_TRAINING_VIDEO_TYPES)}, "
                f"got {self.training_video_type!r}"
            )
        if self.client_reported_metadata is not None:
            # Strip unknown keys silently — do not raise
            self.client_reported_metadata = {
                k: v for k, v in self.client_reported_metadata.items()
                if k in _CLIENT_METADATA_ALLOWED_KEYS
            }
        return self


class JugglingUploadInitOut(BaseModel):
    video_id:   str
    status:     str
    upload_url: str
    message:    str = "Upload ready. POST the video file to upload_url."


# ── File upload response ──────────────────────────────────────────────────────

class JugglingUploadFileOut(BaseModel):
    video_id:        str
    status:          str
    file_size_bytes: int
    checksum_sha256: str


# ── Complete response ─────────────────────────────────────────────────────────

class JugglingCompleteOut(BaseModel):
    video_id: str
    status:   str
    message:  str = "Analysis queued. Poll GET /quality for results."


# ── Quality response ──────────────────────────────────────────────────────────

class JugglingQualityOut(BaseModel):
    video_id:                   str
    status:                     str
    quality_status:             Optional[str]
    quality_score:              Optional[float]
    server_detected_metadata:   Optional[Dict[str, Any]]
    quality_detail:             Optional[Dict[str, Any]]
    rejection_reason:           Optional[str]
    warnings:                   List[str] = Field(default_factory=list)
    # P2 transcode fields — paths are NEVER included; only derived/safe metadata
    transcode_status:           Optional[str] = None
    audio_stripped:             Optional[bool] = None
    processed_resolution:       Optional[str] = None
    processed_fps:              Optional[float] = None
    processed_file_size_bytes:  Optional[int] = None

    model_config = {"from_attributes": True}


# ── Video list schemas (P5) ───────────────────────────────────────────────────

class JugglingVideoItemOut(BaseModel):
    """One video row in the list response.

    Privacy invariant: no raw path, no filesystem path, no URL is ever included.
    has_thumbnail / has_media signal expected availability; the media endpoints
    perform the authoritative disk check and return 404 if the file is absent.

    AN-1: annotation_status added to drive iOS CTA display.
    """
    video_id:                   str
    status:                     str
    transcode_status:           Optional[str]
    quality_status:             Optional[str]
    quality_score:              Optional[float]
    created_at:                 datetime
    updated_at:                 datetime
    duration_seconds:           Optional[float]
    processed_resolution:       Optional[str]
    processed_fps:              Optional[float]
    processed_file_size_bytes:  Optional[int]
    has_thumbnail:              bool
    has_media:                  bool
    upload_source:              str
    source_type:                str
    annotation_status:          Optional[str] = None
    user_rotation_degrees:      int = 0
    training_video_type:        str = "juggling"


class JugglingVideoListOut(BaseModel):
    videos: List[JugglingVideoItemOut]
    total:  int
    limit:  int
    offset: int


# ── User rotation persistence (PATCH /videos/{id}/rotation) ──────────────────

class JugglingRotationPatchRequest(BaseModel):
    rotation_degrees: Literal[0, 90, 180, 270]


class JugglingRotationPatchOut(BaseModel):
    video_id:             str
    user_rotation_degrees: int
    model_config = {"from_attributes": True}


# ── AN-1: Contact event schemas ───────────────────────────────────────────────

_VALID_CONFIDENCE = frozenset({"certain", "probable", "uncertain"})
_VALID_SIDE       = frozenset({"left", "right", "center", "unknown"})
_CUSTOM_LABEL_RE  = re.compile(r"^[a-z][a-z0-9_]{0,38}[a-z0-9]$")


class ContactEventCreateRequest(BaseModel):
    """
    Single contact event submission.

    Server-set fields (client MUST NOT include):
      annotation_source, annotation_review_status, taxonomy_review_status,
      excluded_from_training, side (for stable types).

    custom_other requires: custom_label + custom_description + side.
    Stable types: side is derived server-side; do not send.
    """
    device_event_id:    uuid.UUID = Field(..., description="Client-generated UUID for idempotency")
    timestamp_ms:       int       = Field(..., ge=0, description="Contact timestamp in ms from video start")
    contact_type:       str       = Field(..., description="Taxonomy v1 key")
    annotation_confidence: Literal["certain", "probable", "uncertain"]

    # custom_other only
    side:               Optional[str]  = Field(None, description="Required for custom_other only")
    custom_label:       Optional[str]  = Field(None, max_length=40)
    custom_description: Optional[str]  = Field(None, max_length=200)

    @field_validator("side")
    @classmethod
    def _validate_side(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_SIDE:
            raise ValueError(f"side must be one of {sorted(_VALID_SIDE)}, got {v!r}")
        return v

    @field_validator("custom_label")
    @classmethod
    def _validate_custom_label(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _CUSTOM_LABEL_RE.match(v):
            raise ValueError(
                "custom_label must match ^[a-z][a-z0-9_]{0,38}[a-z0-9]$ "
                "(lowercase, underscore-separated, 2–40 chars)"
            )
        return v


class ContactEventOut(BaseModel):
    """Read-only contact event representation."""
    event_id: uuid.UUID = Field(validation_alias=AliasChoices("event_id", "id"))
    device_event_id:         uuid.UUID
    timestamp_ms:            int
    contact_type:            str
    side:                    Optional[str]
    annotation_confidence:   str
    annotation_review_status:   str
    taxonomy_review_status:     str
    excluded_from_training:     bool
    custom_label:            Optional[str]
    custom_description:      Optional[str]
    version:                 int
    created_at:              datetime
    updated_at:              datetime

    model_config = {"from_attributes": True}


class ContactEventListOut(BaseModel):
    video_id:          str
    annotation_status: Optional[str]
    events:            List[ContactEventOut]


# ── Batch ─────────────────────────────────────────────────────────────────────

class ContactEventBatchRequest(BaseModel):
    events: List[ContactEventCreateRequest] = Field(..., min_length=1, max_length=200)


class ContactEventBatchItemResult(BaseModel):
    device_event_id: uuid.UUID
    status:          Literal["created", "duplicate", "conflict"]
    event_id:        Optional[uuid.UUID] = None
    detail:          Optional[str]       = None


class ContactEventBatchResult(BaseModel):
    created:           int
    duplicate_skipped: int
    conflict:          int
    results:           List[ContactEventBatchItemResult]

    @property
    def http_status(self) -> int:
        """
        Derive the appropriate HTTP status code from batch outcome.

        all created, no duplicates, no conflicts → 201
        all exact duplicates, no created, no conflicts → 200
        mixed or any conflict present → 207
        """
        if self.created > 0 and self.duplicate_skipped == 0 and self.conflict == 0:
            return 201
        if self.duplicate_skipped > 0 and self.created == 0 and self.conflict == 0:
            return 200
        return 207


# ── Patch ─────────────────────────────────────────────────────────────────────

class ContactEventPatchRequest(BaseModel):
    """
    Partial update. Only the supplied fields are changed.
    version is required for optimistic locking (409 on mismatch).
    """
    contact_type:          Optional[str]  = None
    annotation_confidence: Optional[Literal["certain", "probable", "uncertain"]] = None
    side:                  Optional[str]  = None
    custom_label:          Optional[str]  = Field(None, max_length=40)
    custom_description:    Optional[str]  = Field(None, max_length=200)
    version:               int            = Field(..., description="Optimistic lock version")

    @field_validator("side")
    @classmethod
    def _validate_side(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _VALID_SIDE:
            raise ValueError(f"side must be one of {sorted(_VALID_SIDE)}, got {v!r}")
        return v

    @field_validator("custom_label")
    @classmethod
    def _validate_custom_label(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _CUSTOM_LABEL_RE.match(v):
            raise ValueError("custom_label does not match required pattern")
        return v


# ── Finish ────────────────────────────────────────────────────────────────────

class FinishAnnotationRequest(BaseModel):
    confirm_zero_contacts: bool = Field(
        default=False,
        description=(
            "Must be true when finishing with 0 active events. "
            "Prevents accidental zero-contact finish."
        ),
    )


class FinishAnnotationOut(BaseModel):
    video_id:              str
    annotation_status:     str
    total_juggling_count:  int
    contact_event_count:   int
    annotation_finished_at: datetime


# ── Phase 2A: Pose Snapshot schemas ──────────────────────────────────────────

class PoseSnapshotCreateRequest(BaseModel):
    """
    iOS uploads this after capturing VNHumanBodyPoseObservation on the video frame.

    keypoints must contain a "body" key (list; may be empty if no person detected).
    capture_source must be "ios_realtime" (new FAB tap) or "ios_retroactive"
    (retroactive generation for pre-existing events) for iOS uploads.
    captured_at_ms should match the contact event's timestamp_ms (echo for audit).
    """
    model_config = {"protected_namespaces": ()}

    keypoints:            Dict[str, Any]
    model_version:        str = Field(..., min_length=1, max_length=40,
                                      description="e.g. 'apple_vision_v1'")
    capture_source:       Literal["ios_realtime", "ios_retroactive", "backend_task"]
    captured_at_ms:       int  = Field(..., ge=0)
    image_width_px:       Optional[int]   = Field(None, gt=0)
    image_height_px:      Optional[int]   = Field(None, gt=0)
    inference_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    @field_validator("keypoints")
    @classmethod
    def _validate_keypoints(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        if "body" not in v:
            raise ValueError("keypoints must contain a 'body' key")
        if not isinstance(v["body"], list):
            raise ValueError("keypoints['body'] must be a list")
        return v


class PoseSnapshotOut(BaseModel):
    """Returned by GET /pose-snapshots and POST (upsert response)."""
    id:                   uuid.UUID
    contact_event_id:     uuid.UUID
    video_id:             uuid.UUID
    timestamp_ms:         int
    keypoints:            Dict[str, Any]
    model_version:        str
    capture_source:       str
    inference_confidence: Optional[float]
    image_width_px:       Optional[int]
    image_height_px:      Optional[int]
    created_at:           datetime

    model_config = {"from_attributes": True, "protected_namespaces": ()}


# ── Ball detection schemas (AN-3B2B-1) ──────────────────────────────────────


class BallDetectionManualRequest(BaseModel):
    """Manual ball position override by user or admin.

    no_ball_detected=False (default): ball_x and ball_y are required.
    no_ball_detected=True:            ball_x and ball_y must be omitted / None.
    """
    ball_x:           Optional[float] = Field(None, ge=0.0, le=1.0)
    ball_y:           Optional[float] = Field(None, ge=0.0, le=1.0)
    confidence:       Optional[float] = Field(None, ge=0.0, le=1.0)
    no_ball_detected: bool            = Field(False)

    @model_validator(mode="after")
    def _coords_required_unless_no_ball(self) -> "BallDetectionManualRequest":
        if not self.no_ball_detected and (self.ball_x is None or self.ball_y is None):
            raise ValueError("ball_x and ball_y are required when no_ball_detected=False")
        return self


class BallDetectionOut(BaseModel):
    """Returned by GET and POST ball-detection endpoints."""
    id:                     uuid.UUID
    contact_event_id:       uuid.UUID
    video_id:               uuid.UUID
    detection_source:       str
    ball_x:                 Optional[float]
    ball_y:                 Optional[float]
    confidence:             Optional[float]
    world_x_m:              Optional[float]
    world_y_m:              Optional[float]
    model_version:          Optional[str]
    image_width_px:         Optional[int]
    image_height_px:        Optional[int]
    no_ball_detected:       bool
    excluded_from_training: bool
    # AN-3B2C-1 (Opció A): original automatic state preserved on first manual override.
    # None when detection was manual-first (auto pipeline never ran for this event).
    auto_ball_x:            Optional[float]
    auto_ball_y:            Optional[float]
    # AN-3B2C-1 follow-up: model confidence at auto detection time; None for pre-migration rows.
    auto_confidence:        Optional[float]
    created_at:             datetime
    updated_at:             datetime

    model_config = {"from_attributes": True, "protected_namespaces": ()}


class BallDetectionTriggerResult(BaseModel):
    """Response from admin trigger endpoint."""
    video_id:               uuid.UUID
    training_video_type:    str
    model_used:             str
    events_queued:          int
    events_skipped:         int
    skipped_reasons:        List[str]

    model_config = {"protected_namespaces": ()}


# ── Dense ball trajectory schemas (AN-3B2D-1) ───────────────────────────────

class BallTrajectoryPointOut(BaseModel):
    frame_ms:       int
    ball_x:         Optional[float]
    ball_y:         Optional[float]
    confidence:     Optional[float]
    is_manual:      bool
    tracking_state: str

    model_config = {"from_attributes": True}


class BallTrajectoryResponse(BaseModel):
    status: str
    points: List[BallTrajectoryPointOut]


class BallTrajectoryManualSeedRequest(BaseModel):
    frame_ms: int   = Field(..., ge=0)
    ball_x:   float = Field(..., ge=0.0, le=1.0)
    ball_y:   float = Field(..., ge=0.0, le=1.0)


class BallTrajectoryManualSeedOut(BaseModel):
    frame_ms:       int
    ball_x:         float
    ball_y:         float
    tracking_state: str
    is_manual:      bool

    model_config = {"from_attributes": True}


# ── AN-3B2D-B0: Ball feedback schemas ────────────────────────────────────────


class BallFeedbackRequest(BaseModel):
    model_config = {"protected_namespaces": ()}

    frame_ms:          int   = Field(..., ge=0)
    decision:          str   = Field(..., description="confirm | reject | no_ball | corrected")
    corrected_x:       Optional[float] = Field(None, ge=0.0, le=1.0)
    corrected_y:       Optional[float] = Field(None, ge=0.0, le=1.0)
    correction_method: Optional[str]   = Field(None)
    # Model context snapshot sent from iOS
    model_predicted_x:    Optional[float] = Field(None)
    model_predicted_y:    Optional[float] = Field(None)
    model_confidence:     Optional[float] = Field(None, ge=0.0, le=1.0)
    model_tracking_state: Optional[str]   = Field(None)

    @field_validator("decision")
    @classmethod
    def decision_must_be_valid(cls, v: str) -> str:
        allowed = {"confirm", "reject", "no_ball", "corrected"}
        if v not in allowed:
            raise ValueError(f"decision must be one of {sorted(allowed)}")
        return v

    @field_validator("correction_method")
    @classmethod
    def correction_method_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in {"tap", "drag"}:
            raise ValueError("correction_method must be 'tap' or 'drag'")
        return v

    @model_validator(mode="after")
    def corrected_requires_coords(self) -> "BallFeedbackRequest":
        if self.decision == "corrected" and (
            self.corrected_x is None or self.corrected_y is None
        ):
            raise ValueError(
                "corrected_x and corrected_y are required when decision='corrected'"
            )
        return self


class BallFeedbackOut(BaseModel):
    id:             uuid.UUID
    video_id:       uuid.UUID
    frame_ms:       int
    decision:       str
    approval_state: str
    created_at:     datetime

    model_config = {"from_attributes": True}


class BallFeedbackQueueItem(BaseModel):
    model_config = {"protected_namespaces": ()}

    frame_ms:                int
    priority_score:          float
    model_predicted_x:       Optional[float]
    model_predicted_y:       Optional[float]
    model_confidence:        Optional[float]
    model_tracking_state:    Optional[str]
    existing_feedback_count: int


class BallFeedbackQueueResponse(BaseModel):
    video_id:        str
    queue_items:     List[BallFeedbackQueueItem]
    total:           int
    max_per_session: int = 3


# ── B2 Admin review schemas ───────────────────────────────────────────────────

class BallFeedbackAdminItem(BaseModel):
    """Extended feedback row for admin review queue."""
    id:                   uuid.UUID
    video_id:             uuid.UUID
    frame_ms:             int
    user_id:              int
    decision:             str
    corrected_x:          Optional[float] = None
    corrected_y:          Optional[float] = None
    approval_state:       str
    spam_flags:           List[str] = []
    created_at:           datetime
    reviewed_at:          Optional[datetime] = None
    reviewed_by_user_id:  Optional[int] = None

    model_config = {"from_attributes": True}


class BallFeedbackAdminQueueResponse(BaseModel):
    items: List[BallFeedbackAdminItem]
    total: int


class BallFeedbackReviewAction(BaseModel):
    action: str  # "approve" | "reject" | "escalate_to_review"

    @field_validator("action")
    @classmethod
    def action_must_be_valid(cls, v: str) -> str:
        allowed = {"approve", "reject", "escalate_to_review"}
        if v not in allowed:
            raise ValueError(f"action must be one of {sorted(allowed)}")
        return v


# ── B2 Training export schemas ────────────────────────────────────────────────

class TrainingExportFrame(BaseModel):
    video_id:       uuid.UUID
    frame_ms:       int
    gt_decision:    str
    gt_x:           Optional[float] = None
    gt_y:           Optional[float] = None
    confidence_score: float
    agreement_rate: float
    vote_count:     int
    correction_count: int
    is_gold_standard: bool

    model_config = {"from_attributes": True}


class TrainingExportResponse(BaseModel):
    version:      str
    exported_at:  datetime
    frame_count:  int
    frames:       List[TrainingExportFrame]