"""Add topic column to quizzes — topic is the primary selection unit.

Each quiz corresponds to exactly one topic (one JSON file).
module (already present) is the chapter-level visual grouper.
topic is the selectable item in the adaptive learning picker.

Revision ID: 2026_04_25_1200
Revises: 2026_04_25_1100
Create Date: 2026-04-25 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "2026_04_25_1200"
down_revision = "2026_04_25_1100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("quizzes", sa.Column("topic", sa.String(300), nullable=True))

    # One-time backfill from description column. Description format written by
    # the seeder is "{topic} — {module}". SPLIT_PART on the first ' — ' gives
    # the topic without any title parsing.
    # This backfill covers all quizzes seeded before this migration.
    # All future seeds write topic directly via Quiz.topic — no fallback needed.
    op.execute(
        """
        UPDATE quizzes
        SET topic = TRIM(SPLIT_PART(description, ' — ', 1))
        WHERE title LIKE 'AL — %%'
          AND description LIKE '%% — %%'
          AND TRIM(SPLIT_PART(description, ' — ', 1)) <> ''
        """
    )


def downgrade() -> None:
    op.drop_column("quizzes", "topic")
