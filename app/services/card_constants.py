"""Authoritative export constants for the FClassic / Welcome Card card system.

All platform dimensions, template-bucket routing, and animated-capability
declarations live here.  Every other module imports from this file — never
defines its own copy.

Invariants enforced by tests/unit/services/test_card_constants.py:
  - EXPORT_FORMAT_BUCKETS.keys() == CANVAS_SIZES.keys() − {"default"}
  - Every platform_id in ANIMATED_EXPORT_CAPABLE exists in CANVAS_SIZES

CS-1 note: ANIMATED_EXPORT_CAPABLE is now derived from card_design_service.DESIGNS
(the fallback dict) rather than being hardcoded as a frozenset literal.  The name
and type are preserved for backward compatibility.  Future phases (CS-4+) will
derive it from the DB-backed cache instead of the fallback dict.
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
    "default":               ( 820,  800),   # native FClassic Player; clip = card-wrap bbox (est. 2026-05-12)
    "instagram_square":      (1080, 1080),
    "instagram_portrait":    (1080, 1350),
    "instagram_story":       (1080, 1920),
    "tiktok":                (1080, 1920),
    "facebook_square":       (1080, 1080),
    "facebook_landscape":    (1200,  630),
    "og":                    (1200,  630),
    "banner_custom":         (1500,  500),
    "facebook_post":         (1200,  630),
    "challenge_post_16_9":   (1280,  720),
    "challenge_story_9_16":  (1080, 1920),
}

# ── Export template routing ───────────────────────────────────────────────────
# Maps platform preset id → template bucket directory.
# Template path resolved as: public/export/{bucket}/{card_variant_id}.html
EXPORT_FORMAT_BUCKETS: dict[str, str] = {
    "instagram_square":     "square",
    "facebook_square":      "square",
    "instagram_portrait":   "portrait",
    "instagram_story":      "story",
    "tiktok":               "tiktok",
    "facebook_landscape":   "landscape",
    "og":                   "og",
    "banner_custom":        "banner",
    "facebook_post":        "landscape",
    "challenge_post_16_9":  "challenge",
    "challenge_story_9_16": "challenge",
}

# ── Animated video export capability registry ─────────────────────────────────
# Derived from card_design_service.DESIGNS (the fallback dict) at import time.
# (variant_id, platform_id) pairs that have a dedicated animated export template.
# All other combinations return 422 — no fallback, no silent degradation.
#
# CS-1: source of truth moved from a hardcoded frozenset literal to the DESIGNS
# dict so that animated_platforms is declared alongside all other design metadata.
# The frozenset name and structure are preserved for backward compatibility.
def _build_animated_capable() -> frozenset[tuple[str, str]]:
    from .card_design_service import DESIGNS  # noqa: PLC0415
    # Deduplicate by canonical design.id to avoid duplicate entries from
    # deprecated alias keys (deduplication by canonical design.id is a safety net).
    seen_ids: set[str] = set()
    result: set[tuple[str, str]] = set()
    for design in DESIGNS.values():
        if design.id not in seen_ids:
            seen_ids.add(design.id)
            for platform_id in design.animated_platforms:
                result.add((design.id, platform_id))
    return frozenset(result)


ANIMATED_EXPORT_CAPABLE: frozenset[tuple[str, str]] = _build_animated_capable()


def is_animated_capable(variant_id: str, platform_id: str) -> bool:
    """Return True if (variant_id, platform_id) supports animated video export.

    Resolves deprecated design ID aliases (e.g. legacy inputs → canonical id) before
    the lookup so both the legacy and canonical IDs return the correct result.
    """
    from .card_design_service import resolve_design_id  # noqa: PLC0415
    canonical = resolve_design_id(variant_id)
    return (canonical, platform_id) in ANIMATED_EXPORT_CAPABLE


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

# Platforms shown in the public Player Card gallery hub.
# Subset of CARD_EDITOR_PLATFORM_IDS: excludes facebook_post (secondary landscape variant)
# and facebook_square (alias of instagram_square). Ordered for visual hierarchy.
CARD_GALLERY_PLATFORM_IDS: tuple[str, ...] = (
    "instagram_portrait",
    "instagram_story",
    "instagram_square",
    "tiktok",
    "facebook_landscape",
    "og",
    "banner_custom",
)

# Player Card format metadata — maps export bucket → display metadata.
# Used by /shop/cards/player/{collection_id} detail page (C1 collection browsing).
# Ordering: portrait-first, then vertical formats, then square, then wide/landscape.
# C2 note: when format-level purchase is introduced, each entry gains a credit_cost
# and design_id convention (e.g. "fclassic_instagram_portrait"). For now, ownership is
# collection-level (design_id="fclassic") and all formats are unlocked together.
PC_FORMAT_META: list[dict] = [
    {"bucket": "portrait",  "platform": "instagram_portrait",  "label": "Instagram Portrait", "dims": "1080 × 1350", "ratio": "mfg-ratio-45",  "display_order": 0},
    {"bucket": "story",     "platform": "instagram_story",     "label": "Instagram Story",    "dims": "1080 × 1920", "ratio": "mfg-ratio-916", "display_order": 1},
    {"bucket": "tiktok",    "platform": "tiktok",              "label": "TikTok",             "dims": "1080 × 1920", "ratio": "mfg-ratio-916", "display_order": 2},
    {"bucket": "square",    "platform": "instagram_square",    "label": "Square",             "dims": "1080 × 1080", "ratio": "mfg-ratio-11",  "display_order": 3},
    {"bucket": "landscape", "platform": "facebook_landscape",  "label": "Landscape",          "dims": "1200 × 630",  "ratio": "mfg-ratio-169", "display_order": 4},
    {"bucket": "og",        "platform": "og",                  "label": "Open Graph",         "dims": "1200 × 630",  "ratio": "mfg-ratio-169", "display_order": 5},
    {"bucket": "banner",    "platform": "banner_custom",       "label": "Banner",             "dims": "1500 × 500",  "ratio": "mfg-ratio-169", "display_order": 6},
]
