"""Add challenger_card_photo_url and challenged_card_photo_url to vt_challenges.

Per-challenge photo snapshot for Challenge Invitation cards (CC-DESIGN-1).
Each participant can independently save their chosen mood photo for the card.
NULL = fallback to mood_intro_neutral at render time.

Revision ID: 2026_06_02_1000
Revises:     2026_05_31_1000
"""
import sqlalchemy as sa
from alembic import op

revision = "2026_06_02_1000"
down_revision = "2026_05_31_1000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vt_challenges",
        sa.Column("challenger_card_photo_url", sa.String(512), nullable=True),
    )
    op.add_column(
        "vt_challenges",
        sa.Column("challenged_card_photo_url", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("vt_challenges", "challenged_card_photo_url")
    op.drop_column("vt_challenges", "challenger_card_photo_url")
