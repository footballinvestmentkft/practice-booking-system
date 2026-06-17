"""add juggling pose snapshots

Revision ID: 2026_06_16_1000
Revises: 2026_06_14_1000
Create Date: 2026-06-16

New table: juggling_pose_snapshots
  Stores Apple Vision body pose keypoints captured on iOS at the exact video
  timestamp of each contact annotation event. One row per contact event
  (UNIQUE index on contact_event_id — upsert-safe for network retries).

  Phase 2A architecture:
    - iOS captures pose via VNDetectHumanBodyPoseRequest on the video frame
    - Sends keypoints to POST /contacts/{event_id}/pose-snapshot
    - Backend stores them here; no ML inference runs server-side in Phase 2A
    - Backend fallback (MediaPipe) is explicitly deferred to Phase 2A-B

  Privacy:
    - Pose snapshots are analysis/visualization data only
    - excluded_from_training enforced at service layer (same Policy B as contact events)
    - Phase 2A: POSE_SNAPSHOT_ENABLED=False by default; turned on per-deployment

  FK policy:
    contact_event_id FK ON DELETE CASCADE  — snapshot deleted when event is deleted
    video_id FK ON DELETE CASCADE          — snapshot deleted when video is deleted
    Both are redundant guards (event is always deleted with video via its own CASCADE)
    but video_id enables fast single-query bulk lookups without a JOIN.

  Invariants:
    - No Celery task is registered in this migration
    - No endpoint or service is registered in this migration
    - downgrade() fully reverts all changes
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision      = "2026_06_16_1000"
down_revision = "2026_06_14_1000"
branch_labels = None
depends_on    = None

_CK_CAPTURE_SOURCE = "ck_juggling_pose_snapshots_capture_source"
_CK_CONFIDENCE     = "ck_juggling_pose_snapshots_inference_confidence"
_UX_EVENT          = "ux_juggling_pose_snapshots_event"
_IX_VIDEO          = "ix_juggling_pose_snapshots_video"


def upgrade() -> None:
    op.create_table(
        "juggling_pose_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("contact_event_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("juggling_contact_events.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("juggling_videos.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
        sa.Column("keypoints", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_version", sa.String(40), nullable=False),
        sa.Column("capture_source", sa.String(20), nullable=False),
        sa.Column("inference_confidence", sa.Float(), nullable=True),
        sa.Column("image_width_px", sa.Integer(), nullable=True),
        sa.Column("image_height_px", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.CheckConstraint(
            "capture_source IN ('ios_realtime', 'backend_task')",
            name=_CK_CAPTURE_SOURCE,
        ),
        sa.CheckConstraint(
            "inference_confidence IS NULL OR "
            "(inference_confidence >= 0.0 AND inference_confidence <= 1.0)",
            name=_CK_CONFIDENCE,
        ),
    )

    op.create_index(
        _UX_EVENT,
        "juggling_pose_snapshots",
        ["contact_event_id"],
        unique=True,
    )

    op.create_index(
        _IX_VIDEO,
        "juggling_pose_snapshots",
        ["video_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(_IX_VIDEO,  table_name="juggling_pose_snapshots")
    op.drop_index(_UX_EVENT,  table_name="juggling_pose_snapshots")
    op.drop_table("juggling_pose_snapshots")
