"""add PROMOTION_EVENT value to semester_category_type enum

Revision ID: 2026_05_02_1000
Revises: 2026_05_01_1200
Create Date: 2026-05-02 10:00:00.000000

Adds PROMOTION_EVENT to the semester_category_type PostgreSQL ENUM.

PROMOTION_EVENT semesters reuse the full Tournament/Semester pipeline
(tournament_status, TournamentConfiguration, reward and ranking pipeline).
The new value enables clean filtering in admin lists and the events hub
without touching any backend business logic.

Deployment order:
  1. Run this migration (ALTER TYPE ADD VALUE)
  2. Deploy application code (which now recognises PROMOTION_EVENT)
  3. Run scripts/migrate_promo_events_category.py --dry-run
  4. Run scripts/migrate_promo_events_category.py --apply

Downgrade:
  PostgreSQL does not support ALTER TYPE DROP VALUE.
  The downgrade script reverts data then rebuilds the enum type from scratch.
  Only run downgrade if no rows have semester_category = 'PROMOTION_EVENT'.
"""
from alembic import op
import sqlalchemy as sa

revision = '2026_05_02_1000'
down_revision = '2026_05_01_1200'
branch_labels = None
depends_on = None

_ENUM_NAME = 'semester_category_type'
_ENUM_VALUES_OLD = ('ACADEMY_SEASON', 'MINI_SEASON', 'TOURNAMENT', 'CAMP')
_ENUM_VALUES_NEW = ('ACADEMY_SEASON', 'MINI_SEASON', 'TOURNAMENT', 'CAMP', 'PROMOTION_EVENT')


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE is transactional on PostgreSQL 12+.
    # IF NOT EXISTS makes it safe to re-run (idempotent).
    op.execute(
        "ALTER TYPE semester_category_type ADD VALUE IF NOT EXISTS 'PROMOTION_EVENT'"
    )


def downgrade() -> None:
    # PostgreSQL has no DROP VALUE — must rebuild the enum type.
    # Step 1: revert any PROMOTION_EVENT rows to TOURNAMENT before removing the value.
    op.execute(
        "UPDATE semesters SET semester_category = 'TOURNAMENT' "
        "WHERE semester_category = 'PROMOTION_EVENT'"
    )
    # Step 2: detach column from the enum type.
    op.execute(
        "ALTER TABLE semesters "
        "ALTER COLUMN semester_category TYPE TEXT"
    )
    # Step 3: drop old enum type (now has no dependents).
    op.execute("DROP TYPE semester_category_type")
    # Step 4: recreate without PROMOTION_EVENT.
    op.execute(
        "CREATE TYPE semester_category_type AS ENUM "
        "('ACADEMY_SEASON', 'MINI_SEASON', 'TOURNAMENT', 'CAMP')"
    )
    # Step 5: restore column to enum type.
    op.execute(
        "ALTER TABLE semesters "
        "ALTER COLUMN semester_category TYPE semester_category_type "
        "USING semester_category::semester_category_type"
    )
