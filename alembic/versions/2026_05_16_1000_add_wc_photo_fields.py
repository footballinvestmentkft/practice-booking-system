"""Add Welcome Card photo fields to user_licenses

Revision ID: 2026_05_16_1000
Revises:     2026_05_15_1100
Create Date: 2026-05-16 10:00:00.000000

Adds three nullable VARCHAR(512) columns to user_licenses for Welcome Card
photos that are fully independent from the existing Player Card photo fields:

  wc_photo_url          — Welcome Card primary photo (fallback: player_card_photo_url)
  wc_photo_portrait_url — Welcome Card portrait crop  (fallback: wc_photo_url → card_photo_portrait_url)
  wc_photo_landscape_url— Welcome Card landscape crop (fallback: wc_photo_url → card_photo_landscape_url)

All three are nullable with no default.  NULL means "no WC-specific photo
uploaded yet" — the application layer (profile.py _build_welcome_card_context)
applies the explicit fallback chain to the corresponding Player Card field,
ensuring zero visual regression for existing users.

No backfill is applied: existing rows start with NULL (= fallback to PC photo),
which is the correct backward-compatible behaviour.
"""
from alembic import op
import sqlalchemy as sa

revision      = '2026_05_16_1000'
down_revision = '2026_05_15_1100'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.add_column('user_licenses', sa.Column(
        'wc_photo_url',
        sa.String(512),
        nullable=True,
        comment="Welcome Card primary photo — independent from player_card_photo_url; NULL = fall back to player_card_photo_url",
    ))
    op.add_column('user_licenses', sa.Column(
        'wc_photo_portrait_url',
        sa.String(512),
        nullable=True,
        comment="Welcome Card portrait photo — NULL = fall back to wc_photo_url then card_photo_portrait_url",
    ))
    op.add_column('user_licenses', sa.Column(
        'wc_photo_landscape_url',
        sa.String(512),
        nullable=True,
        comment="Welcome Card landscape photo — NULL = fall back to wc_photo_url then card_photo_landscape_url",
    ))


def downgrade() -> None:
    op.drop_column('user_licenses', 'wc_photo_landscape_url')
    op.drop_column('user_licenses', 'wc_photo_portrait_url')
    op.drop_column('user_licenses', 'wc_photo_url')
