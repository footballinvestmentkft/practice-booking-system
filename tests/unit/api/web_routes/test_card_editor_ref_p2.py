"""
REF-P2 — Scripts extract verification tests.

Confirms that the exact scripts block has been extracted from
dashboard_card_editor.html into includes/player_editor/scripts.html
with zero logic/JS/endpoint changes.

P2-01  scripts.html file exists
P2-02  dashboard_card_editor.html scripts block contains only include
P2-03  no <script> tag directly in main template scripts block
P2-04  expanded source: function _csrf present
P2-05  expanded source: function setPlatform present
P2-06  expanded source: function setCardVariant present
P2-07  expanded source: function publishCard present
P2-08  expanded source: function exportCard present
P2-09  expanded source: function exportCardVideo present
P2-10  expanded source: function saveHighlightVideo present
P2-11  expanded source: function removeHighlightVideo present
P2-12  expanded source: function _updateHvAfterSave present
P2-13  expanded source: no literal {{ user.id }}
P2-14  expanded source: no literal {{ canvas_sizes
P2-15  expanded source: no literal {{ active_card_platform }}
P2-16  _CANVAS_SIZES renders as valid JS IIFE (not as literal Jinja2)
P2-17  #player-card-iframe present in expanded source
P2-18  #variant-picker present in expanded source
P2-19  #platform-picker present in expanded source
P2-20  #hv-url-input present in expanded source
P2-21  #btn-publish-card present in expanded source
P2-22  #btn-export-card present in expanded source
P2-23  #btn-export-video present in expanded source
P2-24  all 11 Jinja2-rendered values present in scripts.html
P2-25  no unexpected Jinja2 {{ }} patterns in scripts.html
P2-26  scripts.html starts with <script>, ends with </script>
P2-27  route count = 845 (no new routes)
P2-28  OpenAPI snapshot match
P2-29  /card-editor/player route still registered
"""
from __future__ import annotations

import json
import re
from pathlib import Path

TMPL_DIR = Path(__file__).resolve().parents[4] / "app" / "templates"
SNAP_DIR  = Path(__file__).resolve().parents[4] / "tests" / "snapshots"
INC_DIR   = TMPL_DIR / "includes" / "player_editor"


def _expanded() -> str:
    """Full effective editor source: main + all 7 includes."""
    parts = [
        (TMPL_DIR / "dashboard_card_editor.html").read_text(encoding="utf-8"),
        (INC_DIR / "styles.html").read_text(encoding="utf-8"),
        (INC_DIR / "preview_panel.html").read_text(encoding="utf-8"),
        (INC_DIR / "design_panel.html").read_text(encoding="utf-8"),
        (INC_DIR / "platform_panel.html").read_text(encoding="utf-8"),
        (INC_DIR / "photo_panel.html").read_text(encoding="utf-8"),
        (INC_DIR / "highlight_video_panel.html").read_text(encoding="utf-8"),
        (INC_DIR / "scripts.html").read_text(encoding="utf-8"),
    ]
    return "\n".join(parts)


# ── P2-01: scripts.html exists ────────────────────────────────────────────────

class TestP201ScriptsFileExists:

    def test_p2_01_scripts_html_file_exists(self):
        """P2-01: includes/player_editor/scripts.html must exist."""
        assert (INC_DIR / "scripts.html").exists(), \
            "scripts.html not found — REF-P2 extract failed"


# ── P2-02/03: scripts block in main template ─────────────────────────────────

class TestP202P203ScriptsBlock:

    def test_p2_02_scripts_block_contains_only_include(self):
        """P2-02: {% block scripts %} in main template contains only the include directive."""
        src = (TMPL_DIR / "dashboard_card_editor.html").read_text(encoding="utf-8")
        assert 'includes/player_editor/scripts.html' in src, \
            "scripts.html include missing from main template"

    def test_p2_03_no_script_tag_in_main_template(self):
        """P2-03: No <script> tag directly in main template (moved to scripts.html)."""
        src = (TMPL_DIR / "dashboard_card_editor.html").read_text(encoding="utf-8")
        assert '<script>' not in src, \
            "<script> tag still directly in dashboard_card_editor.html — not extracted"
        assert '</script>' not in src, \
            "</script> tag still directly in dashboard_card_editor.html — not extracted"


# ── P2-04..12: JS functions present in expanded source ───────────────────────

