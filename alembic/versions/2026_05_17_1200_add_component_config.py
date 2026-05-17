"""Add component_config JSONB column to card_designs; seed FIFA portrait/story config.

Revision ID: 2026_05_17_1200
Revises:     2026_05_17_1100
Create Date: 2026-05-17 12:00:00.000000

CS-4c: Adds component_config JSONB column to card_designs.
  - Default '{}' → file-based Level C routing unchanged for all existing designs.
  - FIFA: portrait + story configs inline-seeded → column_driver.html routing active.

component_config schema (first iteration, column archetype buckets):
  {
    "<bucket>": {
      "skill_slice": int | null,     # null = all skills rendered
      "show_dominant_badge": bool,
      "show_height_weight": bool,
      "show_sponsor": bool,
      "platform_vars": {             # CSS custom property overrides applied after
        "--ex-hero-h": "460px",      # column_archetype.html defaults
        ...
      }
    }
  }

Downgrade removes the column (data loss on the seed — acceptable, re-seeded on upgrade).
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "2026_05_17_1200"
down_revision = "2026_05_17_1100"
branch_labels = None
depends_on = None

_FIFA_COMPONENT_CONFIG = {
    "portrait": {
        "skill_slice": 6,
        "show_dominant_badge": False,
        "show_height_weight": False,
        "show_sponsor": False,
        "platform_vars": {},
    },
    "story": {
        "skill_slice": 8,
        "show_dominant_badge": True,
        "show_height_weight": True,
        "show_sponsor": True,
        "platform_vars": {
            "--ex-hero-h":      "460px",
            "--ex-avatar-sz":   "180px",
            "--ex-avatar-font": "60px",
            "--ex-ovr-font":    "96px",
            "--ex-name-font":   "48px",
            "--ex-row-max-h":   "66px",
            "--ex-sname-w":     "155px",
            "--ex-font-skill":  "14px",
        },
    },
}


def upgrade() -> None:
    op.add_column(
        "card_designs",
        sa.Column(
            "component_config",
            sa.dialects.postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE card_designs SET component_config = CAST(:cfg AS jsonb) WHERE id = 'fifa'"
        ).bindparams(cfg=json.dumps(_FIFA_COMPONENT_CONFIG))
    )


def downgrade() -> None:
    op.drop_column("card_designs", "component_config")
