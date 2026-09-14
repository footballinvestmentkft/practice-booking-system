"""PC6B direct capture upload replay boundary.

Revision ID: 2026_09_14_1000
Revises: 2026_09_13_3000
"""
from alembic import op
import sqlalchemy as sa


revision = "2026_09_14_1000"
down_revision = "2026_09_13_3000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "juggling_videos",
        sa.Column("upload_idempotency_key", sa.String(64), nullable=True),
    )
    op.create_unique_constraint(
        "uq_juggling_video_user_upload_idempotency",
        "juggling_videos",
        ["user_id", "upload_idempotency_key"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_juggling_video_user_upload_idempotency",
        "juggling_videos",
        type_="unique",
    )
    op.drop_column("juggling_videos", "upload_idempotency_key")
