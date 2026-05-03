"""Sponsor audience — position + foot_dominance columns

Revision ID: 2026_05_03_1200
Revises: 2026_05_03_1100
Create Date: 2026-05-03 12:00:00.000000

P2-D: tournament-ready baseline fields on sponsor_audience_entries.
  - position       VARCHAR(30) NULL — canonical STRIKER/MIDFIELDER/DEFENDER/GOALKEEPER
  - foot_dominance SMALLINT    NULL — 0=left, 100=right; NULL → 50 default at promote time
"""
from alembic import op
import sqlalchemy as sa

revision = '2026_05_03_1200'
down_revision = '2026_05_03_1100'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'sponsor_audience_entries',
        sa.Column('position', sa.String(30), nullable=True),
    )
    op.add_column(
        'sponsor_audience_entries',
        sa.Column('foot_dominance', sa.SmallInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('sponsor_audience_entries', 'foot_dominance')
    op.drop_column('sponsor_audience_entries', 'position')
