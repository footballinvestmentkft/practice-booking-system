"""PC5 canonical Player progression write authority.

Revision ID: 2026_09_13_2000
Revises: 2026_09_13_1000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "2026_09_13_2000"
down_revision = "2026_09_13_1000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_progression_outcomes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("player_user_id", sa.Integer(), nullable=False),
        sa.Column("specialization_id", sa.String(50), nullable=False),
        sa.Column("source_type", sa.String(50), nullable=False),
        sa.Column("source_id", sa.String(120), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("xp_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("provenance", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("context", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("actor_user_id", sa.Integer(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["player_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["specialization_id"], ["specializations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("idempotency_key", name="uq_player_progression_idempotency"),
        sa.UniqueConstraint("player_user_id", "source_type", "source_id", name="uq_player_progression_source"),
        sa.CheckConstraint("specialization_id = 'LFA_FOOTBALL_PLAYER'", name="ck_player_progression_specialization"),
        sa.CheckConstraint("xp_delta >= 0", name="ck_player_progression_xp_nonnegative"),
    )
    op.create_index("ix_player_progression_outcomes_player_user_id", "player_progression_outcomes", ["player_user_id"])
    op.create_table(
        "player_skill_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("player_user_id", sa.Integer(), nullable=False),
        sa.Column("skill_key", sa.String(50), nullable=False),
        sa.Column("current_value", sa.Float(), nullable=False),
        sa.Column("last_outcome_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["player_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["last_outcome_id"], ["player_progression_outcomes.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("player_user_id", "skill_key", name="uq_player_skill_state"),
        sa.CheckConstraint("current_value >= 0 AND current_value <= 100", name="ck_player_skill_state_range"),
    )
    op.create_index("ix_player_skill_states_player_user_id", "player_skill_states", ["player_user_id"])
    op.create_table(
        "player_skill_changes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("outcome_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("player_user_id", sa.Integer(), nullable=False),
        sa.Column("skill_key", sa.String(50), nullable=False),
        sa.Column("previous_value", sa.Float(), nullable=False),
        sa.Column("new_value", sa.Float(), nullable=False),
        sa.Column("delta", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["outcome_id"], ["player_progression_outcomes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["player_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("outcome_id", "skill_key", name="uq_player_skill_change_outcome"),
        sa.CheckConstraint("new_value >= 0 AND new_value <= 100", name="ck_player_skill_change_range"),
    )
    op.create_index("ix_player_skill_changes_outcome_id", "player_skill_changes", ["outcome_id"])
    op.create_index("ix_player_skill_changes_player_user_id", "player_skill_changes", ["player_user_id"])

    op.add_column("user_achievements", sa.Column("canonical_key", sa.String(100), nullable=True))
    op.add_column("user_achievements", sa.Column("progression_outcome_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_user_achievement_progression_outcome", "user_achievements", "player_progression_outcomes", ["progression_outcome_id"], ["id"], ondelete="RESTRICT")
    op.create_index("uq_user_achievement_canonical", "user_achievements", ["user_id", "canonical_key"], unique=True, postgresql_where=sa.text("canonical_key IS NOT NULL"))

    op.add_column("education_progress_events", sa.Column("progression_outcome_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_unique_constraint("uq_education_event_progression_outcome", "education_progress_events", ["progression_outcome_id"])
    op.create_foreign_key("fk_education_event_progression_outcome", "education_progress_events", "player_progression_outcomes", ["progression_outcome_id"], ["id"], ondelete="RESTRICT")

    op.add_column("football_skill_assessments", sa.Column("progression_outcome_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_unique_constraint("uq_football_assessment_progression_outcome", "football_skill_assessments", ["progression_outcome_id"])
    op.create_foreign_key("fk_football_assessment_progression_outcome", "football_skill_assessments", "player_progression_outcomes", ["progression_outcome_id"], ["id"], ondelete="RESTRICT")


def downgrade() -> None:
    op.drop_constraint("fk_football_assessment_progression_outcome", "football_skill_assessments", type_="foreignkey")
    op.drop_constraint("uq_football_assessment_progression_outcome", "football_skill_assessments", type_="unique")
    op.drop_column("football_skill_assessments", "progression_outcome_id")
    op.drop_constraint("fk_education_event_progression_outcome", "education_progress_events", type_="foreignkey")
    op.drop_constraint("uq_education_event_progression_outcome", "education_progress_events", type_="unique")
    op.drop_column("education_progress_events", "progression_outcome_id")
    op.drop_index("uq_user_achievement_canonical", table_name="user_achievements")
    op.drop_constraint("fk_user_achievement_progression_outcome", "user_achievements", type_="foreignkey")
    op.drop_column("user_achievements", "progression_outcome_id")
    op.drop_column("user_achievements", "canonical_key")
    op.drop_index("ix_player_skill_changes_player_user_id", table_name="player_skill_changes")
    op.drop_index("ix_player_skill_changes_outcome_id", table_name="player_skill_changes")
    op.drop_table("player_skill_changes")
    op.drop_index("ix_player_skill_states_player_user_id", table_name="player_skill_states")
    op.drop_table("player_skill_states")
    op.drop_index("ix_player_progression_outcomes_player_user_id", table_name="player_progression_outcomes")
    op.drop_table("player_progression_outcomes")
