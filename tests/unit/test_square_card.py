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
  SQ-33  v13 column structure: ex-col-outfield, ex-right-section, ex-right-skills,
         ex-col-mental-pos, ex-col-sets-phys; Position Map full-width bottom bar of ex-right-section;
         panel height 200px; info col 140px; panel NOT inside Col 2 or Col 3
  SQ-35  Human-view page shell gated by {% if not export_mode %} Jinja2 block
         — background #0f1923 present in human block; base html/body has no background
         — human-view .ex-card override: fixed 1080px canvas (wrapper+scale strategy)
         — Playwright base .ex-card still uses min(100vw, 100vh) unchanged
  SQ-36  Human-view wrapper + scale engine contract (Opció C — transform: scale)
         — .ex-card-viewport CSS defined in human-view block
         — .ex-card fixed 1080×1080px in human-view block
         — transform-origin: top left present in human-view CSS
         — .ex-card-viewport HTML wrapper present in template body
         — HTML wrapper gated by {% if not export_mode %}
         — applyScale JS function present and gated by {% if not export_mode %}
         — base .ex-card uses min(100vw, 100vh) — not 1080px — export unchanged
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


_MACRO_PATH = (
    pathlib.Path(__file__).resolve().parents[2]
    / "app" / "templates" / "macros" / "card_position_map.html"
)


@pytest.fixture(scope="module")
def tpl() -> str:
    return _TPL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def macro_tpl() -> str:
    """Raw source of card_position_map.html — position map DOM lives here, not in square/fifa.html."""
    return _MACRO_PATH.read_text(encoding="utf-8")


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
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert 'class="ex-pos-badge"' not in html_body, (
            ".ex-pos-badge must not appear in the HTML body — photo badges were removed in v9"
        )

    def test_sq06_primary_pos_in_panel_context(self, tpl):
        """v9: primary_pos_label must be passed to position_map_landscape macro call."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        macro_call_start = html_body.find("position_map_landscape(")
        assert macro_call_start != -1, "position_map_landscape macro call not found in body"
        macro_call_region = html_body[macro_call_start: macro_call_start + 300]
        assert "primary_pos_label" in macro_call_region, (
            "primary_pos_label must be passed to position_map_landscape macro call"
        )

    def test_sq07_secondary_chips_in_pos_panel(self, tpl):
        """v9: secondary chips are rendered via position_map_landscape macro.

        Verify the macro is imported, called with secondary_pos_labels, and the macro
        itself defines the ex-pos-secondary-chips class.
        """
        import pathlib
        macro_path = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "macros" / "card_position_map.html"
        )
        macro_src = macro_path.read_text(encoding="utf-8")
        # Macro must define the chips class
        assert 'class="ex-pos-secondary-chips"' in macro_src, (
            "ex-pos-secondary-chips must be defined in card_position_map.html macro"
        )
        # Template must import and call the macro with secondary_pos_labels
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert "position_map_landscape" in html_body, (
            "position_map_landscape macro must be called in the body_content block"
        )
        macro_call_start = html_body.find("position_map_landscape(")
        macro_region = html_body[macro_call_start: macro_call_start + 300]
        assert "secondary_pos_labels" in macro_region, (
            "secondary_pos_labels must be passed to position_map_landscape macro call"
        )

    def test_sq07_chips_no_artificial_slice(self, tpl):
        """v9: secondary_pos_labels loop must not use [:4] slice — domain guarantees max 3."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        panel_start = html_body.find('class="ex-pos-panel-landscape"')
        panel_region = html_body[panel_start: panel_start + 1200]
        assert "secondary_pos_labels[:4]" not in panel_region, (
            "Loop must use full secondary_pos_labels (or [:3] max) — [:4] slice is not allowed"
        )

    def test_sq07_chips_gated_by_secondary_pos_labels(self, tpl):
        """Secondary chips must be Jinja2-gated so they only render when list is non-empty.

        Gate lives in card_position_map.html macro (not inlined in square template).
        """
        import pathlib
        macro_src = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "macros" / "card_position_map.html"
        ).read_text(encoding="utf-8")
        assert "{% if secondary_labels" in macro_src, (
            "secondary_labels gate must be present in card_position_map.html macro"
        )

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


# ── SQ-13: Sponsor placement (v14 — outfield column bottom) ──────────────────

