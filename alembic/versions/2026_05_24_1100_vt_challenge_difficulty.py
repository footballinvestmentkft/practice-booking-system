"""Add difficulty_level to vt_challenges

Allows Target Tracking challenges to carry a required difficulty level.
MS challenges leave this NULL.

Revision ID: 2026_05_24_1100
Revises:     2026_05_24_1000
Create Date: 2026-05-24 11:00:00
"""
import sqlalchemy as sa
from alembic import op

revision      = "2026_05_24_1100"
down_revision = "2026_05_24_1000"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column(
        "vt_challenges",
        sa.Column("difficulty_level", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vt_challenges", "difficulty_level")