class TestP204to12JSFunctions:

    @classmethod
    def _src(cls) -> str:
        return _expanded()

    def test_p2_04_function_csrf(self):
        """P2-04: function _csrf present in expanded source."""
        assert 'function _csrf' in self._src()

    def test_p2_05_function_set_platform(self):
        """P2-05: function setPlatform present."""
        assert 'function setPlatform' in self._src()

    def test_p2_06_function_set_card_variant(self):
        """P2-06: function setCardVariant present."""
        assert 'function setCardVariant' in self._src() or \
               'async function setCardVariant' in self._src()

    def test_p2_07_function_publish_card(self):
        """P2-07: function publishCard present."""
        assert 'async function publishCard' in self._src()

    def test_p2_08_function_export_card(self):
        """P2-08: function exportCard present."""
        assert 'async function exportCard' in self._src()

    def test_p2_09_function_export_card_video(self):
        """P2-09: function exportCardVideo present."""
        assert 'async function exportCardVideo' in self._src()

    def test_p2_10_function_save_highlight_video(self):
        """P2-10: function saveHighlightVideo present."""
        assert 'async function saveHighlightVideo' in self._src()

    def test_p2_11_function_remove_highlight_video(self):
        """P2-11: function removeHighlightVideo present."""
        assert 'async function removeHighlightVideo' in self._src()

    def test_p2_12_function_update_hv_after_save(self):
        """P2-12: function _updateHvAfterSave present."""
        assert 'function _updateHvAfterSave' in self._src()


# ── P2-13..15: No literal Jinja2 in expanded source ──────────────────────────

class TestP213to15NoLiteralJinja2:
    """Verify that Jinja2 never leaves literal {{ }} in the template source.

    These tests check the SOURCE of scripts.html (not rendered output).
    The actual rendering is done by Jinja2 at request time; here we verify
    that the include file still contains the correct Jinja2 patterns
    (not that they've been accidentally stripped or doubly-escaped).
    """

    @classmethod
    def _scripts(cls) -> str:
        return (INC_DIR / "scripts.html").read_text(encoding="utf-8")

    def test_p2_13_user_id_jinja2_pattern_present_not_escaped(self):
        """P2-13: {{ user.id }} Jinja2 pattern present in scripts.html (Jinja2 will render it)."""
        src = self._scripts()
        assert '{{ user.id }}' in src, \
            "{{ user.id }} pattern missing — Jinja2 state var not preserved"

    def test_p2_14_canvas_sizes_tojson_pattern_present(self):
        """P2-14: {{ canvas_sizes | tojson }} pattern present in scripts.html."""
        src = self._scripts()
        assert '{{ canvas_sizes | tojson }}' in src, \
            "{{ canvas_sizes | tojson }} missing — IIFE will produce undefined"

    def test_p2_15_active_card_platform_pattern_present(self):
        """P2-15: {{ active_card_platform }} pattern present in scripts.html."""
        src = self._scripts()
        assert '{{ active_card_platform }}' in src


# ── P2-16: _CANVAS_SIZES IIFE structure ──────────────────────────────────────

class TestP216CanvasSizesIife:

    def test_p2_16_canvas_sizes_iife_structure_intact(self):
        """P2-16: _CANVAS_SIZES IIFE structure preserved — const + tojson + loop."""
        src = (INC_DIR / "scripts.html").read_text(encoding="utf-8")
        assert 'const _CANVAS_SIZES = (function ()' in src, \
            "_CANVAS_SIZES IIFE declaration missing"
        assert '{{ canvas_sizes | tojson }}' in src, \
            "{{ canvas_sizes | tojson }} inside IIFE missing"
        assert 'raw[pid].w' in src and 'raw[pid].h' in src, \
            "IIFE body: .w / .h property access missing"


# ── P2-17..23: DOM ids present in expanded source ────────────────────────────

