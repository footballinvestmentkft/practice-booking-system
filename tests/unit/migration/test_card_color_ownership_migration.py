"""
MG — card_color_ownership migration tests (TS-1).

Static code-analysis tests: verify ORM model structure, migration metadata,
SQL patterns, and backfill contract without hitting a real database.

MG-01: card_color_ownership table name in migration source
MG-02: UNIQUE constraint present in migration source
MG-03: ix_cco_user_id index in migration source
MG-04: ix_cco_family_color index in migration source
MG-05: backfill INSERT statement present in migration source
MG-06: backfill uses ON CONFLICT DO NOTHING
MG-07: backfill guarded by known color ID set
MG-08: backfill skips empty/null JSON (filter in WHERE clause)
MG-09: backfill is idempotent (ON CONFLICT DO NOTHING)
MG-10: downgrade drops the table
MG-11: downgrade does NOT touch unlocked_card_themes
MG-12: ORM model has all required columns
MG-13: ORM model unique constraint defined
MG-14: ORM model indexes defined
"""
import pathlib
import pytest

_MIGRATION_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "alembic" / "versions" / "2026_05_29_1200_add_card_color_ownership.py"
)

_MODEL_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "app" / "models" / "card_color_ownership.py"
)


def _migration_src() -> str:
    return _MIGRATION_PATH.read_text()


def _model_src() -> str:
    return _MODEL_PATH.read_text()


@pytest.fixture(scope="module")
def migration_src():
    return _migration_src()


@pytest.fixture(scope="module")
def model_src():
    return _model_src()


@pytest.fixture(scope="module")
def cco_cls():
    from app.models.card_color_ownership import CardColorOwnership
    return CardColorOwnership


# ── MG-01..MG-11: Migration source analysis ────────────────────────────────────

class TestCardColorOwnershipMigrationSource:

    def test_mg_01_table_name_in_migration(self, migration_src):
        assert "card_color_ownership" in migration_src

    def test_mg_02_unique_constraint_present(self, migration_src):
        assert "uq_cco_user_type_color" in migration_src

    def test_mg_03_user_id_index_present(self, migration_src):
        assert "ix_cco_user_id" in migration_src

    def test_mg_04_family_color_index_present(self, migration_src):
        assert "ix_cco_family_color" in migration_src

    def test_mg_05_backfill_insert_present(self, migration_src):
        assert "INSERT INTO card_color_ownership" in migration_src

    def test_mg_06_backfill_on_conflict_do_nothing(self, migration_src):
        assert "ON CONFLICT" in migration_src
        assert "DO NOTHING" in migration_src

    def test_mg_07_backfill_guarded_by_known_color_ids(self, migration_src):
        assert "_KNOWN_COLOR_IDS" in migration_src
        assert "gold" in migration_src
        assert "emerald" in migration_src
        assert "crimson" in migration_src

    def test_mg_08_backfill_skips_empty_json(self, migration_src):
        assert "'[]'" in migration_src or "NOT IN" in migration_src

    def test_mg_09_backfill_idempotent_via_on_conflict(self, migration_src):
        assert "ON CONFLICT (user_id, card_type_id, color_id) DO NOTHING" in migration_src

    def test_mg_10_downgrade_drops_table(self, migration_src):
        assert "drop_table" in migration_src
        assert "card_color_ownership" in migration_src.split("def downgrade")[1]

    def test_mg_11_downgrade_does_not_touch_unlocked_card_themes(self, migration_src):
        downgrade_section = migration_src.split("def downgrade")[1]
        # No ALTER TABLE or DROP COLUMN touching unlocked_card_themes in downgrade
        assert "alter_column" not in downgrade_section
        assert "drop_column" not in downgrade_section
        # Only drop_index + drop_table operations present
        assert "drop_table(\"card_color_ownership\")" in downgrade_section or \
               "drop_table('card_color_ownership')" in downgrade_section


# ── MG-12..MG-14: ORM model structure ─────────────────────────────────────────

class TestCardColorOwnershipOrmModel:

    def test_mg_12_all_required_columns_present(self, cco_cls):
        cols = {c.key for c in cco_cls.__table__.columns}
        assert "id"           in cols
        assert "user_id"      in cols
        assert "card_type_id" in cols
        assert "color_id"     in cols
        assert "pack_id"      in cols
        assert "purchased_at" in cols

    def test_mg_13_unique_constraint_defined(self, cco_cls):
        constraint_names = {
            c.name for c in cco_cls.__table__.constraints
        }
        assert "uq_cco_user_type_color" in constraint_names

    def test_mg_14_indexes_defined(self, cco_cls):
        index_names = {i.name for i in cco_cls.__table__.indexes}
        assert "ix_cco_user_id" in index_names
        assert "ix_cco_family_color" in index_names

    def test_mg_15_pack_id_is_nullable(self, cco_cls):
        col = cco_cls.__table__.columns["pack_id"]
        assert col.nullable is True

    def test_mg_16_tablename_correct(self, cco_cls):
        assert cco_cls.__tablename__ == "card_color_ownership"
