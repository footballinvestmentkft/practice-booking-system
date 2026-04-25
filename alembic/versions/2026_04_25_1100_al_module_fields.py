"""Add module to quizzes and source_quiz_ids to adaptive_learning_sessions.

Revision ID: 2026_04_25_1100
Revises: 2026_04_25_0900
Create Date: 2026-04-25 11:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "2026_04_25_1100"
down_revision = "2026_04_25_0900"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # quizzes.module — stable module key from JSON "module" field
    op.add_column("quizzes", sa.Column("module", sa.String(200), nullable=True))

    # Backfill module from existing description column ("topic — module" format).
    # Only applies to AL quizzes whose description was written by the seeder.
    op.execute(
        """
        UPDATE quizzes
        SET module = TRIM(SPLIT_PART(description, ' — ', 2))
        WHERE title LIKE 'AL — %%'
          AND description LIKE '%%  — %%' IS FALSE
          AND description LIKE '%% — %%'
          AND TRIM(SPLIT_PART(description, ' — ', 2)) <> ''
        """
    )

    # adaptive_learning_sessions.source_quiz_ids — comma-separated quiz IDs for
    # the module scope chosen at session start. NULL = no module filter (all
    # quizzes in the session's category + language).
    op.add_column(
        "adaptive_learning_sessions",
        sa.Column("source_quiz_ids", sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("adaptive_learning_sessions", "source_quiz_ids")
    op.drop_column("quizzes", "module")
