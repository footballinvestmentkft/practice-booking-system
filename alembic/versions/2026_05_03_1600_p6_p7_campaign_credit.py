"""P6+P7: Campaign specialization + credit ledger columns

Revision ID: 2026_05_03_1600
Revises: 2026_05_03_1500
Create Date: 2026-05-03 16:00:00.000000

P6 — sponsor_campaigns gains specialization_type so each campaign targets one license type.
P7 — sponsor_campaigns gains credit config (grant_amount, unlock_cost) for the sponsor-funded
     unlock flow.  credit_transactions gains sponsor_id + campaign_id for settlement tracing.

Changes:
  sponsor_campaigns  — ADD specialization_type VARCHAR(50) NOT NULL DEFAULT 'LFA_FOOTBALL_PLAYER'
  sponsor_campaigns  — ADD credit_grant_amount  INTEGER     NOT NULL DEFAULT 100
  sponsor_campaigns  — ADD unlock_cost          INTEGER     NOT NULL DEFAULT 100
  sponsor_campaigns  — ADD CONSTRAINT chk_campaign_min_grant    (credit_grant_amount >= 100)
  sponsor_campaigns  — ADD CONSTRAINT chk_campaign_unlock_lte   (unlock_cost <= credit_grant_amount)
  credit_transactions — ADD sponsor_id   INTEGER NULL FK → sponsors.id   ON DELETE SET NULL
  credit_transactions — ADD campaign_id  INTEGER NULL FK → sponsor_campaigns.id ON DELETE SET NULL
"""
from alembic import op
import sqlalchemy as sa


revision = '2026_05_03_1600'
down_revision = '2026_05_03_1500'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── sponsor_campaigns: P6 specialization + P7 credit config ──────────────
    op.add_column(
        'sponsor_campaigns',
        sa.Column('specialization_type', sa.String(50), nullable=False,
                  server_default='LFA_FOOTBALL_PLAYER'),
    )
    op.add_column(
        'sponsor_campaigns',
        sa.Column('credit_grant_amount', sa.Integer, nullable=False, server_default='100'),
    )
    op.add_column(
        'sponsor_campaigns',
        sa.Column('unlock_cost', sa.Integer, nullable=False, server_default='100'),
    )
    op.create_check_constraint(
        'chk_campaign_min_grant',
        'sponsor_campaigns',
        'credit_grant_amount >= 100',
    )
    op.create_check_constraint(
        'chk_campaign_unlock_lte',
        'sponsor_campaigns',
        'unlock_cost <= credit_grant_amount',
    )

    # ── credit_transactions: P7 settlement columns ────────────────────────────
    op.add_column(
        'credit_transactions',
        sa.Column(
            'sponsor_id',
            sa.Integer,
            sa.ForeignKey('sponsors.id', ondelete='SET NULL'),
            nullable=True,
            index=True,
        ),
    )
    op.add_column(
        'credit_transactions',
        sa.Column(
            'campaign_id',
            sa.Integer,
            sa.ForeignKey('sponsor_campaigns.id', ondelete='SET NULL'),
            nullable=True,
            index=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('credit_transactions', 'campaign_id')
    op.drop_column('credit_transactions', 'sponsor_id')

    op.drop_constraint('chk_campaign_unlock_lte', 'sponsor_campaigns', type_='check')
    op.drop_constraint('chk_campaign_min_grant', 'sponsor_campaigns', type_='check')
    op.drop_column('sponsor_campaigns', 'unlock_cost')
    op.drop_column('sponsor_campaigns', 'credit_grant_amount')
    op.drop_column('sponsor_campaigns', 'specialization_type')
