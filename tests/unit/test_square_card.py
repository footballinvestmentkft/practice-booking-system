"""
Unit tests — FIFA Classic × Instagram Square card template
==========================================================

Static assertions against app/templates/public/export/square/fifa.html.
No DB, no Playwright, no network — all tests read the template source as text.

Coverage:
  SQ-01  Hero zone is 35% (v5 — was 42% in v4; 35% gives 65% for skills)
  SQ-02  Exactly 3 .ex-skill-col divs (3-column layout)
  SQ-03  Col 1 renders skill_categories[0] (Outfield — 1st column, single cat)
  SQ-04  .ex-col-right renders skill_categories[2] (Mental — nested right panel)
  SQ-05  .ex-col-right renders both skill_categories[1] (Set Pieces) + [3] (Physical)
  SQ-06  primary_pos_label referenced in Position Map panel (v9 — photo badges removed)
         .ex-pos-badge class absent from HTML body
  SQ-07  Secondary position chips in Position Map panel (ex-pos-secondary-chips)
         Chips gated by secondary_pos_labels; full list rendered (no artificial slice)
  SQ-08  .ex-sec-pos-chip CSS class defined in stylesheet (reused in pos panel)
  SQ-09  .ex-cat has no overflow:hidden (content layers must never clip skills)
  SQ-10  .ex-skill-rows has no overflow:hidden
  SQ-11  Animation stagger covers rows 1–19 (Outfield is longest at 19 skills)
  SQ-12  Cat animation has ≤2 nth-child selectors (3-col: max 2 cats per column)
  SQ-13  Sponsor in hero layer (.ex-hero-sponsor inside .ex-profile-col) — v8
         Sponsor NOT in skills zone (.ex-sponsor-slot removed in v8)
  SQ-14  Animated mode block is gated by {% if animated_mode %} Jinja2 block
  SQ-15  Template file exists and contains DOCTYPE declaration
  SQ-16  ex-cat--logo-host and ex-logo-slot are NOT in the HTML body (removed in v5)
  SQ-17  .ex-pos-panel-landscape class present in HTML body (v7 landscape panel)
  SQ-18  Landscape pitch SVG viewBox "0 0 105 68" (real 105m×68m geometry) — v8
         Old "0 0 100 24" aspect-squashed viewBox must be absent
  SQ-19  position_nodes Jinja2 variable referenced in SVG rendering
         Coordinate transform: cx = node.x * 105, cy = node.y * 68 (v8 real geometry)
  SQ-20  Landscape panel appears INSIDE .ex-skill-cats, after skill_categories[2] (v11)
  SQ-21  Column count unchanged: still exactly 3 .ex-skill-col divs with panel added
  SQ-22  Position panel gated by {% if position_nodes %} — graceful empty state
  SQ-23  .ex-pos-svg-landscape CSS defines flex or dimension (fills container)
  SQ-24  Position panel does NOT use .ex-cat class — immune to cat fade-slide animation
  SQ-25  REMOVED — .ex-col-right wrapper eliminated in v10 (flat sibling columns)
  SQ-27  Landscape SVG has no {{ node.label }} text — no labels per user request
  SQ-28  preserveAspectRatio="xMidYMid meet" on landscape SVG — no stretch, no crop (v8)
  SQ-29  Position Map panel has info column: .ex-pos-info div present — v9
         .ex-pos-panel-title CSS defined; primary_pos_label in panel context
  SQ-30  .ex-pos-badge absent from HTML body (photo is clean portrait block) — v9
         .ex-sec-pos-chips absent from photo column (chips moved to pos panel)
  SQ-31  No PRIMARY/SECONDARY/OTHER legend in Position Map panel — v9
  SQ-32  v10 flat layout: .ex-col-right and .ex-col-right-skills absent from HTML body
  SQ-33  v11 column modifiers: ex-col-outfield, ex-col-mental-pos, ex-col-sets-phys present
         Position Map inside ex-col-mental-pos; panel height 160px; info col 140px
"""
from __future__ import annotations

