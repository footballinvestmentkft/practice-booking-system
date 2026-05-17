"""Add card_drafts table + backfill player_card rows from UserLicense

Revision ID: 2026_05_15_1100
Revises:     2026_05_14_1000
Create Date: 2026-05-15 11:00:00.000000

Introduces the card_drafts table as the future single source of truth for
per-user card draft and published state.

Phase 4D-1: schema + backfill only.  Routes still read/write UserLicense
legacy columns; Phase 4D-2 will switch the read/write paths.

Backfill logic (LFA_FOOTBALL_PLAYER licences only):
  draft_theme    <- COALESCE(card_theme,           'default')
  draft_variant  <- COALESCE(card_variant,         'fifa')
  draft_platform <- public_card_platform           (NULL preserved)
  published_theme    <- COALESCE(published_card_theme,  'default')
  published_variant  <- COALESCE(published_card_variant,'fifa')
  published_platform <- published_card_platform    (NULL preserved)
  published_at       <- NOW() if published_card_theme IS NOT NULL else NULL

ON CONFLICT DO NOTHING makes the backfill idempotent.
"""
from alembic import op
import sqlalchemy as sa

revision      = '2026_05_15_1100'
down_revision = '2026_05_14_1000'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.create_table(
        'card_drafts',

        sa.Column('id',           sa.Integer(), primary_key=True),
        sa.Column('user_id',      sa.Integer(),
                  sa.ForeignKey('users.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('card_type_id', sa.String(50),  nullable=False),
        sa.Column('instance_name',sa.String(100), nullable=False,
                  server_default='default'),

        # Draft selection state
        sa.Column('draft_theme',    sa.String(50),  nullable=False,
                  server_default='default'),
        sa.Column('draft_variant',  sa.String(50),  nullable=False,
                  server_default='fifa'),
        sa.Column('draft_platform', sa.String(50),  nullable=True),
        sa.Column('draft_data',     sa.JSON(),       nullable=True),

        # Published snapshot
        sa.Column('published_theme',    sa.String(50),  nullable=True),
        sa.Column('published_variant',  sa.String(50),  nullable=True),
        sa.Column('published_platform', sa.String(50),  nullable=True),
        sa.Column('published_data',     sa.JSON(),      nullable=True),
        sa.Column('published_at', sa.TIMESTAMP(timezone=True), nullable=True),

        # Audit timestamps
        sa.Column('created_at', sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text('NOW()')),

        sa.UniqueConstraint(
            'user_id', 'card_type_id', 'instance_name',
            name='uq_card_drafts_user_type_instance',
        ),
    )

    op.create_index(
        'ix_card_drafts_user_id',
        'card_drafts',
        ['user_id'],
    )

    # Backfill: one player_card draft row per LFA_FOOTBALL_PLAYER licence.
    # ON CONFLICT DO NOTHING makes this idempotent.
    op.execute("""
        INSERT INTO card_drafts (
            user_id,
            card_type_id,
            instance_name,
            draft_theme,
            draft_variant,
            draft_platform,
            published_theme,
            published_variant,
            published_platform,
            published_at,
            created_at,
            updated_at
        )
        SELECT
            ul.user_id,
            'player_card',
            'default',
            COALESCE(ul.card_theme,    'default'),
            COALESCE(ul.card_variant,  'fifa'),
            ul.public_card_platform,
            COALESCE(ul.published_card_theme,   'default'),
            COALESCE(ul.published_card_variant, 'fifa'),
            ul.published_card_platform,
            CASE WHEN ul.published_card_theme IS NOT NULL
                 THEN NOW()
                 ELSE NULL
            END,
            NOW(),
            NOW()
        FROM user_licenses ul
        WHERE ul.specialization_type = 'LFA_FOOTBALL_PLAYER'
        ON CONFLICT (user_id, card_type_id, instance_name) DO NOTHING
    """)


def downgrade() -> None:
    op.drop_index('ix_card_drafts_user_id', table_name='card_drafts')
    op.drop_table('card_drafts')
