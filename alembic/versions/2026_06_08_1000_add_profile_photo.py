"""Add profile photo columns to users table (Academy ID Phase 1).

Three nullable VARCHAR columns — no backfill required.
NULL is treated as status="none" (no profile photo) by the application.

Background removal pipeline mirrors mood photo flow:
  status: none → uploaded → processing → ready/failed
  processed_url: NULL until BG_REMOVAL_PROCESSOR=rembg produces a transparent PNG.

Revision ID: 2026_06_08_1000
Revises:     2026_06_05_1000
Create Date: 2026-06-08 10:00:00
"""
import sqlalchemy as sa
from alembic import op

revision      = "2026_06_08_1000"
down_revision = "2026_06_05_1000"
branch_labels = None
depends_on    = None

_TABLE = "users"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            "profile_photo_url",
            sa.String(512),
            nullable=True,
            comment="Raw uploaded profile photo URL (app/static/uploads/profile_photos/)",
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "profile_photo_processed_url",
            sa.String(512),
            nullable=True,
            comment="Background-removed transparent PNG — NULL until rembg processor runs",
        ),
    )
    op.add_column(
        _TABLE,
        sa.Column(
            "profile_photo_status",
            sa.String(20),
            nullable=True,
            comment="none/uploaded/processing/ready/failed — NULL treated as none",
        ),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "profile_photo_status")
    op.drop_column(_TABLE, "profile_photo_processed_url")
    op.drop_column(_TABLE, "profile_photo_url")
