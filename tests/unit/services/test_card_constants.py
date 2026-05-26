"""
Unit tests for app/services/card_constants.py

Verifies:
  - CANVAS_SIZES: platform coverage and value types
  - EXPORT_FORMAT_BUCKETS: keys match CANVAS_SIZES keys (invariant)
  - ANIMATED_EXPORT_CAPABLE: valid (variant, platform) pairs, platform coverage
  - is_animated_capable(): helper semantics
  - Re-export identity: card_export_service re-exports same objects (not copies)

CC-01: CANVAS_SIZES has exactly 10 platforms (9 social + "default" native export)
CC-02: every CANVAS_SIZES value is a (int, int) tuple
CC-03: EXPORT_FORMAT_BUCKETS keys == CANVAS_SIZES keys − {"default"} (invariant; "default" is native export, no bucket)
CC-04: EXPORT_FORMAT_BUCKETS value set is the expected bucket set
CC-05: ANIMATED_EXPORT_CAPABLE is a frozenset of 2-tuples
CC-06: every platform_id in ANIMATED_EXPORT_CAPABLE exists in CANVAS_SIZES
CC-07: is_animated_capable — known capable pair returns True
CC-08: is_animated_capable — capable variant + wrong platform returns False
CC-09: is_animated_capable — unknown variant returns False
CC-10: re-export identity — card_export_service.CANVAS_SIZES is card_constants.CANVAS_SIZES
CC-11: re-export identity — card_export_service.ANIMATED_EXPORT_CAPABLE is card_constants.ANIMATED_EXPORT_CAPABLE
"""
import pytest

from app.services.card_constants import (
    ANIMATED_EXPORT_CAPABLE,
    CANVAS_SIZES,
    EXPORT_FORMAT_BUCKETS,
    is_animated_capable,
)
import app.services.card_export_service as _export_svc


# ── CC-01 / CC-02: CANVAS_SIZES structure ────────────────────────────────────

def test_cc01_canvas_sizes_has_9_platforms():
    assert len(CANVAS_SIZES) == 12  # 9 social + "default" native FIFA Classic export + 2 challenge


def test_cc02_canvas_sizes_values_are_int_tuples():
    for platform, dims in CANVAS_SIZES.items():
        assert isinstance(dims, tuple), f"{platform}: expected tuple, got {type(dims)}"
        assert len(dims) == 2, f"{platform}: expected 2-tuple"
        w, h = dims
        assert isinstance(w, int) and isinstance(h, int), (
            f"{platform}: width and height must be int, got ({type(w)}, {type(h)})"
        )
        assert w > 0 and h > 0, f"{platform}: dimensions must be positive"


# ── CC-03 / CC-04: EXPORT_FORMAT_BUCKETS ─────────────────────────────────────

def test_cc03_export_format_buckets_keys_match_canvas_sizes():
    # "default" is the native FIFA Classic export path — it uses a dynamic clip
    # against card-wrap BoundingClientRect, not a template bucket directory.
    social_canvas_keys = CANVAS_SIZES.keys() - {"default"}
    assert EXPORT_FORMAT_BUCKETS.keys() == social_canvas_keys, (
        "EXPORT_FORMAT_BUCKETS must cover all CANVAS_SIZES platforms except 'default'. "
        f"Extra in buckets: {EXPORT_FORMAT_BUCKETS.keys() - social_canvas_keys}. "
        f"Extra in canvas:  {social_canvas_keys - EXPORT_FORMAT_BUCKETS.keys()}."
    )


def test_cc04_export_format_buckets_value_set():
    expected_buckets = {"square", "portrait", "story", "tiktok", "landscape", "og", "banner", "challenge"}
    actual_buckets = set(EXPORT_FORMAT_BUCKETS.values())
    assert actual_buckets == expected_buckets, (
        f"Unexpected bucket values: {actual_buckets - expected_buckets}. "
        f"Missing: {expected_buckets - actual_buckets}."
    )


# ── CC-05 / CC-06: ANIMATED_EXPORT_CAPABLE ───────────────────────────────────

def test_cc05_animated_export_capable_is_frozenset_of_2tuples():
    assert isinstance(ANIMATED_EXPORT_CAPABLE, frozenset)
    for item in ANIMATED_EXPORT_CAPABLE:
        assert isinstance(item, tuple) and len(item) == 2, (
            f"Expected 2-tuples in ANIMATED_EXPORT_CAPABLE, got: {item!r}"
        )


