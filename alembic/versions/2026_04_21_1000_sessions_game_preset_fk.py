"""sessions.game_preset_id — nullable FK to game_presets

Adds an optional game_preset_id column to the sessions table.
NULL for all existing rows (training sessions and pre-P3 tournament sessions).
A future migration (PR-C) will wire the session generator to populate this
column at session-creation time.

Revision ID: 2026_04_21_1000
Revises: 2026_04_20_0900
"""
import sqlalchemy as sa
from alembic import op

revision = "2026_04_21_1000"
down_revision = "2026_04_20_0900"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "game_preset_id",
            sa.Integer(),
            sa.ForeignKey(
                "game_presets.id",
                name="fk_sessions_game_preset_id",
                ondelete="SET NULL",
            ),
            nullable=True,
            comment=(
                "Optional game preset for this session. "
                "NULL for training sessions and tournaments that pre-date P3 config. "
                "Set by session generator when the parent tournament has a game_preset_id."
            ),
        ),
    )
    op.create_index(
        "ix_sessions_game_preset_id",
        "sessions",
        ["game_preset_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_sessions_game_preset_id", table_name="sessions")
    op.drop_column("sessions", "game_preset_id")
