"""PC3 canonical Player education hierarchy and immutable release provenance.

Revision ID: 2026_09_12_1000
Revises: 2026_09_09_1000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "2026_09_12_1000"
down_revision = "2026_09_09_1000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tracks", sa.Column("specialization_id", sa.String(50), nullable=True))
    op.add_column("tracks", sa.Column("stable_key", sa.String(100), nullable=True))
    op.add_column("tracks", sa.Column("default_locale", sa.String(35), nullable=True))
    op.add_column("tracks", sa.Column("translations", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.create_foreign_key("fk_tracks_specialization", "tracks", "specializations", ["specialization_id"], ["id"])
    op.create_unique_constraint("uq_track_program_stable_key", "tracks", ["specialization_id", "stable_key"])

    op.create_table(
        "track_releases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("track_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("content_hash", sa.String(64), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"]),
        sa.UniqueConstraint("track_id", "version", name="uq_track_release_version"),
        sa.CheckConstraint("status IN ('DRAFT','PUBLISHED','ARCHIVED')", name="ck_track_release_status"),
    )
    op.add_column("tracks", sa.Column("current_release_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_tracks_current_release", "tracks", "track_releases", ["current_release_id"], ["id"], use_alter=True)

    op.add_column("modules", sa.Column("track_release_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("modules", sa.Column("stable_key", sa.String(100), nullable=True))
    op.add_column("modules", sa.Column("translations", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.create_foreign_key("fk_modules_track_release", "modules", "track_releases", ["track_release_id"], ["id"], ondelete="CASCADE")
    op.create_unique_constraint("uq_module_release_stable_key", "modules", ["track_release_id", "stable_key"])
    op.create_unique_constraint("uq_module_release_order", "modules", ["track_release_id", "order_in_track"])

    op.create_table(
        "lessons",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("module_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stable_key", sa.String(100), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("topic_key", sa.String(150), nullable=True),
        sa.Column("order_in_module", sa.Integer(), nullable=False),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("translations", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["module_id"], ["modules.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("module_id", "stable_key", name="uq_lesson_module_stable_key"),
        sa.UniqueConstraint("module_id", "order_in_module", name="uq_lesson_module_order"),
    )

    op.add_column("module_components", sa.Column("lesson_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("module_components", sa.Column("stable_key", sa.String(100), nullable=True))
    op.add_column("module_components", sa.Column("translations", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")))
    op.add_column("module_components", sa.Column("payload_schema_version", sa.String(20), nullable=False, server_default="1.0"))
    op.create_foreign_key("fk_components_lesson", "module_components", "lessons", ["lesson_id"], ["id"], ondelete="CASCADE")
    op.create_unique_constraint("uq_component_lesson_stable_key", "module_components", ["lesson_id", "stable_key"])
    op.create_unique_constraint("uq_component_lesson_order", "module_components", ["lesson_id", "order_in_module"])

    op.add_column("quizzes", sa.Column("content_key", sa.String(150), nullable=True))
    op.add_column("quizzes", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("quizzes", sa.Column("source_checksum", sa.String(64), nullable=True))
    op.add_column("quizzes", sa.Column("source_schema_version", sa.String(20), nullable=True))
    op.add_column("quizzes", sa.Column("supersedes_quiz_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_quizzes_supersedes", "quizzes", "quizzes", ["supersedes_quiz_id"], ["id"], ondelete="RESTRICT")
    op.create_unique_constraint("uq_quiz_content_locale_revision", "quizzes", ["content_key", "language", "revision"])

    op.create_table(
        "lesson_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("lesson_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("track_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("stable_key", sa.String(100), nullable=False),
        sa.Column("delivery_mode", sa.String(20), nullable=False),
        sa.Column("purpose", sa.String(20), nullable=False),
        sa.Column("order_in_lesson", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.String(20), nullable=True),
        sa.Column("topic_key", sa.String(150), nullable=True),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("max_attempts", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["track_release_id"], ["track_releases.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("lesson_id", "stable_key", name="uq_assessment_lesson_stable_key"),
        sa.UniqueConstraint("lesson_id", "order_in_lesson", name="uq_assessment_lesson_order"),
        sa.CheckConstraint("delivery_mode IN ('STANDARD','ADAPTIVE','EXAM')", name="ck_lesson_assessment_delivery"),
        sa.CheckConstraint("purpose IN ('FORMATIVE','SUMMATIVE')", name="ck_lesson_assessment_purpose"),
    )
    op.create_table(
        "lesson_assessment_quizzes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("lesson_assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quiz_id", sa.Integer(), nullable=False),
        sa.Column("locale", sa.String(35), nullable=False),
        sa.ForeignKeyConstraint(["lesson_assessment_id"], ["lesson_assessments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["quiz_id"], ["quizzes.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("lesson_assessment_id", "locale", name="uq_assessment_locale"),
        sa.UniqueConstraint("lesson_assessment_id", "quiz_id", name="uq_assessment_quiz"),
    )
    op.add_column("adaptive_learning_sessions", sa.Column("lesson_assessment_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_al_session_assessment", "adaptive_learning_sessions", "lesson_assessments", ["lesson_assessment_id"], ["id"], ondelete="RESTRICT")

    op.add_column("user_track_progresses", sa.Column("track_release_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_user_track_progress_release", "user_track_progresses", "track_releases", ["track_release_id"], ["id"], ondelete="RESTRICT")

    op.create_table(
        "education_progress_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("track_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("track_release_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lesson_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("lesson_assessment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quiz_id", sa.Integer(), nullable=False),
        sa.Column("quiz_attempt_id", sa.Integer(), nullable=False),
        sa.Column("quiz_revision", sa.Integer(), nullable=False),
        sa.Column("locale", sa.String(35), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False, server_default="ASSESSMENT_COMPLETED"),
        sa.Column("idempotency_key", sa.String(120), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["track_id"], ["tracks.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["track_release_id"], ["track_releases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["lesson_assessment_id"], ["lesson_assessments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["quiz_id"], ["quizzes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["quiz_attempt_id"], ["quiz_attempts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_education_event_idempotency"),
    )


def downgrade() -> None:
    op.drop_table("education_progress_events")
    op.drop_constraint("fk_user_track_progress_release", "user_track_progresses", type_="foreignkey")
    op.drop_column("user_track_progresses", "track_release_id")
    op.drop_constraint("fk_al_session_assessment", "adaptive_learning_sessions", type_="foreignkey")
    op.drop_column("adaptive_learning_sessions", "lesson_assessment_id")
    op.drop_table("lesson_assessment_quizzes")
    op.drop_table("lesson_assessments")
    op.drop_constraint("uq_quiz_content_locale_revision", "quizzes", type_="unique")
    op.drop_constraint("fk_quizzes_supersedes", "quizzes", type_="foreignkey")
    for column in ("supersedes_quiz_id", "source_schema_version", "source_checksum", "revision", "content_key"):
        op.drop_column("quizzes", column)
    op.drop_constraint("uq_component_lesson_order", "module_components", type_="unique")
    op.drop_constraint("uq_component_lesson_stable_key", "module_components", type_="unique")
    op.drop_constraint("fk_components_lesson", "module_components", type_="foreignkey")
    for column in ("payload_schema_version", "translations", "stable_key", "lesson_id"):
        op.drop_column("module_components", column)
    op.drop_table("lessons")
    op.drop_constraint("uq_module_release_order", "modules", type_="unique")
    op.drop_constraint("uq_module_release_stable_key", "modules", type_="unique")
    op.drop_constraint("fk_modules_track_release", "modules", type_="foreignkey")
    for column in ("translations", "stable_key", "track_release_id"):
        op.drop_column("modules", column)
    op.drop_constraint("fk_tracks_current_release", "tracks", type_="foreignkey")
    op.drop_column("tracks", "current_release_id")
    op.drop_table("track_releases")
    op.drop_constraint("uq_track_program_stable_key", "tracks", type_="unique")
    op.drop_constraint("fk_tracks_specialization", "tracks", type_="foreignkey")
    for column in ("translations", "default_locale", "stable_key", "specialization_id"):
        op.drop_column("tracks", column)
