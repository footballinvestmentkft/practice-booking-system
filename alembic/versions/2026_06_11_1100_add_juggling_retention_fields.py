"""add juggling retention fields

Revision ID: 2026_06_11_1100
Revises: 2026_06_11_1000
Create Date: 2026-06-11

Adds P3-retention columns to juggling_videos and creates the audit log table.

New columns on juggling_videos:
  deleted_at               — timestamp when GDPR delete or retention expiry applied
  deletion_reason          — gdpr_request | retention_expired | orphan_cleanup | admin_delete
  retention_expires_at     — when the record becomes eligible for retention cleanup
  retention_last_checked_at — last time the retention scan evaluated this record
  retention_error          — last error during a retention operation; cleared on success

Status enum bővítés: + gdpr_deleted (terminális, visszafordíthatatlan)

New table: juggling_file_deletion_log
  Audit trail for all file deletion events.
  file_path_hash  = HMAC_SHA256(JUGGLING_AUDIT_HASH_SECRET, raw_path)
  user_pseudonym  = HMAC_SHA256(JUGGLING_AUDIT_HASH_SECRET, str(user_id))
  Raw paths and raw user_id are NEVER stored in this table.

Invariant:
  No data is actually deleted by this migration.
  Destructive operations are controlled at the application layer by
  JUGGLING_RETENTION_CLEANUP_ENABLED and JUGGLING_RETENTION_DRY_RUN config.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "2026_06_11_1100"
down_revision = "2026_06_11_1000"
branch_labels = None
depends_on = None

_OLD_STATUSES = (
    "pending_upload", "uploaded", "processing",
    "analyzed", "rejected", "failed",
)
_NEW_STATUSES = _OLD_STATUSES + ("gdpr_deleted",)
_STATUS_CHECK = "ck_juggling_videos_status"


def upgrade() -> None:
    # ── New columns on juggling_videos ───────────────────────────────────────
    op.add_column(
        "juggling_videos",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True,
                  comment="Timestamp when GDPR delete or retention expiry was applied"),
    )
    op.add_column(
        "juggling_videos",
        sa.Column(
            "deletion_reason", sa.String(50), nullable=True,
            comment="gdpr_request | retention_expired | orphan_cleanup | admin_delete",
        ),
    )
    op.add_column(
        "juggling_videos",
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True,
                  comment="When this record is eligible for retention cleanup"),
    )
    op.add_column(
        "juggling_videos",
        sa.Column("retention_last_checked_at", sa.DateTime(timezone=True), nullable=True,
                  comment="Last time the retention scan evaluated this record"),
    )
    op.add_column(
        "juggling_videos",
        sa.Column("retention_error", sa.String(255), nullable=True,
                  comment="Last error during a retention operation; cleared on success"),
    )

    # ── status CHECK constraint bővítés: + gdpr_deleted ──────────────────────
    # Use IF EXISTS so this is safe on both fresh DBs and those that already
    # have the constraint from P1. Raw SQL avoids a broken-transaction from
    # a failed op.drop_constraint() inside the same transaction.
    op.execute(f"ALTER TABLE juggling_videos DROP CONSTRAINT IF EXISTS {_STATUS_CHECK}")

    op.create_check_constraint(
        _STATUS_CHECK,
        "juggling_videos",
        sa.column("status").in_(list(_NEW_STATUSES)),
    )

    # ── Indexes on new columns ────────────────────────────────────────────────
    op.create_index(
        "ix_juggling_videos_deleted_at",
        "juggling_videos", ["deleted_at"],
        postgresql_where=sa.text("deleted_at IS NOT NULL"),
    )
    op.create_index(
        "ix_juggling_videos_retention_expires_at",
        "juggling_videos", ["retention_expires_at"],
        postgresql_where=sa.text(
            "retention_expires_at IS NOT NULL AND deleted_at IS NULL"
        ),
    )

    # ── New table: juggling_file_deletion_log ─────────────────────────────────
    op.create_table(
        "juggling_file_deletion_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=True,
                  comment="SET NULL when the video record is deleted"),
        sa.Column(
            "user_pseudonym", sa.String(64), nullable=True,
            comment="HMAC_SHA256(JUGGLING_AUDIT_HASH_SECRET, str(user_id)) — never raw user_id",
        ),
        sa.Column("event_type", sa.String(50), nullable=False,
                  comment=(
                      "gdpr_delete | retention_expire | orphan_cleanup | "
                      "missing_file_audit | temp_cleanup | "
                      "dry_run_would_delete | scan_started | scan_completed"
                  )),
        sa.Column("file_type", sa.String(30), nullable=True,
                  comment="original | processed | thumbnail | temp | all"),
        sa.Column(
            "file_path_hash", sa.String(64), nullable=True,
            comment="HMAC_SHA256(JUGGLING_AUDIT_HASH_SECRET, raw_path) — never raw path",
        ),
        sa.Column("dry_run", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("success", sa.Boolean, nullable=True),
        sa.Column("error_message", sa.String(255), nullable=True),
        sa.Column("task_run_id", sa.String(36), nullable=True,
                  comment="Celery task ID for correlation"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["video_id"], ["juggling_videos.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_juggling_file_deletion_log_video_id",
        "juggling_file_deletion_log", ["video_id"],
    )
    op.create_index(
        "ix_juggling_file_deletion_log_created_at",
        "juggling_file_deletion_log", ["created_at"],
    )
    op.create_index(
        "ix_juggling_file_deletion_log_event_type",
        "juggling_file_deletion_log", ["event_type"],
    )


def downgrade() -> None:
    # Drop audit log table first (FK reference)
    op.drop_index("ix_juggling_file_deletion_log_event_type",
                  table_name="juggling_file_deletion_log")
    op.drop_index("ix_juggling_file_deletion_log_created_at",
                  table_name="juggling_file_deletion_log")
    op.drop_index("ix_juggling_file_deletion_log_video_id",
                  table_name="juggling_file_deletion_log")
    op.drop_table("juggling_file_deletion_log")

    # Restore indexes on juggling_videos
    op.drop_index("ix_juggling_videos_retention_expires_at",
                  table_name="juggling_videos")
    op.drop_index("ix_juggling_videos_deleted_at",
                  table_name="juggling_videos")

    # Restore status CHECK constraint without gdpr_deleted
    op.drop_constraint(_STATUS_CHECK, "juggling_videos", type_="check")
    op.create_check_constraint(
        _STATUS_CHECK,
        "juggling_videos",
        sa.column("status").in_(list(_OLD_STATUSES)),
    )

    # Drop new columns
    op.drop_column("juggling_videos", "retention_error")
    op.drop_column("juggling_videos", "retention_last_checked_at")
    op.drop_column("juggling_videos", "retention_expires_at")
    op.drop_column("juggling_videos", "deletion_reason")
    op.drop_column("juggling_videos", "deleted_at")