import pathlib
import re

import pytest

# ── Template path ──────────────────────────────────────────────────────────────

_TPL_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "app" / "templates" / "public" / "export" / "square" / "fifa.html"
)


@pytest.fixture(scope="module")
def tpl() -> str:
    return _TPL_PATH.read_text(encoding="utf-8")


# ── SQ-01: Hero zone percentage ───────────────────────────────────────────────

class TestHeroZone:
    def test_sq01_hero_flex_35_percent(self, tpl):
        """Hero must be 35%, not 42%, so skills zone gets 65% (702px at 1080px)."""
        assert "flex: 0 0 35%" in tpl

    def test_sq01_hero_not_42_percent(self, tpl):
        """Old 42% value must be gone — confirms this is v5, not v4."""
        assert "flex: 0 0 42%" not in tpl


# ── SQ-02/03/04/05: 3-column skill layout ─────────────────────────────────────

class TestThreeColumnLayout:
    def test_sq02_three_skill_col_divs(self, tpl):
        """Exactly 3 .ex-skill-col divs present (may carry modifier classes in v11)."""
        matches = re.findall(r'class="ex-skill-col[^"]*"', tpl)
        assert len(matches) == 3, f"Expected 3 ex-skill-col divs, found {len(matches)}"

    def test_sq03_col1_outfield_index_0(self, tpl):
        """Col 1 (Outfield): uses skill_categories[0]."""
        assert "skill_categories[0]" in tpl

    def test_sq04_col2_mental_index_2(self, tpl):
        """Col 2 (Mental): uses skill_categories[2]."""
        assert "skill_categories[2]" in tpl

    def test_sq05_col3_set_pieces_physical(self, tpl):
        """Col 3 stacks Set Pieces [1] + Physical [3] via a for loop."""
        assert "skill_categories[1]" in tpl
        assert "skill_categories[3]" in tpl
        # Both must appear together in the right panel
        col3_start = tpl.rfind("skill_categories[1]")
        col3_end   = tpl.rfind("skill_categories[3]")
        assert col3_start > 0 and col3_end > col3_start, (
            "skill_categories[1] must appear before skill_categories[3] in right panel"
        )


# ── SQ-06/07/08: Position info (v9 — photo badges removed, info in panel) ────

class TestPositionBadge:
    def test_sq06_primary_pos_label_referenced(self, tpl):
        """v9: primary_pos_label must be referenced in the template (Position Map panel)."""
        assert "primary_pos_label" in tpl

    def test_sq06_no_pos_badge_on_photo(self, tpl):
        """v9: .ex-pos-badge class must be absent from the HTML body (photo is clean portrait)."""
        html_body = tpl[tpl.rfind("</style>"):]
        assert 'class="ex-pos-badge"' not in html_body, (
            ".ex-pos-badge must not appear in the HTML body — photo badges were removed in v9"
        )

    def test_sq06_primary_pos_in_panel_context(self, tpl):
        """v9: primary_pos_label must appear inside the .ex-pos-panel-landscape block."""
        html_body = tpl[tpl.rfind("</style>"):]
        panel_start = html_body.find('class="ex-pos-panel-landscape"')
        assert panel_start != -1
        panel_region = html_body[panel_start: panel_start + 1200]
        assert "primary_pos_label" in panel_region, (
            "primary_pos_label must be rendered inside .ex-pos-panel-landscape (Position Map)"
        )

    def test_sq07_secondary_chips_in_pos_panel(self, tpl):
        """v9: secondary chips container must be in the Position Map panel, not the photo column."""
        html_body = tpl[tpl.rfind("</style>"):]
        # Chips container must exist somewhere in the body
        assert 'class="ex-pos-secondary-chips"' in html_body, (
            ".ex-pos-secondary-chips must be present inside the Position Map panel"
        )
        # Must appear after ex-pos-panel-landscape opens
        panel_start = html_body.find('class="ex-pos-panel-landscape"')
        chips_idx   = html_body.find('class="ex-pos-secondary-chips"', panel_start)
        assert chips_idx > panel_start, (
            ".ex-pos-secondary-chips must be inside .ex-pos-panel-landscape, not in the photo column"
        )

    def test_sq07_chips_no_artificial_slice(self, tpl):
        """v9: secondary_pos_labels loop must not use [:4] slice — domain guarantees max 3."""
        html_body = tpl[tpl.rfind("</style>"):]
        panel_start = html_body.find('class="ex-pos-panel-landscape"')
        panel_region = html_body[panel_start: panel_start + 1200]
        assert "secondary_pos_labels[:4]" not in panel_region, (
            "Loop must use full secondary_pos_labels (or [:3] max) — [:4] slice is not allowed"
        )

    def test_sq07_chips_gated_by_secondary_pos_labels(self, tpl):
        """Secondary chips must be Jinja2-gated so they only render when list is non-empty."""
        assert "{% if secondary_pos_labels" in tpl

    def test_sq08_sec_pos_chip_css_defined(self, tpl):
        """.ex-sec-pos-chip CSS must be defined — reused in Position Map panel."""
        assert ".ex-sec-pos-chip" in tpl


