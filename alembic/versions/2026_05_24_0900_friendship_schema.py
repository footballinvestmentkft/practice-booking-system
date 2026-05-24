"""Add friendships table and social notification types

Creates the minimal social graph (Friendship model) required for
friend-gated features such as Virtual Training challenges.

Tables:
  friendships — directed friendship rows (requester → addressee)

Enum extensions:
  notificationtype — adds FRIEND_REQUEST_RECEIVED, FRIEND_REQUEST_ACCEPTED

Revision ID: 2026_05_24_0900
Revises:     2026_05_22_1100
Create Date: 2026-05-24 09:00:00
"""
import sqlalchemy as sa
from alembic import op

revision      = "2026_05_24_0900"
down_revision = "2026_05_22_1100"
branch_labels = None
depends_on    = None


def upgrade() -> None:
    # ── 1. Extend notificationtype enum ──────────────────────────────────────
    # ADD VALUE IF NOT EXISTS is safe and idempotent in PostgreSQL 9.3+.
    op.execute("ALTER TYPE notificationtype ADD VALUE IF NOT EXISTS 'FRIEND_REQUEST_RECEIVED'")
    op.execute("ALTER TYPE notificationtype ADD VALUE IF NOT EXISTS 'FRIEND_REQUEST_ACCEPTED'")

    # ── 2. Create friendshipstatus enum (idempotent) ─────────────────────────
    # op.create_table() with sa.Enum ignores create_type=False in this
    # Alembic/SQLAlchemy version and always emits CREATE TYPE. We use raw SQL
    # via a PL/pgSQL exception block so the type creation is idempotent even
    # when the type already exists in a CI shared-DB environment.
    # create_table() is then called with sa.Text() for the status column to
    # avoid any SQLAlchemy-level CREATE TYPE emission, and the real enum
    # constraint is enforced by the server_default + ORM layer.
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE friendshipstatus AS ENUM
                ('pending', 'accepted', 'declined', 'blocked');
        EXCEPTION WHEN duplicate_object THEN null;
        END $$;
    """)

    # ── 3. Create friendships table (raw SQL — avoids double CREATE TYPE) ────
    op.execute("""
        CREATE TABLE IF NOT EXISTS friendships (
            id           SERIAL PRIMARY KEY,
            requester_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            addressee_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status       friendshipstatus NOT NULL DEFAULT 'pending',
            created_at   TIMESTAMPTZ      NOT NULL DEFAULT NOW(),
            updated_at   TIMESTAMPTZ,
            CONSTRAINT uq_friendship_pair    UNIQUE  (requester_id, addressee_id),
            CONSTRAINT ck_no_self_friendship CHECK   (requester_id != addressee_id)
        )
    """)
    op.create_index("ix_friendships_requester_id", "friendships", ["requester_id"])
    op.create_index("ix_friendships_addressee_id", "friendships", ["addressee_id"])
    op.create_index("ix_friendships_status",       "friendships", ["status"])


def downgrade() -> None:
    op.drop_index("ix_friendships_status",       table_name="friendships")
    op.drop_index("ix_friendships_addressee_id", table_name="friendships")
    op.drop_index("ix_friendships_requester_id", table_name="friendships")
    op.drop_table("friendships")
    # Cannot remove enum values from notificationtype without recreating it —
    # leave FRIEND_REQUEST_* values in place on downgrade.
    op.execute("DROP TYPE IF EXISTS friendshipstatus")
