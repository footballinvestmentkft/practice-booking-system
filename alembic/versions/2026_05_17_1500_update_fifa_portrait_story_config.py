"""Update FIFA portrait/story component_config — full parity (CS-5)

Adds show_position_map, show_extended_profile, show_dominant_badge, resets
skill_slice to null (all 44 skills), and adds --ex-posmap-h platform_var
for both portrait and story buckets.

Revision ID: 2026_05_17_1500
Revises:     2026_05_17_1400
Create Date: 2026-05-17 15:00:00
"""
from __future__ import annotations

import json

from alembic import op
import sqlalchemy as sa

revision        = "2026_05_17_1500"
down_revision   = "2026_05_17_1400"
branch_labels   = None
depends_on      = None

_PORTRAIT_CONFIG = {
    "skill_slice":           None,
    "show_dominant_badge":   True,
    "show_height_weight":    True,
    "show_extended_profile": True,
    "show_position_map":     True,
    "show_sponsor":          False,
    "platform_vars": {
        "--ex-posmap-h": "200px",
    },
}

_STORY_CONFIG = {
    "skill_slice":           None,
    "show_dominant_badge":   True,
    "show_height_weight":    True,
    "show_extended_profile": True,
    "show_position_map":     True,
    "show_sponsor":          True,
    "platform_vars": {
        "--ex-hero-h":      "460px",
        "--ex-avatar-sz":   "180px",
        "--ex-avatar-font": "60px",
        "--ex-ovr-font":    "96px",
        "--ex-name-font":   "48px",
        "--ex-row-max-h":   "66px",
        "--ex-sname-w":     "155px",
        "--ex-font-skill":  "14px",
        "--ex-posmap-h":    "250px",
    },
}


def upgrade() -> None:
    conn = op.get_bind()

    # Read current component_config, merge portrait + story keys, write back
    row = conn.execute(
        sa.text("SELECT component_config FROM card_designs WHERE id = 'fifa'")
    ).fetchone()

    if row is None:
        return  # design not seeded yet; fallback dict in service covers runtime

    current: dict = row[0] if isinstance(row[0], dict) else json.loads(row[0] or "{}")
    current["portrait"] = _PORTRAIT_CONFIG
    current["story"]    = _STORY_CONFIG

    conn.execute(
        sa.text(
            "UPDATE card_designs SET component_config = :cfg WHERE id = 'fifa'"
        ),
        {"cfg": json.dumps(current)},
    )


def downgrade() -> None:
    conn = op.get_bind()

    row = conn.execute(
        sa.text("SELECT component_config FROM card_designs WHERE id = 'fifa'")
    ).fetchone()

    if row is None:
        return

    current: dict = row[0] if isinstance(row[0], dict) else json.loads(row[0] or "{}")

    # Restore pre-CS-5 portrait and story configs
    current["portrait"] = {
        "skill_slice":         6,
        "show_dominant_badge": False,
        "show_height_weight":  False,
        "show_sponsor":        False,
        "platform_vars":       {},
    }
    current["story"] = {
        "skill_slice":         8,
        "show_dominant_badge": True,
        "show_height_weight":  True,
        "show_sponsor":        True,
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
    }

    conn.execute(
        sa.text(
            "UPDATE card_designs SET component_config = :cfg WHERE id = 'fifa'"
        ),
        {"cfg": json.dumps(current)},
    )
