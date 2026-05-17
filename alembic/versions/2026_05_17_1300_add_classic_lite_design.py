"""CS-5: Seed classic_lite manifest-only design into card_designs.

Revision ID: 2026_05_17_1300
Revises:     2026_05_17_1200
Create Date: 2026-05-17 13:00:00.000000

CS-5 proof-of-concept: first manifest-only (data-only / DB-backed) design.

Design intent:
  - No new Jinja2 template files (column_driver.html handles rendering).
  - No modification to the DESIGNS Python fallback dict.
  - Entry via this migration only — demonstrates manifest-compatible runtime.
  - Supported buckets: portrait + story only (subset of FIFA's 6 buckets).
  - component_config deliberately differs from FIFA to validate driver parameterisation.

classic_lite vs FIFA component_config differences:
  portrait: skill_slice=4 (FIFA=6), show_dominant_badge=True (FIFA=False),
            show_height_weight=True (FIFA=False), show_sponsor=True (FIFA=False),
            platform_vars use smaller layout sizes (hero=380px vs FIFA=N/A)
  story:    skill_slice=6 (FIFA=8), show_sponsor=False (FIFA=True),
            platform_vars use mid-range sizes (hero=440px vs FIFA=460px)

Downgrade removes the classic_lite row only (ON CONFLICT DO NOTHING makes it idempotent).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "2026_05_17_1300"
down_revision = "2026_05_17_1200"
branch_labels = None
depends_on = None

_NOW = datetime(2026, 5, 17, 13, 0, 0, tzinfo=timezone.utc)

_CLASSIC_LITE_COMPONENT_CONFIG: dict = {
    "portrait": {
        "skill_slice": 4,
        "show_dominant_badge": True,
        "show_height_weight": True,
        "show_sponsor": True,
        "platform_vars": {
            "--ex-hero-h":      "380px",
            "--ex-avatar-sz":   "170px",
            "--ex-avatar-font": "56px",
            "--ex-ovr-font":    "92px",
            "--ex-name-font":   "42px",
        },
    },
    "story": {
        "skill_slice": 6,
        "show_dominant_badge": True,
        "show_height_weight": True,
        "show_sponsor": False,
        "platform_vars": {
            "--ex-hero-h":      "440px",
            "--ex-avatar-sz":   "175px",
            "--ex-avatar-font": "58px",
            "--ex-ovr-font":    "94px",
            "--ex-name-font":   "46px",
        },
    },
}


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            INSERT INTO card_designs (
                id, label, description, is_premium, credit_cost, sort_order,
                archetype_id, browser_template,
                supported_export_buckets, animated_platforms,
                component_config,
                is_active, created_at, updated_at
            ) VALUES (
                :id, :label, :description, :is_premium, :credit_cost, :sort_order,
                NULL, :browser_template,
                CAST(:supported_export_buckets AS jsonb),
                CAST(:animated_platforms AS jsonb),
                CAST(:component_config AS jsonb),
                true, :now, :now
            )
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id":                       "classic_lite",
            "label":                    "Classic Lite",
            "description":              (
                "Proof-of-concept manifest-only design. "
                "Column archetype, portrait and story export only. "
                "No dedicated template files — rendered via column_driver.html."
            ),
            "is_premium":               False,
            "credit_cost":              0,
            "sort_order":               1,
            "browser_template":         "public/player_card_fifa.html",
            "supported_export_buckets": json.dumps(["portrait", "story"]),
            "animated_platforms":       json.dumps([]),
            "component_config":         json.dumps(_CLASSIC_LITE_COMPONENT_CONFIG),
            "now":                      _NOW,
        },
    )


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM card_designs WHERE id = 'classic_lite'"))
