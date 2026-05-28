"""Shop preview / watermark 3A+3B structural tests — SUX-01..12.

SUX-01  mc_format_grid.html defines .mfg-unavailable
SUX-02  mc_format_grid.html defines .mfg-cc-mock
SUX-03  mc_format_grid.html defines .mfg-ratio-916
SUX-04  mc_format_grid.html defines .mfg-ratio-169
SUX-05  shop_player_card.html not_available state uses mfg-unavailable (not mfg-locked)
SUX-06  shop_player_card.html get_card/locked branch uses mfg-preview-iframe
SUX-07  shop_player_card.html does not contain mfg-preview-placeholder anywhere
SUX-08  shop_challenge_card.html mfg-locked is conditional (state != 'owned')
SUX-09  shop_challenge_card.html mfg-cc-mock present, mfg-preview-placeholder absent
SUX-10  shop_challenge_card.html owned CTA uses mfg-btn-edit, not mfg-btn-download
SUX-11  shop_challenge_card.html mfg-ratio-916 present (9:16 format mock)
SUX-12  my_cards_challenge_card.html does not contain mfg-locked (owned-only collection)
"""
import pathlib

_TMPL = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates"
)
_GRID  = (_TMPL / "includes" / "mc_format_grid.html").read_text()
_SPC   = (_TMPL / "shop_player_card.html").read_text()
_SCC   = (_TMPL / "shop_challenge_card.html").read_text()
_MCC   = (_TMPL / "my_cards_challenge_card.html").read_text()


# ── SUX-01..04: mc_format_grid.html CSS definitions ───────────────────────────

class TestFormatGridCSS:

    def test_sux01_mfg_unavailable_defined(self):
        """SUX-01: mc_format_grid.html defines mfg-unavailable."""
        assert "mfg-unavailable" in _GRID, (
            "mc_format_grid.html must define .mfg-unavailable for not_available state"
        )

    def test_sux02_mfg_cc_mock_defined(self):
        """SUX-02: mc_format_grid.html defines mfg-cc-mock."""
        assert "mfg-cc-mock" in _GRID, (
            "mc_format_grid.html must define .mfg-cc-mock for Challenge Card aspect-ratio mock"
        )

    def test_sux03_mfg_ratio_916_defined(self):
        """SUX-03: mc_format_grid.html defines mfg-ratio-916 (9:16 aspect ratio)."""
        assert "mfg-ratio-916" in _GRID, (
            "mc_format_grid.html must define .mfg-ratio-916 for 9:16 Challenge Card story format"
        )

    def test_sux04_mfg_ratio_169_defined(self):
        """SUX-04: mc_format_grid.html defines mfg-ratio-169 (16:9 aspect ratio)."""
        assert "mfg-ratio-169" in _GRID, (
            "mc_format_grid.html must define .mfg-ratio-169 for 16:9 Challenge Card post format"
        )


# ── SUX-05..07: shop_player_card.html preview logic ───────────────────────────

class TestPlayerCardShopPreview:

    def test_sux05_not_available_uses_mfg_unavailable(self):
        """SUX-05: shop_player_card.html not_available state uses mfg-unavailable, not mfg-locked."""
        assert "mfg-unavailable" in _SPC, (
            "shop_player_card.html must use mfg-unavailable class for not_available state"
        )
        # not_available branch must reference mfg-unavailable — verified by its presence
        # additionally confirm mfg-na-label is used (inner content)
        assert "mfg-na-label" in _SPC

    def test_sux06_get_card_locked_uses_iframe(self):
        """SUX-06: shop_player_card.html get_card/locked branch uses mfg-preview-iframe."""
        assert "mfg-preview-iframe" in _SPC, (
            "shop_player_card.html must render iframe for get_card/locked states (not placeholder)"
        )
        # The else branch (get_card + locked) must have mfg-locked + iframe
        assert "mfg-locked" in _SPC, (
            "shop_player_card.html must still apply mfg-locked overlay for locked/get_card states"
        )

    def test_sux07_no_placeholder_in_player_shop(self):
        """SUX-07: shop_player_card.html does not use mfg-preview-placeholder anywhere."""
        assert "mfg-preview-placeholder" not in _SPC, (
            "shop_player_card.html must not use 2-letter placeholder — "
            "locked/get_card now use iframe; not_available uses mfg-unavailable"
        )


# ── SUX-08..11: shop_challenge_card.html preview + CTA ────────────────────────

class TestChallengeCardShopPreview:

    def test_sux08_mfg_locked_is_conditional(self):
        """SUX-08: shop_challenge_card.html mfg-locked only when state != 'owned'."""
        assert "state != 'owned'" in _SCC, (
            "shop_challenge_card.html must conditionally apply mfg-locked "
            "only when state != 'owned' — owned CC must not show locked overlay"
        )

    def test_sux09_cc_mock_present_placeholder_absent(self):
        """SUX-09: shop_challenge_card.html has mfg-cc-mock, no mfg-preview-placeholder."""
        assert "mfg-cc-mock" in _SCC, (
            "shop_challenge_card.html must use mfg-cc-mock for CC preview (aspect-ratio mock)"
        )
        assert "mfg-preview-placeholder" not in _SCC, (
            "shop_challenge_card.html must not use 2-letter placeholder — "
            "CC mock replaces it for all states"
        )

    def test_sux10_owned_cta_is_edit_not_download(self):
        """SUX-10: shop_challenge_card.html owned CTA uses mfg-btn-edit, not mfg-btn-download."""
        assert "mfg-btn-edit" in _SCC, (
            "shop_challenge_card.html owned CTA must use mfg-btn-edit (navigate style)"
        )
        assert "View Challenges" in _SCC, (
            "shop_challenge_card.html owned CTA text must be 'View Challenges →'"
        )
        assert "mfg-btn-download" not in _SCC, (
            "shop_challenge_card.html must not use mfg-btn-download — "
            "CC owned CTA navigates to results, does not download"
        )

    def test_sux11_ratio_916_present(self):
        """SUX-11: shop_challenge_card.html uses mfg-ratio-916 for 9:16 story format."""
        assert "mfg-ratio-916" in _SCC, (
            "shop_challenge_card.html must apply mfg-ratio-916 for challenge_story_9_16 format"
        )


# ── SUX-12: my_cards_challenge_card.html — no mfg-locked on owned ─────────────

class TestMyCardsChallengeCardNoBug:

    def test_sux12_owned_cc_no_mfg_locked(self):
        """SUX-12: my_cards_challenge_card.html (owned-only) must not contain mfg-locked."""
        assert "mfg-locked" not in _MCC, (
            "my_cards_challenge_card.html is owned-only — no format should appear locked. "
            "BUG-1 fix: remove mfg-locked class from preview div."
        )
