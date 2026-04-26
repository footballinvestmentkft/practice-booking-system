"""Add card customisation + multi-photo fields to user_licenses.

Adds eight columns required by the card editor, card theme/variant
services, and the public player card render route.  All columns are
nullable so existing rows need no backfill.

The upgrade() is idempotent: it inspects the live schema and only adds
columns that are genuinely absent.  This is safe on production databases
that may already have some columns from earlier local-only migrations
(.pyc artefacts 2026_04_01 / 2026_04_06).

Columns added
-------------
  card_theme               VARCHAR(50)  — active theme ID (default → "default")
  card_variant             VARCHAR(50)  — active variant ID (default → "fifa")
  unlocked_card_themes     JSON         — list of unlocked theme IDs
  unlocked_card_variants   JSON         — list of unlocked variant IDs
  card_photo_portrait_url  VARCHAR(512) — portrait-crop photo
  card_photo_landscape_url VARCHAR(512) — landscape-crop photo
  card_bg_compact_url      VARCHAR(512) — compact-variant background
  card_bg_showcase_url     VARCHAR(512) — showcase-variant background

Revision ID: 2026_04_26_1000
Revises: 2026_04_25_0900
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "2026_04_26_1000"
down_revision = "2026_04_25_0900"
branch_labels = None
depends_on = None

_TABLE = "user_licenses"

_NEW_COLUMNS = [
    ("card_theme",               sa.Column("card_theme",               sa.String(50),  nullable=True)),
    ("card_variant",             sa.Column("card_variant",             sa.String(50),  nullable=True)),
    ("unlocked_card_themes",     sa.Column("unlocked_card_themes",     sa.JSON(),      nullable=True)),
    ("unlocked_card_variants",   sa.Column("unlocked_card_variants",   sa.JSON(),      nullable=True)),
    ("card_photo_portrait_url",  sa.Column("card_photo_portrait_url",  sa.String(512), nullable=True)),
    ("card_photo_landscape_url", sa.Column("card_photo_landscape_url", sa.String(512), nullable=True)),
    ("card_bg_compact_url",      sa.Column("card_bg_compact_url",      sa.String(512), nullable=True)),
    ("card_bg_showcase_url",     sa.Column("card_bg_showcase_url",     sa.String(512), nullable=True)),
]


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in inspect(bind).get_columns(_TABLE)}
    for col_name, col_def in _NEW_COLUMNS:
        if col_name not in existing:
            op.add_column(_TABLE, col_def)


def downgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in inspect(bind).get_columns(_TABLE)}
    for col_name, _ in reversed(_NEW_COLUMNS):
        if col_name in existing:
            op.drop_column(_TABLE, col_name)
