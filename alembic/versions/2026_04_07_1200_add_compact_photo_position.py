"""add card_compact_photo_position to user_licenses

Revision ID: 2026_04_07_1200
Revises: 2026_04_07_1100
Create Date: 2026-04-07 12:00:00.000000

Adds a column to store the photo sidebar position for the Compact card variant.
Values: 'left' (default) or 'right'.
"""

from alembic import op
import sqlalchemy as sa

revision = "2026_04_07_1200"
down_revision = "2026_04_07_1100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_licenses",
        sa.Column(
            "card_compact_photo_position",
            sa.String(10),
            nullable=False,
            server_default="left",
            comment="Compact card photo sidebar position: 'left' or 'right'",
        ),
    )


def downgrade() -> None:
    op.drop_column("user_licenses", "card_compact_photo_position")
