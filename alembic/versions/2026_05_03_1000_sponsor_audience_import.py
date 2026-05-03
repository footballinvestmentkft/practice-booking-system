"""Sponsor audience import — csv_import_logs.sponsor_id + sponsor_audience_entries

Revision ID: 2026_05_03_1000
Revises: 2026_05_02_1200
Create Date: 2026-05-03 10:00:00.000000

P2-B Sponsor Audience Import — Migration.
Changes:
  - csv_import_logs: add sponsor_id nullable FK (existing rows keep NULL)
  - sponsor_audience_entries: new table (sponsor audience/prospect records)

Down is fully reversible: no existing data is touched.
"""
from alembic import op
import sqlalchemy as sa

revision = '2026_05_03_1000'
down_revision = '2026_05_02_1200'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── csv_import_logs: add sponsor_id FK ──────────────────────────────────
    op.add_column(
        'csv_import_logs',
        sa.Column('sponsor_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_csv_import_logs_sponsor',
        'csv_import_logs', 'sponsors',
        ['sponsor_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_index('ix_csv_import_logs_sponsor_id', 'csv_import_logs', ['sponsor_id'])

    # ── sponsor_audience_entries ─────────────────────────────────────────────
    op.create_table(
        'sponsor_audience_entries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sponsor_id', sa.Integer(), nullable=False),
        sa.Column('import_log_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=True),

        # Identity
        sa.Column('first_name', sa.String(100), nullable=False),
        sa.Column('last_name',  sa.String(100), nullable=False),
        sa.Column('email',      sa.String(200), nullable=False),
        sa.Column('phone',      sa.String(50),  nullable=True),
        sa.Column('date_of_birth', sa.Date(),   nullable=True),

        # Segmentation
        sa.Column('age_category',   sa.String(20),  nullable=True),
        sa.Column('age_raw',        sa.String(30),  nullable=True),
        sa.Column('target_segment', sa.String(200), nullable=True),
        sa.Column('campaign_source',sa.String(200), nullable=True),

        # Consent
        sa.Column('consent_given',  sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('consent_source', sa.String(300), nullable=True),

        # Parental
        sa.Column('parent_email', sa.String(200), nullable=True),

        # Status + internal
        sa.Column('status', sa.String(20), nullable=False, server_default='SUPPRESSED'),
        sa.Column('notes',  sa.Text(),     nullable=True),

        # Audit
        sa.Column('imported_at',      sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('last_imported_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('imported_by', sa.Integer(), nullable=True),

        # PK
        sa.PrimaryKeyConstraint('id'),

        # FKs
        sa.ForeignKeyConstraint(['sponsor_id'],    ['sponsors.id'],  ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['import_log_id'], ['csv_import_logs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'],       ['users.id'],     ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['imported_by'],   ['users.id'],     ondelete='SET NULL'),
    )
    op.create_index('ix_sponsor_audience_sponsor_id',
                    'sponsor_audience_entries', ['sponsor_id'])
    op.create_index('ix_sponsor_audience_import_log_id',
                    'sponsor_audience_entries', ['import_log_id'])
    op.create_index('ix_sponsor_audience_user_id',
                    'sponsor_audience_entries', ['user_id'])
    op.create_unique_constraint(
        'uq_sponsor_audience_email',
        'sponsor_audience_entries', ['sponsor_id', 'email'],
    )


def downgrade() -> None:
    op.drop_table('sponsor_audience_entries')
    op.drop_index('ix_csv_import_logs_sponsor_id', table_name='csv_import_logs')
    op.drop_constraint('fk_csv_import_logs_sponsor', 'csv_import_logs', type_='foreignkey')
    op.drop_column('csv_import_logs', 'sponsor_id')
