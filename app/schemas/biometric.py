"""
Biometric Pydantic schemas — PR-1 foundation + PR-2 consent request/response.

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
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

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


# ── Consent request schemas (PR-2) ────────────────────────────────────────────

class BiometricConsentGrantRequest(BaseModel):
    """Body for POST /me/biometric-consent."""
    model_config = ConfigDict(extra="forbid")

    consent_version: str = Field(
        ...,
        max_length=20,
        description="Consent text version the user accepted, e.g. 'v1.0'",
    )

    # face_match_score, embedding, liveness raw data — deliberately absent


class BiometricConsentRevokeRequest(BaseModel):
    """Body for DELETE /me/biometric-consent."""
    model_config = ConfigDict(extra="forbid")

    reason: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Optional reason for revocation (user-provided)",
    )

    # No biometric data fields — revocation carries no sensor data


# ── Liveness reference request schema (PR-3) ─────────────────────────────────

class BiometricLivenessSubmitRequest(BaseModel):
    """Body for POST /me/biometric-liveness (PR-3)."""
    model_config = ConfigDict(extra="forbid")

    source: Literal["onboarding_liveness"] = Field(
        ...,
        description="Must be 'onboarding_liveness'; other sources reserved for future PRs",
    )
    liveness_metadata: LivenessMetadata = Field(
        ...,
        description="High-level liveness challenge metadata — forbidden fields rejected by schema",
    )
    photo_filename: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Basename of the captured photo file. No path separators allowed.",
    )

    @field_validator("photo_filename")
    @classmethod
    def _no_path_traversal(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        import os
        if os.path.basename(v) != v:
            raise ValueError("photo_filename must be a plain filename with no path separators")
        return v

    # face_match_score, embedding, raw sensor data — deliberately absent


# ── Consent response schema ───────────────────────────────────────────────────

class BiometricConsentStatusOut(BaseModel):
    """Read-only view of the user's biometric consent state."""
    has_consent: bool
    granted_at:  Optional[datetime] = None
    version:     Optional[str]      = None
    revoked_at:  Optional[datetime] = None
    is_active:   bool               = False

    # face_match_score ABSENT — enforced by schema structure
    # embedding_ciphertext ABSENT — enforced by schema structure


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


# ── Disclosure request / response (PR-7A) ────────────────────────────────────

class BiometricDisclosureAcceptRequest(BaseModel):
    """Body for POST /me/biometric-disclosure (PR-7A)."""
    model_config = ConfigDict(extra="forbid")

    disclosure_version: str = Field(
        ...,
        max_length=20,
        description="Disclosure text version being accepted, e.g. 'v1.0'.",
    )

    # No score, embedding, raw biometric data — deliberately absent


class BiometricDisclosureStatusOut(BaseModel):
    """
    Read-only view of the user's biometric disclosure state.

    face_match_score, embedding, raw liveness/sensor data — intentionally absent.
    """
    has_disclosure:   bool               = False
    is_active:        bool               = False
    accepted_version: Optional[str]      = None
    accepted_at:      Optional[datetime] = None
    revoked_at:       Optional[datetime] = None

    # face_match_score ABSENT — structural enforcement
    # embedding ABSENT — structural enforcement
    # raw liveness data ABSENT — structural enforcement


# ── Face-verify request / response (PR-6) ────────────────────────────────────

class BiometricVerifyRequest(BaseModel):
    """Body for POST /me/biometric-verify (PR-6)."""
    model_config = ConfigDict(extra="forbid")

    photo_filename: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Basename of the live-capture photo. No path separators allowed.",
    )

    @field_validator("photo_filename")
    @classmethod
    def _no_path_traversal(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        import os
        if os.path.basename(v) != v:
            raise ValueError("photo_filename must be a plain filename with no path separators")
        return v

    # face_match_score, embedding, raw sensor data — deliberately absent


class BiometricVerifyResponse(BaseModel):
    """
    Response for POST /me/biometric-verify.

    result is the classified match outcome.
    face_match_score is intentionally absent — clients must never receive
    the raw similarity value; only the classified outcome is returned.
    """
    result: Literal["verified", "manual_review_required", "rejected"]

    # face_match_score ABSENT — structural enforcement
    # embedding ABSENT — structural enforcement


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


# ── Admin Biometric Review schemas (PR-7B) ────────────────────────────────────

class AdminBiometricReviewItemOut(BaseModel):
    """Single item in the admin manual-review queue. No score, no embedding."""
    user_id:                    int
    face_match_status:          str
    face_reference_photo_status: Optional[str] = None
    manual_review_flagged_at:   Optional[datetime] = None
    consent_version:            Optional[str] = None
    disclosure_accepted:        bool = False
    disclosure_version:         Optional[str] = None

    # face_match_score ABSENT — structural enforcement
    # embedding ABSENT — structural enforcement
    # raw liveness / yaw / roll / pitch / landmarks ABSENT


class AdminBiometricReviewQueueOut(BaseModel):
    """Response for GET /admin/biometric/review-queue."""
    items: List[AdminBiometricReviewItemOut]

    # face_match_score ABSENT — structural enforcement


class AdminBiometricHistoryEventOut(BaseModel):
    """One event row from biometric_verification_logs, admin-only view.

    threshold_used and model_version are returned for admin diagnostic use.
    face_match_score is NOT returned — internal DB only.
    """
    event_type:    str
    event_result:  Optional[str]   = None
    threshold_used: Optional[float] = None
    model_version: Optional[str]   = None
    created_at:    datetime

    # face_match_score ABSENT — structural enforcement
    # embedding ABSENT — structural enforcement

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())


class AdminBiometricHistoryOut(BaseModel):
    """Response for GET /admin/biometric/{user_id}/history."""
    user_id: int
    events:  List[AdminBiometricHistoryEventOut]

    # face_match_score ABSENT — structural enforcement


class AdminBiometricOverrideRequest(BaseModel):
    """Body for POST /admin/biometric/{user_id}/override."""
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approved", "rejected"] = Field(
        ...,
        description="Admin decision: 'approved' sets verified; 'rejected' sets rejected.",
    )
    reason: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Optional reason for the decision (sanitised, stored in audit log).",
    )

    # face_match_score ABSENT — structural enforcement
    # embedding, raw biometric data — deliberately absent


class AdminBiometricOverrideOut(BaseModel):
    """Response for POST /admin/biometric/{user_id}/override."""
    result:     Literal["approved", "rejected"]
    user_id:    int
    decided_at: datetime

    # face_match_score ABSENT — structural enforcement
