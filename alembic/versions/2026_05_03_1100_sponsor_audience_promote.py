"""Sponsor audience promote — promoted_at + promoted_by on sponsor_audience_entries

Revision ID: 2026_05_03_1100
Revises: 2026_05_03_1000
Create Date: 2026-05-03 11:00:00.000000

P2-C Promote to User — audit columns.
  - sponsor_audience_entries.promoted_at  TIMESTAMPTZ NULL
  - sponsor_audience_entries.promoted_by  INTEGER NULL FK → users.id SET NULL
"""
from alembic import op
import sqlalchemy as sa

revision = '2026_05_03_1100'
down_revision = '2026_05_03_1000'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'sponsor_audience_entries',
        sa.Column('promoted_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'sponsor_audience_entries',
        sa.Column('promoted_by', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_sae_promoted_by',
        'sponsor_audience_entries', 'users',
        ['promoted_by'], ['id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_sae_promoted_by', 'sponsor_audience_entries', type_='foreignkey')
    op.drop_column('sponsor_audience_entries', 'promoted_by')
    op.drop_column('sponsor_audience_entries', 'promoted_at')
