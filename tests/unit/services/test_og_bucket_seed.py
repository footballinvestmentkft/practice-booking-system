"""OG bucket seed tests — Phase OG.

Verifies that the 'og' bucket is correctly present in the FIFA Classic design
both in the DESIGNS fallback dict and (mocked) DB-backed path, that the
collection detail page renders 7 formats when og is seeded, and that the
export guard accepts og as a supported bucket.

OG-01  DESIGNS fallback fifa.supported_export_buckets contains 'og'
OG-02  DESIGNS fallback and expected 7-bucket canonical set are in parity
OG-03  DB-backed path: og present in FIFA row → get_all_designs returns og
OG-04  DB-backed path: og absent from FIFA row → og absent from get_all_designs
OG-05  PC_FORMAT_META og entry uses mfg-ratio-169
OG-06  Detail page renders 7 format cards when og is in supported_export_buckets
OG-07  export get_supported_buckets: returns og when DB row contains it
OG-08  export get_supported_buckets: does not return og when DB row omits it
"""
from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from app.services.card_design_service import (
    DESIGNS,
    _invalidate_cache,
    get_all_designs,
    get_supported_buckets,
)
from app.services.card_constants import PC_FORMAT_META

_CANONICAL_7 = frozenset(
    ["square", "portrait", "story", "tiktok", "landscape", "og", "banner"]
)
_CANONICAL_6 = frozenset(
    ["square", "portrait", "story", "tiktok", "landscape", "banner"]
)

_TEMPLATE_BASE = (
    pathlib.Path(__file__).resolve().parents[3]
    / "app" / "templates"
)


@pytest.fixture(autouse=True)
def reset_cache():
    _invalidate_cache()
    yield
    _invalidate_cache()


def _make_db_row(buckets: list[str]):
    """Minimal mock CardDesign ORM row."""
    row = MagicMock()
    row.id = "fifa"
    row.label = "FIFA Classic"
    row.description = "Test"
    row.is_premium = True
    row.credit_cost = 300
    row.sort_order = 0
    row.browser_template = "public/player_card_fifa.html"
    row.is_active = True
    row.archetype_id = "fifa_base"
    row.supported_export_buckets = buckets
    row.animated_platforms = ["instagram_square"]
    row.component_config = {}
    return row


def _make_db(rows):
    db = MagicMock()
    db.query.return_value.all.return_value = rows
    return db


# ── OG-01  Fallback dict contains og ─────────────────────────────────────────

class TestOG01FallbackContainsOg:
    def test_og01_designs_fallback_fifa_has_og(self):
        buckets = set(DESIGNS["fifa"].supported_export_buckets)
        assert "og" in buckets, (
            "DESIGNS fallback dict for 'fifa' is missing 'og'. "
            "The fallback dict must match migration 2026_05_29_1100."
        )


# ── OG-02  Fallback parity with canonical 7-bucket set ───────────────────────

class TestOG02FallbackParity:
    def test_og02_fallback_matches_canonical_7(self):
        buckets = set(DESIGNS["fifa"].supported_export_buckets)
        assert buckets == _CANONICAL_7, (
            f"FIFA fallback buckets diverge from canonical set.\n"
            f"  Extra in fallback:   {buckets - _CANONICAL_7}\n"
            f"  Missing in fallback: {_CANONICAL_7 - buckets}"
        )


# ── OG-03  DB path with og present ───────────────────────────────────────────

class TestOG03DbPathOgPresent:
    def test_og03_db_row_with_og_returned_in_get_all_designs(self):
        db = _make_db([_make_db_row(list(_CANONICAL_7))])
        designs = get_all_designs(db)
        fifa = next((d for d in designs if d.id == "fifa"), None)
        assert fifa is not None
        assert "og" in fifa.supported_export_buckets, (
            "After migration 2026_05_29_1100 the DB row for 'fifa' must contain 'og'."
        )

    def test_og03_db_row_with_og_get_supported_buckets(self):
        db = _make_db([_make_db_row(list(_CANONICAL_7))])
        _invalidate_cache()
        get_all_designs(db)  # prime cache from mock DB
        buckets = set(get_supported_buckets("fifa", db))
        assert "og" in buckets


# ── OG-04  DB path with og absent ────────────────────────────────────────────

class TestOG04DbPathOgAbsent:
    def test_og04_db_row_without_og_does_not_leak_into_result(self):
        """Pre-migration DB row (6 buckets) must NOT expose og via DB cache."""
        db = _make_db([_make_db_row(list(_CANONICAL_6))])
        designs = get_all_designs(db)
        fifa = next((d for d in designs if d.id == "fifa"), None)
        assert fifa is not None
        assert "og" not in fifa.supported_export_buckets, (
            "Pre-migration DB row must not expose og through the DB-backed cache."
        )


