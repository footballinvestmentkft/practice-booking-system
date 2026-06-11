"""
Juggling POC — Pydantic schemas for request/response validation.

Response rules (enforced structurally):
  - storage_path is NEVER returned in any response.
  - filename_stored is NEVER returned in any response.
  - quality endpoint returns metadata and scores only; no direct video URL.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

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

class JugglingUploadInitRequest(BaseModel):
    source_type:   str = Field(..., description="in_app_capture | uploaded_video")
    upload_source: str = Field(default="unknown",
                               description="camera | gallery | file | unknown")
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