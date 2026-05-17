"""
CD-MIG — card_drafts migration unit tests (Phase 4D-1).

These are static code-analysis tests: they verify the ORM model structure,
migration metadata, and SQL patterns without hitting a real database.
Round-trip upgrade/downgrade integrity is covered by the CI migration chain
(empty → head → base → head) and volume safety workflows.

Test groups:
  CD-MIG-01..03: ORM model structure (CardDraft class attributes)
  CD-MIG-04..06: Migration metadata (revision, down_revision, SQL patterns)
  CD-MIG-07..08: Backfill filter + idempotency contract
"""
import importlib
import pathlib
import types

import pytest

# ── Paths ──────────────────────────────────────────────────────────────────────

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "alembic" / "versions" / "2026_05_15_1100_add_card_drafts.py"
)


def _migration_src() -> str:
    return _MIGRATION_PATH.read_text()


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def migration_src():
    return _migration_src()


@pytest.fixture(scope="module")
def card_draft_cls():
    from app.models.card_draft import CardDraft
    return CardDraft


# ── CD-MIG-01: ORM model columns ───────────────────────────────────────────────

class TestCardDraftModelStructure:

    def test_cd_mig_01_all_required_columns_present(self, card_draft_cls):
        """CD-MIG-01: CardDraft ORM model defines all required columns."""
        cols = {c.key for c in card_draft_cls.__table__.columns}
        required = {
            "id", "user_id", "card_type_id", "instance_name",
            "draft_theme", "draft_variant", "draft_platform", "draft_data",
            "published_theme", "published_variant", "published_platform",
            "published_data", "published_at",
            "created_at", "updated_at",
        }
        missing = required - cols
        assert not missing, f"Missing columns: {missing}"

    def test_cd_mig_02_unique_constraint_defined(self, card_draft_cls):
        """CD-MIG-02: UNIQUE (user_id, card_type_id, instance_name) constraint present."""
        constraint_names = {
            c.name for c in card_draft_cls.__table__.constraints
        }
        assert "uq_card_drafts_user_type_instance" in constraint_names

    def test_cd_mig_03_index_on_user_id_defined(self, card_draft_cls):
        """CD-MIG-03: ix_card_drafts_user_id index present."""
        index_names = {i.name for i in card_draft_cls.__table__.indexes}
        assert "ix_card_drafts_user_id" in index_names

    def test_cd_mig_03b_instance_name_server_default_is_default(self, card_draft_cls):
        """CD-MIG-03b: instance_name server_default is 'default'."""
        col = card_draft_cls.__table__.columns["instance_name"]
        assert col.server_default is not None
        assert "default" in str(col.server_default.arg)

    def test_cd_mig_03c_user_id_has_cascade_delete(self, card_draft_cls):
        """CD-MIG-03c: user_id FK has ondelete='CASCADE'."""
        fk = next(iter(card_draft_cls.__table__.columns["user_id"].foreign_keys))
        assert fk.ondelete.upper() == "CASCADE"

    def test_cd_mig_03d_nullable_contract(self, card_draft_cls):
        """CD-MIG-03d: published_theme nullable; draft_theme not nullable."""
        cols = card_draft_cls.__table__.columns
        assert cols["published_theme"].nullable is True, \
            "published_theme must be nullable (NULL = never published)"
        assert cols["draft_theme"].nullable is False, \
            "draft_theme must NOT be nullable"
        assert cols["draft_variant"].nullable is False
        assert cols["published_at"].nullable is True


# ── CD-MIG-04..06: Migration file metadata ─────────────────────────────────────

class TestMigrationMetadata:

    def test_cd_mig_04_migration_file_exists(self):
        """CD-MIG-04: Migration file 2026_05_15_1100_add_card_drafts.py exists."""
        assert _MIGRATION_PATH.exists(), (
            f"Migration file not found: {_MIGRATION_PATH}"
        )

    def test_cd_mig_05_revision_and_down_revision(self, migration_src):
        """CD-MIG-05: revision='2026_05_15_1100', down_revision='2026_05_14_1000'."""
        assert "revision      = '2026_05_15_1100'" in migration_src or \
               "revision = '2026_05_15_1100'" in migration_src, \
               "revision ID must be 2026_05_15_1100"
        assert "down_revision = '2026_05_14_1000'" in migration_src or \
               "down_revision = '2026_05_14_1000'" in migration_src, \
               "down_revision must be 2026_05_14_1000"

    def test_cd_mig_06_creates_card_drafts_table(self, migration_src):
        """CD-MIG-06: upgrade() calls op.create_table('card_drafts', ...)."""
        assert "op.create_table(" in migration_src
        assert "'card_drafts'" in migration_src

    def test_cd_mig_06b_drops_table_in_downgrade(self, migration_src):
        """CD-MIG-06b: downgrade() calls op.drop_table('card_drafts')."""
        assert "op.drop_table('card_drafts')" in migration_src

    def test_cd_mig_06c_creates_index_in_upgrade(self, migration_src):
        """CD-MIG-06c: upgrade() creates ix_card_drafts_user_id index."""
        assert "ix_card_drafts_user_id" in migration_src
        assert "op.create_index(" in migration_src

    def test_cd_mig_06d_drops_index_in_downgrade(self, migration_src):
        """CD-MIG-06d: downgrade() drops ix_card_drafts_user_id before dropping table."""
        assert "op.drop_index('ix_card_drafts_user_id'" in migration_src


# ── CD-MIG-07..08: Backfill SQL patterns ──────────────────────────────────────

class TestBackfillSql:

    def test_cd_mig_07_backfill_filters_lfa_football_player(self, migration_src):
        """CD-MIG-07: Backfill INSERT only selects LFA_FOOTBALL_PLAYER licences."""
        assert "LFA_FOOTBALL_PLAYER" in migration_src, (
            "Backfill must filter WHERE specialization_type = 'LFA_FOOTBALL_PLAYER'"
        )
        assert "WHERE ul.specialization_type = 'LFA_FOOTBALL_PLAYER'" in migration_src

    def test_cd_mig_08_backfill_is_idempotent(self, migration_src):
        """CD-MIG-08: Backfill uses ON CONFLICT DO NOTHING for idempotency."""
        assert "ON CONFLICT" in migration_src
        assert "DO NOTHING" in migration_src

    def test_cd_mig_08b_null_platform_preserved(self, migration_src):
        """CD-MIG-08b: draft_platform and published_platform copy raw NULL (no COALESCE)."""
        assert "ul.public_card_platform" in migration_src, \
            "draft_platform must copy ul.public_card_platform directly (NULL preserved)"
        assert "ul.published_card_platform" in migration_src, \
            "published_platform must copy ul.published_card_platform directly"

    def test_cd_mig_08c_theme_variant_coalesce(self, migration_src):
        """CD-MIG-08c: draft_theme/variant use COALESCE with correct defaults."""
        assert "COALESCE(ul.card_theme" in migration_src
        assert "COALESCE(ul.card_variant" in migration_src
        assert "'default'" in migration_src
        assert "'fifa'" in migration_src

    def test_cd_mig_08d_published_at_conditional(self, migration_src):
        """CD-MIG-08d: published_at set to NOW() only when published_card_theme IS NOT NULL."""
        assert "published_card_theme IS NOT NULL" in migration_src, (
            "published_at must be NULL when the user never published their card"
        )