# ── SQ-09/10: No overflow clipping on content layers ─────────────────────────

class TestNoOverflowClipping:
    def test_sq09_ex_cat_no_overflow_hidden(self, tpl):
        """.ex-cat must not have overflow: hidden — all skills must be visible."""
        # Extract the .ex-cat CSS rule block
        cat_rule_start = tpl.find(".ex-cat {")
        assert cat_rule_start != -1
        cat_rule_end = tpl.find("}", cat_rule_start)
        cat_rule = tpl[cat_rule_start: cat_rule_end + 1]
        assert "overflow: hidden" not in cat_rule

    def test_sq10_ex_skill_rows_no_overflow_hidden(self, tpl):
        """.ex-skill-rows must not have overflow: hidden."""
        rows_rule_start = tpl.find(".ex-skill-rows {")
        assert rows_rule_start != -1
        rows_rule_end = tpl.find("}", rows_rule_start)
        rows_rule = tpl[rows_rule_start: rows_rule_end + 1]
        assert "overflow: hidden" not in rows_rule

    def test_sq10_ex_skill_col_no_overflow_hidden(self, tpl):
        """.ex-skill-col must use overflow: visible (not hidden) so content is not clipped."""
        col_rule_start = tpl.find(".ex-skill-col {")
        assert col_rule_start != -1
        col_rule_end = tpl.find("}", col_rule_start)
        col_rule = tpl[col_rule_start: col_rule_end + 1]
        assert "overflow: hidden" not in col_rule


# ── SQ-11/12: Animation stagger ───────────────────────────────────────────────

class TestAnimationStagger:
    def test_sq11_row_stagger_covers_19_rows(self, tpl):
        """Animation row stagger must cover up to nth-child(19) for Outfield (19 skills)."""
        assert ".ex-row:nth-child(19)" in tpl

    def test_sq11_row_stagger_has_no_gaps(self, tpl):
        """Stagger must be contiguous from 1 to 19."""
        for n in range(1, 20):
            assert f".ex-row:nth-child({n})" in tpl, (
                f"Missing animation stagger for .ex-row:nth-child({n})"
            )

    def test_sq12_cat_stagger_max_two_nth_child(self, tpl):
        """Cat animation: at most 2 nth-child selectors (max 2 cats per column in 3-col layout)."""
        # Find the animated_mode block
        anim_start = tpl.find("{% if animated_mode %}")
        anim_end   = tpl.find("{% endif %}", anim_start)
        anim_block = tpl[anim_start: anim_end]
        cat_stagger = re.findall(r"\.ex-cat:nth-child\((\d+)\)", anim_block)
        indices = [int(i) for i in cat_stagger]
        assert max(indices) <= 2, (
            f"Cat stagger should not exceed nth-child(2) in 3-col layout; found {indices}"
        )
        # Old 4-category selectors (nth-child(3) and nth-child(4)) must be gone
        assert ".ex-cat:nth-child(3)" not in anim_block
        assert ".ex-cat:nth-child(4)" not in anim_block


