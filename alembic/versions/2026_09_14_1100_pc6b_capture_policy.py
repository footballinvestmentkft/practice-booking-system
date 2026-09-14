"""PC6B canonical SOLO capture provenance and public identity boundary.

Revision ID: 2026_09_14_1100
Revises: 2026_09_14_1000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "2026_09_14_1100"
down_revision = "2026_09_14_1000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Never rewrite or choose between historical identities. A collision stops
    # the migration for explicit owner/manual resolution.
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM users
            WHERE nickname IS NOT NULL
            GROUP BY lower(nickname)
            HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION 'PUBLIC_NICKNAME_CONFLICT_MANUAL_REVIEW_REQUIRED';
          END IF;
        END $$
        """
    )
    op.create_index(
        "uq_users_nickname_ci",
        "users",
        [sa.text("lower(nickname)")],
        unique=True,
        postgresql_where=sa.text("nickname IS NOT NULL"),
    )

    op.create_table(
        "juggling_capture_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("request_idempotency_key", sa.String(64), nullable=False),
        sa.Column("capture_mode", sa.String(30), nullable=False),
        sa.Column("camera_position", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("bound_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "user_id", "request_idempotency_key",
            name="uq_juggling_capture_attempt_user_request",
        ),
        sa.CheckConstraint("capture_mode = 'SOLO_VERIFIED'", name="ck_juggling_capture_attempt_mode"),
        sa.CheckConstraint("camera_position = 'front'", name="ck_juggling_capture_attempt_camera"),
        sa.CheckConstraint("status IN ('ISSUED','BOUND')", name="ck_juggling_capture_attempt_status"),
    )
    op.create_index(
        "ix_juggling_capture_attempts_user_id", "juggling_capture_attempts", ["user_id"],
    )

    op.add_column(
        "juggling_videos",
        sa.Column("capture_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("juggling_videos", sa.Column("capture_mode", sa.String(30), nullable=True))
    op.add_column(
        "juggling_videos",
        sa.Column("authoritative_camera_position", sa.String(10), nullable=True),
    )
    op.create_foreign_key(
        "fk_juggling_video_capture_attempt",
        "juggling_videos", "juggling_capture_attempts",
        ["capture_attempt_id"], ["id"], ondelete="RESTRICT",
    )
    op.create_index(
        "ix_juggling_videos_capture_attempt_id", "juggling_videos", ["capture_attempt_id"], unique=True,
    )
    op.create_check_constraint(
        "ck_juggling_video_canonical_capture_provenance",
        "juggling_videos",
        "(capture_attempt_id IS NULL AND capture_mode IS NULL AND authoritative_camera_position IS NULL) "
        "OR (capture_attempt_id IS NOT NULL AND capture_mode = 'SOLO_VERIFIED' "
        "AND authoritative_camera_position = 'front' AND source_type = 'in_app_capture' "
        "AND upload_source = 'camera')",
    )

    op.add_column(
        "juggling_verifications",
        sa.Column("player_nickname_snapshot", sa.String(30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("juggling_verifications", "player_nickname_snapshot")
    op.drop_constraint(
        "ck_juggling_video_canonical_capture_provenance", "juggling_videos", type_="check",
    )
    op.drop_index("ix_juggling_videos_capture_attempt_id", table_name="juggling_videos")
    op.drop_constraint("fk_juggling_video_capture_attempt", "juggling_videos", type_="foreignkey")
    op.drop_column("juggling_videos", "authoritative_camera_position")
    op.drop_column("juggling_videos", "capture_mode")
    op.drop_column("juggling_videos", "capture_attempt_id")
    op.drop_index("ix_juggling_capture_attempts_user_id", table_name="juggling_capture_attempts")
    op.drop_table("juggling_capture_attempts")
    op.drop_index("uq_users_nickname_ci", table_name="users")
