"""
Unit tests for app/services/card_platform_service.py — build_platform_list helper.

CPS-01: build_platform_list returns dicts with required keys (id/label/title/dims/w/h)
CPS-02: dims field format is "{w} × {h}" (space-x-space)
CPS-03: w and h values match CANVAS_SIZES for each platform
"""
import pytest

from app.services.card_platform_service import build_platform_list
from app.services.card_constants import (
    CANVAS_SIZES,
    WC_GALLERY_PLATFORM_IDS,
    CARD_EDITOR_PLATFORM_IDS,
)


# ── CPS-01: required keys ─────────────────────────────────────────────────────

@pytest.mark.parametrize("platform_ids", [
    WC_GALLERY_PLATFORM_IDS,
    CARD_EDITOR_PLATFORM_IDS,
])
def test_cps01_build_platform_list_required_keys(platform_ids):
    result = build_platform_list(platform_ids)
    assert len(result) == len(platform_ids)
    required = {"id", "label", "title", "dims", "w", "h"}
    for entry in result:
        missing = required - entry.keys()
        assert not missing, (
            f"Platform entry for {entry.get('id')!r} is missing keys: {missing}"
        )


# ── CPS-02: dims format ───────────────────────────────────────────────────────

def test_cps02_dims_format():
    result = build_platform_list(WC_GALLERY_PLATFORM_IDS)
    for entry in result:
        w, h = entry["w"], entry["h"]
        expected = f"{w} × {h}"
        assert entry["dims"] == expected, (
            f"{entry['id']!r}: expected dims={expected!r}, got {entry['dims']!r}"
        )


# ── CPS-03: w/h match CANVAS_SIZES ───────────────────────────────────────────

def test_cps03_dimensions_match_canvas_sizes():
    all_ids = set(WC_GALLERY_PLATFORM_IDS) | set(CARD_EDITOR_PLATFORM_IDS)
    result = build_platform_list(tuple(all_ids))
    for entry in result:
        pid = entry["id"]
        expected_w, expected_h = CANVAS_SIZES[pid]
        assert entry["w"] == expected_w, (
            f"{pid!r}: w={entry['w']} != CANVAS_SIZES w={expected_w}"
        )
        assert entry["h"] == expected_h, (
            f"{pid!r}: h={entry['h']} != CANVAS_SIZES h={expected_h}"
        )


# ── CPS-04: platform IDs in output match input order ────────────────────────

def test_cps04_output_order_matches_input():
    result = build_platform_list(WC_GALLERY_PLATFORM_IDS)
    assert [e["id"] for e in result] == list(WC_GALLERY_PLATFORM_IDS)

    result2 = build_platform_list(CARD_EDITOR_PLATFORM_IDS)
    assert [e["id"] for e in result2] == list(CARD_EDITOR_PLATFORM_IDS)
