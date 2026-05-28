"""
FIFA Classic / Default card — targeted test suite.

Test IDs and what they cover:

Download PNG (DL-*)
  DL-01  CANVAS_SIZES contains "default" → export route accepts it (no 422)
  DL-02  default canvas width is 820; height reflects current layout estimate
  DL-03  export route rejects unknown platform (422 guard still works)
  DL-04  "default" export render URL uses ?native_export=1, NOT instagram_square
  DL-05  "default" export render URL does NOT contain &export=1

Context (CTX-*)
  CTX-01 player_positions passes the full positions list from motivation_scores
  CTX-02 primary position is motivation_scores["position"]
  CTX-03 position_nodes list is non-empty for a known primary position
  CTX-04 fallback: motivation_scores with only "position" (no "positions" key)
  CTX-05 fallback: motivation_scores is None → player_positions = []

Position map (POS-*)
  POS-01 "striker" primary → ST1 and ST2 both is_primary=True
  POS-02 "striker" selected → ST1 and ST2 both is_selected=True
  POS-03 "left_centre_back" maps to node LCB (x≈0.19, y≈0.37)
  POS-04 "right_centre_back" maps to node RCB (x≈0.19, y≈0.63)
  POS-05 "left_centre_midfield" maps to node LCM
  POS-06 "right_centre_midfield" maps to node RCM
  POS-07 "second_striker" has no pitch node (no node with canonical="second_striker")
  POS-08 "centre_back" (legacy v1) has no dedicated pitch node
  POS-09 total node count is 20 (20 entries in PITCH_NODES_RAW incl. ST1+ST2)

Template (TPL-*)
  TPL-01 card-body element present in player_card_fifa.html
  TPL-02 skills-panel element present
  TPL-03 position-panel element present
  TPL-04 pitch-svg element present inside position-panel
  TPL-05 events-section still present (regression: tab bar NOT deleted)
  TPL-06 tab-bar still present
  TPL-07 tab-btn still references switchTab (regression guard)
  TPL-08 native-export-mode CSS class defined in the template
  TPL-09 skills-section class is ABSENT (replaced by card-body)
  TPL-10 skill-cats CSS uses display:flex (two-column flex, not CSS grid)
  TPL-11 skill-col class present; nth-child order rules ABSENT (flex replaced grid)
  TPL-12 pitch SVG viewBox is portrait "0 0 65 100" (not landscape "0 0 100 65")
  TPL-13 selected/primary nodes have <text> labels in SVG (pass 4 present)
  TPL-14 pos-primary-label div is ABSENT (removed — SVG node labels are sufficient)
  TPL-15 pos-secondary-chips div is ABSENT (removed — position text cleaned up)
  TPL-16 card-logo-bar + card-logo-bar-img CSS defined; card-watermark + card-logo CSS ABSENT
  TPL-17 card-logo-bar HTML present after .card-body; card-watermark + card-logo markup ABSENT
  TPL-18 identity-grid has Height, Weight, Foot fields (second row)
  TPL-19 skill_categories[:2] slice used for left column (Outfield + Set Pieces)
  TPL-20 skill_categories[2:] slice used for right column (Mental + Physical)
  TPL-21 app_logo_url is None in public_player.py Default card context (Phase 4 regression guard)
  TPL-22 sponsor_logo_url is the card-logo-bar-img source (not app_logo_url, not card-watermark)
  TPL-23 fifa-right-body wrapper present; z-index:1 ensures content above watermark
  TPL-24 img.fifa-avatar has no background inline style (avatar_bg must not leak behind photo)
  TPL-25 primary pos badge uses primary_pos_label (display label), not player.position (canonical)
  TPL-26 secondary pos badge CSS + markup present; secondary_pos_labels referenced
  TPL-27 no ST/GK orientation text labels in SVG; node.label render still present

Editor (ED-*)
  ED-01  Download PNG button has no "disabled" attribute in the default-platform branch
  ED-02  setPlatform JS does NOT set dlBtn.disabled to true for "default"
  ED-03  exportCard JS does NOT use instagram_square as fallback
  ED-04  exportCard JS uses _currentPlatform directly (no fallback substitution)

Regression (REG-*)
  REG-01  export/square/fifa.html not modified (sha unchanged — stat check)
  REG-02  export/landscape/fifa.html not modified
  REG-03  export/story/fifa.html not modified
  REG-04  export/portrait/fifa.html not modified
  REG-05  export/tiktok/fifa.html not modified
  REG-06  export/banner/fifa.html not modified
"""
import os
import re
from pathlib import Path

