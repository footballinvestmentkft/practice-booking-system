"""Add biometric face matching foundation tables and user columns.

Creates:
  user_biometric_consents    — GDPR Art. 9 explicit consent records
  user_face_embeddings       — AES-256-GCM encrypted face embeddings (populated PR-4+)
  biometric_verification_logs — immutable biometric audit trail

Adds to users:
  face_match_status, face_match_score, face_reference_photo_status,
  manual_review_required, reviewed_by, reviewed_at, rejection_reason

All new user columns default to NULL / False so the migration is safe for
existing rows without a backfill step.

No ONNX, embedding generation, or Celery task is added in this migration.
Feature is controlled by BIOMETRIC_FACE_MATCHING_ENABLED=false (default).

Revision ID: 2026_06_10_1000
Revises:     2026_06_09_1000
Create Date: 2026-06-10 10:00:00
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import BYTEA, JSONB, UUID

revision      = "2026_06_10_1000"
down_revision = "2026_06_09_1000"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── 1. user_biometric_consents ────────────────────────────────────────────
    op.create_table(
        "user_biometric_consents",
        sa.Column("id",                 sa.Integer(),     primary_key=True),
        sa.Column("user_id",            sa.Integer(),     sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("consent_granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consent_version",    sa.String(20),    nullable=False),
        sa.Column("consent_ip_address", sa.String(45),    nullable=True),
        sa.Column("consent_user_agent", sa.String(500),   nullable=True),
        sa.Column("consent_revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason",  sa.String(200),   nullable=True),
        sa.Column("is_active",          sa.Boolean(),     nullable=False, server_default=sa.text("true")),
        sa.Column("created_at",         sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_user_biometric_consents_user_id", "user_biometric_consents", ["user_id"])
    # Enforce one active consent per user at DB level
    op.create_index(
        "uq_user_biometric_consents_active_user",
        "user_biometric_consents",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("is_active = true"),
    )

    # ── 2. user_face_embeddings ───────────────────────────────────────────────
    op.create_table(
        "user_face_embeddings",
        sa.Column("id",                   sa.Integer(),  primary_key=True),
        sa.Column("user_id",              sa.Integer(),  sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        sa.Column("embedding_ciphertext", BYTEA(),       nullable=True,
                  comment="AES-256-GCM ciphertext of 512-dim float32 embedding"),
        sa.Column("embedding_iv",         BYTEA(),       nullable=True,
                  comment="12-byte GCM nonce — unique per row"),
        sa.Column("model_version",        sa.String(100), nullable=True),
        sa.Column("approved_by",          sa.Integer(),  sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_at",          sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active",            sa.Boolean(),  nullable=False, server_default=sa.text("false")),
        sa.Column("created_at",           sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
        sa.Column("updated_at",           sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_user_face_embeddings_user_id", "user_face_embeddings", ["user_id"])

    # ── 3. biometric_verification_logs ────────────────────────────────────────
    op.create_table(
        "biometric_verification_logs",
        sa.Column("id",                sa.BigInteger(), primary_key=True),
        sa.Column("user_id",           sa.Integer(),    sa.ForeignKey("users.id"), nullable=False),
        sa.Column("event_type",        sa.String(50),   nullable=False),
        sa.Column("event_result",      sa.String(30),   nullable=True),
        # face_match_score: internal only — NEVER returned in API responses
        sa.Column("face_match_score",  sa.Float(),      nullable=True,
                  comment="Cosine similarity [0,1]. NEVER returned in API responses."),
        sa.Column("model_version",     sa.String(100),  nullable=True),
        sa.Column("threshold_used",    sa.Float(),      nullable=True),
        sa.Column("liveness_metadata", JSONB(),         nullable=True,
                  comment=(
                      "High-level liveness metadata only. "
                      "Allowed: challenge_version, steps_completed, total_duration_ms, "
                      "retry_count, failure_reason. "
                      "PROHIBITED: device_model, ios_version, yaw, roll, landmarks, frames."
                  )),
        sa.Column("actor_user_id",     sa.Integer(),    sa.ForeignKey("users.id"), nullable=True),
        sa.Column("actor_ip_address",  sa.String(45),   nullable=True),
        sa.Column("photo_filename",    sa.String(255),  nullable=True),
        sa.Column("error_message",     sa.String(500),  nullable=True),
        sa.Column("created_at",        sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("NOW()")),
    )
    op.create_index("ix_biometric_logs_user_id",    "biometric_verification_logs", ["user_id"])
    op.create_index("ix_biometric_logs_event_type", "biometric_verification_logs", ["event_type"])
    op.create_index("ix_biometric_logs_created_at", "biometric_verification_logs", ["created_at"])

    # ── 4. New columns on users ───────────────────────────────────────────────
    op.add_column("users", sa.Column(
        "face_match_status", sa.String(30), nullable=True,
        comment=(
            "Biometric state: NULL / reference_pending / verified / failed / "
            "manual_review_required / consent_revoked / onboarding_liveness_capture"
        ),
    ))
    op.add_column("users", sa.Column(
        "face_match_score", sa.Float(), nullable=True,
        comment="Last cosine similarity [0,1]. NEVER returned in API responses.",
    ))
    op.add_column("users", sa.Column(
        "face_reference_photo_status", sa.String(30), nullable=True,
        comment=(
            "Reference photo state: NULL / not_set / onboarding_liveness_capture / "
            "pending_review / approved / rejected"
        ),
    ))
    op.add_column("users", sa.Column(
        "manual_review_required", sa.Boolean(), nullable=False,
        server_default=sa.text("false"),
        comment="True when face match score is in review threshold band",
    ))
    op.add_column("users", sa.Column(
        "reviewed_by", sa.Integer(),
        sa.ForeignKey("users.id"), nullable=True,
        comment="Admin user_id who performed manual review",
    ))
    op.add_column("users", sa.Column("reviewed_at",      sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("rejection_reason", sa.String(500),             nullable=True))


def downgrade() -> None:
    # Remove users columns (reverse order of addition)
    op.drop_column("users", "rejection_reason")
    op.drop_column("users", "reviewed_at")
    op.drop_column("users", "reviewed_by")
    op.drop_column("users", "manual_review_required")
    op.drop_column("users", "face_reference_photo_status")
    op.drop_column("users", "face_match_score")
    op.drop_column("users", "face_match_status")

    # Drop tables (reverse creation order for FK safety)
    op.drop_index("ix_biometric_logs_created_at", table_name="biometric_verification_logs")
    op.drop_index("ix_biometric_logs_event_type",  table_name="biometric_verification_logs")
    op.drop_index("ix_biometric_logs_user_id",     table_name="biometric_verification_logs")
    op.drop_table("biometric_verification_logs")

    op.drop_index("ix_user_face_embeddings_user_id", table_name="user_face_embeddings")
    op.drop_table("user_face_embeddings")

    op.drop_index("uq_user_biometric_consents_active_user", table_name="user_biometric_consents")
    op.drop_index("ix_user_biometric_consents_user_id",     table_name="user_biometric_consents")
    op.drop_table("user_biometric_consents")
