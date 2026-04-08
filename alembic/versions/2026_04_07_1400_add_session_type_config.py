"""Add session_type_config to tournament_configurations

Revision ID: 2026_04_07_1400
Revises: 2026_04_07_1300
Create Date: 2026-04-07 14:00:00.000000

Adds session_type_config (on_site / virtual / hybrid) to tournament_configurations.
Default 'on_site' preserves all existing tournament behavior.
"""

from alembic import op
import sqlalchemy as sa

revision = "2026_04_07_1400"
down_revision = "2026_04_07_1300"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tournament_configurations",
        sa.Column(
            "session_type_config",
            sa.String(20),
            nullable=True,
            server_default="on_site",
            comment="Session delivery type for generated sessions: on_site / virtual / hybrid",
        ),
    )


def downgrade() -> None:
    op.drop_column("tournament_configurations", "session_type_config")
