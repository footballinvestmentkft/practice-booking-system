"""add card_photo_portrait_url and card_photo_landscape_url to user_licenses

Revision ID: 2026_04_07_1000
Revises: 2026_04_06_1900
Create Date: 2026-04-07 10:00:00.000000

Adds two nullable photo URL columns to user_licenses for the compact (portrait 9:16)
and showcase (landscape 16:9) card variants. These are separate from the existing
player_card_photo_url (square JPEG used by the FIFA classic card).
"""

from alembic import op
import sqlalchemy as sa

revision = "2026_04_07_1000"
down_revision = "2026_04_06_1900"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_licenses",
        sa.Column("card_photo_portrait_url", sa.String(512), nullable=True,
                  comment="9:16 portrait PNG for Compact card variant"),
    )
    op.add_column(
        "user_licenses",
        sa.Column("card_photo_landscape_url", sa.String(512), nullable=True,
                  comment="16:9 landscape PNG for Showcase card variant"),
    )


def downgrade() -> None:
    op.drop_column("user_licenses", "card_photo_landscape_url")
    op.drop_column("user_licenses", "card_photo_portrait_url")
