"""add sponsors and sponsor_contacts tables

Revision ID: 2026_05_02_1100
Revises: 2026_05_02_1000
Create Date: 2026-05-02 11:00:00.000000

P2-A Sponsors feature — Migration 1 of 2.
Creates:
  - sponsors            (organizer/partner entity)
  - sponsor_contacts    (key contacts per sponsor)
  - partial unique index: uq_sponsor_primary_contact (only one primary per sponsor)
"""
from alembic import op
import sqlalchemy as sa

revision = '2026_05_02_1100'
down_revision = '2026_05_02_1000'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'sponsors',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('code', sa.String(20), nullable=False),
        sa.Column('brand_category', sa.String(50), nullable=True),
        sa.Column('city', sa.String(100), nullable=True),
        sa.Column('country', sa.String(50), nullable=True),
        sa.Column('contact_email', sa.String(200), nullable=True),
        sa.Column('website', sa.String(255), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('TRUE')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_sponsors_id', 'sponsors', ['id'])
    op.create_index('ix_sponsors_code', 'sponsors', ['code'], unique=True)

    op.create_table(
        'sponsor_contacts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sponsor_id', sa.Integer(), sa.ForeignKey('sponsors.id', ondelete='CASCADE'), nullable=False),
        sa.Column('name', sa.String(100), nullable=False),
        sa.Column('email', sa.String(200), nullable=True),
        sa.Column('phone', sa.String(50), nullable=True),
        sa.Column('role', sa.String(50), nullable=True),
        sa.Column('is_primary', sa.Boolean(), nullable=False, server_default=sa.text('FALSE')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_sponsor_contacts_id', 'sponsor_contacts', ['id'])
    op.create_index('ix_sponsor_contacts_sponsor_id', 'sponsor_contacts', ['sponsor_id'])
    # Only one primary contact per sponsor (partial unique index)
    op.execute(
        "CREATE UNIQUE INDEX uq_sponsor_primary_contact "
        "ON sponsor_contacts(sponsor_id) WHERE is_primary = TRUE"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_sponsor_primary_contact")
    op.drop_table('sponsor_contacts')
    op.drop_table('sponsors')
