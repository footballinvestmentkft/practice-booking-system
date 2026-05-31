"""Welcome Card v1 specification — content contract + CardTypeSpec."""
from app.services.card_constants import WC_GALLERY_PLATFORM_IDS
from app.services.card_system._types import (
    CardContentContract,
    CardTypeSpec,
    ContentField,
    ContentFieldKind,
)

_R = ContentFieldKind.REQUIRED
_O = ContentFieldKind.OPTIONAL
_C = ContentFieldKind.COMPUTED

WELCOME_CARD_CONTRACT = CardContentContract(
    card_type_id="welcome_card",
    version=1,
    fields=(
        ContentField("player_name", _R, "str", "Full display name"),
        ContentField("overall_rating", _R, "int", "Overall OVR from self-assessment adapter"),
        ContentField("position", _R, "str", "Primary position abbreviation"),
        ContentField("skill_values", _R, "dict[str, float]", "Skill key → value from self-assessment"),
        ContentField("card_theme", _R, "str", "Theme ID"),
        ContentField("nationality", _O, "str | None", "Two-letter nationality code"),
        ContentField("photo_url", _O, "str | None", "Player headshot URL"),
        ContentField("club_name", _O, "str | None", "Club display name"),
        ContentField("club_logo_url", _O, "str | None", "Club logo image URL"),
        ContentField("skill_categories", _C, "dict[str, list[str]]", "Category → skill key list"),
        ContentField("export_mode", _C, "bool", "True when rendering for PNG export"),
    ),
)

WELCOME_CARD_SPEC = CardTypeSpec(
    card_type_id="welcome_card",
    label="Welcome Card",
    description="Onboarding card generated from self-assessment data; single variant (FClassic Player), not user-editable.",
    content_contract=WELCOME_CARD_CONTRACT,
    # "fifa" here names the export template bucket (public/export/*/fifa.html),
    # not a Player Card editor variant — different semantic from PLAYER_CARD_SPEC.
    supported_variant_ids=("fifa",),
    supported_platform_ids=WC_GALLERY_PLATFORM_IDS,
    theme_compatible=True,
    has_published_state=False,
    is_editable=False,
)
