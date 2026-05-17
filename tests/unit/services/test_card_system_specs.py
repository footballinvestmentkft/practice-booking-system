"""CS_ — Card System Specification tests.

Verifies that CardTypeSpec, CardContentContract, VariantCapabilitySpec,
and CardRegistry are consistent and do NOT duplicate master data from
card_platform_service, card_variant_service, or card_constants.
"""
import pytest

from app.services.card_constants import (
    ANIMATED_EXPORT_CAPABLE,
    CARD_EDITOR_PLATFORM_IDS,
    WC_GALLERY_PLATFORM_IDS,
)
from app.services.card_variant_service import VARIANT_ORDER
from app.services.card_system import (
    CardRegistry,
    CardTypeSpec,
    ContentFieldKind,
    VariantCapabilitySpec,
    card_registry,
)
from app.services.card_system._player_card import PLAYER_CARD_CONTRACT, PLAYER_CARD_SPEC
from app.services.card_system._skeletal_specs import (
    BADGE_CARD_SPEC,
    BIRTHDAY_CARD_SPEC,
    EVENT_CARD_SPEC,
    MATCH_CARD_SPEC,
)
from app.services.card_system._types import CardContentContract, ContentField
from app.services.card_system._welcome_card import WELCOME_CARD_CONTRACT, WELCOME_CARD_SPEC
from app.services.card_system.registry import FIFA_CLASSIC_CAPABILITIES


# ── CS01–CS05: CardTypeSpec immutability and identity ───────────────────────

class TestCardTypeSpecImmutability:
    def test_cs01_player_card_spec_is_frozen(self):
        with pytest.raises((AttributeError, TypeError)):
            PLAYER_CARD_SPEC.card_type_id = "tampered"  # type: ignore[misc]

    def test_cs02_welcome_card_spec_is_frozen(self):
        with pytest.raises((AttributeError, TypeError)):
            WELCOME_CARD_SPEC.card_type_id = "tampered"  # type: ignore[misc]

    def test_cs03_player_card_type_id(self):
        assert PLAYER_CARD_SPEC.card_type_id == "player_card"

    def test_cs04_welcome_card_type_id(self):
        assert WELCOME_CARD_SPEC.card_type_id == "welcome_card"

    def test_cs05_content_field_is_frozen(self):
        field = PLAYER_CARD_CONTRACT.fields[0]
        with pytest.raises((AttributeError, TypeError)):
            field.key = "tampered"  # type: ignore[misc]


# ── CS06–CS08: No duplication of platform master data ───────────────────────

class TestNoDuplication:
    def test_cs06_player_card_platforms_equal_card_editor_platform_ids(self):
        assert PLAYER_CARD_SPEC.supported_platform_ids == CARD_EDITOR_PLATFORM_IDS

    def test_cs07_welcome_card_platforms_equal_wc_gallery_platform_ids(self):
        assert WELCOME_CARD_SPEC.supported_platform_ids == WC_GALLERY_PLATFORM_IDS

    def test_cs08_player_card_variants_equal_variant_order(self):
        assert PLAYER_CARD_SPEC.supported_variant_ids == tuple(VARIANT_ORDER)


# ── CS09–CS12: Content contract structure ───────────────────────────────────

class TestContentContract:
    def test_cs09_player_card_contract_version_1(self):
        assert PLAYER_CARD_CONTRACT.version == 1

    def test_cs10_welcome_card_contract_version_1(self):
        assert WELCOME_CARD_CONTRACT.version == 1

    def test_cs11_player_card_has_required_fields(self):
        required_keys = {f.key for f in PLAYER_CARD_CONTRACT.fields if f.kind == ContentFieldKind.REQUIRED}
        assert {"player_name", "overall_rating", "position", "skill_values", "card_theme", "card_variant"}.issubset(required_keys)

    def test_cs12_welcome_card_has_no_card_variant_field(self):
        keys = {f.key for f in WELCOME_CARD_CONTRACT.fields}
        assert "card_variant" not in keys, "Welcome Card is single-variant — card_variant is not a user-facing field"


# ── CS13–CS16: CardTypeSpec capability flags ────────────────────────────────

