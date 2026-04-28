"""Add module_prefix to adaptive_learning_sessions.

Enables module-scoped adaptive learning sessions: the selected quiz title
prefix (e.g. 'AL — Edzéselmélet') is stored on the session so the resume
policy can match exact language + category + module triples, and the
candidate question pool is narrowed to a single module.

All existing rows receive NULL (backward-compatible).

Revision ID: 2026_04_28_1000
Revises: 2026_04_26_1000
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine.reflection import Inspector

revision = "2026_04_28_1000"
down_revision = "2026_04_26_1000"
branch_labels = None
depends_on = None

_TABLE = "adaptive_learning_sessions"
_COLUMN = "module_prefix"


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in Inspector.from_engine(bind).get_columns(_TABLE)}
    if _COLUMN not in existing:
        op.add_column(
            _TABLE,
            sa.Column(_COLUMN, sa.String(200), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in Inspector.from_engine(bind).get_columns(_TABLE)}
    if _COLUMN in existing:
        op.drop_column(_TABLE, _COLUMN)
