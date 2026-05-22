"""Add raw_metrics JSONB to virtual_training_attempts (Phase 2.2: Performance-based Skill Delta)

Old attempts: raw_metrics = NULL (backward compatible).
New attempts: raw_metrics = {"v": 1, "per_stimulus": [...], "per_color": {...}, "per_phase": [...]}

Revision ID: 2026_05_22_1100
Revises:     2026_05_22_1000
Create Date: 2026-05-22 11:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "2026_05_22_1100"
down_revision = "2026_05_22_1000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "virtual_training_attempts",
        sa.Column(
            "raw_metrics",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("virtual_training_attempts", "raw_metrics")
