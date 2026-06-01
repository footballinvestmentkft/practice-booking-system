"""Shop preview structural tests — SUX-01..04, SUX-12.

SHOP-3B1: SUX-05..11 removed (tested shop_player_card.html + shop_challenge_card.html,
now unused listing templates after SHOP-1/2 redirects).

SUX-01..04: mc_format_grid.html CSS class definitions (still active)
SUX-12:     my_cards_challenge_card.html no mfg-locked (still active)
"""
import pathlib

_TMPL = (
    pathlib.Path(__file__).resolve().parents[4]
    / "app" / "templates"
)
_GRID  = (_TMPL / "includes" / "mc_format_grid.html").read_text()
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

class TestMyCardsChallengeCardNoBug:

    def test_sux12_owned_cc_no_mfg_locked(self):
        """SUX-12: my_cards_challenge_card.html (owned-only) must not contain mfg-locked."""
        assert "mfg-locked" not in _MCC, (
            "my_cards_challenge_card.html is owned-only — no format should appear locked. "
            "BUG-1 fix: remove mfg-locked class from preview div."
        )
