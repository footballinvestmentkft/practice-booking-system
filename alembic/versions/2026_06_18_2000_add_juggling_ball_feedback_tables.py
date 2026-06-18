"""Add juggling_ball_feedback, juggling_frame_ground_truth, user_annotation_reliability.

User-assisted ball model training data pipeline (AN-3B2D-B0).
Three tables:
  juggling_ball_feedback      — per-user per-frame feedback record
  juggling_frame_ground_truth — aggregated majority-vote ground truth (populated B2+)
  user_annotation_reliability — per-user annotation quality tracking (populated B2+)

No training_eligible rows are set True in this migration.
No credit or consensus logic is included.

Revision ID: 2026_06_18_2000
Revises: 2026_06_18_1500
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision      = "2026_06_18_2000"
down_revision = "2026_06_18_1500"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── 1. juggling_ball_feedback ─────────────────────────────────────────
    op.create_table(
        "juggling_ball_feedback",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("frame_ms", sa.Integer, nullable=False),
        sa.Column(
            "trajectory_point_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("user_id", sa.Integer, nullable=False),
        # User decision
        sa.Column("decision",          sa.String(20),  nullable=False),
        sa.Column("corrected_x",       sa.Float,       nullable=True),
        sa.Column("corrected_y",       sa.Float,       nullable=True),
        sa.Column("correction_method", sa.String(20),  nullable=True),
        # Model context (snapshot at submit time)
        sa.Column("model_predicted_x",    sa.Float,      nullable=True),
        sa.Column("model_predicted_y",    sa.Float,      nullable=True),
        sa.Column("model_confidence",     sa.Float,      nullable=True),
        sa.Column("model_tracking_state", sa.String(20), nullable=True),
        # Reliability
        sa.Column(
            "user_reliability_at_submit",
            sa.Float,
            nullable=True,
            server_default=sa.text("0.5"),
        ),
        sa.Column("weighted_vote_contribution", sa.Float, nullable=True),
        # State
        sa.Column(
            "approval_state",
            sa.String(20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "is_gold_standard",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "is_control_sample",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "spam_flags",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        # Timestamps
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("reviewed_at",         sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by_user_id", sa.Integer,                 nullable=True),
        # Foreign keys
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["juggling_videos.id"],
            ondelete="CASCADE",
            name="fk_ball_feedback_video",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_ball_feedback_user",
        ),
        sa.ForeignKeyConstraint(
            ["trajectory_point_id"],
            ["juggling_ball_trajectories.id"],
            ondelete="SET NULL",
            name="fk_ball_feedback_trajectory",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_ball_feedback_reviewer",
        ),
        # Check constraints
        sa.CheckConstraint(
            "decision IN ('confirm','reject','no_ball','corrected')",
            name="ck_ball_feedback_decision",
        ),
        sa.CheckConstraint(
            "decision != 'corrected' OR "
            "(corrected_x IS NOT NULL AND corrected_y IS NOT NULL)",
            name="ck_ball_feedback_corrected_coords",
        ),
        sa.CheckConstraint(
            "corrected_x IS NULL OR (corrected_x >= 0.0 AND corrected_x <= 1.0)",
            name="ck_ball_feedback_cx_range",
        ),
        sa.CheckConstraint(
            "corrected_y IS NULL OR (corrected_y >= 0.0 AND corrected_y <= 1.0)",
            name="ck_ball_feedback_cy_range",
        ),
        sa.CheckConstraint(
            "approval_state IN ('pending','approved','needs_review','rejected','spam')",
            name="ck_ball_feedback_approval_state",
        ),
    )

    op.create_unique_constraint(
        "uq_ball_feedback_user_video_frame",
        "juggling_ball_feedback",
        ["user_id", "video_id", "frame_ms"],
    )
    op.create_index(
        "ix_ball_feedback_video_frame",
        "juggling_ball_feedback",
        ["video_id", "frame_ms"],
    )
    op.create_index(
        "ix_ball_feedback_user",
        "juggling_ball_feedback",
        ["user_id"],
    )
    op.create_index(
        "ix_ball_feedback_approval",
        "juggling_ball_feedback",
        ["approval_state"],
    )

    # ── 2. juggling_frame_ground_truth ────────────────────────────────────
    op.create_table(
        "juggling_frame_ground_truth",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("frame_ms", sa.Integer, nullable=False),
        # Ground truth result (populated in B2)
        sa.Column(
            "gt_decision",
            sa.String(20),
            nullable=False,
            server_default="uncertain",
        ),
        sa.Column("gt_x",          sa.Float, nullable=True),
        sa.Column("gt_y",          sa.Float, nullable=True),
        sa.Column("gt_bbox_width", sa.Float, nullable=True),
        sa.Column("gt_bbox_height",sa.Float, nullable=True),
        # Vote counts + reliability (populated in B2)
        sa.Column(
            "confidence_score",
            sa.Float,
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        sa.Column(
            "agreement_rate",
            sa.Float,
            nullable=False,
            server_default=sa.text("0.0"),
        ),
        sa.Column("vote_count",       sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("yes_votes",        sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("no_votes",         sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("no_ball_votes",    sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("correction_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        # Training eligibility — never True in B0; set by B2 Celery task
        sa.Column(
            "training_eligible",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("dataset_version", sa.String(20),            nullable=True),
        sa.Column("exported_at",     sa.DateTime(timezone=True), nullable=True),
        # Metadata
        sa.Column(
            "is_gold_standard",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["juggling_videos.id"],
            ondelete="CASCADE",
            name="fk_frame_gt_video",
        ),
        sa.CheckConstraint(
            "gt_decision IN ('ball_present','no_ball','uncertain')",
            name="ck_frame_gt_decision",
        ),
        sa.UniqueConstraint(
            "video_id",
            "frame_ms",
            name="uq_frame_ground_truth_video_frame",
        ),
    )

    op.create_index(
        "ix_frame_gt_video_frame",
        "juggling_frame_ground_truth",
        ["video_id", "frame_ms"],
    )
    op.create_index(
        "ix_frame_gt_eligible",
        "juggling_frame_ground_truth",
        ["training_eligible"],
    )

    # ── 3. user_annotation_reliability ────────────────────────────────────
    op.create_table(
        "user_annotation_reliability",
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "ball_annotation_reliability",
            sa.Float,
            nullable=False,
            server_default=sa.text("0.5"),
        ),
        sa.Column("total_feedbacks",   sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("correct_feedbacks", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("gold_attempts",     sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("gold_correct",      sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("spam_flags_count",  sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "last_updated",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "ball_annotation_reliability >= 0.1 AND ball_annotation_reliability <= 1.0",
            name="ck_reliability_range",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_annotation_reliability")
    op.drop_table("juggling_frame_ground_truth")
    op.drop_table("juggling_ball_feedback")
