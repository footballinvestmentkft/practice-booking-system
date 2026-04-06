"""add card_variant and unlocked_card_variants to user_licenses

Revision ID: 2026_04_06_1900
Revises: 2026_04_02_1300
Create Date: 2026-04-06 19:00:00.000000

Adds two columns to user_licenses to support the card variant (layout) system:
  - card_variant: active layout variant id (default: 'fifa')
  - unlocked_card_variants: list of premium variant ids unlocked by the user

These columns are orthogonal to card_theme / unlocked_card_themes (colour system).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "2026_04_06_1900"
down_revision = "2026_04_02_1300"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_licenses",
        sa.Column(
            "card_variant",
            sa.String(length=30),
            nullable=False,
            server_default="fifa",
            comment="Active player card layout variant (e.g. fifa, compact, showcase)",
        ),
    )
    op.add_column(
        "user_licenses",
        sa.Column(
            "unlocked_card_variants",
            postgresql.ARRAY(sa.String(length=30)),
            nullable=False,
            server_default="{}",
            comment="Premium variant IDs unlocked by this user (e.g. ['compact', 'showcase'])",
        ),
    )


def downgrade() -> None:
    op.drop_column("user_licenses", "unlocked_card_variants")
    op.drop_column("user_licenses", "card_variant")
