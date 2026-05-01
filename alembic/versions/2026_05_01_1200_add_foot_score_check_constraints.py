"""Add CHECK constraints for right_foot_score / left_foot_score on user_licenses.

Laterality-aware skill aggregation — Phase F2b.
Enforces that foot scores, when present, stay within the valid 0–100 range.
Both columns remain nullable (NULL = not yet assessed).

Revision ID: 2026_05_01_1200
Revises: 2026_05_01_1100
Depends on: 2026_05_01_1100 (add_foot_context_to_tournament_participations)
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "2026_05_01_1200"
down_revision = "2026_05_01_1100"
branch_labels = None
depends_on = None

_TABLE  = "user_licenses"
_CK_R   = "ck_user_licenses_right_foot_range"
_CK_L   = "ck_user_licenses_left_foot_range"


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in inspect(bind).get_check_constraints(_TABLE)}

    if _CK_R not in existing:
        op.create_check_constraint(
            _CK_R,
            _TABLE,
            "right_foot_score IS NULL OR (right_foot_score >= 0 AND right_foot_score <= 100)",
        )
    if _CK_L not in existing:
        op.create_check_constraint(
            _CK_L,
            _TABLE,
            "left_foot_score IS NULL OR (left_foot_score >= 0 AND left_foot_score <= 100)",
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in inspect(bind).get_check_constraints(_TABLE)}

    if _CK_R in existing:
        op.drop_constraint(_CK_R, _TABLE, type_="check")
    if _CK_L in existing:
        op.drop_constraint(_CK_L, _TABLE, type_="check")
