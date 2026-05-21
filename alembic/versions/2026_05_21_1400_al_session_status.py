"""Add status, last_activity_at, void_reason to adaptive_learning_sessions

Introduces explicit session lifecycle status replacing the binary ended_at IS NULL
/ NOT NULL distinction. Adds last_activity_at for recovery prompt timing and
void_reason for audit trail when a user discards a session.

Status values:
  IN_PROGRESS — active session (ended_at IS NULL)
  COMPLETED   — user explicitly called /complete (xp computed)
  EXPIRED     — auto-retired due to timer; questions were answered
  ABANDONED   — auto-retired or never used; 0 questions answered
  VOIDED      — user explicitly discarded via /discard endpoint

Backfill (safe on any data volume — no locks held across rows):
  ended_at IS NULL                                        → IN_PROGRESS (DEFAULT)
  ended_at IS NOT NULL AND xp_earned > 0                 → COMPLETED
  ended_at IS NOT NULL AND xp_earned = 0
      AND questions_presented > 0                        → EXPIRED
  ended_at IS NOT NULL AND questions_presented = 0        → ABANDONED

Revision ID: 2026_05_21_1400
Revises:     2026_05_20_1300
Create Date: 2026-05-21 14:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = "2026_05_21_1400"
down_revision = "2026_05_20_1300"
branch_labels = None
depends_on = None


def upgrade():
    # ── New columns ───────────────────────────────────────────────────────────
    op.add_column(
        "adaptive_learning_sessions",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="IN_PROGRESS",
        ),
    )
    op.add_column(
        "adaptive_learning_sessions",
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "adaptive_learning_sessions",
        sa.Column(
            "void_reason",
            sa.String(100),
            nullable=True,
        ),
    )

    # ── Backfill existing rows ─────────────────────────────────────────────
    op.execute("""
        UPDATE adaptive_learning_sessions
        SET status = 'COMPLETED'
        WHERE ended_at IS NOT NULL
          AND xp_earned > 0
    """)
    op.execute("""
        UPDATE adaptive_learning_sessions
        SET status = 'EXPIRED'
        WHERE ended_at IS NOT NULL
          AND xp_earned = 0
          AND questions_presented > 0
    """)
    op.execute("""
        UPDATE adaptive_learning_sessions
        SET status = 'ABANDONED'
        WHERE ended_at IS NOT NULL
          AND questions_presented = 0
    """)

    # ── Index for fast IN_PROGRESS lookup (entry page recovery query) ──────
    op.create_index(
        "ix_al_sessions_user_status",
        "adaptive_learning_sessions",
        ["user_id", "status"],
    )


def downgrade():
    op.drop_index("ix_al_sessions_user_status", table_name="adaptive_learning_sessions")
    op.drop_column("adaptive_learning_sessions", "void_reason")
    op.drop_column("adaptive_learning_sessions", "last_activity_at")
    op.drop_column("adaptive_learning_sessions", "status")
