"""P3: SponsorCampaign — Phase 1 (structural + backfill, no constraint change)

Revision ID: 2026_05_03_1300
Revises: 2026_05_03_1200
Create Date: 2026-05-03 13:00:00.000000

Steps:
  M1  CREATE sponsor_campaigns
  M2  ADD csv_import_logs.campaign_id (nullable FK)
  M3  ADD sponsor_audience_entries.campaign_id (nullable FK)
  M4  Backfill: one SponsorCampaign per existing csv_import_log with sponsor_id
  M5  Backfill: csv_import_logs.campaign_id from step M4
  M6  Backfill: sponsor_audience_entries.campaign_id via import_log_id JOIN
  Validate: abort if any entry with sponsor_id still has campaign_id IS NULL

Phase 2 (constraint swap + NOT NULL) is a separate migration that must run
AFTER the new campaign-aware code is deployed.

Down: fully reversible — drops all new columns and the new table.
      Old unique constraint uq_sponsor_audience_email is NOT touched here.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision = '2026_05_03_1300'
down_revision = '2026_05_03_1200'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── M1: CREATE sponsor_campaigns ────────────────────────────────────────────
    op.create_table(
        'sponsor_campaigns',
        sa.Column('id',            sa.Integer(),     nullable=False),
        sa.Column('sponsor_id',    sa.Integer(),     nullable=False),
        sa.Column('name',          sa.String(200),   nullable=False),
        sa.Column('campaign_type', sa.String(30),    nullable=False, server_default='IMPORT'),
        sa.Column('event_date',    sa.Date(),        nullable=True),
        sa.Column('status',        sa.String(20),    nullable=False, server_default='ACTIVE'),
        sa.Column('semester_id',   sa.Integer(),     nullable=True),
        sa.Column('notes',         sa.Text(),        nullable=True),
        sa.Column('created_at',    sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=False),
        sa.Column('created_by',    sa.Integer(),     nullable=True),

        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['sponsor_id'],  ['sponsors.id'],  ondelete='CASCADE',
                                name='fk_sponsor_campaigns_sponsor'),
        sa.ForeignKeyConstraint(['semester_id'], ['semesters.id'], ondelete='SET NULL',
                                name='fk_sponsor_campaigns_semester'),
        sa.ForeignKeyConstraint(['created_by'],  ['users.id'],     ondelete='SET NULL',
                                name='fk_sponsor_campaigns_creator'),
    )
    op.create_index('ix_sponsor_campaigns_sponsor_id',  'sponsor_campaigns', ['sponsor_id'])
    op.create_index('ix_sponsor_campaigns_semester_id', 'sponsor_campaigns', ['semester_id'])

    # ── M2: ADD csv_import_logs.campaign_id (nullable) ──────────────────────────
    op.add_column(
        'csv_import_logs',
        sa.Column('campaign_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_csv_import_logs_campaign',
        'csv_import_logs', 'sponsor_campaigns',
        ['campaign_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_index('ix_csv_import_logs_campaign_id', 'csv_import_logs', ['campaign_id'])

    # ── M3: ADD sponsor_audience_entries.campaign_id (nullable) ─────────────────
    op.add_column(
        'sponsor_audience_entries',
        sa.Column('campaign_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_sae_campaign',
        'sponsor_audience_entries', 'sponsor_campaigns',
        ['campaign_id'], ['id'],
        ondelete='CASCADE',
    )
    op.create_index('ix_sponsor_audience_campaign_id',
                    'sponsor_audience_entries', ['campaign_id'])

    # ── M4 + M5: Backfill sponsor_campaigns from csv_import_logs ────────────────
    # One campaign per import log (name = filename, IMPORT type).
    # We iterate in Python so we can capture the RETURNING id for each row.
    logs = conn.execute(text(
        "SELECT id, sponsor_id, filename, uploaded_at, uploaded_by "
        "FROM csv_import_logs "
        "WHERE sponsor_id IS NOT NULL "
        "ORDER BY id"
    )).fetchall()

    for log_row in logs:
        campaign_name = log_row.filename or f"Import {log_row.id}"
        result = conn.execute(text(
            "INSERT INTO sponsor_campaigns "
            "  (sponsor_id, name, campaign_type, status, created_at, created_by, notes) "
            "VALUES "
            "  (:sid, :name, 'IMPORT', 'ACTIVE', "
            "   COALESCE(:ts, now()), :created_by, :notes) "
            "RETURNING id"
        ), {
            "sid":        log_row.sponsor_id,
            "name":       campaign_name,
            "ts":         log_row.uploaded_at,
            "created_by": log_row.uploaded_by,
            "notes":      f"Auto-created from import log #{log_row.id}",
        })
        campaign_id = result.scalar()
        conn.execute(text(
            "UPDATE csv_import_logs SET campaign_id = :cid WHERE id = :lid"
        ), {"cid": campaign_id, "lid": log_row.id})

    # ── M6: Backfill sponsor_audience_entries.campaign_id ───────────────────────
    conn.execute(text(
        "UPDATE sponsor_audience_entries sae "
        "SET campaign_id = cl.campaign_id "
        "FROM csv_import_logs cl "
        "WHERE sae.import_log_id = cl.id"
    ))

    # ── Validate (abort gate) ───────────────────────────────────────────────────
    null_count = conn.execute(text(
        "SELECT COUNT(*) FROM sponsor_audience_entries "
        "WHERE campaign_id IS NULL AND sponsor_id IS NOT NULL"
    )).scalar()
    if null_count:
        raise RuntimeError(
            f"M6 abort gate: {null_count} audience entries still have NULL campaign_id "
            "after backfill.  Investigate csv_import_logs.sponsor_id consistency before "
            "proceeding with phase 2."
        )


def downgrade() -> None:
    op.drop_index('ix_sponsor_audience_campaign_id', table_name='sponsor_audience_entries')
    op.drop_constraint('fk_sae_campaign', 'sponsor_audience_entries', type_='foreignkey')
    op.drop_column('sponsor_audience_entries', 'campaign_id')

    op.drop_index('ix_csv_import_logs_campaign_id', table_name='csv_import_logs')
    op.drop_constraint('fk_csv_import_logs_campaign', 'csv_import_logs', type_='foreignkey')
    op.drop_column('csv_import_logs', 'campaign_id')

    op.drop_index('ix_sponsor_campaigns_semester_id', table_name='sponsor_campaigns')
    op.drop_index('ix_sponsor_campaigns_sponsor_id',  table_name='sponsor_campaigns')
    op.drop_table('sponsor_campaigns')