# ── OG-05  PC_FORMAT_META og ratio ───────────────────────────────────────────

class TestOG05OgRatioClass:
    def test_og05_og_entry_uses_mfg_ratio_169(self):
        og_entry = next((m for m in PC_FORMAT_META if m["bucket"] == "og"), None)
        assert og_entry is not None, "'og' bucket missing from PC_FORMAT_META"
        assert og_entry["ratio"] == "mfg-ratio-169", (
            f"og bucket ratio should be 'mfg-ratio-169' (1200×630), got {og_entry['ratio']!r}"
        )

    def test_og05_og_platform_is_og(self):
        og_entry = next((m for m in PC_FORMAT_META if m["bucket"] == "og"), None)
        assert og_entry is not None
        assert og_entry["platform"] == "og"

    def test_og05_og_dims_correct(self):
        og_entry = next((m for m in PC_FORMAT_META if m["bucket"] == "og"), None)
        assert og_entry is not None
        assert og_entry["dims"] == "1200 × 630"


# ── OG-06  Detail page renders 7 format cards ────────────────────────────────

class TestOG06DetailPageSevenFormats:
    def test_og06_renders_seven_format_cards_with_og_in_db(self):
        """When format_rows has 7 entries (og included), 7 format cards render."""
        from jinja2 import Environment, FileSystemLoader

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_BASE)), autoescape=True
        )

        meta_by_bucket = {m["bucket"]: m for m in PC_FORMAT_META}
        format_rows = [meta_by_bucket[b] for b in _CANONICAL_7 if b in meta_by_bucket]
        assert len(format_rows) == 7, f"Expected 7 format rows, got {len(format_rows)}"

        user = MagicMock()
        user.id = 42
        user.credit_balance = 500
        design = MagicMock()
        design.label = "FIFA Classic"
        design.description = ""
        design.credit_cost = 300
        design.supported_export_buckets = tuple(_CANONICAL_7)

        html = env.get_template("shop_player_card_detail.html").render(
            request=MagicMock(),
            user=user,
            design=design,
            collection_id="fifa",
            state="get_card",
            format_rows=format_rows,
            flash_purchased=None,
            flash_error=None,
        )
        assert html.count("mfg-card") >= 7, (
            "Expected at least 7 .mfg-card elements when 7 format rows are passed."
        )
        assert "Open Graph" in html, "OG format label 'Open Graph' must appear in rendered HTML"

    def test_og06_six_format_cards_without_og(self):
        """When format_rows has 6 entries (pre-migration), og card is absent."""
        from jinja2 import Environment, FileSystemLoader

        env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_BASE)), autoescape=True
        )

        meta_by_bucket = {m["bucket"]: m for m in PC_FORMAT_META}
        format_rows = [meta_by_bucket[b] for b in _CANONICAL_6 if b in meta_by_bucket]
        assert len(format_rows) == 6

        user = MagicMock()
        user.id = 42
        user.credit_balance = 500
        design = MagicMock()
        design.label = "FIFA Classic"
        design.description = ""
        design.credit_cost = 300

        html = env.get_template("shop_player_card_detail.html").render(
            request=MagicMock(),
            user=user,
            design=design,
            collection_id="fifa",
            state="get_card",
            format_rows=format_rows,
            flash_purchased=None,
            flash_error=None,
        )
        assert "Open Graph" not in html, (
            "Pre-migration: og card must not appear when og is absent from format_rows."
        )


# ── OG-07 / OG-08  export get_supported_buckets gate ────────────────────────

class TestOG07OgBucketExportGate:
    def test_og07_supported_buckets_with_og_does_not_exclude_og(self):
        """After migration: get_supported_buckets('fifa', db) must include 'og'."""
        db = _make_db([_make_db_row(list(_CANONICAL_7))])
        get_all_designs(db)  # prime cache
        result = set(get_supported_buckets("fifa", db))
        assert "og" in result, (
            "With og in DB row, get_supported_buckets must return og. "
            "If missing, export?platform=og would return 422."
        )

    def test_og08_supported_buckets_without_og_excludes_og(self):
        """Pre-migration: get_supported_buckets('fifa', db) must NOT include 'og'."""
        db = _make_db([_make_db_row(list(_CANONICAL_6))])
        get_all_designs(db)
        result = set(get_supported_buckets("fifa", db))
        assert "og" not in result, (
            "Pre-migration DB row must not return og from get_supported_buckets."
        )
