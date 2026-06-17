"""Add auto_ball_x / auto_ball_y to juggling_ball_detections (AN-3B2C-1 Opció A).

Preserves the original automatic model coordinates on the first manual override
so that auto-vs-manual position delta can be measured for model validation.

Revision ID: 2026_06_18_1300
Revises: 2026_06_18_1200
"""
from alembic import op
import sqlalchemy as sa

revision      = "2026_06_18_1300"
down_revision = "2026_06_18_1200"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column(
        "juggling_ball_detections",
        sa.Column("auto_ball_x", sa.Float(), nullable=True),
    )
    op.add_column(
        "juggling_ball_detections",
        sa.Column("auto_ball_y", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("juggling_ball_detections", "auto_ball_y")
    op.drop_column("juggling_ball_detections", "auto_ball_x")