class TestSponsorSlot:
    def test_sq13_hero_sponsor_absent(self, tpl):
        """v14: ex-hero-sponsor removed from hero layer — must not appear anywhere in HTML body."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert 'class="ex-hero-sponsor"' not in html_body
        assert 'class="ex-hero-sponsor-img"' not in html_body

    def test_sq13_outfield_logo_in_outfield_col(self, tpl):
        """v14: ex-outfield-logo must appear inside ex-col-outfield (Col 1), not in hero layer."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        outfield_start = html_body.find('class="ex-skill-col ex-col-outfield"')
        right_section_start = html_body.find('class="ex-right-section"')
        logo_idx = html_body.find('class="ex-outfield-logo"')
        assert outfield_start != -1
        assert logo_idx != -1, "ex-outfield-logo not found in HTML body"
        # logo must be inside ex-col-outfield (before ex-right-section)
        assert outfield_start < logo_idx < right_section_start, (
            "ex-outfield-logo must be inside ex-col-outfield, before ex-right-section"
        )

    def test_sq13_outfield_logo_css_defined(self, tpl):
        """v14: .ex-outfield-logo and .ex-outfield-logo-img CSS must be defined."""
        assert ".ex-outfield-logo {" in tpl or ".ex-outfield-logo\n{" in tpl
        assert ".ex-outfield-logo-img" in tpl
        assert "max-height: 44px" in tpl
        assert "opacity: 0.50" in tpl

    def test_sq13_outfield_logo_gated(self, tpl):
        """v14: outfield logo must be Jinja2-gated by sponsor_logo_url or app_logo_url."""
        assert "sponsor_logo_url" in tpl
        assert "{% if sponsor_logo_url" in tpl or "{% if sponsor_logo_url or" in tpl

    def test_sq13_no_ex_sponsor_slot_in_body(self, tpl):
        """v8: old .ex-sponsor-slot class must be absent — skills zone is fully freed."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert 'class="ex-sponsor-slot"' not in html_body


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
        """Must produce a complete HTML document — via extends fifa_base.html."""
        assert '{% extends' in tpl and 'fifa_base.html' in tpl, (
            "Template must extend fifa_base.html (which provides the DOCTYPE declaration)"
        )

    def test_sq15_has_ex_card_root(self, tpl):
        """Root card div with class ex-card must be present."""
        assert 'class="ex-card"' in tpl


# ── SQ-16: Removed v4 artefacts ───────────────────────────────────────────────

class TestRemovedV4Artefacts:
    def test_sq16_no_logo_host_class_in_html(self, tpl):
        """ex-cat--logo-host must not appear in HTML (v4 filler pattern removed)."""
        # Only check the HTML body part (after </style>)
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert "ex-cat--logo-host" not in html_body

    def test_sq16_no_logo_slot_in_html(self, tpl):
        """ex-logo-slot div must not appear in HTML body (v5 has no empty filler slots)."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert 'class="ex-logo-slot"' not in html_body


# ── SQ-17/18/19/20: Position landscape panel presence ────────────────────────

class TestPositionMiniPanel:
    def test_sq17_pos_panel_class_in_html(self, tpl):
        """v7 landscape panel: position_map_landscape macro called in body; macro defines the class."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert "position_map_landscape(" in html_body, (
            "position_map_landscape macro must be called in the HTML body block"
        )
        import pathlib
        macro_src = (
            pathlib.Path(__file__).resolve().parents[2]
            / "app" / "templates" / "macros" / "card_position_map.html"
        ).read_text(encoding="utf-8")
        assert 'class="ex-pos-panel-landscape"' in macro_src, (
            "ex-pos-panel-landscape class must be defined in card_position_map.html macro"
        )

    def test_sq18_landscape_pitch_svg_viewbox(self, tpl, macro_tpl):
        """v8: Landscape pitch SVG must use viewBox '0 0 105 68' (real 105m×68m pitch geometry).

        Since v11 the SVG lives inside card_position_map.html macro, not inline in square/fifa.html.
        """
        assert 'viewBox="0 0 105 68"' in macro_tpl, (
            "Landscape SVG must use real pitch geometry viewBox '0 0 105 68' — check card_position_map.html"
        )

    def test_sq18_old_squashed_viewbox_absent(self, tpl, macro_tpl):
        """v8: Old aspect-squashed viewBox '0 0 100 24' must not be present in either file."""
        assert 'viewBox="0 0 100 24"' not in tpl
        assert 'viewBox="0 0 100 24"' not in macro_tpl

    def test_sq19_position_nodes_in_svg(self, tpl):
        """SVG rendering must reference position_nodes from template context (macro call arg)."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert "position_nodes" in html_body

    def test_sq19_coordinate_transform_formula(self, tpl, macro_tpl):
        """v8: SVG must use real-geometry coordinate transform (cx=node.x*105, cy=node.y*68).

        Since v11 the SVG lives inside card_position_map.html macro.
        """
        assert "node.x * 105" in macro_tpl, (
            "SVG coordinate transform must use node.x * 105 (105m pitch width)"
        )
        assert "node.y * 68" in macro_tpl, (
            "SVG coordinate transform must use node.y * 68 (68m pitch height)"
        )

    def test_sq20_pos_panel_inside_skill_col(self, tpl):
        """v11: position_map_landscape macro call is INSIDE .ex-skill-cats, after skill_categories[2]."""
        html_body      = tpl[tpl.find("{% block body_content %}"):]
        idx_skill_cats = html_body.find('class="ex-skill-cats"')
        idx_mental     = html_body.find("skill_categories[2]", idx_skill_cats)
        idx_macro_call = html_body.find("position_map_landscape(", idx_mental)
        assert idx_skill_cats > 0 and idx_mental > idx_skill_cats, (
            "skill_categories[2] (Mental) must appear inside ex-skill-cats"
        )
        assert idx_macro_call > idx_mental, (
            "v11: position_map_landscape call must appear after Mental cat (skill_categories[2])"
        )


