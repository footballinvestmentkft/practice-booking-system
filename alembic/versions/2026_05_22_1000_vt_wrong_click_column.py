"""Add wrong_click_count to virtual_training_attempts (Phase 2.1: Target Selection Reaction)

Separate from error_count (misses/timeouts) — tracks deliberate wrong-color
clicks so the G4 random_clicking guard can fire on server-side validation.

Revision ID: 2026_05_22_1000
Revises:     2026_05_21_1600
Create Date: 2026-05-22 10:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "2026_05_22_1000"
down_revision = "2026_05_21_1600"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "virtual_training_attempts",
        sa.Column("wrong_click_count", sa.SmallInteger(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("virtual_training_attempts", "wrong_click_count")
