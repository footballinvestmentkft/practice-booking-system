"""
GL — Public Profile Grid Phase 1 tests.

Verifies the 5-column grid layout structure in player_profile.html.
All tests are static template-analysis only — no server required.

Test list:
  GL-01  5-column grid active at laptop ≥1024px (36px slots, 1rem gap)
  GL-02  psp-l-slot and psp-r-slot grid-area classes present
  GL-03  l-slot and r-slot divs present in HTML (Phase 1 placeholders)
  GL-04  Large desktop ≥1440px gets wider slots (56px) and larger gap
  GL-05  Slot columns NOT 90px on laptop (max acceptable: 48px)
  GL-06  Tablet breakpoint (≤1023px) does not reference l-slot/r-slot areas
  GL-07  Mobile breakpoint (≤599px) single-column layout
  GL-08  --psp-card-h CSS variable set via JavaScript scaleCard()
  GL-09  Rail max-height uses --psp-card-h custom property
  GL-10  landscape override: l-slot and r-slot explicitly hidden
  GL-11  psp-showcase-grid has 5 grid-template-areas in each row at desktop
  GL-12  aria-hidden="true" on slot placeholder divs
  GL-13  Slots hidden at ≤1023px (tablet) via display:none
  GL-14  No max-width:1279px collapse block (laptop keeps 5-col)
"""
from pathlib import Path
import re

_TEMPLATE = (
    Path(__file__).parent.parent.parent
    / "app" / "templates" / "public" / "player_profile.html"
).read_text(encoding="utf-8")


def _css_block(media_query: str) -> str:
    """Extract CSS text inside the FIRST @media block matching the given width string."""
    pattern = re.compile(
        r'@media\s*\([^)]*' + re.escape(media_query) + r'[^)]*\)\s*\{',
        re.IGNORECASE,
    )
    m = pattern.search(_TEMPLATE)
    if not m:
        return ""
    start = m.end()
    depth = 1
    pos = start
    while pos < len(_TEMPLATE) and depth:
        if _TEMPLATE[pos] == '{':
            depth += 1
        elif _TEMPLATE[pos] == '}':
            depth -= 1
        pos += 1
    return _TEMPLATE[start:pos - 1]


def _base_grid_columns() -> str:
    """Extract grid-template-columns from the base (non-media-query) .psp-showcase-grid rule."""
    m = re.search(
        r'\.psp-showcase-grid\s*\{[^}]*grid-template-columns\s*:\s*([^;]+);',
        _TEMPLATE,
        re.DOTALL,
    )
    return m.group(1).strip() if m else ""


class TestLaptopGrid:

    def test_gl_01_laptop_base_uses_small_slots(self):
        """GL-01: Base grid (laptop ≥1024px) uses compact slot columns (36px)."""
        cols = _base_grid_columns()
        assert cols, "grid-template-columns not found in base .psp-showcase-grid"
        assert "36px" in cols, (
            f"Base grid must use 36px slot columns for laptop; got: {cols!r}"
        )

    def test_gl_02_slot_grid_area_classes_present(self):
        """GL-02: .psp-l-slot and .psp-r-slot are assigned grid-area: l-slot / r-slot."""
        assert "grid-area: l-slot" in _TEMPLATE
        assert "grid-area: r-slot" in _TEMPLATE

    def test_gl_05_slot_columns_not_90px_on_laptop(self):
        """GL-05: Base grid slot columns must be ≤48px — 90px is too wide for laptop."""
        cols = _base_grid_columns()
        # Extract the slot column widths (2nd and 4th tokens in: rail slot center slot rail)
        tokens = cols.split()
        # e.g. ['170px', '36px', 'minmax(420px,', '1fr)', '36px', '190px'] — find px values
        px_values = [int(t.rstrip('px')) for t in tokens if re.fullmatch(r'\d+px', t)]
        slot_candidates = [v for v in px_values if v < 100]  # rails are 150-200px; slots are 32-56px
        for v in slot_candidates:
            assert v <= 48, (
                f"Slot column {v}px exceeds 48px laptop limit — use 32–48px range"
            )

    def test_gl_11_desktop_grid_template_areas_five_cols(self):
        """GL-11: grid-template-areas rows at desktop each have 5 tokens."""
        match = re.search(
            r'grid-template-areas:\s*'
            r'"identity identity identity identity identity"',
            _TEMPLATE,
        )
        assert match, "identity row with 5 tokens not found in grid-template-areas"
        assert 'left     l-slot  center   r-slot  right' in _TEMPLATE or \
               'left l-slot center r-slot right' in _TEMPLATE


