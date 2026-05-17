"""Backfill WC photo fields from existing Player Card photo fields

Revision ID: 2026_05_16_1001
Revises:     2026_05_16_1000
Create Date: 2026-05-16 10:01:00.000000

One-time snapshot: copies existing Player Card photo URLs into the new
Welcome Card photo fields for all user_licenses rows where a PC photo
exists but the WC-specific field is still NULL.

After this migration every user who already had a Player Card photo has
an independent WC photo starting value.  From this point on:
  - Uploading to /dashboard/lfa-player-photo changes only player_card_photo_url
  - Uploading to /dashboard/wc-photo        changes only wc_photo_url
  - The display fallback in profile.py is a genuine null-safe guard,
    only reached by brand-new users who have never uploaded any photo.

The portrait and landscape WC fields are backfilled from their PC
counterparts separately, so pre-existing crops are also preserved.

Downgrade is a no-op (data loss on downgrade is acceptable; the prior
migration's downgrade removes the columns entirely anyway).
"""
from alembic import op

revision      = '2026_05_16_1001'
down_revision = '2026_05_16_1000'
branch_labels = None
depends_on    = None


def upgrade() -> None:
    op.execute("""
        UPDATE user_licenses
        SET wc_photo_url = player_card_photo_url
        WHERE player_card_photo_url IS NOT NULL
          AND wc_photo_url IS NULL
    """)
    op.execute("""
        UPDATE user_licenses
        SET wc_photo_portrait_url = card_photo_portrait_url
        WHERE card_photo_portrait_url IS NOT NULL
          AND wc_photo_portrait_url IS NULL
    """)
    op.execute("""
        UPDATE user_licenses
        SET wc_photo_landscape_url = card_photo_landscape_url
        WHERE card_photo_landscape_url IS NOT NULL
          AND wc_photo_landscape_url IS NULL
    """)


def downgrade() -> None:
    pass  # intentional no-op — columns are dropped by 2026_05_16_1000 downgrade
