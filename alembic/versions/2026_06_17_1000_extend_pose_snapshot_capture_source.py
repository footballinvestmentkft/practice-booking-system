"""extend juggling_pose_snapshots capture_source — add ios_retroactive

Adds 'ios_retroactive' to the ck_juggling_pose_snapshots_capture_source
CHECK constraint to support Phase 2A retroactive pose generation from
pre-existing annotated events.

Revision ID: 2026_06_17_1000
Revises: 2026_06_16_1000
Create Date: 2026-06-17
"""
from alembic import op

revision      = "2026_06_17_1000"
down_revision = "2026_06_16_1000"
branch_labels = None
depends_on    = None

_CK_NAME = "ck_juggling_pose_snapshots_capture_source"
_TABLE   = "juggling_pose_snapshots"


def upgrade() -> None:
    op.drop_constraint(_CK_NAME, _TABLE, type_="check")
    op.create_check_constraint(
        _CK_NAME,
        _TABLE,
        "capture_source IN ('ios_realtime', 'ios_retroactive', 'backend_task')",
    )


def downgrade() -> None:
    op.drop_constraint(_CK_NAME, _TABLE, type_="check")
    op.create_check_constraint(
        _CK_NAME,
        _TABLE,
        "capture_source IN ('ios_realtime', 'backend_task')",
    )
