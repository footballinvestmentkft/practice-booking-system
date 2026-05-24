"""
GL — Public Profile Grid Phase 1 tests.

Verifies the 5-column grid layout structure in player_profile.html.
All tests are static template-analysis only — no server required.

Test list:
  GL-01  5-column grid defined at ≥1280px breakpoint
  GL-02  psp-l-slot and psp-r-slot grid-area classes present
  GL-03  l-slot and r-slot divs present in HTML (Phase 1 placeholders)
  GL-04  Laptop breakpoint (≤1279px) collapses to 3-column layout
  GL-05  Slot columns hidden at ≤1279px via display:none
  GL-06  Tablet breakpoint (≤1023px) does not reference l-slot/r-slot areas
  GL-07  Mobile breakpoint (≤599px) single-column layout
  GL-08  --psp-card-h CSS variable set via JavaScript scaleCard()
  GL-09  Rail max-height uses --psp-card-h custom property
  GL-10  landscape override: l-slot and r-slot explicitly hidden
  GL-11  psp-showcase-grid has 5 grid-template-areas in each row at desktop
  GL-12  aria-hidden="true" on slot placeholder divs
"""
from pathlib import Path
import re

_TEMPLATE = (
    Path(__file__).parent.parent.parent
    / "app" / "templates" / "public" / "player_profile.html"
).read_text(encoding="utf-8")


def _css_block(media_query: str) -> str:
    """Extract CSS text inside a @media block matching the given max-width."""
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


class TestDesktopGrid:

    def test_gl_01_five_column_grid_defined(self):
        """GL-01: Default grid uses 5 columns (180px 90px minmax … 90px 200px)."""
        assert "180px 90px minmax(280px, 1fr) 90px 200px" in _TEMPLATE, (
            "5-column grid-template-columns not found in template"
        )

    def test_gl_02_slot_grid_area_classes_present(self):
        """GL-02: .psp-l-slot and .psp-r-slot are assigned grid-area: l-slot / r-slot."""
        assert "grid-area: l-slot" in _TEMPLATE
        assert "grid-area: r-slot" in _TEMPLATE

    def test_gl_11_desktop_grid_template_areas_five_cols(self):
        """GL-11: grid-template-areas rows at desktop each have 5 tokens."""
        match = re.search(
            r'grid-template-areas:\s*'
            r'"identity identity identity identity identity"',
            _TEMPLATE,
        )
        assert match, "identity row with 5 tokens not found in grid-template-areas"
        # check main rows
        assert '"left     l-slot  center   r-slot  right"' in _TEMPLATE or \
               'left     l-slot  center   r-slot  right' in _TEMPLATE


class TestSlotPlaceholders:

    def test_gl_03_slot_divs_in_html(self):
        """GL-03: psp-l-slot and psp-r-slot placeholder divs are in the HTML."""
        assert 'class="psp-l-slot"' in _TEMPLATE
        assert 'class="psp-r-slot"' in _TEMPLATE

    def test_gl_12_slot_divs_have_aria_hidden(self):
        """GL-12: Slot placeholder divs carry aria-hidden="true"."""
        assert 'class="psp-l-slot" aria-hidden="true"' in _TEMPLATE
        assert 'class="psp-r-slot" aria-hidden="true"' in _TEMPLATE


class TestLaptopBreakpoint:

    def test_gl_04_laptop_collapses_to_three_columns(self):
        """GL-04: @media (max-width:1279px) redefines grid to 3-col layout."""
        block = _css_block("1279px")
        assert "grid-template-columns" in block, (
            "Laptop breakpoint must redefine grid-template-columns"
        )
        assert "minmax(300px" in block or "minmax(300px, 1fr)" in block, (
            "Laptop breakpoint must include a minmax center column"
        )

    def test_gl_05_slots_hidden_at_laptop(self):
        """GL-05: Slot columns get display:none inside the ≤1279px media query."""
        block = _css_block("1279px")
        assert "display: none" in block or "display:none" in block, (
            "psp-l-slot / psp-r-slot must be hidden at ≤1279px"
        )
        assert "psp-l-slot" in block and "psp-r-slot" in block, (
            "Both slot classes must be targeted in the laptop breakpoint"
        )


class TestTabletBreakpoint:

    def test_gl_06_tablet_grid_does_not_reference_slot_areas(self):
        """GL-06: Tablet grid-template-areas rows do not include l-slot or r-slot tokens."""
        block = _css_block("1023px")
        # extract only grid-template-areas lines inside the tablet block
        areas_matches = re.findall(r'"([^"]+)"', block)
        for row in areas_matches:
            tokens = row.split()
            assert "l-slot" not in tokens, f"l-slot found in tablet grid row: {row!r}"
            assert "r-slot" not in tokens, f"r-slot found in tablet grid row: {row!r}"


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
        assert "--psp-card-h" in _TEMPLATE, (
            "--psp-card-h must be referenced in the JavaScript scaleCard function"
        )
        assert "setProperty" in _TEMPLATE, (
            "setProperty call missing from scaleCard — needed to update --psp-card-h"
        )
        assert "style.setProperty('--psp-card-h'" in _TEMPLATE or \
               'style.setProperty("--psp-card-h"' in _TEMPLATE


class TestRailMaxHeight:

    def test_gl_09_rail_max_height_uses_custom_property(self):
        """GL-09: .psp-left-rail and .psp-right-rail use var(--psp-card-h) for max-height."""
        assert "max-height: var(--psp-card-h" in _TEMPLATE, (
            "Rail max-height must reference --psp-card-h CSS variable"
        )


class TestLandscapeOverride:

    def test_gl_10_landscape_hides_slots(self):
        """GL-10: .psp-card-landscape overrides hide l-slot and r-slot."""
        assert "psp-card-landscape .psp-l-slot" in _TEMPLATE
        assert "psp-card-landscape .psp-r-slot" in _TEMPLATE
        # The rule must set display:none
        landscape_section = _TEMPLATE[
            _TEMPLATE.index("psp-card-landscape .psp-l-slot"):
            _TEMPLATE.index("psp-card-landscape .psp-l-slot") + 200
        ]
        assert "display: none" in landscape_section or "display:none" in landscape_section