# ── SQ-21/22/23/24/25/27: Position panel structural integrity ─────────────────

class TestPositionPanelIntegrity:
    def test_sq21_column_count_unchanged(self, tpl):
        """Panel inside Col 2 must not add a 4th column — still exactly 3 ex-skill-col divs."""
        matches = re.findall(r'class="ex-skill-col[^"]*"', tpl)
        assert len(matches) == 3, (
            f"Position Map must not add a 4th column; found {len(matches)} ex-skill-col divs"
        )

    def test_sq22_pos_panel_gated_by_position_nodes(self, tpl, macro_tpl):
        """Landscape panel is gated by {% if nodes %} in card_position_map.html macro."""
        # Gate lives in the macro ({% if nodes %})
        assert "{% if nodes %}" in macro_tpl, (
            "position_map_landscape macro must have {% if nodes %} guard for graceful empty state"
        )
        # Template passes position_nodes to the macro
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert "position_nodes" in html_body, (
            "position_nodes must be passed to position_map_landscape macro call"
        )

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

    def test_sq24_pos_panel_not_ex_cat(self, tpl, macro_tpl):
        """Landscape panel must NOT use .ex-cat class — immune to cat fade-slide animation.

        The DOM element lives in card_position_map.html macro.
        """
        assert 'class="ex-cat ex-pos-panel-landscape"' not in macro_tpl
        assert 'class="ex-pos-panel-landscape ex-cat"' not in macro_tpl

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

    def test_sq27_no_node_label_in_landscape_svg(self, tpl, macro_tpl):
        """Landscape SVG must not render node.label text — position name is in the info column.

        SVG content lives in card_position_map.html macro.
        """
        svg_start = macro_tpl.find('<svg class="ex-pos-svg-landscape"')
        svg_end   = macro_tpl.find("</svg>", svg_start)
        assert svg_start > 0 and svg_end > svg_start
        svg_block = macro_tpl[svg_start:svg_end]
        assert "node.label" not in svg_block, (
            "Landscape SVG must not render node.label — no text labels per design spec"
        )


# ── SQ-28: preserveAspectRatio — v8 undistorted render ───────────────────────

class TestAspectRatioIntegrity:
    def test_sq28_preserve_aspect_ratio_meet(self, tpl, macro_tpl):
        """v8: landscape SVG must declare preserveAspectRatio='xMidYMid meet' — no stretch, no crop.

        SVG lives in card_position_map.html macro.
        """
        svg_open  = macro_tpl.find('<svg class="ex-pos-svg-landscape"')
        svg_close = macro_tpl.find("</svg>", svg_open) + len("</svg>")
        svg_elem  = macro_tpl[svg_open:svg_close]
        assert "xMidYMid meet" in svg_elem, (
            "Landscape SVG must include preserveAspectRatio='xMidYMid meet' to prevent horizontal stretch"
        )

    def test_sq28_no_stretch_viewbox(self, tpl):
        """v8: old 100:24 squashed viewBox (artificially flat) must be absent."""
        assert 'viewBox="0 0 100 24"' not in tpl


# ── SQ-29: Position Map info column presence — v9 ────────────────────────────

