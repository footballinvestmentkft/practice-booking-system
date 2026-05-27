"""Add card_design_ownerships table.

Schema-only migration: creates the entitlement table for card design ownership.
No data migration — backfill is handled by scripts/backfill_card_design_ownerships.py
which must be run explicitly after a product decision.

Revision ID: 2026_05_28_0900
Revises: 2026_05_26_1200
Create Date: 2026-05-28 09:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = "2026_05_28_0900"
down_revision = "2026_05_26_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "card_design_ownerships",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "card_type_id",
            sa.String(50),
            nullable=False,
            comment="'player_card' | 'welcome_card' | 'challenge_card'",
        ),
        sa.Column(
            "design_id",
            sa.String(50),
            nullable=False,
            comment="e.g. 'fifa', 'compact', 'default', 'challenge'",
        ),
        sa.Column(
            "source",
            sa.String(20),
            nullable=False,
            server_default="purchase",
            comment="'purchase' | 'admin_grant' | 'promo' | 'system'",
        ),
        sa.Column(
            "credit_transaction_id",
            sa.Integer,
            sa.ForeignKey("credit_transactions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "card_type_id", "design_id",
            name="uq_cdo_user_type_design",
        ),
    )
    op.create_index(
        "ix_cdo_user_id",
        "card_design_ownerships",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_cdo_user_id", table_name="card_design_ownerships")
    op.drop_table("card_design_ownerships")
