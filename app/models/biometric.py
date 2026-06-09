"""
Biometric face matching models — PR-1 foundation.

Three tables:
  user_biometric_consents    — GDPR Art. 9 explicit consent records
  user_face_embeddings       — AES-256-GCM encrypted ArcFace embeddings (PR-4+)
  biometric_verification_logs — immutable audit trail for every biometric event

Design constraints:
  - No embedding generation or ONNX dependency in this file (PR-4+).
  - liveness_metadata JSONB stores only high-level step data; raw sensor
    values (yaw, roll, landmarks) must never be written here.
  - face_match_score stored internally for admin use; never returned in API
    responses (enforced by Pydantic schemas in app/schemas/biometric.py).
"""
from __future__ import annotations

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
)
from sqlalchemy.dialects.postgresql import BYTEA, JSONB
from sqlalchemy.orm import relationship

from ..database import Base


# ── UserBiometricConsent ──────────────────────────────────────────────────────

class UserBiometricConsent(Base):
    """
    GDPR Art. 9 explicit biometric consent record.

    One active row per user (enforced by unique index on user_id WHERE is_active).
    Revocation sets is_active=False and consent_revoked_at; the row is retained
    for proof-of-consent obligations (5-year retention per ops runbook).
    """
    __tablename__ = "user_biometric_consents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Consent grant
    consent_granted_at = Column(DateTime(timezone=True), nullable=False,
                                default=lambda: datetime.now(timezone.utc))
    consent_version    = Column(String(20), nullable=False,
                                comment="Consent text version, e.g. 'v1.0'")
    consent_ip_address = Column(String(45), nullable=True,
                                comment="IPv4 or IPv6 of the granting request")
    consent_user_agent = Column(String(500), nullable=True)

    # Revocation
    consent_revoked_at = Column(DateTime(timezone=True), nullable=True)
    revocation_reason  = Column(String(200), nullable=True)

    # State
    is_active  = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="biometric_consents")


# ── UserFaceEmbedding ─────────────────────────────────────────────────────────

class UserFaceEmbedding(Base):
    """
    AES-256-GCM encrypted ArcFace embedding (512 × float32 = 2048 bytes).

    Populated by Celery task in PR-4. In PR-1 this table exists but stays empty.
    is_active=False until admin approves the reference photo (or liveness passes).

    Security:
      embedding_ciphertext — BYTEA, AES-256-GCM ciphertext
      embedding_iv         — BYTEA, 12-byte GCM nonce (unique per row)
      The plaintext embedding is NEVER stored or returned via any API.
    """
    __tablename__ = "user_face_embeddings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    # Encrypted embedding (populated by PR-4 Celery task)
    embedding_ciphertext = Column(BYTEA, nullable=True,
                                  comment="AES-256-GCM ciphertext of 512-dim float32 vector")
    embedding_iv         = Column(BYTEA, nullable=True,
                                  comment="12-byte GCM nonce — unique per row")
    model_version        = Column(String(100), nullable=True,
                                  comment="e.g. 'insightface_buffalo_sc_v1'")

    # Reference approval
    approved_by = Column(Integer, ForeignKey("users.id"), nullable=True,
                         comment="Admin user_id who approved; NULL for auto-approved liveness")
    approved_at = Column(DateTime(timezone=True), nullable=True)

    # State
    is_active  = Column(Boolean, nullable=False, default=False,
                        comment="False until embedding generated and reference approved")
    created_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False,
                        default=lambda: datetime.now(timezone.utc),
                        onupdate=lambda: datetime.now(timezone.utc))

    user     = relationship("User", foreign_keys=[user_id], back_populates="face_embedding")
    approver = relationship("User", foreign_keys=[approved_by])


# ── BiometricVerificationLog ──────────────────────────────────────────────────

class BiometricVerificationLog(Base):
    """
    Immutable audit trail for every biometric event.

    Rows are INSERT-only — no UPDATE or DELETE via application code.
    Retention: 5 years (per ops runbook), even after consent revocation.

    liveness_metadata JSONB constraints (enforced by sanitizer before INSERT):
      Allowed : challenge_version, steps_completed, total_duration_ms,
                retry_count, failure_reason
      Forbidden: device_model, ios_version, yaw, roll, pitch, landmarks,
                 face_landmarks, eye_data, frames, frame_data, pixel_data,
                 bounding_box, face_rect — and any other sensor/biometric detail

    face_match_score is stored internally for admin / threshold-tuning purposes.
    It is NEVER returned in any API response (enforced by Pydantic schema).
    """
    __tablename__ = "biometric_verification_logs"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    # Event classification
    event_type   = Column(String(50), nullable=False, index=True,
                          comment="See BiometricEventType constants")
    event_result = Column(String(30), nullable=True,
                          comment="accepted / rejected / pending / deleted / auto_approved_liveness")

    # Matching metrics — internal only, never exposed via API
    face_match_score = Column(Float, nullable=True,
                              comment="Cosine similarity [0,1]. NEVER returned in API responses.")
    model_version    = Column(String(100), nullable=True)
    threshold_used   = Column(Float, nullable=True)

    # Liveness metadata — high-level only, sanitized before INSERT
    liveness_metadata = Column(
        JSONB,
        nullable=True,
        comment=(
            "High-level liveness challenge metadata. "
            "Allowed: challenge_version, steps_completed, total_duration_ms, "
            "retry_count, failure_reason. "
            "PROHIBITED: device_model, ios_version, yaw, roll, landmarks, frames."
        ),
    )

    # Actor (NULL = system action)
    actor_user_id  = Column(Integer, ForeignKey("users.id"), nullable=True,
                            comment="Admin user_id for manual actions; NULL for automated events")
    actor_ip_address = Column(String(45), nullable=True)

    # Reference
    photo_filename = Column(String(255), nullable=True,
                            comment="Basename of the photo file; not a full URL")
    error_message  = Column(String(500), nullable=True)

    created_at = Column(DateTime(timezone=True), nullable=False,
                        index=True,
                        default=lambda: datetime.now(timezone.utc))

    user  = relationship("User", foreign_keys=[user_id])
    actor = relationship("User", foreign_keys=[actor_user_id])