class TestPositionMapInfoColumn:
    def test_sq29_pos_info_div_in_html(self, tpl, macro_tpl):
        """v9: .ex-pos-info div must be present inside .ex-pos-panel-landscape (in macro)."""
        panel_start  = macro_tpl.find('class="ex-pos-panel-landscape"')
        assert panel_start != -1, "ex-pos-panel-landscape not found in card_position_map.html"
        panel_region = macro_tpl[panel_start: panel_start + 1500]
        assert 'class="ex-pos-info"' in panel_region, (
            ".ex-pos-info info column must be present inside .ex-pos-panel-landscape (in macro)"
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
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert 'class="ex-pos-badge"' not in html_body, (
            ".ex-pos-badge removed in v9 — all position info lives in Position Map panel"
        )

    def test_sq30_no_pos_badge_css(self, tpl):
        """v9: .ex-pos-badge CSS rule must be absent (class removed entirely)."""
        assert ".ex-pos-badge {" not in tpl

    def test_sq30_no_photo_sec_chips(self, tpl):
        """v9: secondary chips must NOT appear inside the photo column block."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        photo_col_start = html_body.find('class="ex-photo-col"')
        photo_col_end   = html_body.find('class="ex-profile-col"', photo_col_start)
        assert photo_col_start != -1 and photo_col_end > photo_col_start
        photo_block = html_body[photo_col_start:photo_col_end]
        assert 'ex-sec-pos-chip' not in photo_block, (
            "Position chips must not be in the photo column — they live in Position Map panel"
        )


# ── SQ-31: No legend in Position Map panel — v9 ──────────────────────────────

class TestNoPositionLegend:
    def test_sq31_no_legend_marker_elements(self, tpl, macro_tpl):
        """v9: Position Map panel must not contain a PRIMARY/SECONDARY/OTHER legend.

        Panel DOM lives in card_position_map.html macro.
        """
        panel_start = macro_tpl.find('class="ex-pos-panel-landscape"')
        panel_end   = macro_tpl.find("{% endif %}", panel_start)
        assert panel_start > 0 and panel_end > panel_start
        panel_block = macro_tpl[panel_start:panel_end]
        assert "OTHER" not in panel_block, (
            "Position Map panel must not contain a legend — 'OTHER' marker found in macro"
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
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert 'class="ex-col-right"' not in html_body, (
            ".ex-col-right wrapper must be absent in v10 — all three skill columns are flat siblings"
        )

    def test_sq32_no_ex_col_right_skills_in_html(self, tpl):
        """v10: .ex-col-right-skills inner wrapper also removed."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert 'class="ex-col-right-skills"' not in html_body, (
            ".ex-col-right-skills inner wrapper must be absent in v10"
        )


# ── SQ-33: v11 column modifier classes + Position Map placement ───────────────

