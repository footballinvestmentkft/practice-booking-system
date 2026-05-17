"""Authoritative export constants for the FIFA Classic / Welcome Card card system.

All platform dimensions, template-bucket routing, and animated-capability
declarations live here.  Every other module imports from this file — never
defines its own copy.

Invariants enforced by tests/unit/services/test_card_constants.py:
  - EXPORT_FORMAT_BUCKETS.keys() == CANVAS_SIZES.keys() − {"default"}
  - Every platform_id in ANIMATED_EXPORT_CAPABLE exists in CANVAS_SIZES
"""
from __future__ import annotations

# ── Canvas dimensions ─────────────────────────────────────────────────────────
# Social canvas sizes keyed by platform preset id.
#
# "default" export canvas — original measurement 2026-05-12 via Playwright at 820px
# viewport using ?native_export=1.  Layout since changed: skills panel is now a 2×2
# grid (Outfield+Mental top row / Set Pieces+Physical bottom row) and the position
# panel uses a portrait SVG pitch (viewBox "0 0 65 100", GK bottom, ST top).
# CSS-derived estimate: header≈170px + tab-bar≈33px + card-body≈590px ≈ 793px →
# rounded to 800px.  Re-measure with Playwright after deploying the new layout to
# confirm; update this value and the comment date if the live clip differs.
# Export path: render_url uses ?native_export=1; _sync_take_screenshot clips
# to the card-wrap bounding rect — height is content-determined at render time,
# so the export is never truncated regardless of what this constant says.
CANVAS_SIZES: dict[str, tuple[int, int]] = {
    "default":            ( 820,  800),   # native FIFA Classic; clip = card-wrap bbox (est. 2026-05-12)
    "instagram_square":   (1080, 1080),
    "instagram_portrait": (1080, 1350),
    "instagram_story":    (1080, 1920),
    "tiktok":             (1080, 1920),
    "facebook_square":    (1080, 1080),
    "facebook_landscape": (1200,  630),
    "og":                 (1200,  630),
    "banner_custom":      (1500,  500),
    "facebook_post":      (1200,  630),
}

# ── Export template routing ───────────────────────────────────────────────────
# Maps platform preset id → template bucket directory.
# Template path resolved as: public/export/{bucket}/{card_variant_id}.html
EXPORT_FORMAT_BUCKETS: dict[str, str] = {
    "instagram_square":   "square",
    "facebook_square":    "square",
    "instagram_portrait": "portrait",
    "instagram_story":    "story",
    "tiktok":             "tiktok",
    "facebook_landscape": "landscape",
    "og":                 "landscape",
    "banner_custom":      "banner",
    "facebook_post":      "landscape",
}

# ── Animated video export capability registry ─────────────────────────────────
# (variant_id, platform_id) pairs that have a dedicated animated export
# template.  All other combinations return 422 — no fallback, no silent
# degradation.
ANIMATED_EXPORT_CAPABLE: frozenset[tuple[str, str]] = frozenset({
    ("fifa",  "instagram_square"),
    ("pulse", "instagram_square"),
})


def is_animated_capable(variant_id: str, platform_id: str) -> bool:
    """Return True if (variant_id, platform_id) supports animated video export."""
    return (variant_id, platform_id) in ANIMATED_EXPORT_CAPABLE


# ── Gallery / editor platform ID lists ───────────────────────────────────────

# Maps WC platform IDs to the Dir C layout template name under export/welcome/.
# Layout files: panel · full_bleed · cinematic · split · band · banner
WC_PLATFORM_LAYOUT: dict[str, str] = {
    "instagram_square":   "panel",
    "instagram_portrait": "panel",
    "instagram_story":    "panel",
    "tiktok":             "cinematic",
    "facebook_square":    "full_bleed",
    "facebook_landscape": "split",
    "facebook_post":      "split",
    "og":                 "band",
    "banner_custom":      "banner",
}

# Short archetype label shown as a style badge in the gallery picker.
# Drives visual differentiation between platforms with the same canvas size
# (e.g. instagram_square vs facebook_square — both 1080×1080, different layouts).
WC_PLATFORM_STYLE_TAGS: dict[str, str] = {
    "instagram_square":   "IDENTITY CARD",
    "instagram_portrait": "IDENTITY CARD",
    "instagram_story":    "IDENTITY CARD",
    "tiktok":             "CINEMATIC",
    "facebook_square":    "EDITORIAL",
    "facebook_landscape": "LANDSCAPE",
    "facebook_post":      "LANDSCAPE",
    "og":                 "BAND",
    "banner_custom":      "WIDE BANNER",
}

# Platforms shown in the Welcome Card gallery.
# og and facebook_post are excluded: og duplicates fb_landscape sizing;
# facebook_post requires a 3-column layout template not present in Welcome Card.
# facebook_square uses a distinct editorial full_bleed layout — included so the
# publishing format is discoverable alongside the identity-card Instagram formats.
WC_GALLERY_PLATFORM_IDS: tuple[str, ...] = (
    "instagram_square",
    "instagram_portrait",
    "instagram_story",
    "tiktok",
    "facebook_square",
    "facebook_landscape",
    "banner_custom",
)

# Platforms shown in the Dashboard Card Editor platform picker.
# "default" is excluded — it has no canvas size and is not an export target.
# facebook_post was previously missing from the editor UI (functional gap).
CARD_EDITOR_PLATFORM_IDS: tuple[str, ...] = (
    "instagram_square",
    "instagram_portrait",
    "instagram_story",
    "tiktok",
    "facebook_square",
    "facebook_landscape",
    "og",
    "banner_custom",
    "facebook_post",
)
