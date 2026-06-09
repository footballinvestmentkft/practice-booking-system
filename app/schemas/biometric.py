"""
Biometric Pydantic schemas — PR-1 foundation.

Design rules enforced structurally (compile/import time):
  1. face_match_score is ABSENT from every response schema.
     It is stored in BiometricVerificationLog for admin/threshold use
     but must never appear in any API response dict.
  2. embedding_ciphertext / embedding_iv are ABSENT from every schema.
  3. LivenessMetadata only exposes the five allowed fields.
     Unknown extra fields are rejected (model_config extra="forbid").
  4. BiometricVerificationLogOut excludes face_match_score explicitly.

Any PR that introduces a new response schema must ensure face_match_score
and embedding-related fields remain absent.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

# ── Liveness metadata ─────────────────────────────────────────────────────────

_FAILURE_REASON_VALUES = {"timeout", "face_lost", "multiple_faces", "max_retries", "capture_error"}


class LivenessMetadata(BaseModel):
    """
    High-level liveness challenge metadata.

    extra="forbid" ensures that forbidden fields (yaw, roll, device_model,
    ios_version, landmarks, frames, etc.) are rejected at API ingestion time.
    The liveness_metadata_sanitizer provides a third layer of defence for
    any code path that bypasses schema validation.
    """
    model_config = ConfigDict(extra="forbid")

    challenge_version: str = Field(
        ...,
        max_length=20,
        description="Challenge spec version, e.g. 'v1.0'",
    )
    steps_completed: List[str] = Field(
        default_factory=list,
        max_length=10,
        description="Ordered list of completed challenge steps",
    )
    total_duration_ms: int = Field(
        ...,
        ge=0,
        le=120_000,
        description="Wall-clock duration from challenge start to completion/failure (ms)",
    )
    retry_count: int = Field(
        ...,
        ge=0,
        le=10,
        description="Number of challenge retries in this session",
    )
    failure_reason: Optional[str] = Field(
        default=None,
        description="Only set when challenge failed; null on success",
    )

    # NOTE: face_match_score, yaw, roll, device_model, ios_version,
    # landmarks, frames — deliberately absent. Adding them here would
    # violate the data-minimization requirement.


# ── Consent schemas ───────────────────────────────────────────────────────────

class BiometricConsentStatusOut(BaseModel):
    """Read-only view of the user's biometric consent state."""
    has_consent: bool
    granted_at:  Optional[datetime] = None
    version:     Optional[str]      = None
    revoked_at:  Optional[datetime] = None
    is_active:   bool               = False

    # face_match_score ABSENT — enforced by schema structure


# ── Verification status (GET /me/profile-photo/verification-status) ───────────

class BiometricVerificationStatusOut(BaseModel):
    """
    Current biometric verification state returned to the client.

    face_match_score is intentionally absent — clients must not receive
    the raw similarity score; only the classified status is exposed.
    """
    face_match_status:           Optional[str] = None
    face_reference_photo_status: Optional[str] = None
    has_biometric_consent:       bool          = False
    manual_review_required:      bool          = False

    # face_match_score ABSENT — structural enforcement


# ── Audit log output (admin) ──────────────────────────────────────────────────

class BiometricVerificationLogOut(BaseModel):
    """
    Admin-facing audit log entry.

    face_match_score is excluded even from admin responses —
    threshold tuning uses the database directly, not the API.
    """
    id:               int
    user_id:          int
    event_type:       str
    event_result:     Optional[str]      = None
    model_version:    Optional[str]      = None
    threshold_used:   Optional[float]    = None
    liveness_metadata: Optional[dict]    = None
    actor_user_id:    Optional[int]      = None
    actor_ip_address: Optional[str]      = None
    photo_filename:   Optional[str]      = None
    error_message:    Optional[str]      = None
    created_at:       datetime

    # face_match_score ABSENT — structural enforcement
    # embedding_ciphertext ABSENT — structural enforcement

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())