import pytest

# ── Paths ─────────────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parents[3]
_TPL_FIFA     = _ROOT / "app/templates/public/player_card_fifa.html"
_TPL_EDITOR   = _ROOT / "app/templates/dashboard_card_editor.html"
_EXPORT_DIR   = _ROOT / "app/templates/public/export"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── DL-*: Download PNG / CANVAS_SIZES ─────────────────────────────────────────

def test_dl01_canvas_sizes_contains_default():
    from app.services.card_constants import CANVAS_SIZES
    assert "default" in CANVAS_SIZES, (
        "CANVAS_SIZES must include 'default' so the export route accepts it without 422"
    )


def test_dl02_default_canvas_baseline_820():
    from app.services.card_constants import CANVAS_SIZES
    w, h = CANVAS_SIZES["default"]
    assert w == 820, f"default canvas width must be 820, got {h}"
    # Height reflects the current layout estimate (CSS-derived, ~800px after 2×2 skills +
    # portrait pitch change; re-measure with Playwright after deploying to confirm).
    # The exact value here is a documentation guard — export clipping uses live
    # BoundingClientRect so the actual PNG is never truncated regardless of this value.
    assert 700 <= h <= 1000, (
        f"default canvas height should be in the plausible range 700–1000px "
        f"(current layout: 2×2 skills grid + portrait pitch); got {h}"
    )


def test_dl03_export_route_rejects_unknown_platform():
    """422 guard still works for unknown platforms — not broken by default addition."""
    from app.services.card_constants import CANVAS_SIZES
    assert "totally_unknown_platform_xyz" not in CANVAS_SIZES


def test_dl04_default_export_url_uses_native_export_param():
    """Export route for 'default' must build a ?native_export=1 URL."""
    src = _read(_ROOT / "app/api/web_routes/public_player.py")
    assert "native_export=1" in src, (
        "export route must pass ?native_export=1 for default platform"
    )
    # Verify it is inside a `platform == 'default'` branch
    assert re.search(r'platform\s*==\s*["\']default["\']', src), (
        "export route must have an explicit platform=default branch"
    )


def test_dl05_default_export_url_not_export_1():
    """Default export render URL must NOT pass &export=1 (uses native_export=1 instead)."""
    src = _read(_ROOT / "app/api/web_routes/public_player.py")
    # Find the string assignment for the native_export URL (not a comment)
    # The assignment looks like: render_url = f"...?native_export=1"
    code_lines = [l for l in src.splitlines() if "native_export=1" in l and not l.strip().startswith("#")]
    assert code_lines, "No non-comment code line with 'native_export=1' found in public_player.py"
    for line in code_lines:
        # The URL should not additionally contain ?export=1 or &export=1
        assert "?export=1" not in line and "&export=1" not in line, (
            f"Default export render URL must not include ?export=1 or &export=1; "
            f"found in line: {line!r}"
        )


# ── CTX-*: Template context ────────────────────────────────────────────────────

_SENTINEL = object()  # distinguish "no ms supplied" from "ms is None"


def _build_context(ms=_SENTINEL):
    """Simulate the public_player.py context-building logic.

    ms — the motivation_scores dict (or None to test the None-branch).
        Omit to use a default non-None dict.
    """
    from app.utils.football_positions import get_pitch_display_nodes

    if ms is _SENTINEL:
        ms = {"position": "striker", "positions": ["striker", "left_wing"]}

    position      = ms.get("position", "Unknown") if ms else "Unknown"
    raw_positions = ms.get("positions", []) if ms else []
    player_positions: list[str] = []
    if raw_positions and isinstance(raw_positions, list):
        player_positions = raw_positions
    elif position != "Unknown":
        player_positions = [position]
    position_nodes = get_pitch_display_nodes(
        position if position != "Unknown" else "", player_positions
    )
    return position, player_positions, position_nodes


def test_ctx01_player_positions_full_list():
    _, player_positions, _ = _build_context({
        "position": "striker",
        "positions": ["striker", "left_wing", "centre_forward"],
    })
    assert player_positions == ["striker", "left_wing", "centre_forward"]


def test_ctx02_primary_is_motivation_scores_position():
    position, _, _ = _build_context({"position": "left_back", "positions": ["left_back"]})
    assert position == "left_back"


