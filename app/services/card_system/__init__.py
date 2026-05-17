"""card_system — card type specifications, content contracts, and variant capabilities."""
from app.services.card_system._types import (
    CardContentContract,
    CardTypeSpec,
    ContentField,
    ContentFieldKind,
    VariantCapabilitySpec,
)
from app.services.card_system.registry import CardRegistry, card_registry

__all__ = [
    "CardContentContract",
    "CardRegistry",
    "CardTypeSpec",
    "ContentField",
    "ContentFieldKind",
    "VariantCapabilitySpec",
    "card_registry",
]
