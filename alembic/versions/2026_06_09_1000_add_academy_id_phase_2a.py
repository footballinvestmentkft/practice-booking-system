"""Add lfa_academy_id and public_token to users table (Academy ID Phase 2A).

lfa_academy_id: human-readable, shown on the Academy ID card (LFA-YYYY-NNNNN).
public_token:   non-guessable UUID v4 — used ONLY in /verify/{token} QR URL.

Backfill strategy:
  - public_token: DB DEFAULT gen_random_uuid() covers all existing rows.
  - lfa_academy_id: Python loop assigns LFA-{year}-{seq:05d} ordered by
    created_at ASC within each year so the earliest member gets 00001.

Revision ID: 2026_06_09_1000
Revises:     2026_06_08_1000
Create Date: 2026-06-09 10:00:00
"""
import uuid as _uuid_mod
from collections import defaultdict

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision      = "2026_06_09_1000"
down_revision = "2026_06_08_1000"
branch_labels = None
depends_on    = None

_TABLE = "users"


def upgrade() -> None:
    # 1. Add public_token — DB default covers existing rows immediately.
    op.add_column(
        _TABLE,
        sa.Column(
            "public_token",
            UUID(as_uuid=True),
            nullable=True,
            server_default=sa.text("gen_random_uuid()"),
            comment="Non-guessable UUID for /verify/{token} QR URL — do not log",
        ),
    )
    op.create_index("ix_users_public_token", _TABLE, ["public_token"], unique=True)

    # 2. Add lfa_academy_id — nullable for now; backfill below; NOT NULL after.
    op.add_column(
        _TABLE,
        sa.Column(
            "lfa_academy_id",
            sa.String(20),
            nullable=True,
            comment="Human-readable Academy ID: LFA-YYYY-NNNNN",
        ),
    )

    # 3. Backfill lfa_academy_id for all existing users.
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, created_at FROM users "
            "WHERE lfa_academy_id IS NULL "
            "ORDER BY created_at ASC NULLS LAST, id ASC"
        )
    ).fetchall()

    year_counters: dict[int, int] = defaultdict(int)
    for row in rows:
        year = row.created_at.year if row.created_at else 2026
        year_counters[year] += 1
        academy_id = f"LFA-{year}-{year_counters[year]:05d}"
        conn.execute(
            sa.text("UPDATE users SET lfa_academy_id = :aid WHERE id = :uid"),
            {"aid": academy_id, "uid": row.id},
        )

    # 4. Apply UNIQUE index.
    # Column stays NULLABLE — new users receive lfa_academy_id lazily via
    # GET /me/academy-id (lazy assign with UNIQUE retry).  This avoids
    # breaking any user-creation path that does not go through the
    # academy-id service (e.g. fixtures, admin-created users, CI test setup).
    op.create_index("ix_users_lfa_academy_id", _TABLE, ["lfa_academy_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_lfa_academy_id", table_name=_TABLE)
    op.drop_column(_TABLE, "lfa_academy_id")
    op.drop_index("ix_users_public_token", table_name=_TABLE)
    op.drop_column(_TABLE, "public_token")
