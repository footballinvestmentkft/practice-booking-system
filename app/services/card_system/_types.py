"""Frozen dataclasses for card type specifications and content contracts."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class ContentFieldKind(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"
    COMPUTED = "computed"


@dataclass(frozen=True)
class ContentField:
    key: str
    kind: ContentFieldKind
    python_type: str
    description: str


@dataclass(frozen=True)
class CardContentContract:
    card_type_id: str
    version: int
    fields: tuple[ContentField, ...]


@dataclass(frozen=True)
class VariantCapabilitySpec:
    """Capability/compatibility layer only — does NOT duplicate VariantDefinition master data."""
    variant_id: str
    supported_card_types: tuple[str, ...]
    content_blocks: tuple[str, ...]
    animated_mode: bool


@dataclass(frozen=True)
class CardTypeSpec:
    card_type_id: str
    label: str
    description: str
    content_contract: CardContentContract
    supported_variant_ids: tuple[str, ...]
    supported_platform_ids: tuple[str, ...]
    theme_compatible: bool
    has_published_state: bool
    is_editable: bool
