"""add organizer FKs and CHECK constraint to semesters

Revision ID: 2026_05_02_1200
Revises: 2026_05_02_1100
Create Date: 2026-05-02 12:00:00.000000

P2-A Sponsors feature — Migration 2 of 2.
Adds to semesters:
  - organizer_club_id    FK → clubs(id) ON DELETE SET NULL
  - organizer_sponsor_id FK → sponsors(id) ON DELETE SET NULL
  - CHECK constraint: at most one organizer may be set at a time
"""
from alembic import op
import sqlalchemy as sa

revision = '2026_05_02_1200'
down_revision = '2026_05_02_1100'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('semesters', sa.Column('organizer_club_id', sa.Integer(), nullable=True))
    op.add_column('semesters', sa.Column('organizer_sponsor_id', sa.Integer(), nullable=True))

    op.create_foreign_key(
        'fk_semesters_organizer_club',
        'semesters', 'clubs',
        ['organizer_club_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_foreign_key(
        'fk_semesters_organizer_sponsor',
        'semesters', 'sponsors',
        ['organizer_sponsor_id'], ['id'],
        ondelete='SET NULL',
    )
    op.create_index('ix_semesters_organizer_club_id', 'semesters', ['organizer_club_id'])
    op.create_index('ix_semesters_organizer_sponsor_id', 'semesters', ['organizer_sponsor_id'])

    op.create_check_constraint(
        'chk_semester_single_organizer',
        'semesters',
        'organizer_club_id IS NULL OR organizer_sponsor_id IS NULL',
    )


def downgrade() -> None:
    op.drop_constraint('chk_semester_single_organizer', 'semesters', type_='check')
    op.drop_index('ix_semesters_organizer_club_id', table_name='semesters')
    op.drop_index('ix_semesters_organizer_sponsor_id', table_name='semesters')
    op.drop_constraint('fk_semesters_organizer_club', 'semesters', type_='foreignkey')
    op.drop_constraint('fk_semesters_organizer_sponsor', 'semesters', type_='foreignkey')
    op.drop_column('semesters', 'organizer_sponsor_id')
    op.drop_column('semesters', 'organizer_club_id')
