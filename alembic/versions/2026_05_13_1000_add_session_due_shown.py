"""adaptive_learning_sessions.session_due_shown — spaced-repetition cap tracking

Revision ID: 2026_05_13_1000
Revises: 2026_05_10_1000
Create Date: 2026-05-13 10:00:00.000000

Tracks how many due-for-review questions have been served in a session so the
weighted selector can cap spaced-repetition monopolisation at _SESSION_DUE_CAP.
Existing rows default to 0 (no due questions served yet).
"""
from alembic import op
import sqlalchemy as sa

revision = '2026_05_13_1000'
down_revision = '2026_05_10_1000'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'adaptive_learning_sessions',
        sa.Column('session_due_shown', sa.Integer(), nullable=False, server_default='0'),
    )


def downgrade() -> None:
    op.drop_column('adaptive_learning_sessions', 'session_due_shown')
