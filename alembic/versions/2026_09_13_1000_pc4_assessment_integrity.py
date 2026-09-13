"""PC4 canonical assessment attempts and immutable issued-question snapshots.

Revision ID: 2026_09_13_1000
Revises: 2026_09_12_1000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "2026_09_13_1000"
down_revision = "2026_09_12_1000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("track_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lesson_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lesson_assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quiz_id", sa.Integer(), nullable=False),
        sa.Column("quiz_revision", sa.Integer(), nullable=False),
        sa.Column("locale", sa.String(35), nullable=False),
        sa.Column("delivery_mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="IN_PROGRESS"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_questions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("answered_questions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_answers", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("earned_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("score_percent", sa.Float(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("xp_awarded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completion_idempotency_key", sa.String(120), nullable=True),
        sa.Column("content_provenance", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["track_release_id"], ["track_releases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["lesson_assessment_id"], ["lesson_assessments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["quiz_id"], ["quizzes.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("user_id", "completion_idempotency_key", name="uq_assessment_completion_key"),
        sa.CheckConstraint("status IN ('IN_PROGRESS','COMPLETED','VOIDED')", name="ck_assessment_attempt_status"),
        sa.CheckConstraint("delivery_mode IN ('STANDARD','ADAPTIVE','EXAM')", name="ck_assessment_attempt_delivery"),
    )
    op.create_index("ix_assessment_attempts_user_id", "assessment_attempts", ["user_id"])
    op.create_index(
        "uq_assessment_active_attempt",
        "assessment_attempts",
        ["user_id", "lesson_assessment_id", "locale"],
        unique=True,
        postgresql_where=sa.text("status = 'IN_PROGRESS'"),
    )
    op.create_table(
        "assessment_issued_questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_question_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("question_type", sa.String(40), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("question_snapshot", sa.JSON(), nullable=False),
        sa.Column("option_snapshot", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        sa.Column("correct_option_id", sa.Integer(), nullable=True),
        sa.Column("correct_answer_snapshot", sa.Text(), nullable=True),
        sa.Column("answer_idempotency_key", sa.String(120), nullable=True),
        sa.Column("selected_option_id", sa.Integer(), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("is_correct", sa.Boolean(), nullable=True),
        sa.Column("omitted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("points_earned", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["attempt_id"], ["assessment_attempts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_question_id"], ["quiz_questions.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("attempt_id", "source_question_id", name="uq_assessment_issued_source"),
        sa.UniqueConstraint("attempt_id", "sequence", name="uq_assessment_issued_sequence"),
        sa.UniqueConstraint("attempt_id", "answer_idempotency_key", name="uq_assessment_answer_key"),
        sa.CheckConstraint("points > 0", name="ck_assessment_issued_points_positive"),
    )
    op.create_index("ix_assessment_issued_questions_attempt_id", "assessment_issued_questions", ["attempt_id"])
    op.add_column("quiz_attempts", sa.Column("assessment_attempt_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_unique_constraint("uq_quiz_attempt_assessment", "quiz_attempts", ["assessment_attempt_id"])
    op.create_foreign_key("fk_quiz_attempt_assessment", "quiz_attempts", "assessment_attempts", ["assessment_attempt_id"], ["id"], ondelete="RESTRICT")
    op.add_column("adaptive_learning_sessions", sa.Column("assessment_attempt_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_unique_constraint("uq_al_session_assessment_attempt", "adaptive_learning_sessions", ["assessment_attempt_id"])
    op.create_foreign_key("fk_al_session_assessment_attempt", "adaptive_learning_sessions", "assessment_attempts", ["assessment_attempt_id"], ["id"], ondelete="RESTRICT")
    op.add_column("education_progress_events", sa.Column("assessment_attempt_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_unique_constraint("uq_education_event_assessment_attempt", "education_progress_events", ["assessment_attempt_id"])
    op.create_foreign_key("fk_education_event_assessment_attempt", "education_progress_events", "assessment_attempts", ["assessment_attempt_id"], ["id"], ondelete="RESTRICT")


def downgrade() -> None:
    op.drop_constraint("fk_education_event_assessment_attempt", "education_progress_events", type_="foreignkey")
    op.drop_constraint("uq_education_event_assessment_attempt", "education_progress_events", type_="unique")
    op.drop_column("education_progress_events", "assessment_attempt_id")
    op.drop_constraint("fk_al_session_assessment_attempt", "adaptive_learning_sessions", type_="foreignkey")
    op.drop_constraint("uq_al_session_assessment_attempt", "adaptive_learning_sessions", type_="unique")
    op.drop_column("adaptive_learning_sessions", "assessment_attempt_id")
    op.drop_constraint("fk_quiz_attempt_assessment", "quiz_attempts", type_="foreignkey")
    op.drop_constraint("uq_quiz_attempt_assessment", "quiz_attempts", type_="unique")
    op.drop_column("quiz_attempts", "assessment_attempt_id")
    op.drop_index("ix_assessment_issued_questions_attempt_id", table_name="assessment_issued_questions")
    op.drop_table("assessment_issued_questions")
    op.drop_index("uq_assessment_active_attempt", table_name="assessment_attempts")
    op.drop_index("ix_assessment_attempts_user_id", table_name="assessment_attempts")
    op.drop_table("assessment_attempts")
