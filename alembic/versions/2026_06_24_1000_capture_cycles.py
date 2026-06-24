"""add capture_cycles and capture_cycle_devices tables (PR-MC1)

New tables: capture_cycles, capture_cycle_devices.
Add capture_cycle_id FK to capture_streams (nullable).
Add partial unique index: (capture_cycle_id, session_device_id, stream_type)
  WHERE capture_cycle_id IS NOT NULL.
Expand ck_mcs_status to include 'recording_pending' + 'active'.

Revision ID: 2026_06_24_1000
Revises: 2026_06_22_2000
Create Date: 2026-06-24

PREFLIGHT NOTE (initial deployment):
  Both new tables are created from scratch by this migration.  No existing
  rows can violate the new constraints because the tables do not yet exist
  before upgrade runs.  The capture_cycle_id column added to capture_streams
  is nullable, so all legacy stream rows remain valid (capture_cycle_id=NULL
  rows are excluded from the partial unique index).  No preflight data checks
  are required for a green-field schema.  For a re-run after a partial failure,
  verify capture_cycles / capture_cycle_devices do not already exist before
  running upgrade head again.

DOWNGRADE DATA LOSS WARNING:
  downgrade() drops capture_cycles and capture_cycle_devices entirely.
  All recorded cycle data and device snapshots are permanently destroyed.
  Only run downgrade in dev/test environments or with explicit operator sign-off.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "2026_06_24_1000"
down_revision = "2026_06_22_2000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. Expand session status constraint to include 'recording_pending' + 'active' ──
    op.drop_constraint("ck_mcs_status", "multicamera_sessions")
    op.create_check_constraint(
        "ck_mcs_status",
        "multicamera_sessions",
        "status IN ('lobby','devices_ready','recording_pending','recording','stopped',"
        "'finalizing','completed','cancelled','active')",
    )

    # ── 2. capture_cycles ─────────────────────────────────────────────────────
    op.create_table(
        "capture_cycles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("multicamera_sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("cycle_index", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="preparing",
        ),
        sa.Column("result", sa.String(20), nullable=True),
        sa.Column("scheduled_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recording_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stop_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recording_stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_by_participant_id",
            sa.Integer(),
            sa.ForeignKey("session_participants.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "status IN ('preparing','recording_pending','recording','stopping',"
            "'completed','failed','aborted')",
            name="ck_cc_status",
        ),
        sa.CheckConstraint(
            "result IS NULL OR result IN ('success','partial','failed')",
            name="ck_cc_result",
        ),
        sa.CheckConstraint("cycle_index >= 0", name="ck_cc_cycle_index_nonneg"),
        sa.UniqueConstraint("session_id", "cycle_index", name="uq_cc_session_cycle"),
        sa.UniqueConstraint(
            "session_id", "idempotency_key", name="uq_cc_session_idempotency"
        ),
    )

    # ── 3. capture_cycle_devices ──────────────────────────────────────────────
    op.create_table(
        "capture_cycle_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "capture_cycle_id",
            sa.Integer(),
            sa.ForeignKey("capture_cycles.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "session_device_id",
            sa.Integer(),
            sa.ForeignKey("session_devices.id"),
            nullable=False,
        ),
        sa.Column("required", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "recording_status",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "recording_status IN ('pending','confirmed_start','confirmed_stop','failed')",
            name="ck_ccd_recording_status",
        ),
        sa.UniqueConstraint(
            "capture_cycle_id",
            "session_device_id",
            name="uq_ccd_cycle_device",
        ),
    )

    # ── 4. Add capture_cycle_id FK to capture_streams (nullable) ─────────────
    op.add_column(
        "capture_streams",
        sa.Column(
            "capture_cycle_id",
            sa.Integer(),
            sa.ForeignKey("capture_cycles.id"),
            nullable=True,
        ),
    )
    # Partial unique: one stream per (cycle, device, type) — only when cycle set
    op.create_index(
        "uix_cs_cycle_device_type",
        "capture_streams",
        ["capture_cycle_id", "session_device_id", "stream_type"],
        unique=True,
        postgresql_where=sa.text("capture_cycle_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uix_cs_cycle_device_type", table_name="capture_streams")
    op.drop_column("capture_streams", "capture_cycle_id")
    op.drop_table("capture_cycle_devices")
    op.drop_table("capture_cycles")

    # Migrate 'active' sessions (introduced by this revision) to 'cancelled'
    # before restoring the constraint that does not include 'active'.
    # 'recording_pending' was valid before this revision (added by 2026_06_22_2000)
    # and is kept in the restored constraint below.
    op.execute(sa.text(
        "UPDATE multicamera_sessions SET status = 'cancelled' WHERE status = 'active'"
    ))

    op.drop_constraint("ck_mcs_status", "multicamera_sessions")
    op.create_check_constraint(
        "ck_mcs_status",
        "multicamera_sessions",
        "status IN ('lobby','devices_ready','recording_pending','recording',"
        "'stopped','finalizing','completed','cancelled')",
    )
