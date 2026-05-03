"""P4: Promotion event ↔ campaign link

Revision ID: 2026_05_03_1500
Revises: 2026_05_03_1400
Create Date: 2026-05-03 15:00:00.000000

Domain: sponsor promotion events must reference the campaign whose audience feeds them.

Changes:
  semesters               — ADD organizer_campaign_id INT NULL FK → sponsor_campaigns.id (SET NULL)
  semesters               — ADD CONSTRAINT chk_campaign_requires_sponsor
                             (organizer_campaign_id IS NULL OR organizer_sponsor_id IS NOT NULL)
  sponsor_campaigns       — DROP COLUMN semester_id  (wrong-direction FK, replaced by semesters.organizer_campaign_id)
"""
from alembic import op
import sqlalchemy as sa


revision = '2026_05_03_1500'
down_revision = '2026_05_03_1400'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add organizer_campaign_id to semesters
    op.add_column(
        'semesters',
        sa.Column(
            'organizer_campaign_id',
            sa.Integer(),
            sa.ForeignKey('sponsor_campaigns.id', ondelete='SET NULL'),
            nullable=True,
        ),
    )
    op.create_index(
        'ix_semesters_organizer_campaign_id',
        'semesters',
        ['organizer_campaign_id'],
    )

    # 2. DB-level guard: campaign requires sponsor
    op.create_check_constraint(
        'chk_campaign_requires_sponsor',
        'semesters',
        'organizer_campaign_id IS NULL OR organizer_sponsor_id IS NOT NULL',
    )

    # 3. Drop the wrong-direction FK from sponsor_campaigns
    op.drop_constraint(
        'fk_sponsor_campaigns_semester',
        'sponsor_campaigns',
        type_='foreignkey',
    )
    op.drop_index('ix_sponsor_campaigns_semester_id', table_name='sponsor_campaigns')
    op.drop_column('sponsor_campaigns', 'semester_id')


def downgrade() -> None:
    # Reverse 3: restore semester_id on sponsor_campaigns
    op.add_column(
        'sponsor_campaigns',
        sa.Column('semester_id', sa.Integer(), nullable=True),
    )
    op.create_index(
        'ix_sponsor_campaigns_semester_id',
        'sponsor_campaigns',
        ['semester_id'],
    )
    op.create_foreign_key(
        'fk_sponsor_campaigns_semester',
        'sponsor_campaigns',
        'semesters',
        ['semester_id'],
        ['id'],
        ondelete='SET NULL',
    )

    # Reverse 2
    op.drop_constraint('chk_campaign_requires_sponsor', 'semesters', type_='check')

    # Reverse 1
    op.drop_index('ix_semesters_organizer_campaign_id', table_name='semesters')
    op.drop_column('semesters', 'organizer_campaign_id')
