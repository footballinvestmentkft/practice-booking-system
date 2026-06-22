"""scheduled start + stream lifecycle (AN-3B PR-4B3B-0B)

Add scheduled_start_at to multicamera_sessions.
Add recording_pending to session status enum.
Add capture_result to capture_streams.

Revision ID: 2026_06_22_2000
Revises: 2026_06_22_1000
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa

revision = "2026_06_22_2000"
down_revision = "2026_06_22_1000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("multicamera_sessions",
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True))

    op.drop_constraint("ck_mcs_status", "multicamera_sessions")
    op.create_check_constraint("ck_mcs_status", "multicamera_sessions",
        "status IN ('lobby','devices_ready','recording_pending','recording',"
        "'stopped','finalizing','completed','cancelled')")

    op.add_column("capture_streams",
        sa.Column("capture_result", sa.String(20), nullable=True))
    op.create_check_constraint("ck_cs_capture_result", "capture_streams",
        "capture_result IS NULL OR capture_result IN ('success','error','interrupted')")


def downgrade() -> None:
    op.execute("UPDATE multicamera_sessions SET status='devices_ready' "
               "WHERE status='recording_pending'")

    op.drop_constraint("ck_cs_capture_result", "capture_streams")
    op.drop_column("capture_streams", "capture_result")

    op.drop_constraint("ck_mcs_status", "multicamera_sessions")
    op.create_check_constraint("ck_mcs_status", "multicamera_sessions",
        "status IN ('lobby','devices_ready','recording','stopped',"
        "'finalizing','completed','cancelled')")

    op.drop_column("multicamera_sessions", "scheduled_start_at")
