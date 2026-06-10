"""add juggling video tables

Revision ID: 2026_06_10_1300
Revises: 2026_06_10_1200
Create Date: 2026-06-10

Tables created:
  juggling_consents — per-user consent record (service + training + admin_review)
  juggling_videos   — per-video upload record with status state machine

Video storage:
  Files are stored under JUGGLING_UPLOAD_DIR (outside app/static/).
  DB stores storage_path (filesystem path), never a public URL.

State machine (juggling_videos.status):
  pending_upload → uploaded → processing → analyzed | rejected | failed
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2026_06_10_1300"
down_revision = "2026_06_10_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── juggling_consents ────────────────────────────────────────────────────
    op.create_table(
        "juggling_consents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("service_consent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("training_consent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("admin_review_consent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_juggling_consents_user_id"),
    )
    op.create_index("ix_juggling_consents_user_id", "juggling_consents", ["user_id"])

    # ── juggling_videos ──────────────────────────────────────────────────────
    op.create_table(
        "juggling_videos",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("upload_source", sa.String(30), nullable=False,
                  server_default="unknown"),
        sa.Column("status", sa.String(30), nullable=False,
                  server_default="pending_upload"),
        sa.Column("storage_path", sa.String(512), nullable=True),
        sa.Column("filename_stored", sa.String(255), nullable=True),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("checksum_sha256", sa.String(64), nullable=True),
        sa.Column("client_reported_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("server_detected_metadata", postgresql.JSONB(), nullable=True),
        sa.Column("quality_score", sa.String(10), nullable=True),
        sa.Column("quality_status", sa.String(30), nullable=True,
                  server_default="pending"),
        sa.Column("quality_detail", postgresql.JSONB(), nullable=True),
        sa.Column("rejection_reason", sa.String(255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_juggling_videos_user_id", "juggling_videos", ["user_id"])
    op.create_index("ix_juggling_videos_status", "juggling_videos", ["status"])
    op.create_index("ix_juggling_videos_created_at", "juggling_videos", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_juggling_videos_created_at", table_name="juggling_videos")
    op.drop_index("ix_juggling_videos_status", table_name="juggling_videos")
    op.drop_index("ix_juggling_videos_user_id", table_name="juggling_videos")
    op.drop_table("juggling_videos")

    op.drop_index("ix_juggling_consents_user_id", table_name="juggling_consents")
    op.drop_table("juggling_consents")