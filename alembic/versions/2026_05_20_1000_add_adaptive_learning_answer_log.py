"""Add adaptive_learning_answer_log table for per-question audit trail

Stores which options were presented (and in what order), which was selected,
and the correct answer position — enabling positional bias analysis and
retrospective auditability for Adaptive Learning sessions.

Revision ID: 2026_05_20_1000
Revises:     2026_05_17_1500
Create Date: 2026-05-20 10:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "2026_05_20_1000"
down_revision = "2026_05_17_1500"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "adaptive_learning_answer_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("adaptive_learning_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "question_id",
            sa.Integer(),
            sa.ForeignKey("quiz_questions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "selected_option_id",
            sa.Integer(),
            sa.ForeignKey("quiz_answer_options.id", ondelete="SET NULL"),
            nullable=True,  # NULL when timed_out=True
        ),
        sa.Column(
            "correct_option_id",
            sa.Integer(),
            sa.ForeignKey("quiz_answer_options.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("timed_out", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # Array of option IDs in the order they were presented to the user.
        # presented_option_ids[0] = option shown at position A (index 0)
        # correct_option_position = index of correct_option_id in this array
        sa.Column(
            "presented_option_ids",
            postgresql.ARRAY(sa.Integer()),
            nullable=True,
        ),
        sa.Column("correct_option_position", sa.SmallInteger(), nullable=True),
        sa.Column("time_spent_seconds", sa.Float(), nullable=True),
        sa.Column(
            "answered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_al_answer_log_session_id",
        "adaptive_learning_answer_log",
        ["session_id"],
    )
    op.create_index(
        "ix_al_answer_log_user_id",
        "adaptive_learning_answer_log",
        ["user_id"],
    )
    op.create_index(
        "ix_al_answer_log_session_question",
        "adaptive_learning_answer_log",
        ["session_id", "question_id"],
    )


def downgrade():
    op.drop_index("ix_al_answer_log_session_question", table_name="adaptive_learning_answer_log")
    op.drop_index("ix_al_answer_log_user_id", table_name="adaptive_learning_answer_log")
    op.drop_index("ix_al_answer_log_session_id", table_name="adaptive_learning_answer_log")
    op.drop_table("adaptive_learning_answer_log")
