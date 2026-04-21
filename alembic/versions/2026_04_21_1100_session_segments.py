"""session_segments + session_segment_results

Creates two new tables:
  - session_segments: ordered drill/exercise records within a training session
  - session_segment_results: one row per (segment, attendance) pair, storing
    resolved training skill deltas and per-segment XP

No existing tables are altered.  All existing sessions remain unaffected;
the service layer skips sessions with zero active segments.

Revision ID: 2026_04_21_1100
Revises: 2026_04_21_1000
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from alembic import op

revision = "2026_04_21_1100"
down_revision = "2026_04_21_1000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── session_segments ────────────────────────────────────────────────────
    op.create_table(
        "session_segments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("sessions.id", name="fk_session_segments_session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("label", sa.String(200), nullable=False),
        sa.Column("duration_minutes", sa.SmallInteger(), nullable=True),
        sa.Column(
            "skill_targets",
            JSONB(),
            nullable=True,
            comment=(
                "JSONB map of skill_key → weight (instructor explicit override). "
                "NULL = inherit from session.game_preset at result time."
            ),
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id", name="pk_session_segments"),
        sa.UniqueConstraint("session_id", "position", name="uq_segment_session_position"),
    )
    op.create_index("ix_session_segments_session_id", "session_segments", ["session_id"])

    # ── session_segment_results ──────────────────────────────────────────────
    op.create_table(
        "session_segment_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "segment_id",
            sa.Integer(),
            sa.ForeignKey("session_segments.id", name="fk_ssr_segment_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "attendance_id",
            sa.Integer(),
            sa.ForeignKey("attendance.id", name="fk_ssr_attendance_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.Integer(),
            sa.ForeignKey("sessions.id", name="fk_ssr_session_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", name="fk_ssr_user_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "skill_deltas",
            JSONB(),
            nullable=False,
            server_default="{}",
            comment="Resolved per-skill additive deltas at write time. Immutable after creation.",
        ),
        sa.Column("xp_awarded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "idempotency_key",
            sa.String(255),
            nullable=False,
            comment='Format: "seg_{segment_id}_att_{attendance_id}"',
        ),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("id", name="pk_session_segment_results"),
        sa.UniqueConstraint(
            "segment_id", "attendance_id",
            name="uq_segment_result_seg_att",
        ),
    )
    # Partial unique index on idempotency_key (mirrors xp_transactions pattern)
    op.execute(
        "CREATE UNIQUE INDEX uq_segment_result_idempotency "
        "ON session_segment_results (idempotency_key) "
        "WHERE idempotency_key IS NOT NULL"
    )
    op.create_index("ix_session_segment_results_user_id", "session_segment_results", ["user_id"])
    op.create_index("ix_session_segment_results_session_id", "session_segment_results", ["session_id"])


def downgrade() -> None:
    op.drop_table("session_segment_results")
    op.drop_table("session_segments")
