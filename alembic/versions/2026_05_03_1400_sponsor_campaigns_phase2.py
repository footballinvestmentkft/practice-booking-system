"""P3: SponsorCampaign — Phase 2 (constraint swap + NOT NULL)

Revision ID: 2026_05_03_1400
Revises: 2026_05_03_1300
Create Date: 2026-05-03 14:00:00.000000

DEPLOY ORDER (mandatory):
  1. Run phase 1 migration (2026_05_03_1300)
  2. Deploy campaign-aware application code (apply_import requires campaign_id)
  3. Manual QA: create campaign, import CSV, verify audience list
  4. Run THIS migration (phase 2)

Steps (single DDL transaction — atomic in PostgreSQL):
  M7  ADD UNIQUE (campaign_id, email) — verify before dropping old constraint
  M8  DROP UNIQUE (sponsor_id, email) — old global constraint
  M9  ALTER sponsor_audience_entries.campaign_id SET NOT NULL

Rollback: see downgrade() — restores old constraint (safe because no
(sponsor_id, email) duplicates can have been created between phases).
"""
from alembic import op
import sqlalchemy as sa

revision = '2026_05_03_1400'
down_revision = '2026_05_03_1300'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # M7: Add new unique constraint FIRST (will fail fast if data is inconsistent)
    op.create_unique_constraint(
        'uq_campaign_entry_email',
        'sponsor_audience_entries',
        ['campaign_id', 'email'],
    )

    # M8: Drop the old global (sponsor_id, email) constraint
    op.drop_constraint(
        'uq_sponsor_audience_email',
        'sponsor_audience_entries',
        type_='unique',
    )

    # M9: Enforce NOT NULL — safe because M6 validated zero NULLs
    op.alter_column(
        'sponsor_audience_entries',
        'campaign_id',
        nullable=False,
        existing_type=sa.Integer(),
    )


def downgrade() -> None:
    # Reverse M9
    op.alter_column(
        'sponsor_audience_entries',
        'campaign_id',
        nullable=True,
        existing_type=sa.Integer(),
    )

    # Reverse M8 — restore old global unique (safe: no sponsor+email dupes exist)
    op.create_unique_constraint(
        'uq_sponsor_audience_email',
        'sponsor_audience_entries',
        ['sponsor_id', 'email'],
    )

    # Reverse M7
    op.drop_constraint(
        'uq_campaign_entry_email',
        'sponsor_audience_entries',
        type_='unique',
    )