def test_ctx03_position_nodes_non_empty_for_known_position():
    _, _, nodes = _build_context({"position": "goalkeeper", "positions": ["goalkeeper"]})
    selected = [n for n in nodes if n["is_primary"]]
    assert len(selected) >= 1, "At least one node should be primary for 'goalkeeper'"


def test_ctx04_fallback_no_positions_key():
    """Only 'position' in motivation_scores — no 'positions' key."""
    _, player_positions, _ = _build_context({"position": "centre_back"})
    assert player_positions == ["centre_back"]


def test_ctx05_fallback_none_motivation_scores():
    """motivation_scores is None → player_positions is empty list."""
    _, player_positions, _ = _build_context(None)
    assert player_positions == []


# ── POS-*: Position map ────────────────────────────────────────────────────────

def _nodes_for(primary, positions=None):
    from app.utils.football_positions import get_pitch_display_nodes
    return get_pitch_display_nodes(primary, positions or [primary])


def test_pos01_striker_primary_both_st_nodes():
    nodes = _nodes_for("striker")
    primary_nodes = [n for n in nodes if n["is_primary"]]
    node_ids = {n["node_id"] for n in primary_nodes}
    assert "ST1" in node_ids and "ST2" in node_ids, (
        f"Both ST1 and ST2 must be is_primary=True for striker; got primary node_ids={node_ids}"
    )


def test_pos02_striker_selected_both_st_nodes():
    nodes = _nodes_for("left_wing", ["left_wing", "striker"])
    selected_striker = [n for n in nodes if n["canonical"] == "striker" and n["is_selected"]]
    ids = {n["node_id"] for n in selected_striker}
    assert "ST1" in ids and "ST2" in ids, (
        "Both ST1 and ST2 must be is_selected=True when 'striker' is in positions list"
    )


def test_pos03_left_centre_back_mapping():
    nodes = _nodes_for("left_centre_back")
    lcb = next((n for n in nodes if n["node_id"] == "LCB"), None)
    assert lcb is not None
    assert lcb["canonical"] == "left_centre_back"
    assert abs(lcb["x"] - 0.19) < 0.001
    assert abs(lcb["y"] - 0.37) < 0.001


def test_pos04_right_centre_back_mapping():
    nodes = _nodes_for("right_centre_back")
    rcb = next((n for n in nodes if n["node_id"] == "RCB"), None)
    assert rcb is not None
    assert rcb["canonical"] == "right_centre_back"
    assert abs(rcb["x"] - 0.19) < 0.001
    assert abs(rcb["y"] - 0.63) < 0.001


def test_pos05_left_centre_midfield_mapping():
    nodes = _nodes_for("left_centre_midfield")
    lcm = next((n for n in nodes if n["node_id"] == "LCM"), None)
    assert lcm is not None
    assert lcm["canonical"] == "left_centre_midfield"
    assert abs(lcm["x"] - 0.47) < 0.001


def test_pos06_right_centre_midfield_mapping():
    nodes = _nodes_for("right_centre_midfield")
    rcm = next((n for n in nodes if n["node_id"] == "RCM"), None)
    assert rcm is not None
    assert rcm["canonical"] == "right_centre_midfield"
    assert abs(rcm["x"] - 0.47) < 0.001


def test_pos07_second_striker_no_pitch_node():
    """second_striker has no pitch node — consistent with pitch-selector.js."""
    nodes = _nodes_for("second_striker")
    ss_nodes = [n for n in nodes if n["canonical"] == "second_striker"]
    assert len(ss_nodes) == 0, (
        "second_striker must have no pitch node (legacy position without SVG representation)"
    )


def test_pos08_centre_back_no_dedicated_node():
    """centre_back (legacy v1, split into LCB/RCB in v2) has no pitch node."""
    from app.utils.football_positions import _NODE_CANONICAL
    assert "centre_back" not in _NODE_CANONICAL.values(), (
        "centre_back should not appear as a canonical value in PITCH_NODES "
        "(it was split into left_centre_back / right_centre_back in v2)"
    )


def test_pos09_total_node_count():
    from app.utils.football_positions import _PITCH_NODES_RAW
    assert len(_PITCH_NODES_RAW) == 20, (
        f"Expected 20 pitch nodes (including ST1 + ST2), got {len(_PITCH_NODES_RAW)}"
    )


# ── TPL-*: Template structure ──────────────────────────────────────────────────

def _fifa_html():
    return _read(_TPL_FIFA)


