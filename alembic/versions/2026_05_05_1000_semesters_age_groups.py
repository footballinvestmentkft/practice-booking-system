"""semesters.age_groups JSONB — multi-age promotion event support

Revision ID: 2026_05_05_1000
Revises: 2026_05_03_1600
Create Date: 2026-05-05 10:00:00.000000

Domain: sponsor promotion events must create a single event for multiple age categories.
        The new age_groups JSONB column stores the full list; age_group (String) is kept
        for backward compat (single-age semesters and existing queries).

Changes:
  semesters — ADD COLUMN age_groups JSONB NULL
              (no backfill — get_allowed_age_groups() fallback covers existing rows)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '2026_05_05_1000'
down_revision = '2026_05_03_1600'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'semesters',
        sa.Column(
            'age_groups',
            JSONB,
            nullable=True,
            comment='Multi-age eligibility list e.g. ["PRE","YOUTH"]. '
                    'Overrides age_group when set. '
                    'Populated by sponsor promotion when >1 age selected.',
        ),
    )


def downgrade() -> None:
    op.drop_column('semesters', 'age_groups')
