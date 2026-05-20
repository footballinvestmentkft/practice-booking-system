"""Add content_status to quizzes (DRAFT / PUBLISHED / ARCHIVED)

Introduces a three-state content lifecycle column separate from the legacy
is_active boolean. is_active is kept for backward compatibility but stays
synchronised: PUBLISHED ↔ is_active=True, DRAFT/ARCHIVED ↔ is_active=False.

Backfill:
  is_active=True  → PUBLISHED
  is_active=False → ARCHIVED  (was already hidden from students)

Revision ID: 2026_05_20_1300
Revises:     2026_05_20_1200
Create Date: 2026-05-20 13:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "2026_05_20_1300"
down_revision = "2026_05_20_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "quizzes",
        sa.Column(
            "content_status",
            sa.String(20),
            nullable=False,
            server_default="PUBLISHED",
        ),
    )
    # Backfill existing rows
    op.execute("""
        UPDATE quizzes
        SET content_status = CASE
            WHEN is_active = TRUE  THEN 'PUBLISHED'
            ELSE 'ARCHIVED'
        END
    """)
    op.create_index("ix_quizzes_content_status", "quizzes", ["content_status"])


def downgrade() -> None:
    op.drop_index("ix_quizzes_content_status", table_name="quizzes")
    op.drop_column("quizzes", "content_status")