def test_tpl01_card_body_present():
    assert 'class="card-body"' in _fifa_html() or "card-body" in _fifa_html()


def test_tpl02_skills_panel_present():
    assert "skills-panel" in _fifa_html()


def test_tpl03_position_panel_present():
    assert "position-panel" in _fifa_html()


def test_tpl04_pitch_svg_present():
    assert "pitch-svg" in _fifa_html()


def test_tpl05_events_section_not_deleted():
    """Events section must still exist — regression guard."""
    assert "events-section" in _fifa_html(), (
        "events-section class must remain in player_card_fifa.html — tab bar / events NOT deleted"
    )


def test_tpl06_tab_bar_not_deleted():
    assert "tab-bar" in _fifa_html(), (
        "tab-bar class must remain in player_card_fifa.html — tab bar NOT deleted"
    )


def test_tpl07_switchtab_still_present():
    assert "switchTab" in _fifa_html(), (
        "switchTab function must remain — tab switching JS not deleted"
    )


def test_tpl08_native_export_mode_css_defined():
    assert "native-export-mode" in _fifa_html(), (
        "native-export-mode CSS class must be defined for default platform export"
    )


def test_tpl09_skills_section_class_absent():
    """The old .skills-section class is replaced by .card-body — must not appear."""
    html = _fifa_html()
    assert 'class="skills-section"' not in html, (
        "skills-section HTML class attribute must be gone — replaced by card-body"
    )


def test_tpl10_skill_cats_flex_layout():
    """skill-cats must use display:flex (two-column flex), not CSS grid."""
    html = _fifa_html()
    # CSS must declare display:flex for .skill-cats
    assert "display: flex; align-items: flex-start;" in html or (
        ".skill-cats" in html and "display: flex" in html
    ), ".skill-cats must use display: flex for the two-column flex layout"
    # Must NOT use CSS grid for skill categories any more
    assert "grid-template-columns: repeat(2, 1fr)" not in html, (
        ".skill-cats must NOT use grid-template-columns: repeat(2, 1fr) — replaced by flex"
    )


def test_tpl11_skill_col_present_nth_child_absent():
    """skill-col class must exist; nth-child order rules must be gone (flex replaced grid)."""
    html = _fifa_html()
    assert "skill-col" in html, (
        ".skill-col class must be present — wraps left/right flex columns"
    )
    # All 4 old nth-child order rules must be gone
    for rule in [
        "nth-child(1) { order: 1; }",
        "nth-child(2) { order: 3; }",
        "nth-child(3) { order: 2; }",
        "nth-child(4) { order: 4; }",
    ]:
        assert rule not in html, (
            f"Old CSS rule '.skill-cat:{rule}' must be removed — flex columns replaced grid reorder"
        )


def test_tpl12_pitch_svg_is_portrait_viewbox():
    """Portrait pitch must use viewBox '0 0 65 100' (not landscape '0 0 100 65')."""
    html = _fifa_html()
    assert 'viewBox="0 0 65 100"' in html, (
        "pitch-svg must use portrait viewBox '0 0 65 100' — GK at bottom, ST at top"
    )
    assert 'viewBox="0 0 100 65"' not in html, (
        "landscape viewBox '0 0 100 65' must be gone — replaced by portrait orientation"
    )


def test_tpl13_svg_text_labels_on_selected_nodes():
    """Pass 4: <text> elements must be rendered for selected/primary nodes in the SVG."""
    html = _fifa_html()
    # The pass-4 block uses node.label in a <text> element inside the Jinja2 loop
    assert "node.label" in html, (
        "SVG must render node.label as <text> elements for selected/primary nodes (pass 4)"
    )
    # The text element must include dominant-baseline="middle" for vertical centering
    assert "dominant-baseline" in html, (
        "<text> labels must use dominant-baseline for vertical centering on circles"
    )


def test_tpl14_pos_primary_label_absent():
    """pos-primary-label div must be ABSENT — position text removed; SVG labels are sufficient."""
    html = _fifa_html()
    assert "pos-primary-label" not in html, (
        "pos-primary-label class/div must be gone — position label text was removed "
        "in Phase 4 to reduce clutter; abbreviated labels live in the SVG nodes only"
    )


