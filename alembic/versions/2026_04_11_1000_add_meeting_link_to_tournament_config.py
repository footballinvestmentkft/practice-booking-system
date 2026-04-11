"""Add meeting_link to tournament_configurations

Revision ID: 2026_04_11_1000
Revises: 2026_04_07_1400
Create Date: 2026-04-11 10:00:00.000000

Adds meeting_link (URL string) to tournament_configurations.
Propagated to all generated sessions for virtual/hybrid tournaments.
NULL for on_site tournaments.
"""

from alembic import op
import sqlalchemy as sa

revision = "2026_04_11_1000"
down_revision = "2026_04_07_1400"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tournament_configurations",
        sa.Column(
            "meeting_link",
            sa.String(),
            nullable=True,
            comment="Meeting URL for virtual/hybrid tournament sessions — propagated to all generated sessions",
        ),
    )


def downgrade() -> None:
    op.drop_column("tournament_configurations", "meeting_link")