# ── SQ-13: Sponsor placement (v8 — hero layer) ───────────────────────────────

class TestSponsorSlot:
    def test_sq13_sponsor_in_hero_layer(self, tpl):
        """v8: sponsor must use .ex-hero-sponsor class (moved to hero layer)."""
        html_body = tpl[tpl.rfind("</style>"):]
        assert 'class="ex-hero-sponsor"' in html_body

    def test_sq13_sponsor_inside_profile_col(self, tpl):
        """v8: .ex-hero-sponsor must appear inside .ex-profile-col (hero layer, not skills zone)."""
        html_body = tpl[tpl.rfind("</style>"):]
        profile_col_idx = html_body.find('class="ex-profile-col"')
        sponsor_idx = html_body.find('class="ex-hero-sponsor"', profile_col_idx)
        skill_cats_idx = html_body.find('class="ex-skill-cats"')
        assert profile_col_idx != -1
        assert sponsor_idx != -1, ".ex-hero-sponsor not found after .ex-profile-col"
        # sponsor must come before skill-cats (it's in the hero zone, above the skills zone)
        assert sponsor_idx < skill_cats_idx, (
            ".ex-hero-sponsor must be in the hero layer (before .ex-skill-cats), not in the skills zone"
        )

    def test_sq13_no_ex_sponsor_slot_in_body(self, tpl):
        """v8: old .ex-sponsor-slot class must be absent — skills zone is fully freed."""
        html_body = tpl[tpl.rfind("</style>"):]
        assert 'class="ex-sponsor-slot"' not in html_body

    def test_sq13_sponsor_gated_by_jinja2_condition(self, tpl):
        """Sponsor block must be gated — no layout break when sponsor_logo_url is absent."""
        assert "sponsor_logo_url" in tpl
        assert "{% if sponsor_logo_url" in tpl or "{% if sponsor_logo_url or" in tpl


# ── SQ-14: Animated mode gating ───────────────────────────────────────────────

class TestAnimatedModeGating:
    def test_sq14_animated_mode_jinja2_gate(self, tpl):
        """@keyframes must only be inside the {% if animated_mode %} block."""
        assert "{% if animated_mode %}" in tpl
        assert "@keyframes" in tpl
        # All @keyframes must be after the animated_mode if block
        first_keyframe = tpl.find("@keyframes")
        anim_gate      = tpl.find("{% if animated_mode %}")
        assert first_keyframe > anim_gate, (
            "@keyframes appeared before the animated_mode gate — static exports would be affected"
        )


# ── SQ-15: File integrity ─────────────────────────────────────────────────────

class TestFileIntegrity:
    def test_sq15_file_exists(self):
        """Template file must exist at expected path."""
        assert _TPL_PATH.exists(), f"Template not found: {_TPL_PATH}"

    def test_sq15_has_doctype(self, tpl):
        """Must be a complete HTML document."""
        assert "<!DOCTYPE html>" in tpl

    def test_sq15_has_ex_card_root(self, tpl):
        """Root card div with class ex-card must be present."""
        assert 'class="ex-card"' in tpl


# ── SQ-16: Removed v4 artefacts ───────────────────────────────────────────────

class TestRemovedV4Artefacts:
    def test_sq16_no_logo_host_class_in_html(self, tpl):
        """ex-cat--logo-host must not appear in HTML (v4 filler pattern removed)."""
        # Only check the HTML body part (after </style>)
        html_body = tpl[tpl.rfind("</style>"):]
        assert "ex-cat--logo-host" not in html_body

    def test_sq16_no_logo_slot_in_html(self, tpl):
        """ex-logo-slot div must not appear in HTML body (v5 has no empty filler slots)."""
        html_body = tpl[tpl.rfind("</style>"):]
        assert 'class="ex-logo-slot"' not in html_body


