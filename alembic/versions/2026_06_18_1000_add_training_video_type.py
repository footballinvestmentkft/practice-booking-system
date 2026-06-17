"""Add training_video_type column to juggling_videos.

Supports type-aware analysis pipeline: juggling, gan_footvolley, gan_foottennis.
Existing rows get server_default 'juggling' (instant, no table rewrite on PG 11+).

Revision ID: 2026_06_18_1000
Revises: 2026_06_17_1200
"""
from alembic import op
import sqlalchemy as sa

revision      = "2026_06_18_1000"
down_revision = "2026_06_17_1200"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column(
        "juggling_videos",
        sa.Column(
            "training_video_type",
            sa.String(30),
            nullable=False,
            server_default="juggling",
            comment="Training activity type: juggling | gan_footvolley | gan_foottennis",
        ),
    )
    op.create_check_constraint(
        "ck_juggling_videos_training_video_type",
        "juggling_videos",
        "training_video_type IN ('juggling','gan_footvolley','gan_foottennis')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_juggling_videos_training_video_type",
        "juggling_videos",
        type_="check",
    )
    op.drop_column("juggling_videos", "training_video_type")
