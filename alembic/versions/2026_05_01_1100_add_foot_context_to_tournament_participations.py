"""Add foot_context column to tournament_participations.

Laterality-aware skill aggregation — Phase F2.
Stores which foot-laterality context the tournament preset targets
so that per-tournament EMA deltas can be routed to the correct
lateral_components bucket in UserLicense.football_skills.

Revision ID: 2026_05_01_1100
Revises: 2026_05_01_1000
Depends on: 2026_05_01_1000 (add_sponsor_logo_url)
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "2026_05_01_1100"
down_revision = "2026_05_01_1000"
branch_labels = None
depends_on = None

_TABLE = "tournament_participations"
_COL   = "foot_context"
_CK    = "ck_tournament_participations_foot_context"


def upgrade() -> None:
    bind = op.get_bind()
    existing_cols = {c["name"] for c in inspect(bind).get_columns(_TABLE)}
    if _COL not in existing_cols:
        op.add_column(
            _TABLE,
            sa.Column(
                _COL,
                sa.String(10),
                nullable=False,
                server_default="neutral",
            ),
        )
    # CHECK constraint — idempotent guard via raw SQL
    existing_constraints = {
        c["name"]
        for c in inspect(bind).get_check_constraints(_TABLE)
    }
    if _CK not in existing_constraints:
        op.create_check_constraint(
            _CK,
            _TABLE,
            f"{_COL} IN ('right', 'left', 'neutral')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing_constraints = {
        c["name"]
        for c in inspect(bind).get_check_constraints(_TABLE)
    }
    if _CK in existing_constraints:
        op.drop_constraint(_CK, _TABLE, type_="check")

    existing_cols = {c["name"] for c in inspect(bind).get_columns(_TABLE)}
    if _COL in existing_cols:
        op.drop_column(_TABLE, _COL)
