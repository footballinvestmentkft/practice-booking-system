"""Add sponsor_logo_url to user_licenses.

Revision ID: 2026_05_01_1000
Revises: 2026_04_30_1000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "2026_05_01_1000"
down_revision = "2026_04_30_1000"
branch_labels = None
depends_on = None

_TABLE = "user_licenses"
_COLUMN = "sponsor_logo_url"


def upgrade() -> None:
    bind = op.get_bind()
    cols = [c["name"] for c in inspect(bind).get_columns(_TABLE)]
    if _COLUMN not in cols:
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.String(512), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    cols = [c["name"] for c in inspect(bind).get_columns(_TABLE)]
    if _COLUMN in cols:
        op.drop_column(_TABLE, _COLUMN)
