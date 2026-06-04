"""Static template tests — vt-location.js integration in all 6 VT game templates.

VTL-01  vt-location.js script include present in color_reaction template
VTL-02  VtLocation.warmUp() called in color_reaction template
VTL-03  browser_timezone field present in color_reaction submit payload
VTL-04  location field present in color_reaction submit payload

VTL-05  vt-location.js script include present in go_no_go template
VTL-06  VtLocation.warmUp() called in go_no_go template
VTL-07  browser_timezone field present in go_no_go submit payload
VTL-08  location field present in go_no_go submit payload

VTL-09  vt-location.js script include present in target_tracking template
VTL-10  VtLocation.warmUp() called in target_tracking template
VTL-11  browser_timezone field present in target_tracking submit payload
VTL-12  location field present in target_tracking submit payload

VTL-13  vt-location.js script include present in memory_sequence template
VTL-14  VtLocation.warmUp() called in memory_sequence template
VTL-15  browser_timezone field present in memory_sequence submit payload
VTL-16  location field present in memory_sequence submit payload

VTL-17  vt-location.js script include present in direction_swipe template
VTL-18  VtLocation.warmUp() called in direction_swipe template
VTL-19  browser_timezone field present in direction_swipe submit payload
VTL-20  location field present in direction_swipe submit payload

VTL-21  vt-location.js script include present in number_color_conflict template
VTL-22  VtLocation.warmUp() called in number_color_conflict template
VTL-23  browser_timezone field present in number_color_conflict submit payload
VTL-24  location field present in number_color_conflict submit payload

VTL-25  vt-location.js file exists at app/static/js/vt-location.js
VTL-26  vt-location.js exports VtLocation object (getBrowserTimezone, warmUp, buildSubmitPayload)
VTL-27  vt-location.js: getBrowserTimezone fallback returns 'UTC' on error
VTL-28  vt-location.js: buildSubmitPayload called when location unavailable → source='unavailable'
VTL-29  vt-location.js: _classifySource stale threshold is 5 minutes
VTL-30  vt-location.js: sessionStorage cache key is 'vtloc_cache'
"""
from __future__ import annotations

import pathlib

_TEMPLATES_DIR = pathlib.Path(__file__).parents[2] / "app" / "templates"
_STATIC_DIR    = pathlib.Path(__file__).parents[2] / "app" / "static" / "js"

_CR  = (_TEMPLATES_DIR / "virtual_training_color_reaction.html").read_text(encoding="utf-8")
_GNG = (_TEMPLATES_DIR / "virtual_training_go_no_go.html").read_text(encoding="utf-8")
_TT  = (_TEMPLATES_DIR / "virtual_training_target_tracking.html").read_text(encoding="utf-8")
_MS  = (_TEMPLATES_DIR / "virtual_training_memory_sequence.html").read_text(encoding="utf-8")
_DS  = (_TEMPLATES_DIR / "virtual_training_direction_swipe.html").read_text(encoding="utf-8")
_NCC = (_TEMPLATES_DIR / "virtual_training_number_color_conflict.html").read_text(encoding="utf-8")

_VTL_JS = (_STATIC_DIR / "vt-location.js").read_text(encoding="utf-8")

_INCLUDE  = 'src="/static/js/vt-location.js"'
_WARMUP   = "VtLocation.warmUp()"
_BRTZONE  = "browser_timezone:"
_LOCATION = "location:"


# ── VTL-01..04: color_reaction ────────────────────────────────────────────────

def test_vtl01_cr_includes_vt_location_js():
    assert _INCLUDE in _CR

def test_vtl02_cr_calls_warmup():
    assert _WARMUP in _CR

def test_vtl03_cr_payload_has_browser_timezone():
    assert "_vtCtx.browser_timezone" in _CR

def test_vtl04_cr_payload_has_location():
    assert "_vtCtx.location" in _CR


# ── VTL-05..08: go_no_go ─────────────────────────────────────────────────────

def test_vtl05_gng_includes_vt_location_js():
    assert _INCLUDE in _GNG

def test_vtl06_gng_calls_warmup():
    assert _WARMUP in _GNG

def test_vtl07_gng_payload_has_browser_timezone():
    assert "_vtCtx.browser_timezone" in _GNG

def test_vtl08_gng_payload_has_location():
    assert "_vtCtx.location" in _GNG


# ── VTL-09..12: target_tracking ──────────────────────────────────────────────

def test_vtl09_tt_includes_vt_location_js():
    assert _INCLUDE in _TT

def test_vtl10_tt_calls_warmup():
    assert _WARMUP in _TT

def test_vtl11_tt_payload_has_browser_timezone():
    assert "_vtCtx.browser_timezone" in _TT

def test_vtl12_tt_payload_has_location():
    assert "_vtCtx.location" in _TT


# ── VTL-13..16: memory_sequence ──────────────────────────────────────────────

def test_vtl13_ms_includes_vt_location_js():
    assert _INCLUDE in _MS

def test_vtl14_ms_calls_warmup():
    assert _WARMUP in _MS

def test_vtl15_ms_payload_has_browser_timezone():
    assert "_vtCtx.browser_timezone" in _MS

def test_vtl16_ms_payload_has_location():
    assert "_vtCtx.location" in _MS


# ── VTL-17..20: direction_swipe ──────────────────────────────────────────────

def test_vtl17_ds_includes_vt_location_js():
    assert _INCLUDE in _DS

def test_vtl18_ds_calls_warmup():
    assert _WARMUP in _DS

def test_vtl19_ds_payload_has_browser_timezone():
    assert "_vtCtx.browser_timezone" in _DS

def test_vtl20_ds_payload_has_location():
    assert "_vtCtx.location" in _DS


# ── VTL-21..24: number_color_conflict ────────────────────────────────────────

def test_vtl21_ncc_includes_vt_location_js():
    assert _INCLUDE in _NCC

def test_vtl22_ncc_calls_warmup():
    assert _WARMUP in _NCC

def test_vtl23_ncc_payload_has_browser_timezone():
    assert "_vtCtx.browser_timezone" in _NCC

def test_vtl24_ncc_payload_has_location():
    assert "_vtCtx.location" in _NCC


# ── VTL-25..30: vt-location.js structure ────────────────────────────────────

def test_vtl25_js_file_exists():
    assert (_STATIC_DIR / "vt-location.js").exists()

def test_vtl26_js_exports_vtlocation_object():
    assert "root.VtLocation" in _VTL_JS
    assert "getBrowserTimezone" in _VTL_JS
    assert "warmUp" in _VTL_JS
    assert "buildSubmitPayload" in _VTL_JS

def test_vtl27_js_getbrowsertimezone_utc_fallback():
    assert "return 'UTC'" in _VTL_JS

def test_vtl28_js_unavailable_source_on_no_location():
    assert "'unavailable'" in _VTL_JS

def test_vtl29_js_stale_threshold_5_minutes():
    assert "_STALE_LIMIT_MS" in _VTL_JS
    assert "5 * 60 * 1000" in _VTL_JS

def test_vtl30_js_cache_key():
    assert "'vtloc_cache'" in _VTL_JS