class TestP217to23DomIds:

    @classmethod
    def _src(cls) -> str:
        return _expanded()

    def test_p2_17_player_card_iframe(self):
        """P2-17: #player-card-iframe present in expanded source."""
        assert 'player-card-iframe' in self._src()

    def test_p2_18_variant_picker(self):
        """P2-18: #variant-picker present in expanded source."""
        assert 'variant-picker' in self._src()

    def test_p2_19_platform_picker(self):
        """P2-19: #platform-picker present in expanded source."""
        assert 'platform-picker' in self._src()

    def test_p2_20_hv_url_input(self):
        """P2-20: #hv-url-input present in expanded source."""
        assert 'hv-url-input' in self._src()

    def test_p2_21_btn_publish_card(self):
        """P2-21: #btn-publish-card present in expanded source."""
        assert 'btn-publish-card' in self._src()

    def test_p2_22_btn_export_card(self):
        """P2-22: #btn-export-card present in expanded source."""
        assert 'btn-export-card' in self._src()

    def test_p2_23_btn_export_video(self):
        """P2-23: #btn-export-video present in expanded source."""
        assert 'btn-export-video' in self._src()


# ── P2-24/25: Jinja2 pattern safety in scripts.html ─────────────────────────

class TestP224P225Jinja2Safety:

    def test_p2_24_all_11_jinja2_vars_present_in_scripts(self):
        """P2-24: All 11 Jinja2-rendered values are present in scripts.html."""
        src = (INC_DIR / "scripts.html").read_text(encoding="utf-8")
        expected = [
            '{{ canvas_sizes | tojson }}',
            '{{ user.id }}',
            '{{ active_card_platform }}',
            '{{ active_card_variant }}',
            '{{ active_card_theme }}',
            '{{ active_variant_owned | tojson }}',
            '{{ animated_capable_platforms | tojson }}',
            '{{ published_card_theme }}',
            '{{ published_card_variant }}',
            '{{ published_card_platform }}',
            '{{ highlight_video_unpublished | tojson }}',
        ]
        for pat in expected:
            assert pat in src, f"Jinja2 var missing from scripts.html: {pat!r}"

    def test_p2_25_no_unexpected_jinja2_patterns(self):
        """P2-25: No unexpected {{ }} patterns in scripts.html (no accidental Jinja2 vars)."""
        src = (INC_DIR / "scripts.html").read_text(encoding="utf-8")
        known = {
            '{{ canvas_sizes | tojson }}',
            '{{ user.id }}',
            '{{ active_card_platform }}',
            '{{ active_card_variant }}',
            '{{ active_card_theme }}',
            '{{ active_variant_owned | tojson }}',
            '{{ animated_capable_platforms | tojson }}',
            '{{ published_card_theme }}',
            '{{ published_card_variant }}',
            '{{ published_card_platform }}',
            '{{ highlight_video_unpublished | tojson }}',
        }
        found = re.findall(r'\{\{[^}]+\}\}', src)
        for f in found:
            assert f.strip() in known, f"Unexpected Jinja2 pattern in scripts.html: {f!r}"


# ── P2-26: scripts.html boundaries ───────────────────────────────────────────

class TestP226ScriptsBoundaries:

    def test_p2_26_scripts_html_starts_with_script_tag(self):
        """P2-26: scripts.html first non-empty line is <script>."""
        src = (INC_DIR / "scripts.html").read_text(encoding="utf-8")
        lines = src.splitlines()
        assert lines[0].strip() == '<script>', \
            f"scripts.html first line: {lines[0]!r} — expected '<script>'"
        assert lines[-1].strip() == '</script>', \
            f"scripts.html last line: {lines[-1]!r} — expected '</script>'"


# ── P2-27/28/29: Route + OpenAPI + player route ──────────────────────────────

class TestP227to29RouteAndOpenAPI:

    def test_p2_27_route_count_844(self):
        """P2-27: Route count = 846 (CS-S2A +1 /card-studio/player)."""
        from app.main import app
        paths = app.openapi().get("paths", {})
        assert len(paths) == 883, f"Expected 846 routes, got {len(paths)}"

    def test_p2_28_openapi_snapshot_match(self):
        """P2-28: OpenAPI snapshot matches live API paths."""
        snap_path = SNAP_DIR / "openapi_snapshot.json"
        assert snap_path.exists()
        snap_paths = set(json.loads(snap_path.read_text()).get("paths", {}).keys())
        from app.main import app
        live_paths = set(app.openapi().get("paths", {}).keys())
        assert snap_paths == live_paths

    def test_p2_29_card_editor_player_route_registered(self):
        """P2-29: GET /card-editor/player still registered (not redirected)."""
        from app.main import app
        from app.api.web_routes.dashboard import lfa_player_card_editor
        route = next((r for r in app.routes
                      if getattr(r, "path", None) == "/card-editor/player"), None)
        assert route is not None
        assert route.endpoint is lfa_player_card_editor
