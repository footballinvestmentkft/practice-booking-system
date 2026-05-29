"""
Unit tests for card_design_service (CS-1).

Coverage:
  CD-01  DESIGNS fallback dict contains exactly the 7 expected design IDs
  CD-02  get_design("fifa") returns FIFA Classic with all expected fields
  CD-03  get_design("unknown_id") falls back to FIFA Classic
  CD-04  _load_cache returns CardDesignDefinition from DB row
  CD-05  _maybe_reload: DB row with is_active=False yields available=False
  CD-06  get_all_designs with mock DB returns designs sorted by sort_order
  CD-07  get_all_designs with no DB (fallback) returns DESIGNS in sort_order
  CD-08  _invalidate_cache clears the cache
  CD-09  is_animated_capable: True for registered (fifa, instagram_square)
  CD-10  is_animated_capable: True for registered (pulse, instagram_square)
  CD-11  is_animated_capable: False for non-animated design (compact)
  CD-12  is_animated_capable: False for animated design on wrong platform
  CD-13  get_supported_buckets: fifa returns all 6 buckets
  CD-14  get_supported_buckets: pulse returns square only
  CD-15  get_supported_buckets: compact returns empty tuple (browser-only)
  CD-16  DESIGNS fallback dict animated_platforms matches ANIMATED_EXPORT_CAPABLE frozenset
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

import app.services.card_design_service as svc
from app.services.card_design_service import (
    DESIGNS,
    DESIGN_ORDER,
    CardDesignDefinition,
    get_design,
    get_all_designs,
    get_supported_buckets,
    is_animated_capable,
    is_design_available,
    _invalidate_cache,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_cache():
    """Ensure a clean cache state before and after every test."""
    _invalidate_cache()
    yield
    _invalidate_cache()


def _make_row(
    id="fifa",
    label="FIFA Classic",
    description="Test description",
    is_premium=False,
    credit_cost=0,
    sort_order=0,
    browser_template="public/player_card_fifa.html",
    is_active=True,
    archetype_id=None,
    supported_export_buckets=None,
    animated_platforms=None,
):
    row = MagicMock()
    row.id = id
    row.label = label
    row.description = description
    row.is_premium = is_premium
    row.credit_cost = credit_cost
    row.sort_order = sort_order
    row.browser_template = browser_template
    row.is_active = is_active
    row.archetype_id = archetype_id
    row.supported_export_buckets = supported_export_buckets or []
    row.animated_platforms = animated_platforms or []
    return row


def _make_db(*rows):
    """Return a mock db where query().all() returns rows."""
    db = MagicMock()
    db.query.return_value.all.return_value = list(rows)
    return db


# ── CD-01  DESIGNS fallback contains exactly the 7 expected IDs ───────────────

def test_cd01_designs_has_7_expected_ids():
    expected = {"fifa", "compact", "compact_bg", "showcase", "showcase_bg", "atlas", "pulse"}
    assert set(DESIGNS.keys()) == expected


# ── CD-02  get_design("fifa") returns FIFA Classic with correct fields ─────────

def test_cd02_get_design_fifa_fields():
    d = get_design("fifa")
    assert d.id == "fifa"
    assert d.label == "FIFA Classic"
    assert d.is_premium is True
    assert d.credit_cost == 300
    assert d.template == "public/player_card_fifa.html"
    assert d.available is True
    assert d.archetype_id == "column"
    assert "square" in d.supported_export_buckets
    assert "og" in d.supported_export_buckets
    assert "instagram_square" in d.animated_platforms


# ── CD-03  get_design("unknown") falls back to fifa ───────────────────────────

def test_cd03_get_design_unknown_falls_back_to_fifa():
    d = get_design("nonexistent_design_id")
    assert d.id == "fifa"


# ── CD-04  _load_cache converts DB row to CardDesignDefinition ────────────────

def test_cd04_load_cache_from_db_row():
    row = _make_row(
        id="ocean_card",
        label="Ocean Card",
        is_premium=True,
        credit_cost=500,
        sort_order=7,
        browser_template="public/player_card_ocean.html",
        is_active=True,
        supported_export_buckets=["square"],
        animated_platforms=["instagram_square"],
    )
    db = _make_db(row)
    loaded = svc._load_cache(db)
    assert "ocean_card" in loaded
    d = loaded["ocean_card"]
    assert isinstance(d, CardDesignDefinition)
    assert d.label == "Ocean Card"
    assert d.is_premium is True
    assert d.credit_cost == 500
    assert d.template == "public/player_card_ocean.html"
    assert d.available is True
    assert d.supported_export_buckets == ("square",)
    assert d.animated_platforms == ("instagram_square",)


# ── CD-05  DB row with is_active=False yields available=False ─────────────────

def test_cd05_inactive_db_row_yields_available_false():
    row = _make_row(id="old_design", is_active=False)
    db = _make_db(row)
    loaded = svc._load_cache(db)
    assert loaded["old_design"].available is False


# ── CD-06  get_all_designs with mock DB returns sorted by sort_order ──────────

def test_cd06_get_all_designs_sorted_by_sort_order():
    row_b = _make_row(id="b_design", sort_order=10, label="B Design")
    row_a = _make_row(id="a_design", sort_order=5,  label="A Design")
    # reversed order in DB → should be sorted by sort_order
    db = _make_db(row_b, row_a)
    designs = get_all_designs(db=db)
    ids = [d.id for d in designs]
    assert ids.index("a_design") < ids.index("b_design")


# ── CD-07  get_all_designs with no DB uses DESIGNS fallback ───────────────────

def test_cd07_get_all_designs_fallback_no_db():
    designs = get_all_designs(db=None)
    assert len(designs) == 7
    # FIFA Classic (sort_order=0) is the first design
    assert designs[0].id == "fifa"
    assert designs[0].is_premium is True


# ── CD-08  _invalidate_cache clears the cache ─────────────────────────────────

def test_cd08_invalidate_cache_clears_state():
    row = _make_row(id="test_design", label="Test Design")
    db = _make_db(row)
    # Force cache load
    svc._maybe_reload(db)
    assert svc._design_cache  # cache populated

    _invalidate_cache()
    assert svc._design_cache == {}
    assert svc._cache_loaded_at == 0.0


# ── CD-09  is_animated_capable: True for (fifa, instagram_square) ─────────────

def test_cd09_animated_capable_fifa_square():
    assert is_animated_capable("fifa", "instagram_square") is True


# ── CD-10  is_animated_capable: True for (pulse, instagram_square) ────────────

def test_cd10_animated_capable_pulse_square():
    assert is_animated_capable("pulse", "instagram_square") is True


# ── CD-11  is_animated_capable: False for non-animated design ────────────────

def test_cd11_animated_capable_compact_false():
    assert is_animated_capable("compact", "instagram_square") is False


# ── CD-12  is_animated_capable: False for animated design on wrong platform ───

def test_cd12_animated_capable_wrong_platform():
    assert is_animated_capable("fifa", "instagram_story") is False
    assert is_animated_capable("pulse", "tiktok") is False
    assert is_animated_capable("fifa", "instagram_portrait") is False


# ── CD-13  get_supported_buckets: fifa returns all 7 ─────────────────────────

def test_cd13_supported_buckets_fifa_all_seven():
    buckets = get_supported_buckets("fifa")
    expected = {"square", "portrait", "story", "tiktok", "landscape", "og", "banner"}
    assert set(buckets) == expected, (
        f"FIFA fallback dict missing buckets: {expected - set(buckets)}. "
        "If 'og' is missing, the DESIGNS dict and migration 2026_05_29_1100 are out of sync."
    )


# ── CD-14  get_supported_buckets: pulse returns square only ───────────────────

def test_cd14_supported_buckets_pulse_square_only():
    assert get_supported_buckets("pulse") == ("square",)


# ── CD-15  get_supported_buckets: compact returns empty (browser-only) ────────

def test_cd15_supported_buckets_compact_empty():
    assert get_supported_buckets("compact") == ()
    assert get_supported_buckets("showcase") == ()
    assert get_supported_buckets("atlas") == ()


# ── CD-16  DESIGNS animated_platforms matches ANIMATED_EXPORT_CAPABLE ─────────

def test_cd16_designs_animated_matches_animated_export_capable():
    from app.services.card_constants import ANIMATED_EXPORT_CAPABLE

    # Build expected set from DESIGNS
    expected = frozenset(
        (design_id, platform_id)
        for design_id, design in DESIGNS.items()
        for platform_id in design.animated_platforms
    )
    assert ANIMATED_EXPORT_CAPABLE == expected, (
        "ANIMATED_EXPORT_CAPABLE must exactly match animated_platforms in DESIGNS.\n"
        f"In ANIMATED_EXPORT_CAPABLE but not DESIGNS: {ANIMATED_EXPORT_CAPABLE - expected}\n"
        f"In DESIGNS but not ANIMATED_EXPORT_CAPABLE: {expected - ANIMATED_EXPORT_CAPABLE}"
    )
