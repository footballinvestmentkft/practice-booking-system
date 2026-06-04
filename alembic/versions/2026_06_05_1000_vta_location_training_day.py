"""vta: location + training_day columns for browser-based daily reset.

Phase 1 — browser_timezone only:
  - location_* fields stored for future lat/lng → timezone (Phase 2)
  - training_timezone resolved from browser_timezone or UTC fallback
  - training_local_date is the single source of truth for daily window

NOTE: This migration replaces the UTC completed_at range approach with
      a stored training_local_date per attempt.

Revision ID: 2026_06_05_1000
Revises: 2026_06_03_1000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "2026_06_05_1000"
down_revision = "2026_06_03_1000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Location fields (stored Phase 1; used for tz derivation in Phase 2) ──
    op.add_column("virtual_training_attempts",
        sa.Column("location_lat",         sa.Float(),                    nullable=True))
    op.add_column("virtual_training_attempts",
        sa.Column("location_lng",         sa.Float(),                    nullable=True))
    op.add_column("virtual_training_attempts",
        sa.Column("location_accuracy_m",  sa.Integer(),                  nullable=True))
    op.add_column("virtual_training_attempts",
        sa.Column("location_captured_at", sa.DateTime(timezone=True),    nullable=True))
    op.add_column("virtual_training_attempts",
        sa.Column("location_source",      sa.String(40),                 nullable=True))
    # Values: "browser_geolocation" | "stale_browser_geolocation" | "unavailable"

    # ── Timezone fields ───────────────────────────────────────────────────────
    op.add_column("virtual_training_attempts",
        sa.Column("browser_timezone",          sa.String(64), nullable=True))
    # Raw IANA string from browser Intl API — stored for audit

    op.add_column("virtual_training_attempts",
        sa.Column("training_timezone",         sa.String(64), nullable=True))
    # Resolved IANA timezone: "Europe/Budapest", "UTC", etc.
    # Phase 2: "lat_lng_derived" source will populate this from coords

    op.add_column("virtual_training_attempts",
        sa.Column("training_timezone_source",  sa.String(32), nullable=True))
    # "browser_iana" | "utc_fallback"  (Phase 2: "lat_lng_derived")

    op.add_column("virtual_training_attempts",
        sa.Column("training_local_date",       sa.Date(),     nullable=True))
    # Local date in training_timezone — single source of truth for daily window

    # ── Index: covers daily cap + eligibility queries ─────────────────────────
    op.create_index(
        "ix_vta_user_game_training_date",
        "virtual_training_attempts",
        ["user_id", "game_id", "training_local_date"],
    )

    # ── Backfill legacy records with UTC date ─────────────────────────────────
    op.execute("""
        UPDATE virtual_training_attempts
        SET training_local_date       = (completed_at AT TIME ZONE 'UTC')::date,
            training_timezone         = 'UTC',
            training_timezone_source  = 'utc_fallback'
        WHERE training_local_date IS NULL
          AND completed_at IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index("ix_vta_user_game_training_date", table_name="virtual_training_attempts")
    for col in (
        "training_local_date",
        "training_timezone_source",
        "training_timezone",
        "browser_timezone",
        "location_source",
        "location_captured_at",
        "location_accuracy_m",
        "location_lng",
        "location_lat",
    ):
        op.drop_column("virtual_training_attempts", col)