class TestLargeDesktopBreakpoint:

    def test_gl_04_large_desktop_uses_wider_slots(self):
        """GL-04: @media (min-width:1440px) increases slot columns to 56px."""
        block = _css_block("1440px")
        assert block, "@media (min-width: 1440px) block not found"
        assert "56px" in block, (
            "Large desktop breakpoint must use 56px slot columns; block: " + block[:200]
        )

    def test_gl_04b_large_desktop_has_grid_template_columns(self):
        """GL-04b: ≥1440px block redefines grid-template-columns."""
        block = _css_block("1440px")
        assert "grid-template-columns" in block


class TestSlotPlaceholders:

    def test_gl_03_slot_divs_in_html(self):
        """GL-03: psp-l-slot and psp-r-slot placeholder divs are in the HTML."""
        assert 'class="psp-l-slot"' in _TEMPLATE
        assert 'class="psp-r-slot"' in _TEMPLATE

    def test_gl_12_slot_divs_have_aria_hidden(self):
        """GL-12: Slot placeholder divs carry aria-hidden="true"."""
        assert 'class="psp-l-slot" aria-hidden="true"' in _TEMPLATE
        assert 'class="psp-r-slot" aria-hidden="true"' in _TEMPLATE


class TestNoLaptopCollapse:

    def test_gl_14_no_1279px_collapse_block(self):
        """GL-14: There is no max-width:1279px block that collapses to 3-col.
        Laptop (1024–1279px) now keeps the 5-column layout."""
        block = _css_block("1279px")
        assert "grid-template-columns" not in block, (
            "@media(max-width:1279px) must NOT redefine grid-template-columns — "
            "laptop now inherits the 5-column base layout"
        )


class TestTabletBreakpoint:

    def test_gl_06_tablet_grid_does_not_reference_slot_areas(self):
        """GL-06: Tablet grid-template-areas rows do not include l-slot or r-slot tokens."""
        block = _css_block("1023px")
        areas_matches = re.findall(r'"([^"]+)"', block)
        for row in areas_matches:
            tokens = row.split()
            assert "l-slot" not in tokens, f"l-slot found in tablet grid row: {row!r}"
            assert "r-slot" not in tokens, f"r-slot found in tablet grid row: {row!r}"

    def test_gl_13_slots_hidden_at_tablet(self):
        """GL-13: Slot columns are hidden (display:none) in the ≤1023px tablet breakpoint."""
        block = _css_block("1023px")
        assert "display: none" in block or "display:none" in block, (
            "Tablet breakpoint must hide psp-l-slot / psp-r-slot"
        )
        assert "psp-l-slot" in block and "psp-r-slot" in block


class TestMobileBreakpoint:

    def test_gl_07_mobile_single_column_layout(self):
        """GL-07: @media (max-width:599px) uses 1fr single-column grid."""
        block = _css_block("599px")
        assert "grid-template-columns: 1fr" in block, (
            "Mobile breakpoint must set grid-template-columns: 1fr"
        )


class TestJavaScript:

    def test_gl_08_psp_card_h_set_in_scale_card(self):
        """GL-08: scaleCard() calls setProperty('--psp-card-h', …) to update rail height."""
        assert "--psp-card-h" in _TEMPLATE
        assert "setProperty" in _TEMPLATE
        assert "style.setProperty('--psp-card-h'" in _TEMPLATE or \
               'style.setProperty("--psp-card-h"' in _TEMPLATE


class TestRailMaxHeight:

    def test_gl_09_rail_max_height_uses_custom_property(self):
        """GL-09: .psp-left-rail and .psp-right-rail use var(--psp-card-h) for max-height."""
        assert "max-height: var(--psp-card-h" in _TEMPLATE


class TestLandscapeOverride:

    def test_gl_10_landscape_hides_slots(self):
        """GL-10: .psp-card-landscape overrides hide l-slot and r-slot."""
        assert "psp-card-landscape .psp-l-slot" in _TEMPLATE
        assert "psp-card-landscape .psp-r-slot" in _TEMPLATE
        landscape_section = _TEMPLATE[
            _TEMPLATE.index("psp-card-landscape .psp-l-slot"):
            _TEMPLATE.index("psp-card-landscape .psp-l-slot") + 200
        ]
        assert "display: none" in landscape_section or "display:none" in landscape_section
