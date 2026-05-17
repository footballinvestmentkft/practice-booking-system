"""Card registry — single lookup point for all CardTypeSpec instances."""
from __future__ import annotations

from app.services.card_constants import ANIMATED_EXPORT_CAPABLE
from app.services.card_system._player_card import PLAYER_CARD_SPEC
from app.services.card_system._skeletal_specs import (
    BADGE_CARD_SPEC,
    BIRTHDAY_CARD_SPEC,
    EVENT_CARD_SPEC,
    MATCH_CARD_SPEC,
)
from app.services.card_system._types import CardTypeSpec, VariantCapabilitySpec
from app.services.card_system._welcome_card import WELCOME_CARD_SPEC

# FIFA Classic is the only variant with animated export capability.
# All other capability attributes are derived from the variant master data
# in card_variant_service — this spec adds the card-system-level layer only.
FIFA_CLASSIC_CAPABILITIES = VariantCapabilitySpec(
    variant_id="fifa",
    supported_card_types=("player_card", "welcome_card"),
    content_blocks=("hero", "skill_rows", "sponsor"),
    animated_mode=True,
)

_VARIANT_CAPABILITIES: dict[str, VariantCapabilitySpec] = {
    FIFA_CLASSIC_CAPABILITIES.variant_id: FIFA_CLASSIC_CAPABILITIES,
}

_ALL_SPECS: tuple[CardTypeSpec, ...] = (
    PLAYER_CARD_SPEC,
    WELCOME_CARD_SPEC,
    MATCH_CARD_SPEC,
    EVENT_CARD_SPEC,
    BIRTHDAY_CARD_SPEC,
    BADGE_CARD_SPEC,
)

_REGISTRY: dict[str, CardTypeSpec] = {spec.card_type_id: spec for spec in _ALL_SPECS}


class CardRegistry:
    def get_card_type_spec(self, card_type_id: str) -> CardTypeSpec:
        if card_type_id not in _REGISTRY:
            raise KeyError(f"Unknown card type: {card_type_id!r}")
        return _REGISTRY[card_type_id]

    def get_supported_platforms(self, card_type_id: str) -> tuple[str, ...]:
        return self.get_card_type_spec(card_type_id).supported_platform_ids

    def get_supported_variants(self, card_type_id: str) -> tuple[str, ...]:
        return self.get_card_type_spec(card_type_id).supported_variant_ids

    def get_variant_capabilities(self, variant_id: str) -> VariantCapabilitySpec | None:
        return _VARIANT_CAPABILITIES.get(variant_id)

    def list_card_type_ids(self) -> list[str]:
        return list(_REGISTRY.keys())

    def is_animated_capable(self, variant_id: str, platform_id: str) -> bool:
        """Delegates to card_constants — no duplication of ANIMATED_EXPORT_CAPABLE."""
        return (variant_id, platform_id) in ANIMATED_EXPORT_CAPABLE


card_registry = CardRegistry()
