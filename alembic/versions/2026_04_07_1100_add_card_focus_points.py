"""add card photo focus point coordinates to user_licenses

Revision ID: 2026_04_07_1100
Revises: 2026_04_07_1000
Create Date: 2026-04-07 11:00:00.000000

Adds four nullable integer columns to user_licenses for the compact and showcase
card variants' photo focus point (0-100 percentage, default 50 = centre).
These drive the CSS object-position property on the photo zone of each variant.
"""

from alembic import op
import sqlalchemy as sa

revision = "2026_04_07_1100"
down_revision = "2026_04_07_1000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_licenses",
        sa.Column("card_compact_focus_x", sa.Integer(), nullable=True, server_default="50"),
    )
    op.add_column(
        "user_licenses",
        sa.Column("card_compact_focus_y", sa.Integer(), nullable=True, server_default="50"),
    )
    op.add_column(
        "user_licenses",
        sa.Column("card_showcase_focus_x", sa.Integer(), nullable=True, server_default="50"),
    )
    op.add_column(
        "user_licenses",
        sa.Column("card_showcase_focus_y", sa.Integer(), nullable=True, server_default="50"),
    )


def downgrade() -> None:
    op.drop_column("user_licenses", "card_showcase_focus_y")
    op.drop_column("user_licenses", "card_showcase_focus_x")
    op.drop_column("user_licenses", "card_compact_focus_y")
    op.drop_column("user_licenses", "card_compact_focus_x")