# ── SQ-17/18/19/20: Position landscape panel presence ────────────────────────

class TestPositionMiniPanel:
    def test_sq17_pos_panel_class_in_html(self, tpl):
        """v7 landscape panel: .ex-pos-panel-landscape div must be present in the HTML body."""
        html_body = tpl[tpl.rfind("</style>"):]
        assert 'class="ex-pos-panel-landscape"' in html_body

    def test_sq18_landscape_pitch_svg_viewbox(self, tpl):
        """v8: Landscape pitch SVG must use viewBox '0 0 105 68' (real 105m×68m pitch geometry)."""
        assert 'viewBox="0 0 105 68"' in tpl, (
            "Landscape SVG must use real pitch geometry viewBox '0 0 105 68', not the old '0 0 100 24'"
        )

    def test_sq18_old_squashed_viewbox_absent(self, tpl):
        """v8: Old aspect-squashed viewBox '0 0 100 24' must not be present."""
        assert 'viewBox="0 0 100 24"' not in tpl

    def test_sq19_position_nodes_in_svg(self, tpl):
        """SVG rendering must reference position_nodes from template context."""
        html_body = tpl[tpl.rfind("</style>"):]
        # position_nodes must be used in the for loop inside the SVG block
        assert "position_nodes" in html_body

    def test_sq19_coordinate_transform_formula(self, tpl):
        """v8: SVG must use real-geometry coordinate transform (cx=node.x*105, cy=node.y*68)."""
        html_body = tpl[tpl.rfind("</style>"):]
        assert "node.x * 105" in html_body, (
            "SVG coordinate transform must use node.x * 105 (105m pitch width), not node.x * 100"
        )
        assert "node.y * 68" in html_body, (
            "SVG coordinate transform must use node.y * 68 (68m pitch height), not node.y * 24"
        )

    def test_sq20_pos_panel_inside_skill_col(self, tpl):
        """v11: panel is INSIDE .ex-skill-cats and appears after skill_categories[2] (Mental)."""
        html_body      = tpl[tpl.rfind("</style>"):]
        idx_skill_cats = html_body.find('class="ex-skill-cats"')
        idx_mental     = html_body.find("skill_categories[2]", idx_skill_cats)
        idx_panel      = html_body.find('class="ex-pos-panel-landscape"', idx_mental)
        assert idx_skill_cats > 0 and idx_mental > idx_skill_cats, (
            "skill_categories[2] (Mental) must appear inside ex-skill-cats"
        )
        assert idx_panel > idx_mental, (
            "v11: ex-pos-panel-landscape must appear after Mental cat (skill_categories[2])"
        )


# ── SQ-21/22/23/24/25/27: Position panel structural integrity ─────────────────