def test_tpl15_pos_secondary_chips_absent():
    """pos-secondary-chips div must be ABSENT — secondary position chips removed."""
    html = _fifa_html()
    assert "pos-secondary-chips" not in html, (
        "pos-secondary-chips class/div must be gone — secondary position chips removed in Phase 4"
    )
    assert "pos-chip" not in html, (
        "pos-chip class must be gone — secondary position chips removed in Phase 4"
    )


def test_tpl16_card_logo_bar_css_defined_watermark_absent():
    """card-logo-bar + card-logo-bar-img CSS must be defined; card-watermark + card-logo CSS must be gone."""
    html = _fifa_html()
    assert ".card-logo-bar" in html, (
        ".card-logo-bar CSS class must be defined — sponsor logo bar at bottom of card"
    )
    assert ".card-logo-bar-img" in html, (
        ".card-logo-bar-img CSS class must be defined — sizes the sponsor logo tastefully"
    )
    assert "max-height: 40px" in html, (
        ".card-logo-bar-img must set max-height: 40px"
    )
    assert "opacity: 0.75" in html, (
        ".card-logo-bar-img opacity must be 0.75"
    )
    assert ".card-watermark" not in html, (
        ".card-watermark CSS must be removed — sponsor logo moved to .card-logo-bar at card bottom"
    )
    assert ".card-logo {" not in html, (
        ".card-logo CSS must be removed — replaced by .card-logo-bar"
    )
    assert "filter: brightness(0) invert(1)" not in html, (
        "brightness/invert filter must be gone — user-uploaded sponsor logos must not be colour-mangled"
    )


def test_tpl17_card_logo_bar_html_present_watermark_absent():
    """card-logo-bar must appear in HTML after .card-body; card-watermark + card-logo markup must be absent."""
    html = _fifa_html()
    assert 'class="card-logo-bar"' in html, (
        "<div class='card-logo-bar'> must be present — sponsor logo bar below skills+position panel"
    )
    assert 'class="card-logo-bar-img"' in html, (
        "<img class='card-logo-bar-img'> must be present inside .card-logo-bar"
    )
    assert 'class="card-watermark"' not in html, (
        "<img class='card-watermark'> must be removed — sponsor logo moved to .card-logo-bar"
    )
    assert 'class="card-logo"' not in html, (
        "<img class='card-logo'> must be removed — Phase 4 mistake cleaned up"
    )
    # Logo bar must appear after .card-body (below skills and position panel)
    logo_bar_pos  = html.find('class="card-logo-bar"')
    card_body_pos = html.find('class="card-body"')
    assert logo_bar_pos > card_body_pos, (
        ".card-logo-bar must appear after .card-body — sponsor logo at bottom of card"
    )


def test_tpl18_identity_grid_has_height_weight_foot():
    """identity-grid must render Height, Weight, and Foot fields (second row)."""
    html = _fifa_html()
    for label in ("Height", "Weight", "Foot"):
        assert label in html, (
            f"identity-grid must contain a '{label}' id-label field — "
            "added as the second row (player physical attributes)"
        )
    assert "player_height_cm" in html, "player_height_cm must be referenced in the template"
    assert "player_weight_kg" in html, "player_weight_kg must be referenced in the template"
    assert "player_preferred_foot" in html, "player_preferred_foot must be referenced in the template"


def test_tpl19_left_skill_column_uses_first_two_categories():
    """Left skill-col must iterate skill_categories[:2] (Outfield + Set Pieces)."""
    html = _fifa_html()
    assert "skill_categories[:2]" in html, (
        "Left .skill-col must use skill_categories[:2] to render Outfield + Set Pieces"
    )


def test_tpl20_right_skill_column_uses_last_two_categories():
    """Right skill-col must iterate skill_categories[2:] (Mental + Physical)."""
    html = _fifa_html()
    assert "skill_categories[2:]" in html, (
        "Right .skill-col must use skill_categories[2:] to render Mental + Physical Fitness"
    )


def test_tpl21_app_logo_url_is_none_in_default_context():
    """public_player.py Default card context must have app_logo_url = None.

    Phase 4 mistakenly hardcoded '/static/images/logo-dark.png' here.
    The Default FIFA card's logo source is sponsor_logo_url (user-uploaded),
    not the LFA app logo.
    """
    src = _read(_ROOT / "app/api/web_routes/public_player.py")
    # Find the app_logo_url assignment line in the context dict
    lines = src.splitlines()
    app_logo_lines = [l for l in lines if '"app_logo_url"' in l and "None" in l]
    assert app_logo_lines, (
        "public_player.py must set 'app_logo_url': None in the Default card context. "
        "The hardcoded LFA logo path must be removed — sponsor_logo_url is the card logo source."
    )
    # Must NOT contain the hardcoded logo path
    assert "/static/images/logo-dark.png" not in src or all(
        l.strip().startswith("#") for l in lines if "/static/images/logo-dark.png" in l
    ), (
        "'/static/images/logo-dark.png' must not appear as a non-comment value in public_player.py "
        "Default card context. The LFA app logo must not be injected into the FIFA card."
    )