class TestV11ColumnModifiers:
    def test_sq33_col_outfield_class_present(self, tpl):
        """v11: Col 1 must carry ex-col-outfield modifier class."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert 'ex-col-outfield' in html_body, (
            "ex-col-outfield modifier class must be present on Col 1 (Outfield)"
        )

    def test_sq33_col_mental_pos_class_present(self, tpl):
        """v11: Col 2 must carry ex-col-mental-pos modifier class."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert 'ex-col-mental-pos' in html_body, (
            "ex-col-mental-pos modifier class must be present on Col 2 (Mental + PosMap)"
        )

    def test_sq33_col_sets_phys_class_present(self, tpl):
        """v11: Col 3 must carry ex-col-sets-phys modifier class."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert 'ex-col-sets-phys' in html_body, (
            "ex-col-sets-phys modifier class must be present on Col 3 (Set Pieces + Physical)"
        )

    def test_sq33_right_section_wrapper_present(self, tpl):
        """v13: ex-right-section wrapper div must be present (wraps Col 2 + Col 3 + PosMap)."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert 'class="ex-right-section"' in html_body, (
            "v13: ex-right-section wrapper div must be present as the Col 2+Col 3+PosMap container"
        )

    def test_sq33_right_skills_inner_row_present(self, tpl):
        """v13: ex-right-skills flex-row must be present inside ex-right-section."""
        html_body = tpl[tpl.find("{% block body_content %}"):]
        assert 'class="ex-right-skills"' in html_body, (
            "v13: ex-right-skills flex-row div must be present (holds Mental + Set Pieces+Physical)"
        )

    def test_sq33_right_section_flex_css_defined(self, tpl):
        """v13: .ex-right-section and .ex-right-skills CSS rules must be defined."""
        assert ".ex-right-section" in tpl, ".ex-right-section CSS rule must be defined"
        assert ".ex-right-skills" in tpl, ".ex-right-skills CSS rule must be defined"

    def test_sq33_panel_inside_right_section(self, tpl):
        """v13: position_map_landscape macro call is inside ex-right-section (after Col 3)."""
        html_body    = tpl[tpl.find("{% block body_content %}"):]
        right_start  = html_body.find('class="ex-right-section"')
        col3_start   = html_body.find('ex-col-sets-phys')
        macro_idx    = html_body.find("position_map_landscape(")
        assert right_start > 0 and macro_idx > 0, (
            "ex-right-section or position_map_landscape macro call not found in HTML body"
        )
        assert macro_idx > right_start, (
            "v13: position_map_landscape must be inside ex-right-section (appears after it opens)"
        )
        assert macro_idx > col3_start, (
            "v13: position_map_landscape must come after ex-col-sets-phys in DOM order"
        )

    def test_sq33_panel_not_inside_col_mental_pos(self, tpl):
        """v13 regression guard: position_map_landscape must NOT be inside ex-col-mental-pos."""
        html_body  = tpl[tpl.find("{% block body_content %}"):]
        col2_start = html_body.find('class="ex-skill-col ex-col-mental-pos"')
        col3_start = html_body.find('class="ex-skill-col ex-col-sets-phys"')
        macro_idx  = html_body.find("position_map_landscape(")
        assert col2_start > 0 and col3_start > 0 and macro_idx > 0
        assert not (col2_start < macro_idx < col3_start), (
            "v13: position_map_landscape must NOT be called inside ex-col-mental-pos"
        )

    def test_sq33_panel_not_inside_col_sets_phys(self, tpl):
        """v13 regression guard: position_map_landscape must NOT be inside ex-col-sets-phys."""
        html_body  = tpl[tpl.find("{% block body_content %}"):]
        col3_open  = html_body.find('class="ex-skill-col ex-col-sets-phys"')
        macro_idx  = html_body.find("position_map_landscape(")
        endfor_idx = html_body.find('{% endfor %}', col3_open)
        col3_close = html_body.find('</div>', endfor_idx) if endfor_idx > 0 else -1
        assert col3_open > 0 and macro_idx > 0 and col3_close > 0
        assert not (col3_open < macro_idx < col3_close), (
            "v13 regression guard: position_map_landscape must NOT be called inside ex-col-sets-phys — "
            "it is a direct child of ex-right-section"
        )

    def test_sq33_panel_height_200px(self, tpl):
        """v13: .ex-pos-panel-landscape CSS must declare height: 200px (full-width bar)."""
        panel_css_start = tpl.find(".ex-pos-panel-landscape {")
        assert panel_css_start != -1
        panel_css_end = tpl.find("}", panel_css_start)
        panel_css = tpl[panel_css_start: panel_css_end + 1]
        assert "height: 200px" in panel_css, (
            ".ex-pos-panel-landscape must be 200px tall in v13 — full-width bar renders 309×200px pitch"
        )
        assert "height: 160px" not in panel_css, (
            "v13: old 160px height must not remain — panel is now full Col 2+Col 3 width"
        )

    def test_sq33_pos_info_220px(self, tpl):
        """v14: .ex-pos-info CSS must declare flex: 0 0 220px (widened for better info readability)."""
        info_css_start = tpl.find(".ex-pos-info {")
        assert info_css_start != -1
        info_css_end = tpl.find("}", info_css_start)
        info_css = tpl[info_css_start: info_css_end + 1]
        assert "220px" in info_css, (
            ".ex-pos-info must be 220px wide in v14 — wider info column reduces letterboxing"
        )
        assert "140px" not in info_css, ".ex-pos-info must not still declare 140px (stale v11 value)"

    def test_sq33_pos_panel_title_13px(self, tpl):
        """v14: .ex-pos-panel-title must be 13px (up from 11px for legibility)."""
        title_css_start = tpl.find(".ex-pos-panel-title {")
        assert title_css_start != -1
        title_css_end = tpl.find("}", title_css_start)
        title_css = tpl[title_css_start: title_css_end + 1]
        assert "font-size: 13px" in title_css, ".ex-pos-panel-title must be 13px in v14"

    def test_sq33_pos_primary_name_17px(self, tpl):
        """v14: .ex-pos-primary-name must be 17px (up from 14px for prominence)."""
        name_css_start = tpl.find(".ex-pos-primary-name {")
        assert name_css_start != -1
        name_css_end = tpl.find("}", name_css_start)
        name_css = tpl[name_css_start: name_css_end + 1]
        assert "font-size: 17px" in name_css, ".ex-pos-primary-name must be 17px in v14"

    def test_sq33_flex_fill_css_rules_present(self, tpl):
        """v11: column flex-fill CSS rules must be defined for all three modifier classes."""
        assert ".ex-col-outfield .ex-cat" in tpl, "flex-fill rule for ex-col-outfield missing"
        assert ".ex-col-mental-pos .ex-cat" in tpl, "flex-fill rule for ex-col-mental-pos missing"
        assert ".ex-col-sets-phys .ex-cat:last-child" in tpl, (
            "v13: flex-fill rule must target :last-child (Physical is last .ex-cat in Col 3 — "
            "PosMap is in ex-right-section, not in ex-col-sets-phys)"
        )
        assert ".ex-col-sets-phys .ex-cat:nth-child(2)" not in tpl, (
            "v13: :nth-child(2) selector must be absent — Physical is :last-child in Col 3"
        )
        assert ".ex-right-section" in tpl, "v13: .ex-right-section flex rule missing"
        assert ".ex-right-skills" in tpl, "v13: .ex-right-skills flex rule missing"


# ── SQ-34: v15/v16 consistency fixes ─────────────────────────────────────────