class TestPositionPanelIntegrity:
    def test_sq21_column_count_unchanged(self, tpl):
        """Panel inside Col 2 must not add a 4th column — still exactly 3 ex-skill-col divs."""
        matches = re.findall(r'class="ex-skill-col[^"]*"', tpl)
        assert len(matches) == 3, (
            f"Position Map must not add a 4th column; found {len(matches)} ex-skill-col divs"
        )

    def test_sq22_pos_panel_gated_by_position_nodes(self, tpl):
        """Landscape panel must be inside {% if position_nodes %} guard — graceful when no pos set."""
        assert "{% if position_nodes %}" in tpl
        panel_idx = tpl.find('class="ex-pos-panel-landscape"')
        gate_idx  = tpl.rfind("{% if position_nodes %}", 0, panel_idx)
        assert gate_idx != -1, "ex-pos-panel-landscape must be inside a {% if position_nodes %} block"

    def test_sq23_pos_svg_explicit_dimensions(self, tpl):
        """.ex-pos-svg-landscape must define flex or explicit dimension to fill container."""
        svg_rule_start = tpl.find(".ex-pos-svg-landscape {")
        assert svg_rule_start != -1, ".ex-pos-svg-landscape CSS rule must be defined"
        svg_rule_end = tpl.find("}", svg_rule_start)
        svg_rule = tpl[svg_rule_start: svg_rule_end + 1]
        # v9: flex:1 fills remaining width; OR explicit width/height — either is acceptable
        has_flex   = "flex:" in svg_rule
        has_width  = "width:" in svg_rule
        assert has_flex or has_width, (
            ".ex-pos-svg-landscape must use flex: or width: to fill the panel"
        )

    def test_sq24_pos_panel_not_ex_cat(self, tpl):
        """Landscape panel must NOT use .ex-cat class — immune to cat fade-slide animation."""
        html_body = tpl[tpl.rfind("</style>"):]
        assert 'class="ex-pos-panel-landscape"' in html_body
        assert 'class="ex-cat ex-pos-panel-landscape"' not in html_body
        assert 'class="ex-pos-panel-landscape ex-cat"' not in html_body

    def test_sq24_pos_panel_animated_mode_self_contained(self, tpl):
        """In animated_mode block, .ex-pos-panel-landscape gets its own animation rule."""
        anim_start = tpl.find("{% if animated_mode %}")
        anim_end   = tpl.find("{% endif %}", anim_start)
        anim_block = tpl[anim_start: anim_end]
        assert ".ex-pos-panel-landscape" in anim_block
        pos_panel_anim = anim_block.find(".ex-pos-panel-landscape {")
        ex_cat_anim    = anim_block.find(".ex-cat {")
        assert pos_panel_anim != ex_cat_anim, (
            ".ex-pos-panel-landscape and .ex-cat must have separate animation rules"
        )

    def test_sq27_no_node_label_in_landscape_svg(self, tpl):
        """Landscape SVG must not render node.label text — position name is in the hero badge."""
        html_body = tpl[tpl.rfind("</style>"):]
        panel_start = html_body.find('class="ex-pos-panel-landscape"')
        panel_end   = html_body.find("{% endif %}", panel_start)
        assert panel_start > 0 and panel_end > panel_start
        svg_block = html_body[panel_start:panel_end]
        assert "node.label" not in svg_block, (
            "Landscape SVG must not render node.label — no text labels per design spec"
        )


# ── SQ-28: preserveAspectRatio — v8 undistorted render ───────────────────────

class TestAspectRatioIntegrity:
    def test_sq28_preserve_aspect_ratio_meet(self, tpl):
        """v8: landscape SVG must declare preserveAspectRatio='xMidYMid meet' — no stretch, no crop."""
        html_body = tpl[tpl.rfind("</style>"):]
        panel_start = html_body.find('class="ex-pos-panel-landscape"')
        assert panel_start != -1
        # Scope to the <svg>…</svg> element directly — avoids inner {% endif %} ambiguity
        svg_open  = html_body.find("<svg", panel_start)
        svg_close = html_body.find("</svg>", svg_open) + len("</svg>")
        svg_elem  = html_body[svg_open:svg_close]
        assert 'preserveAspectRatio="xMidYMid meet"' in svg_elem, (
            "Landscape SVG must use preserveAspectRatio='xMidYMid meet' to prevent horizontal stretch"
        )

    def test_sq28_no_stretch_viewbox(self, tpl):
        """v8: old 100:24 squashed viewBox (artificially flat) must be absent."""
        assert 'viewBox="0 0 100 24"' not in tpl


# ── SQ-29: Position Map info column presence — v9 ────────────────────────────