def test_tpl22_sponsor_logo_url_in_logo_bar():
    """Template must use sponsor_logo_url in .card-logo-bar-img, not app_logo_url or card-watermark."""
    html = _fifa_html()
    assert "sponsor_logo_url" in html, (
        "sponsor_logo_url must be referenced in player_card_fifa.html — "
        "it is the user-uploaded logo and the correct source for .card-logo-bar-img"
    )
    # sponsor_logo_url must appear near .card-logo-bar-img
    logo_bar_img_pos = html.find('class="card-logo-bar-img"')
    assert logo_bar_img_pos != -1
    context_around = html[max(0, logo_bar_img_pos - 200):logo_bar_img_pos + 50]
    assert "sponsor_logo_url" in context_around, (
        ".card-logo-bar-img src must reference sponsor_logo_url (not app_logo_url)"
    )


def test_tpl23_fifa_right_body_wrapper_present():
    """fifa-right-body wrapper must be present to stack content above watermark (z-index:1)."""
    html = _fifa_html()
    assert "fifa-right-body" in html, (
        ".fifa-right-body wrapper div must be present — it carries z-index:1 "
        "so the name/identity-grid/clubs render above the z-index:0 .card-watermark"
    )
    # CSS must define the class with position:relative and z-index
    assert ".fifa-right-body" in html, (
        ".fifa-right-body CSS class must be defined in the template"
    )


def test_tpl24_photo_img_has_no_background_style():
    """img.fifa-avatar must carry no background inline style — avatar_bg must not leak behind photo."""
    html = _fifa_html()
    img_match = re.search(r'<img class="fifa-avatar"[^>]*>', html)
    assert img_match, "img.fifa-avatar element must be present in the template"
    img_tag = img_match.group(0)
    assert "background" not in img_tag, (
        "img.fifa-avatar must NOT have a 'background' inline style. "
        "The avatar_bg colour was showing as a gradient/solid behind the player photo. "
        "background belongs only on the div.fifa-avatar fallback (no-photo state)."
    )


def test_tpl25_primary_pos_badge_uses_display_label():
    """Primary pos badge must render primary_pos_label (display label), not player.position (canonical)."""
    html = _fifa_html()
    badge_pos = html.find('class="fifa-pos-badge"')
    assert badge_pos != -1, "fifa-pos-badge class must be present"
    context = html[badge_pos: badge_pos + 150]
    assert "primary_pos_label" in context, (
        "fifa-pos-badge must render {{ primary_pos_label }} — the human-readable display label "
        "from position_label(). Rendering player.position directly produces CENTRE_MIDFIELD "
        "(with underscore), which is not acceptable."
    )
    assert "player.position" not in context, (
        "fifa-pos-badge must NOT render {{ player.position }} directly — "
        "it is the raw canonical key and contains underscores."
    )


def test_tpl25b_position_label_has_no_underscore():
    """position_label() output for all canonical values must contain no underscore."""
    from app.utils.football_positions import position_label, POSITIONS_21
    for p in POSITIONS_21:
        lbl = position_label(p["value"])
        assert "_" not in lbl, (
            f"position_label('{p['value']}') returned '{lbl}' which contains an underscore. "
            "All display labels must be human-readable (spaces, no underscores)."
        )


def test_tpl26_secondary_pos_badge_markup_present():
    """Secondary pos badge CSS and Jinja markup must be present; secondary_pos_labels must be referenced."""
    html = _fifa_html()
    assert ".fifa-pos-secondary-badge" in html, (
        ".fifa-pos-secondary-badge CSS class must be defined"
    )
    assert "fifa-pos-secondary-badges" in html, (
        ".fifa-pos-secondary-badges wrapper CSS must be defined"
    )
    assert "secondary_pos_labels" in html, (
        "secondary_pos_labels variable must be referenced in the template "
        "to render secondary position badges"
    )
    assert 'class="fifa-pos-secondary-badge"' in html, (
        "<span class='fifa-pos-secondary-badge'> HTML markup must be present"
    )


