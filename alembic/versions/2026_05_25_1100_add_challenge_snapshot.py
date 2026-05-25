"""Add challenge_mode and challenge_config_snapshot to vt_challenges

challenge_mode:            VARCHAR(10) NOT NULL DEFAULT 'async'
challenge_config_snapshot: JSONB NULL (NULL = pre-snapshot era legacy row)

DB-level CHECK constraint enforces only 'async'|'live' are valid modes.
Existing rows receive challenge_mode='async', challenge_config_snapshot=NULL.

Revision ID: 2026_05_25_1100
Revises:     2026_05_24_1100
Create Date: 2026-05-25 11:00:00
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision      = "2026_05_25_1100"
down_revision = "2026_05_24_1100"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column(
        "vt_challenges",
        sa.Column(
            "challenge_mode",
            sa.String(10),
            nullable=False,
            server_default=sa.text("'async'"),
        ),
    )
    op.add_column(
        "vt_challenges",
        sa.Column(
            "challenge_config_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_vt_challenge_mode_valid",
        "vt_challenges",
        "challenge_mode IN ('async', 'live')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_vt_challenge_mode_valid", "vt_challenges", type_="check")
    op.drop_column("vt_challenges", "challenge_config_snapshot")
    op.drop_column("vt_challenges", "challenge_mode")