class TestPositionMapInfoColumn:
    def test_sq29_pos_info_div_in_html(self, tpl):
        """v9: .ex-pos-info div must be present inside .ex-pos-panel-landscape."""
        html_body = tpl[tpl.rfind("</style>"):]
        panel_start = html_body.find('class="ex-pos-panel-landscape"')
        assert panel_start != -1
        panel_region = html_body[panel_start: panel_start + 1500]
        assert 'class="ex-pos-info"' in panel_region, (
            ".ex-pos-info info column must be present inside .ex-pos-panel-landscape"
        )

    def test_sq29_pos_panel_title_css_defined(self, tpl):
        """v9: .ex-pos-panel-title CSS rule must be defined for the panel header."""
        assert ".ex-pos-panel-title" in tpl

    def test_sq29_pos_info_css_defined(self, tpl):
        """v9: .ex-pos-info CSS rule must be defined."""
        assert ".ex-pos-info {" in tpl

    def test_sq29_pos_primary_name_css_defined(self, tpl):
        """v9: .ex-pos-primary-name CSS rule must be defined (gold primary position text)."""
        assert ".ex-pos-primary-name" in tpl

    def test_sq29_pos_panel_flex_row(self, tpl):
        """v9: .ex-pos-panel-landscape must use flex-direction: row (info left, SVG right)."""
        panel_css_start = tpl.find(".ex-pos-panel-landscape {")
        assert panel_css_start != -1
        panel_css_end = tpl.find("}", panel_css_start)
        panel_css = tpl[panel_css_start: panel_css_end + 1]
        assert "flex-direction: row" in panel_css, (
            ".ex-pos-panel-landscape must be flex-direction: row in v9"
        )


# ── SQ-30: Photo column is clean portrait block — v9 ─────────────────────────

class TestCleanPhotoColumn:
    def test_sq30_no_pos_badge_in_body(self, tpl):
        """v9: .ex-pos-badge class must not appear anywhere in the HTML body."""
        html_body = tpl[tpl.rfind("</style>"):]
        assert 'class="ex-pos-badge"' not in html_body, (
            ".ex-pos-badge removed in v9 — all position info lives in Position Map panel"
        )

    def test_sq30_no_pos_badge_css(self, tpl):
        """v9: .ex-pos-badge CSS rule must be absent (class removed entirely)."""
        assert ".ex-pos-badge {" not in tpl

    def test_sq30_no_photo_sec_chips(self, tpl):
        """v9: secondary chips must NOT appear inside the photo column block."""
        html_body = tpl[tpl.rfind("</style>"):]
        photo_col_start = html_body.find('class="ex-photo-col"')
        photo_col_end   = html_body.find('class="ex-profile-col"', photo_col_start)
        assert photo_col_start != -1 and photo_col_end > photo_col_start
        photo_block = html_body[photo_col_start:photo_col_end]
        assert 'ex-sec-pos-chip' not in photo_block, (
            "Position chips must not be in the photo column — they live in Position Map panel"
        )


# ── SQ-31: No legend in Position Map panel — v9 ──────────────────────────────

class TestNoPositionLegend:
    def test_sq31_no_legend_marker_elements(self, tpl):
        """v9: Position Map panel must not contain a PRIMARY/SECONDARY/OTHER legend."""
        html_body = tpl[tpl.rfind("</style>"):]
        panel_start = html_body.find('class="ex-pos-panel-landscape"')
        panel_end   = html_body.find("{% endif %}", panel_start)
        assert panel_start > 0 and panel_end > panel_start
        panel_block = html_body[panel_start:panel_end]
        # "OTHER" would only appear as a legend item — its absence confirms no legend
        assert "OTHER" not in panel_block, (
            "Position Map panel must not contain a legend — 'OTHER' marker found"
        )

    def test_sq31_no_legend_css_classes(self, tpl):
        """v9: legend-specific CSS classes must not be defined."""
        legend_classes = [".ex-pos-legend", ".ex-legend-item", ".ex-legend-marker"]
        for cls in legend_classes:
            assert cls not in tpl, f"Legend CSS class {cls} must not be defined in v9"


# ── SQ-32: v10 flat layout — panel above columns, no ex-col-right ─────────────

