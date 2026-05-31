"""
FC-01..FC-20 — FClassic family alias layer + label + family mapping (PR-FC-1A).

FC-01  FCLASSIC_FAMILY_ID == "fclassic"
FC-02  FCLASSIC_PLAYER_DESIGN_IDS contains "fifa" (legacy) and "fclassic" (canonical)
FC-03  resolve_design_id("fifa") == "fclassic"
FC-04  resolve_design_id("fclassic") == "fclassic" (idempotent)
FC-05  resolve_design_id("compact") == "compact" (pass-through)
FC-06  resolve_design_id("pulse") == "pulse" (pass-through)
FC-07  get_card_family("player_card", "fclassic") == "fclassic"
FC-08  get_card_family("player_card", "fifa") == "fclassic" (alias)
FC-09  get_card_family("player_card", "compact") is None
FC-10  get_card_family("player_card", "pulse") is None
FC-11  get_card_family("welcome_card", "instagram_portrait") == "fclassic"
FC-12  get_card_family("welcome_card", None) == "fclassic"
FC-13  get_card_family("challenge_card", "challenge_post_16_9") == "fclassic"
FC-14  get_card_family("challenge_card", "challenge_story_9_16") == "fclassic"
FC-15  get_card_family("unknown_type", None) is None
FC-16  NonPlayerCardFormatDefinition.family_id default == "fclassic" for all WC formats
FC-17  NonPlayerCardFormatDefinition.family_id default == "fclassic" for all CC formats
FC-18  assert_new_design_id_is_canonical("fclassic") does NOT raise
FC-19  assert_new_design_id_is_canonical("fifa") raises ValueError
FC-20  DESIGNS["fifa"].label == "FClassic Player"
"""
from __future__ import annotations

import pytest

from app.services.card_design_service import (
    CHALLENGE_CARD_FORMATS,
    DESIGNS,
    FCLASSIC_FAMILY_ID,
    FCLASSIC_PLAYER_DESIGN_IDS,
    WELCOME_CARD_FORMATS,
    assert_new_design_id_is_canonical,
    get_card_family,
    resolve_design_id,
)


# ── FC-01/02: constants ────────────────────────────────────────────────────────

class TestFC0102Constants:

    def test_fc_01_fclassic_family_id(self):
        """FC-01: FCLASSIC_FAMILY_ID is the canonical family identifier."""
        assert FCLASSIC_FAMILY_ID == "fclassic"

    def test_fc_02_player_design_ids_canonical_only(self):
        """FC-02: FCLASSIC_PLAYER_DESIGN_IDS contains only canonical 'fclassic' (PR-FC-1F)."""
        assert "fclassic" in FCLASSIC_PLAYER_DESIGN_IDS, (
            "'fclassic' must be in FCLASSIC_PLAYER_DESIGN_IDS as canonical ID"
        )
        assert "fifa" not in FCLASSIC_PLAYER_DESIGN_IDS, (
            "'fifa' must be removed from FCLASSIC_PLAYER_DESIGN_IDS after PR-FC-1F; "
            "legacy input is handled by resolve_design_id() sanitizer"
        )


# ── FC-03..FC-06: resolve_design_id ───────────────────────────────────────────

class TestFC0306ResolveDesignId:

    def test_fc_03_resolve_fifa_to_fclassic(self):
        """FC-03: resolve_design_id('fifa') returns the canonical 'fclassic'."""
        assert resolve_design_id("fifa") == "fclassic"

    def test_fc_04_resolve_fclassic_is_idempotent(self):
        """FC-04: resolve_design_id('fclassic') returns 'fclassic' unchanged."""
        assert resolve_design_id("fclassic") == "fclassic"

    def test_fc_05_resolve_compact_passthrough(self):
        """FC-05: resolve_design_id passes through non-aliased IDs unchanged."""
        assert resolve_design_id("compact") == "compact"

    def test_fc_06_resolve_pulse_passthrough(self):
        """FC-06: resolve_design_id('pulse') passes through unchanged."""
        assert resolve_design_id("pulse") == "pulse"


# ── FC-07..FC-15: get_card_family ─────────────────────────────────────────────

