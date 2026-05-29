"""Add 'og' bucket to FIFA Classic supported_export_buckets.

Revision ID: 2026_05_29_1100
Revises:     2026_05_28_1000
Create Date: 2026-05-29 11:00:00.000000

Root cause:
  Migration 2026_05_17_1100 seeded FIFA with 6 buckets (_ALL_BUCKETS did not
  include 'og').  The DESIGNS fallback dict in card_design_service.py was later
  updated to include 'og', and the export template
  public/export/og/fifa.html was created, but no migration was written to bring
  the DB row into alignment.

  Result: og exports return 422 (bucket not in supported_export_buckets);
  /shop/cards/player/fifa detail page shows 6 formats instead of 7.

Fix:
  Add 'og' between 'landscape' and 'banner' — matching the order in the
  DESIGNS fallback dict and PC_FORMAT_META.

Scope:
  Only the 'fifa' row in card_designs is touched.
  No CDO, no pricing, no new design_ids.
"""
from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "2026_05_29_1100"
down_revision = "2026_05_28_1000"
branch_labels = None
depends_on = None

_BUCKETS_WITH_OG    = ["square", "portrait", "story", "tiktok", "landscape", "og", "banner"]
_BUCKETS_WITHOUT_OG = ["square", "portrait", "story", "tiktok", "landscape", "banner"]


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE card_designs
        SET supported_export_buckets = CAST(:buckets AS jsonb)
        WHERE id = 'fifa'
    """), {"buckets": json.dumps(_BUCKETS_WITH_OG)})


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE card_designs
        SET supported_export_buckets = CAST(:buckets AS jsonb)
        WHERE id = 'fifa'
    """), {"buckets": json.dumps(_BUCKETS_WITHOUT_OG)})