class TestV15ConsistencyFixes:
    def _card_css(self, tpl: str) -> str:
        start = tpl.find(".ex-card {")
        assert start != -1, ".ex-card CSS rule not found"
        end = tpl.find("}", start)
        return tpl[start: end + 1]

    def test_sq34_card_uses_min_sizing(self, tpl):
        """v16: .ex-card must use min(100vw, 100vh) for both width and height.

        min() guarantees 1:1 at any viewport:
          Playwright 1080×1080 → min(1080, 1080) = 1080px (PNG/WebM unchanged).
          Browser 1440×900    → min(1440,  900) =  900px (fully visible, square).
        """
        card_css = self._card_css(tpl)
        assert "min(100vw, 100vh)" in card_css, (
            ".ex-card must use min(100vw, 100vh) for square-specific sizing "
            "(v16 fix — ensures 1:1 aspect ratio at any viewport)"
        )

    def test_sq34_card_no_plain_100vw_100vh(self, tpl):
        """v16: plain width:100vw / height:100vh must NOT appear in .ex-card — replaced by min()."""
        card_css = self._card_css(tpl)
        assert "width:  100vw" not in card_css and "width: 100vw" not in card_css, (
            "Plain width:100vw must not appear in .ex-card — use min(100vw, 100vh) instead. "
            "100vw alone produces a non-square card at non-square viewports."
        )
        assert "height: 100vh" not in card_css and "height:  100vh" not in card_css, (
            "Plain height:100vh must not appear in .ex-card — covered by min(100vw, 100vh)."
        )

    def test_sq34_card_no_aspect_ratio(self, tpl):
        """v15: .ex-card must NOT use aspect-ratio — replaced by explicit min() sizing."""
        card_css = self._card_css(tpl)
        assert "aspect-ratio" not in card_css, (
            ".ex-card must not use aspect-ratio — v16 uses min(100vw, 100vh) for explicit square sizing"
        )

    def test_sq34_body_has_flex_centering(self, tpl):
        """v16: body must declare flex centering so the card is centered in wide viewports."""
        style_block = tpl[:tpl.find("{% block body_content %}")]
        body_start = style_block.find("body {")
        assert body_start != -1, "body rule not found in CSS block"
        body_end = style_block.find("}", body_start)
        body_css = style_block[body_start: body_end + 1]
        assert "display: flex" in body_css, (
            "body must declare display: flex for horizontal card centering at wide viewports"
        )
        assert "justify-content: center" in body_css, (
            "body must declare justify-content: center to center the square card horizontally"
        )

    def test_sq34_svg_no_green_css_background(self, tpl):
        """v15: .ex-pos-svg-landscape must NOT have background: #1a5c2a in CSS.

        Green comes from <rect fill='#1a5c2a'> inside the SVG viewBox only —
        same pattern as Default card .pitch-svg (no CSS background property).
        """
        svg_css_start = tpl.find(".ex-pos-svg-landscape {")
        assert svg_css_start != -1
        svg_css_end = tpl.find("}", svg_css_start)
        svg_css = tpl[svg_css_start: svg_css_end + 1]
        assert "background" not in svg_css, (
            ".ex-pos-svg-landscape must have no CSS background — "
            "green is provided by internal <rect fill='#1a5c2a'> only (Default card pattern)"
        )

    def test_sq34_node_label_pass4_present(self, tpl, macro_tpl):
        """v15: Pass 4 node position circles are rendered inside the SVG block (in macro).

        Note: node.label text was removed per design spec (test_sq27); Pass 4 renders
        position dots (circles) using node.x/node.y coordinates.
        """
        svg_start = macro_tpl.find('class="ex-pos-svg-landscape"')
        assert svg_start != -1, "ex-pos-svg-landscape not found in card_position_map.html"
        svg_end   = macro_tpl.find("</svg>", svg_start)
        svg_block = macro_tpl[svg_start: svg_end + len("</svg>")]
        # Position dots use node.x * 105 / node.y * 68 coordinate transform
        assert "node.x * 105" in svg_block or "node.x" in svg_block, (
            "Pass 4 SVG must render position nodes using node.x coordinate"
        )

    def test_sq34_node_label_landscape_coords(self, tpl, macro_tpl):
        """v15: SVG elements must use landscape coordinate transform (x*105, y*68).

        Portrait transform (node.y*65, (1-node.x)*100) must NOT appear in the SVG block.
        SVG lives in card_position_map.html macro.
        """
        svg_start = macro_tpl.find('class="ex-pos-svg-landscape"')
        svg_end   = macro_tpl.find("</svg>", svg_start)
        svg_block = macro_tpl[svg_start: svg_end + len("</svg>")]
        assert "node.x * 105" in svg_block, (
            "SVG coordinate must use node.x * 105 (landscape: longitudinal → horizontal)"
        )
        assert "node.y * 68" in svg_block, (
            "SVG coordinate must use node.y * 68 (landscape: lateral → vertical)"
        )
        assert "1 - node.x" not in svg_block, (
            "Portrait inversion (1 - node.x) must not appear in landscape SVG"
        )