class TestFC0715GetCardFamily:

    def test_fc_07_player_card_fclassic_canonical(self):
        """FC-07: player_card + 'fclassic' (canonical) → 'fclassic' family."""
        assert get_card_family("player_card", "fclassic") == "fclassic"

    def test_fc_08_player_card_fifa_alias(self):
        """FC-08: player_card + 'fifa' (deprecated alias) → 'fclassic' family."""
        assert get_card_family("player_card", "fifa") == "fclassic"

    def test_fc_09_player_card_compact_not_fclassic(self):
        """FC-09: player_card + 'compact' is NOT in FClassic family."""
        assert get_card_family("player_card", "compact") is None

    def test_fc_10_player_card_pulse_not_fclassic(self):
        """FC-10: player_card + 'pulse' is NOT in FClassic family."""
        assert get_card_family("player_card", "pulse") is None

    def test_fc_11_welcome_card_instagram_portrait(self):
        """FC-11: welcome_card + 'instagram_portrait' → 'fclassic' family."""
        assert get_card_family("welcome_card", "instagram_portrait") == "fclassic"

    def test_fc_12_welcome_card_no_design_id(self):
        """FC-12: welcome_card with no design_id → 'fclassic' family."""
        assert get_card_family("welcome_card") == "fclassic"
        assert get_card_family("welcome_card", None) == "fclassic"

    def test_fc_13_challenge_card_post_16_9(self):
        """FC-13: challenge_card + 'challenge_post_16_9' → 'fclassic' family."""
        assert get_card_family("challenge_card", "challenge_post_16_9") == "fclassic"

    def test_fc_14_challenge_card_story_9_16(self):
        """FC-14: challenge_card + 'challenge_story_9_16' → 'fclassic' family."""
        assert get_card_family("challenge_card", "challenge_story_9_16") == "fclassic"

    def test_fc_15_unknown_type_returns_none(self):
        """FC-15: unknown card_type_id returns None."""
        assert get_card_family("unknown_type", None) is None
        assert get_card_family("player_card", None) is None  # no design_id given


# ── FC-16/17: NonPlayerCardFormatDefinition family_id ────────────────────────

class TestFC1617FormatFamilyId:

    def test_fc_16_all_welcome_formats_are_fclassic(self):
        """FC-16: all 7 Welcome Card formats have family_id='fclassic'."""
        for fmt in WELCOME_CARD_FORMATS:
            assert fmt.family_id == "fclassic", (
                f"Welcome Card format '{fmt.design_id}' must have family_id='fclassic', "
                f"got {fmt.family_id!r}"
            )
        assert len(WELCOME_CARD_FORMATS) == 7

    def test_fc_17_all_challenge_formats_are_fclassic(self):
        """FC-17: both Challenge Card formats have family_id='fclassic'."""
        for fmt in CHALLENGE_CARD_FORMATS:
            assert fmt.family_id == "fclassic", (
                f"Challenge Card format '{fmt.design_id}' must have family_id='fclassic', "
                f"got {fmt.family_id!r}"
            )
        assert len(CHALLENGE_CARD_FORMATS) == 2


# ── FC-18/19: assert_new_design_id_is_canonical ──────────────────────────────

class TestFC1819WriteGuard:

    def test_fc_18_canonical_id_does_not_raise(self):
        """FC-18: assert_new_design_id_is_canonical('fclassic') must not raise."""
        assert_new_design_id_is_canonical("fclassic")  # must not raise

    def test_fc_18b_other_ids_do_not_raise(self):
        """FC-18b: non-deprecated IDs pass through the write guard cleanly."""
        for design_id in ("compact", "showcase", "atlas", "pulse"):
            assert_new_design_id_is_canonical(design_id)  # must not raise

    def test_fc_19_deprecated_fifa_raises_value_error(self):
        """FC-19: assert_new_design_id_is_canonical('fifa') must raise ValueError."""
        with pytest.raises(ValueError, match="fclassic"):
            assert_new_design_id_is_canonical("fifa")

    def test_fc_19b_error_message_names_canonical(self):
        """FC-19b: ValueError message tells caller to use 'fclassic'."""
        with pytest.raises(ValueError) as exc:
            assert_new_design_id_is_canonical("fifa", context="test purchase")
        assert "fclassic" in str(exc.value)
        assert "fifa" in str(exc.value)


# ── FC-20: label ──────────────────────────────────────────────────────────────

class TestFC20Label:

    def test_fc_20_fclassic_design_label_is_fclassic_player(self):
        """FC-20: DESIGNS['fclassic'].label == 'FClassic Player' (PR-FC-1F: 'fifa' key removed)."""
        assert DESIGNS["fclassic"].label == "FClassic Player", (
            f"Expected 'FClassic Player', got {DESIGNS['fclassic'].label!r}"
        )
