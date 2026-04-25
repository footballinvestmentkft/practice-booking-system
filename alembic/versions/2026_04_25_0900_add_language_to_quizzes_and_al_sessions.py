"""Add language column to quizzes and adaptive_learning_sessions.

Enables language-filtered candidate question selection in the adaptive
learning engine, preventing HU and EN questions from mixing in a single
session.

Existing rows receive DEFAULT 'en' (previously seeded EN content remains
accessible via language='en' sessions).

Revision ID: 2026_04_25_0900
Revises: 2026_04_21_1100
"""
import sqlalchemy as sa
from alembic import op

revision = "2026_04_25_0900"
down_revision = "2026_04_21_1100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quizzes",
        sa.Column(
            "language",
            sa.String(10),
            nullable=False,
            server_default="en",
        ),
    )
    op.add_column(
        "adaptive_learning_sessions",
        sa.Column(
            "language",
            sa.String(10),
            nullable=False,
            server_default="en",
        ),
    )


def downgrade() -> None:
    op.drop_column("adaptive_learning_sessions", "language")
    op.drop_column("quizzes", "language")