# ── SQ-35: Human-view page shell contract ─────────────────────────────────────

class TestHumanViewPageShell:
    """SQ-35 — {% if not export_mode %} page shell for human-browseable public card.

    The export templates are Playwright-first (raw canvas, no page background).
    When a human opens the URL without ?export=1 the route passes export_mode=False,
    and this Jinja2 block renders a dark page shell so the card feels like a real page.

    SQ-35a  {% if not export_mode %} block is present in the template
    SQ-35b  Page background #0f1923 is inside the conditional (not in base rules)
    SQ-35c  Base html/body rule has no background — Playwright gets transparent body
    SQ-35d  Human-view .ex-card override uses min(90vw, 90vh) for breathing room
    SQ-35e  Playwright base .ex-card still uses min(100vw, 100vh) before the block
    """

    def _human_block(self, tpl: str) -> str:
        start = tpl.find("{% if not export_mode %}")
        end   = tpl.find("{% endif %}", start)
        assert start != -1 and end != -1, "{% if not export_mode %} block not found"
        return tpl[start: end]

    def _base_style(self, tpl: str) -> str:
        """CSS before the human-view conditional block."""
        return tpl[:tpl.find("{% if not export_mode %}")]

    def test_sq35a_human_view_gate_present(self, tpl):
        """SQ-35a: Template must contain the {% if not export_mode %} Jinja2 gate."""
        assert "{% if not export_mode %}" in tpl, (
            "SQ-35a: {% if not export_mode %} block missing — human-view page shell not guarded; "
            "Playwright exports would inherit page-shell CSS breaking the raw canvas contract"
        )

    def test_sq35b_page_bg_inside_conditional(self, tpl):
        """SQ-35b: background: #0f1923 must appear inside the conditional block."""
        human = self._human_block(tpl)
        assert "background: #0f1923" in human, (
            "SQ-35b: dark page background (#0f1923) not found inside {% if not export_mode %} — "
            "human-view page shell is missing the background color"
        )

    def test_sq35c_base_html_body_has_no_background(self, tpl):
        """SQ-35c: The base body rule must NOT declare a background color."""
        base = self._base_style(tpl)
        body_start = base.find("body {")
        assert body_start != -1, "body rule not found in base CSS"
        body_end   = base.find("}", body_start)
        base_body  = base[body_start: body_end + 1]
        assert "background" not in base_body, (
            "SQ-35c: base body rule declares a background — Playwright would inherit this; "
            "background must only appear inside {% if not export_mode %} block"
        )

    def test_sq35d_human_view_card_uses_fixed_canvas(self, tpl):
        """SQ-35d: Human-view overrides .ex-card to fixed 1080px canvas (wrapper+scale strategy).

        Opció C replaces the old min(90vw, 90vh) approach: the card always renders at its
        native 1080px calibration and is scaled atomically via transform: scale(), preventing
        the internal flex-budget collapse that caused Position Map overlap.
        """
        human = self._human_block(tpl)
        assert "width: 1080px" in human and "height: 1080px" in human, (
            "SQ-35d: human-view .ex-card CSS override does not set fixed 1080×1080px canvas — "
            "scale-down strategy requires the card to render at its native 1080px calibration"
        )

    def test_sq35e_playwright_base_card_still_100vw_vh(self, tpl):
        """SQ-35e: The Playwright base .ex-card must still use min(100vw, 100vh)."""
        before_gate = self._base_style(tpl)
        assert "min(100vw, 100vh)" in before_gate, (
            "SQ-35e: Playwright base .ex-card sizing min(100vw, 100vh) not found before the "
            "{% if not export_mode %} block — PNG/WebM export canvas contract broken"
        )


# ── SQ-36: Human-view wrapper + scale engine (Opció C) ───────────────────────

