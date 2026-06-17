"""add user_rotation_degrees to juggling_videos

User display rotation override — persisted per video so the user's manual
rotation survives close and re-open.  NOT a transcode parameter; the processed
file is never re-encoded based on this value.

Revision ID: 2026_06_17_1100
Revises: 2026_06_17_1000
Create Date: 2026-06-17
"""
from alembic import op
import sqlalchemy as sa

revision      = "2026_06_17_1100"
down_revision = "2026_06_17_1000"
branch_labels = None
depends_on    = None

_TABLE   = "juggling_videos"
_COL     = "user_rotation_degrees"
_CK_NAME = "ck_juggling_videos_user_rotation_degrees"


def upgrade() -> None:
    op.add_column(
        _TABLE,
        sa.Column(
            _COL,
            sa.SmallInteger(),
            nullable=False,
            server_default="0",
            comment="User display rotation override (0/90/180/270). Not a transcode parameter.",
        ),
    )
    op.create_check_constraint(
        _CK_NAME,
        _TABLE,
        f"{_COL} IN (0, 90, 180, 270)",
    )


def downgrade() -> None:
    op.drop_constraint(_CK_NAME, _TABLE, type_="check")
    op.drop_column(_TABLE, _COL)
