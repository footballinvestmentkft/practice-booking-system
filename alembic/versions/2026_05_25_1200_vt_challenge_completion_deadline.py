"""Add completion deadline and forfeit fields to vt_challenges.

Revision ID: 2026_05_25_1200
Revises:     2026_05_25_1100
Create Date: 2026-05-25 12:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision    = "2026_05_25_1200"
down_revision = "2026_05_25_1100"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column("vt_challenges",
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vt_challenges",
        sa.Column("completion_window_seconds", sa.Integer(), nullable=True))
    op.add_column("vt_challenges",
        sa.Column("completion_deadline", sa.DateTime(timezone=True), nullable=True))
    op.add_column("vt_challenges",
        sa.Column("forfeit_user_id",
                  sa.Integer(),
                  sa.ForeignKey("users.id", ondelete="SET NULL"),
                  nullable=True))
    op.add_column("vt_challenges",
        sa.Column("forfeit_reason", sa.String(30), nullable=True))
    op.create_check_constraint(
        "ck_vt_forfeit_reason_valid",
        "vt_challenges",
        "forfeit_reason IS NULL OR forfeit_reason IN ('deadline_expired', 'no_contest')",
    )
    op.create_index("ix_vt_challenges_forfeit_user_id",
                    "vt_challenges", ["forfeit_user_id"])


def downgrade() -> None:
    op.drop_index("ix_vt_challenges_forfeit_user_id", table_name="vt_challenges")
    op.drop_constraint("ck_vt_forfeit_reason_valid", "vt_challenges", type_="check")
    op.drop_column("vt_challenges", "forfeit_reason")
    op.drop_column("vt_challenges", "forfeit_user_id")
    op.drop_column("vt_challenges", "completion_deadline")
    op.drop_column("vt_challenges", "completion_window_seconds")
    op.drop_column("vt_challenges", "accepted_at")
