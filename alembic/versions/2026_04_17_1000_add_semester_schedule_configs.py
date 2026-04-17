"""Add semester_schedule_configs table for MINI_SEASON / ACADEMY_SEASON session generation

Revision ID: 2026_04_17_1000
Revises: 2026_03_28_1200
Create Date: 2026-04-17 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '2026_04_17_1000'
down_revision = '2026_03_28_1200'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "semester_schedule_configs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "semester_id",
            sa.Integer(),
            sa.ForeignKey("semesters.id", ondelete="CASCADE"),
            unique=True,
            nullable=False,
        ),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=False,
                  comment="0=Monday .. 6=Sunday"),
        sa.Column("start_time", sa.Time(), nullable=False,
                  comment="Local start time of the session, e.g. 17:00"),
        sa.Column("duration_minutes", sa.Integer(), nullable=False,
                  server_default="90",
                  comment="Duration of each session in minutes"),
        sa.Column("sessions_per_week", sa.SmallInteger(), nullable=False,
                  server_default="1",
                  comment="1 or 2 sessions per week on the same weekday"),
        sa.Column("campus_id", sa.Integer(),
                  sa.ForeignKey("campuses.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("pitch_id", sa.Integer(),
                  sa.ForeignKey("pitches.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("sessions_generated", sa.Boolean(), nullable=False,
                  server_default="false"),
        sa.Column("sessions_generated_at", sa.DateTime(), nullable=True),
        sa.Column("sessions_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_semester_schedule_configs_semester_id",
        "semester_schedule_configs",
        ["semester_id"],
    )


def downgrade():
    op.drop_index(
        "ix_semester_schedule_configs_semester_id",
        table_name="semester_schedule_configs",
    )
    op.drop_table("semester_schedule_configs")
