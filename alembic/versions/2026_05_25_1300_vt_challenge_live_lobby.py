"""Add live lobby fields to vt_challenges.

Revision ID: 2026_05_25_1300
Revises:     2026_05_25_1200
Create Date: 2026-05-25 13:00:00

NOTE: PostgreSQL `ALTER TYPE … ADD VALUE` is NOT transactional.
The downgrade() below can drop columns and restore the forfeit CHECK
but CANNOT remove enum values already committed to the catalog.
After downgrade the DB enum will still contain 'live_lobby' and
'live_in_progress' — this is safe because no rows will reference them.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_05_25_1300"
down_revision = "2026_05_25_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add enum values (not transactional — cannot be rolled back)
    op.execute("ALTER TYPE challengestatus ADD VALUE IF NOT EXISTS 'live_lobby'")
    op.execute("ALTER TYPE challengestatus ADD VALUE IF NOT EXISTS 'live_in_progress'")

    # 2. Add live lobby timestamp columns
    op.add_column("vt_challenges", sa.Column("challenger_ready_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("vt_challenges", sa.Column("challenged_ready_at",  sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("vt_challenges", sa.Column("live_start_at",        sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("vt_challenges", sa.Column("lobby_expires_at",     sa.TIMESTAMP(timezone=True), nullable=True))

    # 3. Expand forfeit_reason CHECK to include live-mode reasons
    op.execute("ALTER TABLE vt_challenges DROP CONSTRAINT IF EXISTS ck_vt_forfeit_reason_valid")
    op.execute(
        "ALTER TABLE vt_challenges ADD CONSTRAINT ck_vt_forfeit_reason_valid "
        "CHECK (forfeit_reason IN ('deadline_expired','no_contest','no_show','post_start_timeout'))"
    )


def downgrade() -> None:
    # Restore narrower forfeit_reason CHECK
    op.execute("ALTER TABLE vt_challenges DROP CONSTRAINT IF EXISTS ck_vt_forfeit_reason_valid")
    op.execute(
        "ALTER TABLE vt_challenges ADD CONSTRAINT ck_vt_forfeit_reason_valid "
        "CHECK (forfeit_reason IN ('deadline_expired','no_contest'))"
    )

    # Drop live lobby columns
    op.drop_column("vt_challenges", "lobby_expires_at")
    op.drop_column("vt_challenges", "live_start_at")
    op.drop_column("vt_challenges", "challenged_ready_at")
    op.drop_column("vt_challenges", "challenger_ready_at")

    # NOTE: 'live_lobby' and 'live_in_progress' enum values CANNOT be removed
    # from the PostgreSQL catalog without dropping and recreating the type.
    # They remain harmless as long as no rows reference them.
