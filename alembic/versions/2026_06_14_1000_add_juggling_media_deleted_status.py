"""add juggling media_deleted status

Revision ID: 2026_06_14_1000
Revises: 2026_06_13_1000
Create Date: 2026-06-14

Extends the ck_juggling_videos_status CHECK constraint to include 'media_deleted'.

media_deleted semantics:
  User-initiated media file deletion. Physical files removed, but analysis results,
  quality data, annotation_status, total_juggling_count, and contact events are
  all preserved. Distinct from gdpr_deleted (which nulls all personal data).

  deletion_reason = "user_request" for user-initiated deletes.
  Contrast with gdpr_deleted: deletion_reason = "gdpr_request" | "retention_expired" | etc.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_06_14_1000"
down_revision = "2026_06_13_1000"
branch_labels = None
depends_on = None

_STATUS_CHECK = "ck_juggling_videos_status"

_OLD_STATUSES = (
    "pending_upload", "uploaded", "processing",
    "analyzed", "rejected", "failed",
    "gdpr_deleted",
)
_NEW_STATUSES = _OLD_STATUSES + ("media_deleted",)


def upgrade() -> None:
    op.execute(f"ALTER TABLE juggling_videos DROP CONSTRAINT IF EXISTS {_STATUS_CHECK}")
    op.create_check_constraint(
        _STATUS_CHECK,
        "juggling_videos",
        sa.column("status").in_(list(_NEW_STATUSES)),
    )


def downgrade() -> None:
    op.execute(f"ALTER TABLE juggling_videos DROP CONSTRAINT IF EXISTS {_STATUS_CHECK}")
    op.create_check_constraint(
        _STATUS_CHECK,
        "juggling_videos",
        sa.column("status").in_(list(_OLD_STATUSES)),
    )
