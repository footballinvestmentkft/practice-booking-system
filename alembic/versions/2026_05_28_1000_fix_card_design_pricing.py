"""Fix card design pricing — FIFA Classic + classic_lite.

Revision ID: 2026_05_28_1000
Revises:     2026_05_28_0900
Create Date: 2026-05-28 10:00:00.000000

Changes:
  fifa        → is_premium=TRUE, credit_cost=300
               Was seeded as free (CS-1); entitlement MVP requires a real price.
               DESIGNS fallback dict already reflects this value; migration
               brings the DB row into alignment so staging/prod/fresh installs
               are consistent.

  classic_lite → is_active=FALSE
               CS-5 proof-of-concept design; never productised; 0 CDO rows.
               Hiding it removes the ambiguous 0-CR active row from the shop
               without destroying migration history or the component_config data.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "2026_05_28_1000"
down_revision = "2026_05_28_0900"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        UPDATE card_designs
        SET is_premium = TRUE,
            credit_cost = 300
        WHERE id = 'fifa'
    """))

    conn.execute(sa.text("""
        UPDATE card_designs
        SET is_active = FALSE
        WHERE id = 'classic_lite'
    """))


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(sa.text("""
        UPDATE card_designs
        SET is_premium = FALSE,
            credit_cost = 0
        WHERE id = 'fifa'
    """))

    conn.execute(sa.text("""
        UPDATE card_designs
        SET is_active = TRUE
        WHERE id = 'classic_lite'
    """))