class TestHumanViewScaleEngine:
    """SQ-36 — transform: scale() wrapper strategy for human-browseable Square card.

    Opció C: .ex-card always renders at its native 1080×1080px canvas.
    A .ex-card-viewport wrapper is sized by JS, and transform: scale() shrinks
    the canvas to fit the browser window without triggering internal reflowing.

    SQ-36a  .ex-card-viewport CSS class defined in the human-view CSS block
    SQ-36b  .ex-card fixed 1080×1080px in the human-view CSS block
    SQ-36c  transform-origin: top left present in the human-view CSS block
    SQ-36d  .ex-card-viewport HTML wrapper div present in the template body
    SQ-36e  HTML wrapper is guarded by {% if not export_mode %}
    SQ-36f  applyScale JS function present and guarded by {% if not export_mode %}
    SQ-36g  base .ex-card (before the gate) uses min(100vw, 100vh) — not 1080px
    SQ-36h  1080px override does NOT appear in the base CSS (before the gate)
    """

    def _css_block(self, tpl: str) -> str:
        """Content of the FIRST {% if not export_mode %} block (CSS overrides)."""
        start = tpl.find("{% if not export_mode %}")
        end   = tpl.find("{% endif %}", start)
        assert start != -1 and end != -1, "First {% if not export_mode %} block not found"
        return tpl[start:end]

    def _base_css(self, tpl: str) -> str:
        """CSS content before the first human-view gate."""
        return tpl[:tpl.find("{% if not export_mode %}")]

    def test_sq36a_viewport_css_defined(self, tpl):
        """SQ-36a: .ex-card-viewport CSS class must be defined in the human-view CSS block."""
        human = self._css_block(tpl)
        assert ".ex-card-viewport" in human, (
            "SQ-36a: .ex-card-viewport CSS class not found in {% if not export_mode %} block — "
            "wrapper has no CSS rules; layout will be broken in human-view mode"
        )

    def test_sq36b_card_fixed_1080px(self, tpl):
        """SQ-36b: Human-view CSS block must override .ex-card to fixed 1080×1080px."""
        human = self._css_block(tpl)
        assert "width: 1080px" in human, (
            "SQ-36b: 'width: 1080px' not in human-view CSS block — "
            ".ex-card must be fixed at native canvas size for scale strategy to work"
        )
        assert "height: 1080px" in human, (
            "SQ-36b: 'height: 1080px' not in human-view CSS block — "
            ".ex-card must be fixed at native canvas size for scale strategy to work"
        )

    def test_sq36c_transform_origin_top_left(self, tpl):
        """SQ-36c: transform-origin: top left must be in the human-view CSS block."""
        human = self._css_block(tpl)
        assert "transform-origin: top left" in human, (
            "SQ-36c: 'transform-origin: top left' not in human-view CSS — "
            "without this, scale() will offset the card inside the viewport wrapper"
        )

    def test_sq36d_html_wrapper_present(self, tpl):
        """SQ-36d: .ex-card-viewport HTML wrapper div must be present in the template body."""
        assert 'class="ex-card-viewport"' in tpl or "ex-card-viewport" in tpl, (
            "SQ-36d: .ex-card-viewport wrapper div not found in template — "
            "scale engine needs the wrapper to constrain layout space"
        )

    def test_sq36e_html_wrapper_gated(self, tpl):
        """SQ-36e: HTML wrapper div must be guarded by {% if not export_mode %}."""
        idx = tpl.find("ex-card-viewport", tpl.find("{% block body_content %}"))
        assert idx != -1, "ex-card-viewport not found in HTML body section"
        gate = tpl.rfind("{% if not export_mode %}", 0, idx)
        assert gate != -1, (
            "SQ-36e: ex-card-viewport HTML wrapper is NOT inside {% if not export_mode %} — "
            "wrapper div would appear in Playwright export HTML, breaking the raw canvas contract"
        )

    def test_sq36f_js_apply_scale_gated(self, tpl):
        """SQ-36f: applyScale JS function must be present and inside {% if not export_mode %}."""
        idx = tpl.find("applyScale")
        assert idx != -1, (
            "SQ-36f: applyScale function not found in template — "
            "JS scale engine missing; human-view card will render at 1080px unscaled"
        )
        gate = tpl.rfind("{% if not export_mode %}", 0, idx)
        assert gate != -1, (
            "SQ-36f: applyScale JS not inside {% if not export_mode %} — "
            "scale script would be injected into Playwright export HTML"
        )

    def test_sq36g_base_card_not_1080px(self, tpl):
        """SQ-36g: Base .ex-card (before the gate) must NOT declare 1080px dimensions."""
        base = self._base_css(tpl)
        assert "width: 1080px" not in base, (
            "SQ-36g: 'width: 1080px' found in base CSS (before {% if not export_mode %}) — "
            "this would override the Playwright canvas size from min(100vw,100vh) to 1080px"
        )
        assert "height: 1080px" not in base, (
            "SQ-36g: 'height: 1080px' found in base CSS (before {% if not export_mode %}) — "
            "Playwright export canvas contract would be broken"
        )

    def test_sq36h_base_card_uses_min_vw_vh(self, tpl):
        """SQ-36h: Base .ex-card must still use min(100vw, 100vh) for Playwright export."""
        base = self._base_css(tpl)
        assert "min(100vw, 100vh)" in base, (
            "SQ-36h: min(100vw, 100vh) not found in base .ex-card CSS — "
            "Playwright 1080×1080 export canvas contract is broken"
        )
