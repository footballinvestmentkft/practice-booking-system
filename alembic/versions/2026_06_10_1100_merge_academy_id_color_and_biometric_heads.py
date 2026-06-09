"""Merge academy-id-color and biometric foundation migration heads.

PR #263 (academy-id-color, 2026_06_09_1100) and PR #264 (biometric
foundation, 2026_06_10_1000) were merged to main independently, both
branching from 2026_06_09_1000. This creates two sibling heads and
breaks `alembic upgrade head` across all CI workflows.

This migration contains NO schema changes. Its only purpose is to
reunite the two heads into a single linear chain so that
`alembic upgrade head` resolves unambiguously.

Migration chain after this fix:
  2026_06_09_1000
    ├─ 2026_06_09_1100  (academy-id-color)
    └─ 2026_06_10_1000  (biometric foundation)
         └─ (merged by this migration)
              └─ 2026_06_10_1100  ← single new head

Revision ID: 2026_06_10_1100
Revises:     2026_06_09_1100, 2026_06_10_1000
Create Date: 2026-06-10 11:00:00
"""
from alembic import op

revision      = "2026_06_10_1100"
down_revision = ("2026_06_09_1100", "2026_06_10_1000")
branch_labels = None
depends_on    = None


def upgrade() -> None:
    pass   # schema-change free merge point


def downgrade() -> None:
    pass   # schema-change free merge point
