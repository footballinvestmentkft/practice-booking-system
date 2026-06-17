"""widen audit_logs.action to VARCHAR(255)

The DefaultActionHandler fallback produces f"{method}_{path}" strings.
For juggling pose-snapshot paths two UUIDs are embedded, making the action
string ~134 chars and overflowing the old VARCHAR(100) limit with a
StringDataRightTruncation error on every pose-snapshot request.

Fix: widen action to VARCHAR(255) and add JugglingActionHandler so
juggling paths now emit short semantic constants (max 30 chars).

Revision ID: 2026_06_17_1200
Revises: 2026_06_17_1100
Create Date: 2026-06-17
"""
from alembic import op
import sqlalchemy as sa

revision      = "2026_06_17_1200"
down_revision = "2026_06_17_1100"
branch_labels = None
depends_on    = None

_TABLE = "audit_logs"
_COL   = "action"


def upgrade() -> None:
    op.alter_column(
        _TABLE,
        _COL,
        existing_type=sa.String(100),
        type_=sa.String(255),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        _TABLE,
        _COL,
        existing_type=sa.String(255),
        type_=sa.String(100),
        existing_nullable=False,
    )
