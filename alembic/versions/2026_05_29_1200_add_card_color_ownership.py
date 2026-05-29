"""Add card_color_ownership table.

Family-aware color entitlement table for the Player Card Color Shop (TS-1).

Schema: card_color_ownership
  - id, user_id (FK→users), card_type_id, color_id, pack_id (nullable),
    purchased_at, UNIQUE(user_id, card_type_id, color_id)

Backfill: migrates every entry in user_licenses.unlocked_card_themes JSON
array to a card_color_ownership row with card_type_id='player_card'.
ON CONFLICT DO NOTHING ensures idempotency.

The user_licenses.unlocked_card_themes JSON column is NOT removed — it
remains as legacy / backward-compat storage for the old unlock-theme
endpoint.

Revision ID: 2026_05_29_1200
Revises:     2026_05_29_1100
Create Date: 2026-05-29 12:00:00.000000
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "2026_05_29_1200"
down_revision = "2026_05_29_1100"
branch_labels = None
depends_on = None

_KNOWN_COLOR_IDS = frozenset(
    {"default", "midnight", "arctic", "gold", "emerald", "crimson"}
)


def upgrade() -> None:
    op.create_table(
        "card_color_ownership",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "card_type_id",
            sa.String(64),
            nullable=False,
            comment="'player_card' | 'welcome_card' | 'challenge_card'",
        ),
        sa.Column(
            "color_id",
            sa.String(64),
            nullable=False,
            comment="e.g. 'gold', 'emerald', 'crimson'",
        ),
        sa.Column(
            "pack_id",
            sa.String(128),
            nullable=True,
            comment="NULL for individual purchase; bundle id for TS-3 bundle logic",
        ),
        sa.Column(
            "purchased_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_id", "card_type_id", "color_id",
            name="uq_cco_user_type_color",
        ),
    )
    op.create_index("ix_cco_user_id",      "card_color_ownership", ["user_id"])
    op.create_index("ix_cco_family_color", "card_color_ownership", ["card_type_id", "color_id"])

    # Backfill: migrate existing unlocked_card_themes JSON entries to the new table.
    # Only player_card family is applicable — that was the only family in TS-1 scope.
    # ON CONFLICT DO NOTHING makes this idempotent.
    conn = op.get_bind()

    rows = conn.execute(
        sa.text(
            "SELECT user_id, json_array_elements_text(unlocked_card_themes) AS color_id "
            "FROM user_licenses "
            "WHERE unlocked_card_themes IS NOT NULL "
            "  AND unlocked_card_themes::text NOT IN ('null', '[]', '')"
        )
    ).fetchall()

    for row in rows:
        color_id = row[1]
        if color_id not in _KNOWN_COLOR_IDS:
            # Skip unknown color IDs (data anomaly guard)
            continue
        conn.execute(
            sa.text(
                "INSERT INTO card_color_ownership (user_id, card_type_id, color_id) "
                "VALUES (:user_id, 'player_card', :color_id) "
                "ON CONFLICT (user_id, card_type_id, color_id) DO NOTHING"
            ),
            {"user_id": row[0], "color_id": color_id},
        )


def downgrade() -> None:
    op.drop_index("ix_cco_family_color", table_name="card_color_ownership")
    op.drop_index("ix_cco_user_id",      table_name="card_color_ownership")
    op.drop_table("card_color_ownership")
    # user_licenses.unlocked_card_themes is intentionally NOT touched —
    # it remains as the legacy JSON field and source of truth for the old endpoint.
