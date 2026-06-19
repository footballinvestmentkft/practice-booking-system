"""Add ball_training_assignments table.

Server-side assignment store for the global Ball Training Hub (AN-3B2F PR-1A).

One assignment row per (user, frame). The client receives only the opaque UUID id;
video_id and frame_ms are never returned to the client (privacy-by-construction).

Assignment lifecycle:
  - consumed_at IS NULL + expires_at > now() → pending, usable
  - consumed_at IS NOT NULL                  → submitted (feedback inserted)
  - consumed_at IS NULL + expires_at <= now() → expired pending (swept on next queue request)

Partial unique index uix_bta_active_per_user_video_frame prevents concurrent creation
of two active assignments for the same (user, video, frame) combination.
The sweep step in the queue service marks expired-pending rows consumed before
new assignments are created, so the index never blocks legitimate re-assignment
after expiry.

Cleanup: expired rows (expires_at < now() - 7 days) accumulate harmlessly and can be
purged with a periodic maintenance query. No cron is wired in PR-1A.

Revision ID: 2026_06_19_1000
Revises: 2026_06_18_2000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision      = "2026_06_19_1000"
down_revision = "2026_06_18_2000"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.create_table(
        "ball_training_assignments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # video_id FK to juggling_videos.id (UUID PK).
        # CASCADE: if a video is hard-deleted, its pending assignments are removed.
        sa.Column(
            "video_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("juggling_videos.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("frame_ms",     sa.Integer,                     nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at",   sa.DateTime(timezone=True),     nullable=False),
        # NULL = pending; non-NULL = consumed (submitted or swept-expired)
        sa.Column("consumed_at",  sa.DateTime(timezone=True),     nullable=True),
        # Reserved for PR-1B (frame-serving display mode); NULL in PR-1A.
        sa.Column("display_mode", sa.String(20),                  nullable=True),
    )

    # ── Indexes ───────────────────────────────────────────────────────────────
    op.create_index("idx_bta_user_id",   "ball_training_assignments", ["user_id"])
    op.create_index("idx_bta_expires_at","ball_training_assignments", ["expires_at"])
    op.create_index(
        "idx_bta_user_video_frame",
        "ball_training_assignments",
        ["user_id", "video_id", "frame_ms"],
    )

    # Partial unique index: prevents two *active* (unconsumed) assignments for the
    # same (user, video, frame) combination. Expired-pending rows are swept to
    # consumed_at=expires_at before the index is consulted on queue requests.
    op.create_index(
        "uix_bta_active_per_user_video_frame",
        "ball_training_assignments",
        ["user_id", "video_id", "frame_ms"],
        unique=True,
        postgresql_where=sa.text("consumed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uix_bta_active_per_user_video_frame", table_name="ball_training_assignments")
    op.drop_index("idx_bta_user_video_frame",            table_name="ball_training_assignments")
    op.drop_index("idx_bta_expires_at",                  table_name="ball_training_assignments")
    op.drop_index("idx_bta_user_id",                     table_name="ball_training_assignments")
    op.drop_table("ball_training_assignments")