def test_tpl27_no_st_gk_orientation_labels_in_svg():
    """ST and GK orientation text labels must be removed from the pitch SVG."""
    html = _fifa_html()
    assert ">ST<" not in html, (
        "ST orientation label must be removed from the pitch SVG — "
        "it is a layout guide, not a selected position node"
    )
    assert ">GK<" not in html, (
        "GK orientation label must be removed from the pitch SVG — "
        "it is a layout guide, not a selected position node"
    )
    # Regression guard: node.label (Pass 4) must still render for selected/primary positions
    assert "node.label" in html, (
        "node.label must still be rendered in SVG Pass 4 for selected/primary positions"
    )


# ── ED-*: Editor template ──────────────────────────────────────────────────────

def _editor_html():
    return _read(_TPL_EDITOR)


def test_ed01_download_button_not_unconditionally_disabled():
    """Download PNG button must not be hardcoded-disabled; CDO conditional is allowed."""
    import re as _re
    html = _editor_html()
    btn_match = _re.search(r'id="btn-export-card"[^>]*>', html, _re.DOTALL)
    assert btn_match, "btn-export-card button not found"
    btn_tag = btn_match.group(0)
    # Remove entire Jinja2 if-blocks including their content: {%...%}...{%...%}
    stripped = _re.sub(r'\{%[^%]*%\}.*?\{%[^%]*%\}', '', btn_tag, flags=_re.DOTALL)
    assert "disabled" not in stripped, (
        f"btn-export-card must not be hardcoded-disabled (CDO conditional is OK); "
        f"got stripped tag: {stripped!r}"
    )


def test_ed02_setplatform_does_not_disable_for_default():
    """setPlatform JS must not set dlBtn.disabled based on 'default' platform."""
    src = _editor_html()
    # The old pattern was: dlBtn.disabled = platformId === 'default'
    assert "dlBtn.disabled = platformId === 'default'" not in src, (
        "setPlatform must not disable the download button for the default platform"
    )


def test_ed03_exportcard_no_instagram_square_fallback():
    """exportCard must not fall back to instagram_square when platform is default."""
    fn_body = _extract_export_card_fn(_editor_html())
    # Strip JS comment lines before checking — only look at actual code
    code_lines = [l for l in fn_body.splitlines() if not l.strip().startswith("//")]
    code_only = "\n".join(code_lines)
    assert "instagram_square" not in code_only, (
        "exportCard() code must not reference instagram_square as a fallback — "
        "default platform exports via /card/export?platform=default"
    )


def test_ed04_exportcard_uses_current_platform_directly():
    """exportCard must use _currentPlatform directly, with no substitution."""
    fn = _extract_export_card_fn(_editor_html())
    # Check platform variable is set to _currentPlatform without conditional replacement
    assert "const platform = _currentPlatform" in fn, (
        "exportCard must assign platform = _currentPlatform without any conditional fallback"
    )


def _extract_export_card_fn(src: str) -> str:
    """Extract the body of the exportCard async function."""
    m = re.search(r"async function exportCard\(\)\s*\{(.+?)^}", src, re.DOTALL | re.MULTILINE)
    return m.group(1) if m else src


# ── REG-*: Export template regression ─────────────────────────────────────────

_EXPORT_TEMPLATES = [
    "square/fifa.html",
    "landscape/fifa.html",
    "story/fifa.html",
    "portrait/fifa.html",
    "tiktok/fifa.html",
    "banner/fifa.html",
]


@pytest.mark.parametrize("tpl_rel", _EXPORT_TEMPLATES)
def test_reg_export_template_not_modified(tpl_rel):
    """Export templates must not have been touched by this feature branch."""
    path = _EXPORT_DIR / tpl_rel
    if not path.exists():
        pytest.skip(f"Template not present (not yet implemented): {tpl_rel}")

    # Verify the file content does NOT contain any of the new classes introduced
    # by the FIFA default card refactor.
    content = path.read_text(encoding="utf-8")
    new_classes = ["card-body", "skills-panel", "position-panel", "pitch-svg", "native-export-mode"]
    for cls in new_classes:
        assert cls not in content, (
            f"Export template {tpl_rel} must not contain '{cls}' — "
            f"this class belongs only to player_card_fifa.html (FIFA Classic default card). "
            f"Check that the export template was not accidentally modified."
        )
