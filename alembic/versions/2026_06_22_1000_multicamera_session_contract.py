"""add multicamera session contract tables (AN-3B PR-4B2)

5 tables: managed_devices, multicamera_sessions, session_participants,
session_devices, capture_streams.

Revision ID: 2026_06_22_1000
Revises: 2026_06_19_1000
Create Date: 2026-06-22
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "2026_06_22_1000"
down_revision = "2026_06_19_1000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "managed_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_uuid", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False, unique=True),
        sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("device_type", sa.String(20), nullable=False),
        sa.Column("device_name", sa.String(100)),
        sa.Column("ble_identifier", sa.String(100)),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("device_type IN ('iphone','ipad','gopro')", name="ck_md_device_type"),
    )

    op.create_table(
        "multicamera_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_uuid", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False, unique=True),
        sa.Column("status", sa.String(30), server_default="lobby", nullable=False),
        sa.Column("created_by_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("max_participants", sa.SmallInteger(), server_default="2", nullable=False),
        sa.Column("max_devices", sa.SmallInteger(), server_default="4", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("calibration_json", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('lobby','devices_ready','recording','stopped','finalizing','completed','cancelled')",
            name="ck_mcs_status",
        ),
        sa.CheckConstraint("max_participants BETWEEN 1 AND 4", name="ck_mcs_max_participants"),
        sa.CheckConstraint("max_devices BETWEEN 1 AND 8", name="ck_mcs_max_devices"),
    )

    op.create_table(
        "session_participants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("multicamera_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("left_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("role IN ('instructor','player','observer')", name="ck_sp_role"),
        sa.UniqueConstraint("session_id", "user_id", name="uq_sp_session_user"),
    )

    op.create_table(
        "session_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("multicamera_sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("managed_devices.id"), nullable=False),
        sa.Column("participant_id", sa.Integer(), sa.ForeignKey("session_participants.id")),
        sa.Column("managed_by_device_id", sa.Integer(), sa.ForeignKey("session_devices.id")),
        sa.Column("device_role", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), server_default="registered", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True)),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "device_role IN ('player_primary','player_secondary','instructor_primary','auxiliary_camera')",
            name="ck_sd_device_role",
        ),
        sa.CheckConstraint(
            "status IN ('registered','ready','recording','stopped','disconnected','error')",
            name="ck_sd_status",
        ),
        sa.UniqueConstraint("session_id", "device_id", name="uq_sd_session_device"),
    )

    op.create_table(
        "capture_streams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("session_device_id", sa.Integer(), sa.ForeignKey("session_devices.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stream_type", sa.String(20), nullable=False),
        sa.Column("preset_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("stopped_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "stream_type IN ('video','skeleton_2d','skeleton_3d','audio','telemetry')",
            name="ck_cs_stream_type",
        ),
    )


def downgrade() -> None:
    op.drop_table("capture_streams")
    op.drop_table("session_devices")
    op.drop_table("session_participants")
    op.drop_table("multicamera_sessions")
    op.drop_table("managed_devices")
