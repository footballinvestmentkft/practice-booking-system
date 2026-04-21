"""xp_ledger_repair — drop blocking composite unique constraint

Drops uq_xp_transactions_user_semester_type, which prevented more than one
transaction of the same type per (user, semester) pair.  This constraint
blocks future training-segment XP rows (many TRAINING_SEGMENT_COMPLETION
rows per user per semester are valid).

The partial unique index uq_xp_transaction_idempotency (idempotency_key WHERE
idempotency_key IS NOT NULL) already exists from the squashed baseline migration
and provides all necessary uniqueness guarantees for keyed transactions.

Revision ID: 2026_04_20_0900
Revises: 2026_04_17_1100
Create Date: 2026-04-20 09:00:00.000000
"""
from alembic import op

revision = "2026_04_20_0900"
down_revision = "2026_04_17_1100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_xp_transactions_user_semester_type",
        "xp_transactions",
        type_="unique",
    )


def downgrade() -> None:
    # NOTE: downgrade will fail if any duplicate (user_id, semester_id, transaction_type)
    # rows were inserted after the upgrade.  In that case, deduplicate first.
    op.create_unique_constraint(
        "uq_xp_transactions_user_semester_type",
        "xp_transactions",
        ["user_id", "semester_id", "transaction_type"],
    )