class TestV10FlatLayout:
    def test_sq32_no_ex_col_right_in_html(self, tpl):
        """v10: .ex-col-right wrapper removed — Mental and Set Pieces are flat siblings."""
        html_body = tpl[tpl.rfind("</style>"):]
        assert 'class="ex-col-right"' not in html_body, (
            ".ex-col-right wrapper must be absent in v10 — all three skill columns are flat siblings"
        )

    def test_sq32_no_ex_col_right_skills_in_html(self, tpl):
        """v10: .ex-col-right-skills inner wrapper also removed."""
        html_body = tpl[tpl.rfind("</style>"):]
        assert 'class="ex-col-right-skills"' not in html_body, (
            ".ex-col-right-skills inner wrapper must be absent in v10"
        )


# ── SQ-33: v11 column modifier classes + Position Map placement ───────────────

class TestV11ColumnModifiers:
    def test_sq33_col_outfield_class_present(self, tpl):
        """v11: Col 1 must carry ex-col-outfield modifier class."""
        html_body = tpl[tpl.rfind("</style>"):]
        assert 'ex-col-outfield' in html_body, (
            "ex-col-outfield modifier class must be present on Col 1 (Outfield)"
        )

    def test_sq33_col_mental_pos_class_present(self, tpl):
        """v11: Col 2 must carry ex-col-mental-pos modifier class."""
        html_body = tpl[tpl.rfind("</style>"):]
        assert 'ex-col-mental-pos' in html_body, (
            "ex-col-mental-pos modifier class must be present on Col 2 (Mental + PosMap)"
        )

    def test_sq33_col_sets_phys_class_present(self, tpl):
        """v11: Col 3 must carry ex-col-sets-phys modifier class."""
        html_body = tpl[tpl.rfind("</style>"):]
        assert 'ex-col-sets-phys' in html_body, (
            "ex-col-sets-phys modifier class must be present on Col 3 (Set Pieces + Physical)"
        )

    def test_sq33_panel_inside_col_mental_pos(self, tpl):
        """v11: Position Map panel must be inside .ex-col-mental-pos column."""
        html_body   = tpl[tpl.rfind("</style>"):]
        col2_start  = html_body.find('ex-col-mental-pos')
        col3_start  = html_body.find('ex-col-sets-phys')
        panel_idx   = html_body.find('class="ex-pos-panel-landscape"')
        assert col2_start > 0 and col3_start > 0 and panel_idx > 0
        assert col2_start < panel_idx < col3_start, (
            "v11: ex-pos-panel-landscape must be inside ex-col-mental-pos (after Mental, before Col 3)"
        )

    def test_sq33_panel_height_160px(self, tpl):
        """v11: .ex-pos-panel-landscape CSS must declare height: 160px."""
        panel_css_start = tpl.find(".ex-pos-panel-landscape {")
        assert panel_css_start != -1
        panel_css_end = tpl.find("}", panel_css_start)
        panel_css = tpl[panel_css_start: panel_css_end + 1]
        assert "height: 160px" in panel_css, (
            ".ex-pos-panel-landscape must be 160px tall in v11 for legible pitch rendering"
        )

    def test_sq33_info_col_140px(self, tpl):
        """v11: .ex-pos-info CSS must declare flex: 0 0 140px (narrowed to widen SVG area)."""
        info_css_start = tpl.find(".ex-pos-info {")
        assert info_css_start != -1
        info_css_end = tpl.find("}", info_css_start)
        info_css = tpl[info_css_start: info_css_end + 1]
        assert "140px" in info_css, (
            ".ex-pos-info must be 140px wide in v11 — wider SVG area for legible pitch"
        )

    def test_sq33_flex_fill_css_rules_present(self, tpl):
        """v11: column flex-fill CSS rules must be defined for all three modifier classes."""
        assert ".ex-col-outfield .ex-cat" in tpl, "flex-fill rule for ex-col-outfield missing"
        assert ".ex-col-mental-pos .ex-cat" in tpl, "flex-fill rule for ex-col-mental-pos missing"
        assert ".ex-col-sets-phys .ex-cat:last-child" in tpl, "flex-fill rule for ex-col-sets-phys missing"
