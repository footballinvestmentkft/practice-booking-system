"""add card_bg_compact_url and card_bg_showcase_url to user_licenses

Revision ID: 2026_04_07_1300
Revises: 2026_04_07_1200
Create Date: 2026-04-07 13:00:00.000000

Adds two nullable URL columns to user_licenses for the background photos
of the Compact+BG and Showcase+BG card variants.
"""

from alembic import op
import sqlalchemy as sa

revision = "2026_04_07_1300"
down_revision = "2026_04_07_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_licenses",
        sa.Column("card_bg_compact_url", sa.String(512), nullable=True,
                  comment="Background photo URL for the Compact+BG card variant"),
    )
    op.add_column(
        "user_licenses",
        sa.Column("card_bg_showcase_url", sa.String(512), nullable=True,
                  comment="Background photo URL for the Showcase+BG card variant"),
    )


def downgrade() -> None:
    op.drop_column("user_licenses", "card_bg_showcase_url")
    op.drop_column("user_licenses", "card_bg_compact_url")
