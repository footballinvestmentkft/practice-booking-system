"""Add juggling_ball_trajectories table + ball_trajectory_status column.

Dense ball tracking: 10 FPS sampled trajectory with Kalman smoothing.
One row per (video_id, frame_ms) — unique index enforced.

Revision ID: 2026_06_18_1500
Revises: 2026_06_18_1400
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision      = "2026_06_18_1500"
down_revision = "2026_06_18_1400"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.create_table(
        "juggling_ball_trajectories",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("video_id", UUID(as_uuid=True),
                  sa.ForeignKey("juggling_videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("frame_ms", sa.Integer, nullable=False),
        sa.Column("ball_x", sa.Float, nullable=True),
        sa.Column("ball_y", sa.Float, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("is_manual", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("tracking_state", sa.String(20), nullable=False, server_default=sa.text("'detected'")),
        sa.Column("model_version", sa.String(60), nullable=True),
        sa.Column("image_width_px", sa.Integer, nullable=True),
        sa.Column("image_height_px", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("video_id", "frame_ms", name="ux_ball_traj_video_frame"),
        sa.CheckConstraint(
            "tracking_state IN ('detected', 'predicted', 'lost', 'manual_seed')",
            name="ck_ball_traj_tracking_state",
        ),
        sa.CheckConstraint(
            "(tracking_state = 'lost' AND ball_x IS NULL AND ball_y IS NULL) "
            "OR (tracking_state != 'lost' AND ball_x IS NOT NULL AND ball_y IS NOT NULL)",
            name="ck_ball_traj_coords_state",
        ),
    )
    op.create_index(
        "idx_ball_traj_video_ms",
        "juggling_ball_trajectories",
        ["video_id", "frame_ms"],
    )

    op.add_column(
        "juggling_videos",
        sa.Column("ball_trajectory_status", sa.String(20), nullable=True),
    )
    op.create_check_constraint(
        "ck_juggling_videos_ball_trajectory_status",
        "juggling_videos",
        "ball_trajectory_status IS NULL "
        "OR ball_trajectory_status IN ('pending', 'processing', 'complete', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_juggling_videos_ball_trajectory_status", "juggling_videos")
    op.drop_column("juggling_videos", "ball_trajectory_status")
    op.drop_index("idx_ball_traj_video_ms", table_name="juggling_ball_trajectories")
    op.drop_table("juggling_ball_trajectories")
