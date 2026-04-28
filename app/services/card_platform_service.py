"""Player card platform preset definitions.

A platform preset constrains the card-wrap to a specific aspect ratio so the
card can be screenshotted at an exact social-media canvas size without JS or
server-side image generation. The preset is purely a CSS class injected at
render time — it is never persisted to the database.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformPresetDefinition:
    id: str
    label: str
    css_class: str
    description: str


PLATFORM_PRESETS: dict[str, PlatformPresetDefinition] = {
    "default": PlatformPresetDefinition(
        "default", "Default", "", "Native card proportions"
    ),
    "square": PlatformPresetDefinition(
        "square", "Square (1:1)", "platform-square", "Instagram post, 1080×1080"
    ),
    "story": PlatformPresetDefinition(
        "story", "Story (9:16)", "platform-story", "Instagram / TikTok story"
    ),
    "landscape": PlatformPresetDefinition(
        "landscape", "Landscape (16:9)", "platform-landscape", "Twitter / YouTube banner"
    ),
    "banner": PlatformPresetDefinition(
        "banner", "Banner (3:1)", "platform-banner", "Facebook cover"
    ),
    "og": PlatformPresetDefinition(
        "og", "OG (1.91:1)", "platform-og", "Open Graph / LinkedIn"
    ),
}

_FALLBACK = PLATFORM_PRESETS["default"]


def get_preset(preset_id: str | None) -> PlatformPresetDefinition:
    """Return the preset for preset_id, falling back to 'default' for unknown ids."""
    return PLATFORM_PRESETS.get(preset_id or "default", _FALLBACK)
