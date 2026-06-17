"""Add juggling_ball_detections table.

Per-event ball position detected by ONNX model or manual override.
UNIQUE on contact_event_id (one detection per event).

Revision ID: 2026_06_18_1100
Revises: 2026_06_18_1000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision      = "2026_06_18_1100"
down_revision = "2026_06_18_1000"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.create_table(
        "juggling_ball_detections",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("contact_event_id", UUID(as_uuid=True), sa.ForeignKey("juggling_contact_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("juggling_videos.id", ondelete="CASCADE"), nullable=False),
        sa.Column("detection_source", sa.String(40), nullable=False),
        sa.Column("ball_x", sa.Float, nullable=True),
        sa.Column("ball_y", sa.Float, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("world_x_m", sa.Float, nullable=True),
        sa.Column("world_y_m", sa.Float, nullable=True),
        sa.Column("model_version", sa.String(60), nullable=True),
        sa.Column("image_width_px", sa.Integer, nullable=True),
        sa.Column("image_height_px", sa.Integer, nullable=True),
        sa.Column("no_ball_detected", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("excluded_from_training", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("contact_event_id", name="ux_juggling_ball_detections_event"),
        sa.CheckConstraint(
            "detection_source IN ('mobilenet_ssd_v2', 'manual')",
            name="ck_juggling_ball_detections_source",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)",
            name="ck_juggling_ball_detections_confidence",
        ),
        sa.CheckConstraint(
            "(no_ball_detected = true AND ball_x IS NULL AND ball_y IS NULL) "
            "OR (no_ball_detected = false AND ball_x IS NOT NULL AND ball_y IS NOT NULL)",
            name="ck_juggling_ball_detections_coords",
        ),
    )
    op.create_index("ix_juggling_ball_detections_video_id", "juggling_ball_detections", ["video_id"])


def downgrade() -> None:
    op.drop_index("ix_juggling_ball_detections_video_id", table_name="juggling_ball_detections")
    op.drop_table("juggling_ball_detections")
