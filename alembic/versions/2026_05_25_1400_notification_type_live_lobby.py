"""Add VT_CHALLENGE_FORFEITED and VT_CHALLENGE_LIVE_LOBBY to notificationtype enum.

Revision ID: 2026_05_25_1400
Revises:     2026_05_25_1300
Create Date: 2026-05-25 14:00:00

NOTE: PostgreSQL `ALTER TYPE … ADD VALUE` is NOT transactional.
Downgrade can only drop the values by recreating the type, which is not
done here. After downgrade the enum will still contain these labels —
this is safe as long as no rows reference them.

VT_CHALLENGE_FORFEITED: was added to the Python model in #174 but the
matching DB migration was never created (pre-existing omission).
VT_CHALLENGE_LIVE_LOBBY: needed for the live lobby accept notification (PR-L1).
"""
from __future__ import annotations

from alembic import op

revision = "2026_05_25_1400"
down_revision = "2026_05_25_1300"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE notificationtype ADD VALUE IF NOT EXISTS 'VT_CHALLENGE_FORFEITED'")
    op.execute("ALTER TYPE notificationtype ADD VALUE IF NOT EXISTS 'VT_CHALLENGE_LIVE_LOBBY'")


def downgrade() -> None:
    # PostgreSQL cannot remove enum values without recreating the type.
    # Values remain in the catalog but are harmless if no rows reference them.
    pass
