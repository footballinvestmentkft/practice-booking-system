"""PC6A canonical human-reviewed juggling verification foundation.

Revision ID: 2026_09_13_3000
Revises: 2026_09_13_2000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "2026_09_13_3000"
down_revision = "2026_09_13_2000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "juggling_contact_events",
        sa.Column("reviewed_by_user_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "juggling_contact_events",
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "juggling_contact_events",
        sa.Column("review_note", sa.String(500), nullable=True),
    )
    op.create_foreign_key(
        "fk_juggling_contact_reviewed_by",
        "juggling_contact_events", "users",
        ["reviewed_by_user_id"], ["id"], ondelete="RESTRICT",
    )

    op.create_table(
        "juggling_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("player_user_id", sa.Integer(), nullable=False),
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_user_id", sa.Integer(), nullable=False),
        sa.Column("verification_method", sa.String(40), nullable=False),
        sa.Column("verification_version", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("video_checksum_sha256", sa.String(64), nullable=False),
        sa.Column("video_version", sa.String(96), nullable=False),
        sa.Column("source_annotation_revision_hash", sa.String(64), nullable=False),
        sa.Column("contact_event_revision_hash", sa.String(64), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("contact_event_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("touch_count", sa.Integer(), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("side_breakdown", postgresql.JSONB(), nullable=False),
        sa.Column("body_part_breakdown", postgresql.JSONB(), nullable=False),
        sa.Column("sequence_start_ms", sa.BigInteger(), nullable=True),
        sa.Column("sequence_end_ms", sa.BigInteger(), nullable=True),
        sa.Column("termination_reason", sa.String(30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("provenance_hash", sa.String(64), nullable=False),
        sa.Column("progression_outcome_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credit_transaction_id", sa.Integer(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["player_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["video_id"], ["juggling_videos.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["progression_outcome_id"], ["player_progression_outcomes.id"], ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credit_transaction_id"], ["credit_transactions.id"], ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "video_id", "source_annotation_revision_hash",
            name="uq_juggling_verification_source_revision",
        ),
        sa.UniqueConstraint(
            "video_id", "contact_event_revision_hash",
            name="uq_juggling_verification_contact_revision",
        ),
        sa.UniqueConstraint("progression_outcome_id", name="uq_juggling_verification_progression"),
        sa.UniqueConstraint("credit_transaction_id", name="uq_juggling_verification_credit"),
        sa.CheckConstraint(
            "verification_method IN ('HUMAN','APPLE_VISION_RULE_BASED','ML_MODEL')",
            name="ck_juggling_verification_method",
        ),
        sa.CheckConstraint("status IN ('VALID','INVALID')", name="ck_juggling_verification_status"),
        sa.CheckConstraint(
            "touch_count >= 0 AND score >= 0",
            name="ck_juggling_verification_score",
        ),
        sa.CheckConstraint(
            "score = touch_count",
            name="ck_juggling_verification_score_matches_count",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_juggling_verification_confidence",
        ),
        sa.CheckConstraint(
            "termination_reason IN ('BALL_DROPPED','VIDEO_ENDED','PLAYER_STOPPED','INVALID_SEQUENCE')",
            name="ck_juggling_verification_termination",
        ),
        sa.CheckConstraint(
            "(touch_count = 0 AND sequence_start_ms IS NULL AND sequence_end_ms IS NULL) OR "
            "(touch_count > 0 AND sequence_start_ms IS NOT NULL AND sequence_end_ms IS NOT NULL "
            "AND sequence_start_ms <= sequence_end_ms)",
            name="ck_juggling_verification_boundaries",
        ),
        sa.CheckConstraint(
            "status != 'VALID' OR touch_count >= 1",
            name="ck_juggling_verification_valid_minimum",
        ),
    )
    op.create_index(
        "ix_juggling_verifications_player_user_id", "juggling_verifications", ["player_user_id"],
    )
    op.create_index("ix_juggling_verifications_video_id", "juggling_verifications", ["video_id"])

    op.execute(
        """
        CREATE FUNCTION reject_juggling_verification_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'juggling verification records are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_juggling_verifications_immutable
        BEFORE UPDATE OR DELETE ON juggling_verifications
        FOR EACH ROW EXECUTE FUNCTION reject_juggling_verification_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_juggling_verifications_immutable ON juggling_verifications")
    op.execute("DROP FUNCTION IF EXISTS reject_juggling_verification_mutation()")
    op.drop_index("ix_juggling_verifications_video_id", table_name="juggling_verifications")
    op.drop_index("ix_juggling_verifications_player_user_id", table_name="juggling_verifications")
    op.drop_table("juggling_verifications")
    op.drop_constraint("fk_juggling_contact_reviewed_by", "juggling_contact_events", type_="foreignkey")
    op.drop_column("juggling_contact_events", "review_note")
    op.drop_column("juggling_contact_events", "reviewed_at")
    op.drop_column("juggling_contact_events", "reviewed_by_user_id")
