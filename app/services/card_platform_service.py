"""Player card platform preset definitions.

Each preset carries:
  - id / label / css_class / description  — display + rendering metadata
  - layout_strategy                        — drives internal layout reflow in templates

LayoutStrategy drives CSS classes added to <body> (via platform_class) so templates
can apply different internal layouts per strategy without JS.

Layout strategies
-----------------
NATIVE    — default preview; no viewport constraint; normal card proportions
PORTRAIT  — 1:1 / 4:5 / 9:16 canvases; column layout fills full viewport height
LANDSCAPE — ~2:1 canvases; two-column layout (identity left | skills right)
BANNER    — 3:1 canvas; identity-first strip; skills/events hidden
"""
from dataclasses import dataclass
from enum import Enum


class LayoutStrategy(str, Enum):
    NATIVE    = "native"
    PORTRAIT  = "portrait"
    LANDSCAPE = "landscape"
    BANNER    = "banner"


@dataclass(frozen=True)
class PlatformPresetDefinition:
    id: str
    label: str
    css_class: str
    description: str
    layout_strategy: LayoutStrategy


PLATFORM_PRESETS: dict[str, PlatformPresetDefinition] = {
    "default": PlatformPresetDefinition(
        "default", "Default", "", "Native card proportions", LayoutStrategy.NATIVE
    ),
    "instagram_square": PlatformPresetDefinition(
        "instagram_square", "Instagram Square",
        "platform-instagram-square",
        "Instagram Feed · 1080×1080",
        LayoutStrategy.PORTRAIT,
    ),
    "instagram_portrait": PlatformPresetDefinition(
        "instagram_portrait", "Instagram Portrait",
        "platform-instagram-portrait",
        "Instagram Feed · 1080×1350",
        LayoutStrategy.PORTRAIT,
    ),
    "instagram_story": PlatformPresetDefinition(
        "instagram_story", "Instagram Story",
        "platform-instagram-story",
        "Instagram Story · 1080×1920",
        LayoutStrategy.PORTRAIT,
    ),
    "tiktok": PlatformPresetDefinition(
        "tiktok", "TikTok",
        "platform-tiktok",
        "TikTok · 1080×1920",
        LayoutStrategy.PORTRAIT,
    ),
    "facebook_square": PlatformPresetDefinition(
        "facebook_square", "Facebook Square",
        "platform-facebook-square",
        "Facebook Square · 1080×1080",
        LayoutStrategy.PORTRAIT,
    ),
    "facebook_landscape": PlatformPresetDefinition(
        "facebook_landscape", "Facebook Landscape",
        "platform-facebook-landscape",
        "Facebook / OG · 1200×630",
        LayoutStrategy.LANDSCAPE,
    ),
    "og": PlatformPresetDefinition(
        "og", "Open Graph",
        "platform-og",
        "Open Graph / LinkedIn · 1200×630",
        LayoutStrategy.LANDSCAPE,
    ),
    "banner_custom": PlatformPresetDefinition(
        "banner_custom", "Banner",
        "platform-banner-custom",
        "Facebook Cover · 1500×500",
        LayoutStrategy.BANNER,
    ),
    "facebook_post": PlatformPresetDefinition(
        "facebook_post", "Facebook Post",
        "platform-facebook-post",
        "Facebook Post · 1200×630 — 3-column FClassic layout with all skills",
        LayoutStrategy.LANDSCAPE,
    ),
}

_FALLBACK = PLATFORM_PRESETS["default"]


def get_preset(preset_id: str | None) -> PlatformPresetDefinition:
    """Return the preset for preset_id, falling back to 'default' for unknown ids."""
    return PLATFORM_PRESETS.get(preset_id or "default", _FALLBACK)


def build_platform_list(platform_ids: tuple[str, ...]) -> list[dict]:
    """Build a template-ready platform list from authoritative sources.

    Each entry contains:
      id    — platform preset id (snake_case)
      label — human-readable name from PLATFORM_PRESETS
      title — description string suitable for HTML title attribute
      dims  — formatted dimension string, e.g. "1080 × 1080"
      w, h  — integer pixel dimensions from CANVAS_SIZES
    """
    from .card_constants import CANVAS_SIZES  # local import avoids circular dep
    result = []
    for pid in platform_ids:
        preset = get_preset(pid)
        w, h = CANVAS_SIZES[pid]
        result.append({
            "id":    pid,
            "label": preset.label,
            "title": preset.description,
            "dims":  f"{w} × {h}",
            "w":     w,
            "h":     h,
        })
    return result
