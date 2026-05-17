"""Skeletal v0 specs for future card types — content contracts not yet defined."""
from app.services.card_system._types import (
    CardContentContract,
    CardTypeSpec,
    ContentField,
    ContentFieldKind,
)

_R = ContentFieldKind.REQUIRED

MATCH_CARD_CONTRACT = CardContentContract(
    card_type_id="match_card",
    version=0,
    fields=(
        ContentField("player_name", _R, "str", "Full display name"),
    ),
)

MATCH_CARD_SPEC = CardTypeSpec(
    card_type_id="match_card",
    label="Match Card",
    description="Post-match performance card — v0 skeletal, not yet implemented.",
    content_contract=MATCH_CARD_CONTRACT,
    supported_variant_ids=(),
    supported_platform_ids=(),
    theme_compatible=False,
    has_published_state=False,
    is_editable=False,
)

EVENT_CARD_CONTRACT = CardContentContract(
    card_type_id="event_card",
    version=0,
    fields=(
        ContentField("player_name", _R, "str", "Full display name"),
    ),
)

EVENT_CARD_SPEC = CardTypeSpec(
    card_type_id="event_card",
    label="Event Card",
    description="Training event card — v0 skeletal, not yet implemented.",
    content_contract=EVENT_CARD_CONTRACT,
    supported_variant_ids=(),
    supported_platform_ids=(),
    theme_compatible=False,
    has_published_state=False,
    is_editable=False,
)

BIRTHDAY_CARD_CONTRACT = CardContentContract(
    card_type_id="birthday_card",
    version=0,
    fields=(
        ContentField("player_name", _R, "str", "Full display name"),
    ),
)

BIRTHDAY_CARD_SPEC = CardTypeSpec(
    card_type_id="birthday_card",
    label="Birthday Card",
    description="Birthday celebration card — v0 skeletal, not yet implemented.",
    content_contract=BIRTHDAY_CARD_CONTRACT,
    supported_variant_ids=(),
    supported_platform_ids=(),
    theme_compatible=False,
    has_published_state=False,
    is_editable=False,
)

BADGE_CARD_CONTRACT = CardContentContract(
    card_type_id="badge_card",
    version=0,
    fields=(
        ContentField("player_name", _R, "str", "Full display name"),
    ),
)

BADGE_CARD_SPEC = CardTypeSpec(
    card_type_id="badge_card",
    label="Badge Card",
    description="Achievement badge card — v0 skeletal, not yet implemented.",
    content_contract=BADGE_CARD_CONTRACT,
    supported_variant_ids=(),
    supported_platform_ids=(),
    theme_compatible=False,
    has_published_state=False,
    is_editable=False,
)
