"""Player Card v1 specification — content contract + CardTypeSpec."""
from app.services.card_constants import CARD_EDITOR_PLATFORM_IDS
from app.services.card_variant_service import VARIANT_ORDER
from app.services.card_system._types import (
    CardContentContract,
    CardTypeSpec,
    ContentField,
    ContentFieldKind,
)

_R = ContentFieldKind.REQUIRED
_O = ContentFieldKind.OPTIONAL
_C = ContentFieldKind.COMPUTED

PLAYER_CARD_CONTRACT = CardContentContract(
    card_type_id="player_card",
    version=1,
    fields=(
        ContentField("player_name", _R, "str", "Full display name"),
        ContentField("overall_rating", _R, "int", "Overall rating 0–99"),
        ContentField("position", _R, "str", "Primary position abbreviation"),
        ContentField("skill_values", _R, "dict[str, float]", "Skill key → EMA value mapping"),
        ContentField("card_theme", _R, "str", "Theme ID (e.g. 'default', 'midnight')"),
        ContentField("card_variant", _R, "str", "Variant ID (e.g. 'fifa', 'compact')"),
        ContentField("nationality", _O, "str | None", "Two-letter nationality code"),
        ContentField("photo_url", _O, "str | None", "Player headshot URL"),
        ContentField("club_name", _O, "str | None", "Club display name"),
        ContentField("club_logo_url", _O, "str | None", "Club logo image URL"),
        ContentField("sponsor_logo_url", _O, "str | None", "Sponsor logo URL (may be suppressed)"),
        ContentField("age", _O, "int | None", "Player age"),
        ContentField("height_cm", _O, "int | None", "Height in centimetres"),
        ContentField("weight_kg", _O, "int | None", "Weight in kilograms"),
        ContentField("foot", _O, "str | None", "Preferred foot: 'left', 'right', or 'both'"),
        ContentField("skill_categories", _C, "dict[str, list[str]]", "Category → skill key list (derived from skill taxonomy)"),
        ContentField("export_mode", _C, "bool", "True when rendering for PNG/video export"),
        ContentField("animated_mode", _C, "bool", "True when rendering animated video export"),
    ),
)

PLAYER_CARD_SPEC = CardTypeSpec(
    card_type_id="player_card",
    label="Player Card",
    description="FIFA-style skill card for football players with overall rating and skill breakdown.",
    content_contract=PLAYER_CARD_CONTRACT,
    supported_variant_ids=tuple(VARIANT_ORDER),
    supported_platform_ids=CARD_EDITOR_PLATFORM_IDS,
    theme_compatible=True,
    has_published_state=True,
    is_editable=True,
)
