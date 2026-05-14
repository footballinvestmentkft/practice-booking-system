"""Published card state — separate draft from public for theme/variant/platform

Revision ID: 2026_05_14_1000
Revises: 2026_05_13_1000
Create Date: 2026-05-14 10:00:00.000000

Introduces three new columns on user_licenses to store the explicitly
published public card state, separate from the editor draft state.

  Draft (editor)   → card_theme / card_variant / public_card_platform
  Published (public) → published_card_theme / published_card_variant
                       / published_card_platform

The public card route now reads from published_* fields only.  A user
must press "Publish Card" in the editor to push their draft to public.

Backfill sets published = current draft so existing users see no change.
"""
from alembic import op
import sqlalchemy as sa

revision = '2026_05_14_1000'
down_revision = '2026_05_13_1000'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'user_licenses',
        sa.Column('published_card_theme', sa.String(50), nullable=True,
                  server_default='default',
                  comment="Published public card theme ID (stable, user-controlled)"),
    )
    op.add_column(
        'user_licenses',
        sa.Column('published_card_variant', sa.String(50), nullable=True,
                  server_default='fifa',
                  comment="Published public card variant ID (stable, user-controlled)"),
    )
    op.add_column(
        'user_licenses',
        sa.Column('published_card_platform', sa.String(50), nullable=True,
                  comment="Published public card platform ID (NULL = default; stable)"),
    )

    # Backfill: published = current draft so no existing user's public card changes.
    op.execute("""
        UPDATE user_licenses
        SET
            published_card_theme    = COALESCE(card_theme,    'default'),
            published_card_variant  = COALESCE(card_variant,  'fifa'),
            published_card_platform = public_card_platform
    """)


def downgrade() -> None:
    op.drop_column('user_licenses', 'published_card_platform')
    op.drop_column('user_licenses', 'published_card_variant')
    op.drop_column('user_licenses', 'published_card_theme')
