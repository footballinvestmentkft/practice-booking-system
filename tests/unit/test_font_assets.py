"""
Unit tests — Font Assets: self-hosted woff2 files for FIFA Classic export templates
====================================================================================

Coverage:
  FA-01  app/static/fonts/ directory exists (StaticFiles mount target)
  FA-02  All 9 required woff2 files are present and non-empty
  FA-03  export_base.html contains a @font-face src URL for each woff2 file

These tests gate the font acquisition step (scripts/download_fonts.sh).
All three must pass before any template update that references the new fonts.

Re-acquisition: bash scripts/download_fonts.sh  (idempotent, safe to re-run)
"""
from __future__ import annotations

from pathlib import Path

import pytest

_PROJECT_ROOT  = Path(__file__).resolve().parent.parent.parent
_FONTS_DIR     = _PROJECT_ROOT / "app" / "static" / "fonts"
_EXPORT_BASE   = _PROJECT_ROOT / "app" / "templates" / "public" / "export" / "shared" / "export_base.html"

_REQUIRED_FONTS = [
    "BebasNeue-Regular.woff2",
    "BarlowCondensed-300.woff2",
    "BarlowCondensed-400.woff2",
    "BarlowCondensed-600.woff2",
    "BarlowCondensed-700.woff2",
    "BarlowCondensed-800.woff2",
    "Rajdhani-500.woff2",
    "Rajdhani-600.woff2",
    "Rajdhani-700.woff2",
]


@pytest.mark.unit
class TestFontAssets:

    def test_fa_01_fonts_directory_exists(self):
        """app/static/fonts/ must exist — StaticFiles serves /static/fonts/* from here."""
        assert _FONTS_DIR.is_dir(), (
            f"Font directory missing: {_FONTS_DIR}\n"
            "Run: bash scripts/download_fonts.sh"
        )

    def test_fa_02_all_woff2_files_present_and_non_empty(self):
        """All 9 woff2 files must be present and contain at least 1 KB of data."""
        missing, empty = [], []
        for fname in _REQUIRED_FONTS:
            path = _FONTS_DIR / fname
            if not path.exists():
                missing.append(fname)
            elif path.stat().st_size < 1024:
                empty.append(f"{fname} ({path.stat().st_size} bytes)")
        assert not missing, f"Missing font files: {missing}\nRun: bash scripts/download_fonts.sh"
        assert not empty,   f"Font files too small (truncated?): {empty}"

    def test_fa_03_export_base_references_all_fonts(self):
        """export_base.html must contain a /static/fonts/ src reference for every woff2 file."""
        assert _EXPORT_BASE.exists(), f"export_base.html not found at {_EXPORT_BASE}"
        src = _EXPORT_BASE.read_text(encoding="utf-8")
        missing = [f for f in _REQUIRED_FONTS if f"/static/fonts/{f}" not in src]
        assert not missing, (
            f"export_base.html is missing @font-face src for: {missing}\n"
            "Step B (export_base update) must complete before this test passes."
        )