class TestCapabilityFlags:
    def test_cs13_player_card_editable(self):
        assert PLAYER_CARD_SPEC.is_editable is True

    def test_cs14_welcome_card_not_editable(self):
        assert WELCOME_CARD_SPEC.is_editable is False

    def test_cs15_player_card_has_published_state(self):
        assert PLAYER_CARD_SPEC.has_published_state is True

    def test_cs16_welcome_card_no_published_state(self):
        assert WELCOME_CARD_SPEC.has_published_state is False


# ── CS17–CS19: Skeletal v0 specs ────────────────────────────────────────────

class TestSkeletalSpecs:
    def test_cs17_skeletal_specs_have_version_0(self):
        for spec in (MATCH_CARD_SPEC, EVENT_CARD_SPEC, BIRTHDAY_CARD_SPEC, BADGE_CARD_SPEC):
            assert spec.content_contract.version == 0, f"{spec.card_type_id} version should be 0"

    def test_cs18_skeletal_specs_have_empty_platform_ids(self):
        for spec in (MATCH_CARD_SPEC, EVENT_CARD_SPEC, BIRTHDAY_CARD_SPEC, BADGE_CARD_SPEC):
            assert spec.supported_platform_ids == (), f"{spec.card_type_id} should have no platforms yet"

    def test_cs19_skeletal_specs_have_empty_variant_ids(self):
        for spec in (MATCH_CARD_SPEC, EVENT_CARD_SPEC, BIRTHDAY_CARD_SPEC, BADGE_CARD_SPEC):
            assert spec.supported_variant_ids == (), f"{spec.card_type_id} should have no variants yet"


# ── CS20–CS24: CardRegistry API ─────────────────────────────────────────────

class TestCardRegistry:
    def test_cs20_registry_is_singleton(self):
        assert isinstance(card_registry, CardRegistry)

    def test_cs21_get_card_type_spec_returns_player_card(self):
        spec = card_registry.get_card_type_spec("player_card")
        assert spec is PLAYER_CARD_SPEC

    def test_cs22_get_supported_platforms_delegates_to_spec(self):
        platforms = card_registry.get_supported_platforms("player_card")
        assert platforms == CARD_EDITOR_PLATFORM_IDS

    def test_cs23_get_supported_variants_delegates_to_spec(self):
        variants = card_registry.get_supported_variants("player_card")
        assert variants == tuple(VARIANT_ORDER)

    def test_cs24_unknown_card_type_raises_key_error(self):
        with pytest.raises(KeyError, match="unknown_type"):
            card_registry.get_card_type_spec("unknown_type")


# ── CS25–CS28: VariantCapabilitySpec (FIFA Classic) ─────────────────────────

class TestVariantCapabilitySpec:
    def test_cs25_fifa_classic_capabilities_variant_id(self):
        assert FIFA_CLASSIC_CAPABILITIES.variant_id == "fifa"

    def test_cs26_fifa_supports_player_and_welcome_card(self):
        assert "player_card" in FIFA_CLASSIC_CAPABILITIES.supported_card_types
        assert "welcome_card" in FIFA_CLASSIC_CAPABILITIES.supported_card_types

    def test_cs27_fifa_animated_mode_true(self):
        assert FIFA_CLASSIC_CAPABILITIES.animated_mode is True

    def test_cs28_get_variant_capabilities_returns_none_for_unknown(self):
        result = card_registry.get_variant_capabilities("nonexistent_variant")
        assert result is None


# ── CS29–CS30: is_animated_capable delegates to card_constants ──────────────

class TestAnimatedCapable:
    def test_cs29_is_animated_capable_delegates_to_constants(self):
        for variant_id, platform_id in ANIMATED_EXPORT_CAPABLE:
            assert card_registry.is_animated_capable(variant_id, platform_id) is True

    def test_cs30_non_animated_combo_returns_false(self):
        assert card_registry.is_animated_capable("fifa", "instagram_portrait") is False


# ── CS31: list_card_type_ids includes all registered types ──────────────────

class TestListCardTypeIds:
    def test_cs31_list_includes_all_six_types(self):
        ids = card_registry.list_card_type_ids()
        assert set(ids) == {
            "player_card",
            "welcome_card",
            "match_card",
            "event_card",
            "birthday_card",
            "badge_card",
        }