def test_cc06_animated_capable_platforms_exist_in_canvas_sizes():
    for variant_id, platform_id in ANIMATED_EXPORT_CAPABLE:
        assert platform_id in CANVAS_SIZES, (
            f"ANIMATED_EXPORT_CAPABLE references unknown platform {platform_id!r} "
            f"(variant={variant_id!r}). Add it to CANVAS_SIZES first."
        )


# ── CC-07 / CC-08 / CC-09: is_animated_capable() ────────────────────────────

@pytest.mark.parametrize("variant, platform", [
    ("fifa",  "instagram_square"),
    ("pulse", "instagram_square"),
])
def test_cc07_is_animated_capable_known_pairs_return_true(variant, platform):
    assert is_animated_capable(variant, platform) is True


@pytest.mark.parametrize("variant, platform", [
    ("fifa",  "instagram_story"),
    ("fifa",  "banner_custom"),
    ("pulse", "instagram_portrait"),
    ("pulse", "tiktok"),
])
def test_cc08_capable_variant_wrong_platform_returns_false(variant, platform):
    assert is_animated_capable(variant, platform) is False


@pytest.mark.parametrize("variant, platform", [
    ("unknown_variant", "instagram_square"),
    ("atlas",           "instagram_square"),
    ("compact",         "instagram_square"),
])
def test_cc09_unknown_variant_returns_false(variant, platform):
    assert is_animated_capable(variant, platform) is False


# ── CC-10 / CC-11: re-export identity ────────────────────────────────────────

def test_cc10_card_export_service_canvas_sizes_is_same_object():
    assert _export_svc.CANVAS_SIZES is CANVAS_SIZES, (
        "card_export_service.CANVAS_SIZES must be the same object as "
        "card_constants.CANVAS_SIZES (re-export, not a copy). "
        "This ensures callers via _export_svc.CANVAS_SIZES stay in sync."
    )


def test_cc11_card_export_service_animated_capable_is_same_object():
    assert _export_svc.ANIMATED_EXPORT_CAPABLE is ANIMATED_EXPORT_CAPABLE, (
        "card_export_service.ANIMATED_EXPORT_CAPABLE must be the same object as "
        "card_constants.ANIMATED_EXPORT_CAPABLE (re-export, not a copy)."
    )


# ── CC-12..16: WC_GALLERY_PLATFORM_IDS and CARD_EDITOR_PLATFORM_IDS ──────────

from app.services.card_constants import (
    WC_GALLERY_PLATFORM_IDS,
    CARD_EDITOR_PLATFORM_IDS,
)


def test_cc12_wc_gallery_ids_all_exist_in_canvas_sizes():
    for pid in WC_GALLERY_PLATFORM_IDS:
        assert pid in CANVAS_SIZES, (
            f"WC_GALLERY_PLATFORM_IDS contains {pid!r} which is absent from CANVAS_SIZES. "
            "Add it to CANVAS_SIZES first."
        )


def test_cc13_wc_gallery_ids_intentional_exclusions():
    # facebook_square is intentionally INCLUDED (full_bleed editorial layout — added in S5).
    # og and facebook_post remain excluded: og duplicates banner_custom at a different ratio;
    # facebook_post duplicates facebook_landscape at the same 1200×630 dimensions.
    excluded = {"og", "facebook_post"}
    for pid in excluded:
        assert pid not in WC_GALLERY_PLATFORM_IDS, (
            f"{pid!r} found in WC_GALLERY_PLATFORM_IDS — it should be intentionally excluded. "
            "If the exclusion was reversed, update both the constant and this test with a comment."
        )


def test_cc14_card_editor_ids_all_exist_in_canvas_sizes():
    for pid in CARD_EDITOR_PLATFORM_IDS:
        assert pid in CANVAS_SIZES, (
            f"CARD_EDITOR_PLATFORM_IDS contains {pid!r} which is absent from CANVAS_SIZES."
        )


def test_cc15_card_editor_ids_includes_facebook_post():
    assert "facebook_post" in CARD_EDITOR_PLATFORM_IDS, (
        "facebook_post must appear in CARD_EDITOR_PLATFORM_IDS. "
        "It was previously missing from the dashboard editor UI — this closes that gap."
    )


def test_cc16_card_editor_ids_excludes_default():
    assert "default" not in CARD_EDITOR_PLATFORM_IDS, (
        "'default' must not be in CARD_EDITOR_PLATFORM_IDS — it has no canvas size "
        "and is rendered as a separate static button in the editor template."
    )
