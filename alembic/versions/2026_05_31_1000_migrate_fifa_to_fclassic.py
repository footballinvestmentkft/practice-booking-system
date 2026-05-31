"""Migrate card_designs.id 'fifa' → 'fclassic' and update all references (PR-FC-1B).

Inserts a canonical 'fclassic' row copied from 'fifa', migrates all FK-less
references (CDO, UserLicense, CardDraft), updates column defaults, then deletes
the deprecated 'fifa' row.

NOTE: There are NO FK constraints from any table to card_designs.id in the current
schema — all UPDATE operations are direct and require no cascade handling.

Production rollback warning: if PR-FC-1C (template file rename) is already
deployed, a DB-only downgrade is NOT sufficient — a coordinated code + DB rollback
is required.

Revision ID: 2026_05_31_1000
Revises:     2026_05_30_1200
"""
import sqlalchemy as sa
from alembic import op

revision = "2026_05_31_1000"
down_revision = "2026_05_30_1200"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # ── Safety: verify "fifa" exists before starting ──────────────────────────
    result = conn.execute(
        sa.text("SELECT COUNT(*) FROM card_designs WHERE id = 'fifa'")
    ).scalar()
    if result == 0:
        raise Exception(
            "PR-FC-1B migration: card_designs row id='fifa' not found. "
            "Cannot proceed — check migration chain or seed state."
        )

    # ── Step 1: INSERT "fclassic" as copy of "fifa" (idempotent guard) ────────
    existing = conn.execute(
        sa.text("SELECT COUNT(*) FROM card_designs WHERE id = 'fclassic'")
    ).scalar()
    if existing == 0:
        conn.execute(sa.text("""
            INSERT INTO card_designs (
                id, label, description, is_premium, credit_cost, is_active,
                sort_order, browser_template, archetype_id,
                supported_export_buckets, animated_platforms, component_config,
                created_at, updated_at
            )
            SELECT
                'fclassic', label, description, is_premium, credit_cost, is_active,
                sort_order, browser_template, archetype_id,
                supported_export_buckets, animated_platforms, component_config,
                now(), now()
            FROM card_designs WHERE id = 'fifa'
        """))

    # ── Step 2: card_design_ownerships (no FK constraint — direct UPDATE) ─────
    conn.execute(sa.text("""
        UPDATE card_design_ownerships
        SET design_id = 'fclassic'
        WHERE design_id = 'fifa'
    """))

    # ── Step 3: user_licenses.card_variant ────────────────────────────────────
    conn.execute(sa.text("""
        UPDATE user_licenses
        SET card_variant = 'fclassic'
        WHERE card_variant = 'fifa'
    """))

    # ── Step 4: user_licenses.published_card_variant ──────────────────────────
    conn.execute(sa.text("""
        UPDATE user_licenses
        SET published_card_variant = 'fclassic'
        WHERE published_card_variant = 'fifa'
    """))

    # ── Step 5: user_licenses.unlocked_card_variants JSON ────────────────────
    # unlocked_card_variants is a JSON (not JSONB) column; use json_* functions.
    # Currently 0 rows in dev DB; production-safe guard included.
    conn.execute(sa.text("""
        UPDATE user_licenses
        SET unlocked_card_variants = (
            SELECT json_agg(
                CASE WHEN elem::text = '"fifa"'
                     THEN '"fclassic"'::json
                     ELSE elem
                END
            )
            FROM json_array_elements(unlocked_card_variants) AS elem
        )
        WHERE unlocked_card_variants IS NOT NULL
          AND json_typeof(unlocked_card_variants) = 'array'
          AND unlocked_card_variants::text LIKE '%"fifa"%'
    """))

    # ── Step 6: card_drafts.draft_variant ─────────────────────────────────────
    conn.execute(sa.text("""
        UPDATE card_drafts
        SET draft_variant = 'fclassic'
        WHERE draft_variant = 'fifa'
    """))

    # ── Step 7: alter column defaults ─────────────────────────────────────────
    op.alter_column("user_licenses", "card_variant",
                    server_default=sa.text("'fclassic'"))
    op.alter_column("user_licenses", "published_card_variant",
                    server_default=sa.text("'fclassic'"))
    op.alter_column("card_drafts", "draft_variant",
                    server_default=sa.text("'fclassic'"))

    # ── Step 8: safety assertion — zero CDO rows remaining ────────────────────
    remaining = conn.execute(
        sa.text(
            "SELECT COUNT(*) FROM card_design_ownerships WHERE design_id = 'fifa'"
        )
    ).scalar()
    if remaining > 0:
        raise Exception(
            f"PR-FC-1B abort: {remaining} CDO row(s) still reference 'fifa' "
            "after UPDATE. Rollback and investigate."
        )

    # ── Step 9: delete deprecated "fifa" row ──────────────────────────────────
    conn.execute(sa.text("DELETE FROM card_designs WHERE id = 'fifa'"))


def downgrade() -> None:
    # Production rollback warning: if PR-FC-1C (template file rename) has been
    # deployed, a DB-only downgrade is NOT sufficient.  Coordinate with a full
    # code rollback before running this downgrade.
    conn = op.get_bind()

    # Restore defaults to "fifa"
    op.alter_column("user_licenses", "card_variant",
                    server_default=sa.text("'fifa'"))
    op.alter_column("user_licenses", "published_card_variant",
                    server_default=sa.text("'fifa'"))
    op.alter_column("card_drafts", "draft_variant",
                    server_default=sa.text("'fifa'"))

    # Re-insert "fifa" row from "fclassic"
    conn.execute(sa.text("""
        INSERT INTO card_designs (
            id, label, description, is_premium, credit_cost, is_active,
            sort_order, browser_template, archetype_id,
            supported_export_buckets, animated_platforms, component_config,
            created_at, updated_at
        )
        SELECT
            'fifa', 'FIFA Classic', description, is_premium, credit_cost, is_active,
            sort_order, browser_template, archetype_id,
            supported_export_buckets, animated_platforms, component_config,
            now(), now()
        FROM card_designs WHERE id = 'fclassic'
        ON CONFLICT (id) DO NOTHING
    """))

    # Reverse data updates
    conn.execute(sa.text("""
        UPDATE card_design_ownerships SET design_id = 'fifa'
        WHERE design_id = 'fclassic' AND card_type_id = 'player_card'
    """))
    conn.execute(sa.text("""
        UPDATE user_licenses SET card_variant = 'fifa'
        WHERE card_variant = 'fclassic'
    """))
    conn.execute(sa.text("""
        UPDATE user_licenses SET published_card_variant = 'fifa'
        WHERE published_card_variant = 'fclassic'
    """))
    conn.execute(sa.text("""
        UPDATE user_licenses
        SET unlocked_card_variants = (
            SELECT json_agg(
                CASE WHEN elem::text = '"fclassic"'
                     THEN '"fifa"'::json
                     ELSE elem
                END
            )
            FROM json_array_elements(unlocked_card_variants) AS elem
        )
        WHERE unlocked_card_variants IS NOT NULL
          AND json_typeof(unlocked_card_variants) = 'array'
          AND unlocked_card_variants::text LIKE '%"fclassic"%'
    """))
    conn.execute(sa.text("""
        UPDATE card_drafts SET draft_variant = 'fifa'
        WHERE draft_variant = 'fclassic'
    """))

    # Remove "fclassic" row
    conn.execute(sa.text("DELETE FROM card_designs WHERE id = 'fclassic'"))
