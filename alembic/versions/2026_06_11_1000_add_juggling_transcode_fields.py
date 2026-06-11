"""add juggling transcode fields

Revision ID: 2026_06_11_1000
Revises: 2026_06_10_1300
Create Date: 2026-06-11

Adds P2 transcode + thumbnail columns to juggling_videos.

New columns:
  original_path           — copy of storage_path at migration time (filesystem path)
  processed_path          — ffmpeg output path after transcode (null when skipped/failed)
  thumbnail_path          — first-frame JPEG path (always populated after task runs)
  transcode_status        — pending | processing | done | skipped | failed  (CHECK)
  transcode_error         — last error message when transcode_status=failed
  audio_stripped          — True once audio has been removed from processed file
  processed_resolution    — WxH string of the processed file (null if skipped)
  processed_fps           — FPS of the processed file (null if skipped)
  processed_file_size_bytes — byte size of processed file (null if skipped)
  checksum_processed      — SHA-256 hex of processed file (null if skipped)

Backfill:
  original_path = storage_path  (for all existing rows)

Storage note:
  processed_path and thumbnail_path are NOT under app/static/.
  They are filesystem paths only — never returned to clients.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_06_11_1000"
down_revision = "2026_06_10_1300"
branch_labels = None
depends_on = None

_TRANSCODE_STATUSES = ("pending", "processing", "done", "skipped", "failed")
_CHECK_NAME = "ck_juggling_videos_transcode_status"


def upgrade() -> None:
    op.add_column(
        "juggling_videos",
        sa.Column("original_path", sa.String(512), nullable=True,
                  comment="Filesystem path of the original upload; mirrors storage_path"),
    )
    op.add_column(
        "juggling_videos",
        sa.Column("processed_path", sa.String(512), nullable=True,
                  comment="Filesystem path of the ffmpeg-processed file; null if skipped or failed"),
    )
    op.add_column(
        "juggling_videos",
        sa.Column("thumbnail_path", sa.String(512), nullable=True,
                  comment="Filesystem path of the first-frame JPEG thumbnail"),
    )
    op.add_column(
        "juggling_videos",
        sa.Column(
            "transcode_status",
            sa.String(20),
            nullable=True,
            server_default="pending",
            comment="pending | processing | done | skipped | failed",
        ),
    )
    op.add_column(
        "juggling_videos",
        sa.Column("transcode_error", sa.String(512), nullable=True,
                  comment="Last error message when transcode_status=failed"),
    )
    op.add_column(
        "juggling_videos",
        sa.Column("audio_stripped", sa.Boolean, nullable=True,
                  comment="True once audio has been removed from the processed file"),
    )
    op.add_column(
        "juggling_videos",
        sa.Column("processed_resolution", sa.String(20), nullable=True,
                  comment="WxH of the processed file; null if skipped"),
    )
    op.add_column(
        "juggling_videos",
        sa.Column("processed_fps", sa.Float, nullable=True,
                  comment="FPS of the processed file; null if skipped"),
    )
    op.add_column(
        "juggling_videos",
        sa.Column("processed_file_size_bytes", sa.BigInteger, nullable=True,
                  comment="Byte size of the processed file; null if skipped"),
    )
    op.add_column(
        "juggling_videos",
        sa.Column("checksum_processed", sa.String(64), nullable=True,
                  comment="SHA-256 hex digest of the processed file; null if skipped"),
    )

    # CHECK constraint on transcode_status
    op.create_check_constraint(
        _CHECK_NAME,
        "juggling_videos",
        sa.column("transcode_status").in_(list(_TRANSCODE_STATUSES)),
    )

    # Backfill: original_path = storage_path for all existing rows
    op.execute(
        "UPDATE juggling_videos SET original_path = storage_path "
        "WHERE storage_path IS NOT NULL"
    )


def downgrade() -> None:
    op.drop_constraint(_CHECK_NAME, "juggling_videos", type_="check")
    op.drop_column("juggling_videos", "checksum_processed")
    op.drop_column("juggling_videos", "processed_file_size_bytes")
    op.drop_column("juggling_videos", "processed_fps")
    op.drop_column("juggling_videos", "processed_resolution")
    op.drop_column("juggling_videos", "audio_stripped")
    op.drop_column("juggling_videos", "transcode_error")
    op.drop_column("juggling_videos", "transcode_status")
    op.drop_column("juggling_videos", "thumbnail_path")
    op.drop_column("juggling_videos", "processed_path")
    op.drop_column("juggling_videos", "original_path")