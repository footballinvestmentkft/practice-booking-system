"""Add vt_challenges table and challenge notification types

Creates the async friend-vs-friend VT challenge model.

Tables:
  vt_challenges — pending/accepted/declined/cancelled/expired/completed lifecycle

Enum additions:
  challengestatus      — new enum (DO $$ idempotent block, lesson from PR-F1)
  notificationtype     — 6 new VT_CHALLENGE_* values

Revision ID: 2026_05_24_1000
Revises:     2026_05_24_0900
Create Date: 2026-05-24 10:00:00
"""
import sqlalchemy as sa
from alembic import op

revision      = "2026_05_24_1000"
down_revision = "2026_05_24_0900"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── 1. Extend notificationtype enum ──────────────────────────────────────
    for value in [
        "VT_CHALLENGE_RECEIVED",
        "VT_CHALLENGE_ACCEPTED",
        "VT_CHALLENGE_DECLINED",
        "VT_CHALLENGE_CANCELLED",
        "VT_CHALLENGE_EXPIRED",
        "VT_CHALLENGE_COMPLETED",
    ]:
        op.execute(
            f"ALTER TYPE notificationtype ADD VALUE IF NOT EXISTS '{value}'"
        )

    # ── 2. Create challengestatus enum (idempotent PL/pgSQL block) ───────────
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE challengestatus AS ENUM
                ('pending', 'accepted', 'declined', 'expired', 'cancelled', 'completed');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)

    # ── 3. Create vt_challenges table (raw SQL — avoids double CREATE TYPE) ──
    op.execute("""
        CREATE TABLE IF NOT EXISTS vt_challenges (
            id                    SERIAL PRIMARY KEY,
            challenger_id         INTEGER NOT NULL
                                    REFERENCES users(id) ON DELETE CASCADE,
            challenged_id         INTEGER NOT NULL
                                    REFERENCES users(id) ON DELETE CASCADE,
            game_id               INTEGER NOT NULL
                                    REFERENCES virtual_training_games(id) ON DELETE CASCADE,
            status                challengestatus NOT NULL DEFAULT 'pending',
            message               TEXT,
            challenger_attempt_id INTEGER
                                    REFERENCES virtual_training_attempts(id) ON DELETE SET NULL,
            challenged_attempt_id INTEGER
                                    REFERENCES virtual_training_attempts(id) ON DELETE SET NULL,
            winner_id             INTEGER
                                    REFERENCES users(id) ON DELETE SET NULL,
            is_draw               BOOLEAN NOT NULL DEFAULT FALSE,
            completed_at          TIMESTAMPTZ,
            expires_at            TIMESTAMPTZ NOT NULL,
            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at            TIMESTAMPTZ,
            CONSTRAINT ck_challenge_no_self
                CHECK (challenger_id != challenged_id)
        )
    """)

    op.create_index("ix_vt_challenges_challenger_id", "vt_challenges", ["challenger_id"])
    op.create_index("ix_vt_challenges_challenged_id", "vt_challenges", ["challenged_id"])
    op.create_index("ix_vt_challenges_game_id",       "vt_challenges", ["game_id"])
    op.create_index("ix_vt_challenges_status",        "vt_challenges", ["status"])


def downgrade() -> None:
    op.drop_index("ix_vt_challenges_status",        table_name="vt_challenges")
    op.drop_index("ix_vt_challenges_game_id",       table_name="vt_challenges")
    op.drop_index("ix_vt_challenges_challenged_id", table_name="vt_challenges")
    op.drop_index("ix_vt_challenges_challenger_id", table_name="vt_challenges")
    op.drop_table("vt_challenges")
    op.execute("DROP TYPE IF EXISTS challengestatus")
    # notificationtype VT_CHALLENGE_* values cannot be removed without
    # recreating the type — leave in place on downgrade.
