"""
MIG-01..MIG-11  DB migration data correctness (PR-FC-1B)
CDO-01..CDO-04  Ownership backward compatibility
SVC-01..SVC-06  card_design_service canonical state

These tests assert the *code-level* post-migration state: model defaults,
DESIGNS dict canonical key, DESIGN_ORDER, fallback chain, and write guard.
DB-level integration (MIG-01..MIG-07) are covered by the Migration Chain
Integrity and Migration Volume Safety CI workflows which run the full upgrade
on a live test DB.

MIG-01  card_designs.id="fclassic" canonical entry in DESIGNS fallback dict
MIG-02  card_designs.id="fifa" is NOT a standalone entry; only alias
MIG-09  user_licenses.card_variant Python ORM default = "fclassic"
MIG-10  user_licenses.published_card_variant Python ORM default = "fclassic"
MIG-11  card_drafts.draft_variant server_default = "fclassic"
CDO-01  get_owned_design_ids alias: CDO with design_id="fclassic" is returned
CDO-02  is_design_accessible(..., "fclassic") True when CDO exists
CDO-03  is_design_accessible(..., "fifa") True via alias when "fclassic" CDO exists
CDO-04  assert_new_design_id_is_canonical("fifa") raises ValueError
SVC-01  get_design("fclassic") returns canonical FClassic Player design
SVC-02  get_design("fifa") alias-resolves → fclassic design
SVC-03  DESIGNS["fclassic"] exists and has id="fclassic"
SVC-04  DESIGN_ORDER[0] == "fclassic"
SVC-05  assert_new_design_id_is_canonical("fifa") raises ValueError
SVC-06  admin _PROTECTED_ID == "fclassic"
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ── MIG-01/02: DESIGNS fallback dict canonical key ────────────────────────────

class TestMig0102DesignsFallbackDict:

    def test_mig_01_fclassic_in_designs(self):
        """MIG-01: DESIGNS fallback dict has canonical 'fclassic' key."""
        from app.services.card_design_service import DESIGNS
        assert "fclassic" in DESIGNS, (
            "DESIGNS fallback dict must contain canonical 'fclassic' key"
        )
        assert DESIGNS["fclassic"].id == "fclassic"

    def test_mig_02_fifa_removed_from_designs_dict(self):
        """MIG-02: DESIGNS['fifa'] dict key removed after PR-FC-1F — 'fifa' is input-only."""
        from app.services.card_design_service import DESIGNS
        assert "fifa" not in DESIGNS, (
            "DESIGNS['fifa'] dict key must be removed in PR-FC-1F; "
            "legacy 'fifa' input is handled by resolve_design_id() sanitizer only"
        )

    def test_mig_02b_fclassic_design_label_is_correct(self):
        """MIG-02b: DESIGNS['fclassic'].label is 'FClassic Player'."""
        from app.services.card_design_service import DESIGNS
        assert DESIGNS["fclassic"].label == "FClassic Player"


# ── MIG-09..MIG-11: Model defaults ────────────────────────────────────────────

class TestMig0911ModelDefaults:

    def test_mig_09_user_license_card_variant_default(self):
        """MIG-09: UserLicense.card_variant Python ORM default == 'fclassic'."""
        from app.models.license import UserLicense
        col = UserLicense.__table__.c["card_variant"]
        # SQLAlchemy stores Python ORM defaults as Column.default.arg
        assert col.default is not None, "card_variant must have a default"
        assert col.default.arg == "fclassic", (
            f"card_variant default must be 'fclassic', got {col.default.arg!r}"
        )

    def test_mig_10_user_license_published_card_variant_default(self):
        """MIG-10: UserLicense.published_card_variant Python ORM default == 'fclassic'."""
        from app.models.license import UserLicense
        col = UserLicense.__table__.c["published_card_variant"]
        assert col.default is not None, "published_card_variant must have a default"
        assert col.default.arg == "fclassic", (
            f"published_card_variant default must be 'fclassic', got {col.default.arg!r}"
        )

    def test_mig_11_card_draft_variant_server_default(self):
        """MIG-11: CardDraft.draft_variant server_default == 'fclassic'."""
        from app.models.card_draft import CardDraft
        col = CardDraft.__table__.c["draft_variant"]
        assert col.server_default is not None, "draft_variant must have a server_default"
        # server_default.arg is a string or text clause
        default_val = str(col.server_default.arg)
        assert "fclassic" in default_val, (
            f"draft_variant server_default must contain 'fclassic', got {default_val!r}"
        )


# ── CDO-01..CDO-04: Ownership backward compatibility ─────────────────────────

class TestCdo01to04OwnershipBackwardCompat:

    _SVC = "app.services.card_design_service"

    def _make_db_with_cdo(self, design_id="fclassic"):
        """Mock DB that returns a single CDO row for the given design_id."""
        cdo_row = MagicMock()
        cdo_row.design_id = design_id
        lic = MagicMock()
        lic.unlocked_card_variants = []
        db = MagicMock()
        # get_owned_design_ids calls .filter_by().all() for CDO rows
        db.query.return_value.filter_by.return_value.all.return_value = [cdo_row]
        # also calls .filter_by().first() for UserLicense legacy shim
        db.query.return_value.filter_by.return_value.first.return_value = lic
        return db

    def test_cdo_01_get_owned_design_ids_returns_fclassic(self):
        """CDO-01: get_owned_design_ids for player_card returns 'fclassic' (not 'fifa')."""
        from app.services.card_design_service import get_owned_design_ids
        db = self._make_db_with_cdo("fclassic")
        owned = get_owned_design_ids(db, user_id=42, card_type_id="player_card")
        assert "fclassic" in owned, (
            f"get_owned_design_ids must return 'fclassic'; got {owned}"
        )

    def test_cdo_02_is_design_accessible_fclassic_true(self):
        """CDO-02: is_design_accessible(..., 'fclassic') True when CDO exists."""
        from app.services.card_design_service import is_design_accessible
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = MagicMock()
        result = is_design_accessible(db, user_id=42, card_type_id="player_card",
                                      design_id="fclassic")
        assert result is True

    def test_cdo_03_is_design_accessible_fifa_alias_true(self):
        """CDO-03: is_design_accessible(..., 'fifa') → alias resolve → 'fclassic' CDO."""
        from app.services.card_design_service import is_design_accessible
        db = MagicMock()
        # Mock: query for "fclassic" returns a row (post-migration state)
        db.query.return_value.filter_by.return_value.first.return_value = MagicMock()
        result = is_design_accessible(db, user_id=42, card_type_id="player_card",
                                      design_id="fifa")
        assert result is True, (
            "is_design_accessible('fifa') must resolve via alias and return True "
            "when 'fclassic' CDO exists"
        )

    def test_cdo_04_new_canonical_write_cannot_use_fifa(self):
        """CDO-04: assert_new_design_id_is_canonical('fifa') raises ValueError."""
        from app.services.card_design_service import assert_new_design_id_is_canonical
        with pytest.raises(ValueError, match="fclassic"):
            assert_new_design_id_is_canonical("fifa")


# ── SVC-01..SVC-06: Service canonical state ───────────────────────────────────

class TestSvc01to06ServiceState:

    def test_svc_01_get_design_fclassic_returns_canonical(self):
        """SVC-01: get_design('fclassic') returns canonical FClassic Player design."""
        from app.services.card_design_service import get_design
        design = get_design("fclassic")
        assert design is not None
        assert design.id == "fclassic"
        assert design.label == "FClassic Player"

    def test_svc_02_get_design_fifa_alias_resolves_to_fclassic(self):
        """SVC-02: get_design('fifa') alias-resolves → returns design with id='fclassic'."""
        from app.services.card_design_service import get_design
        design = get_design("fifa")
        assert design is not None
        assert design.id == "fclassic", (
            f"get_design('fifa').id must be 'fclassic' (alias), got {design.id!r}"
        )

    def test_svc_03_designs_fclassic_exists(self):
        """SVC-03: DESIGNS['fclassic'] exists in fallback dict."""
        from app.services.card_design_service import DESIGNS
        assert "fclassic" in DESIGNS
        assert DESIGNS["fclassic"].id == "fclassic"

    def test_svc_04_design_order_starts_with_fclassic(self):
        """SVC-04: DESIGN_ORDER[0] == 'fclassic'."""
        from app.services.card_design_service import DESIGN_ORDER
        assert DESIGN_ORDER[0] == "fclassic", (
            f"DESIGN_ORDER must start with 'fclassic', got {DESIGN_ORDER[0]!r}"
        )

    def test_svc_05_write_guard_still_blocks_fifa(self):
        """SVC-05: assert_new_design_id_is_canonical('fifa') still raises ValueError."""
        from app.services.card_design_service import assert_new_design_id_is_canonical
        with pytest.raises(ValueError):
            assert_new_design_id_is_canonical("fifa")

    def test_svc_06_protected_id_is_fclassic(self):
        """SVC-06: admin _PROTECTED_ID == 'fclassic' after PR-FC-1B."""
        from app.api.web_routes.admin.card_designs import _PROTECTED_ID
        assert _PROTECTED_ID == "fclassic", (
            f"_PROTECTED_ID must be 'fclassic' after DB PK migration, got {_PROTECTED_ID!r}"
        )
