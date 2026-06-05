"""Unit tests for training_day.py — Phase 2: lat/lng → lat_lng_derived timezone.

TD2-01  Budapest coords (47.4979, 19.0402), fresh, 20m → ("Europe/Budapest", "lat_lng_derived")
TD2-02  São Paulo coords (-23.5505, -46.6333), fresh, 30m → ("America/Sao_Paulo", "lat_lng_derived")
TD2-03  London coords (51.5074, -0.1278), fresh, 15m → ("Europe/London", "lat_lng_derived")
TD2-04  Fresh coords but accuracy=600m (> 500m) → fallback "browser_iana"
TD2-05  Stale coords (7 min old), accuracy=20m → fallback "browser_iana"
TD2-06  Invalid coords (lat=999, lng=999): TZF returns None → fallback "browser_iana"
TD2-07  lat=None, lng=None → fallback "browser_iana"
TD2-08  lat=None + browser_timezone=None → ("UTC", "utc_fallback")
TD2-09  GPS (Budapest) wins over contradicting browser_timezone ("America/Sao_Paulo")
TD2-10  Phase 1 regression: resolve_training_timezone("Europe/Budapest") with no lat/lng
        → ("Europe/Budapest", "browser_iana") unchanged
TD2-11  Exactly at freshness boundary (300s old) → still "lat_lng_derived" (≤ is fresh)
TD2-12  accuracy_m=None (unknown) is treated as acceptable for lat_lng_derived
TD2-13  timezonefinder ImportError → graceful fallback to browser_iana (mocked)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.training_day import resolve_training_timezone

_NOW = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)

_BUDAPEST  = (47.4979, 19.0402)
_SAO_PAULO = (-23.5505, -46.6333)
_LONDON    = (51.5074, -0.1278)


def _fresh(minutes_ago: float = 1.0) -> datetime:
    return _NOW - timedelta(minutes=minutes_ago)


# ── TD2-01..03: valid GPS → lat_lng_derived ───────────────────────────────────

class TestLatLngDerived:

    def test_td2_01_budapest_coords(self):
        """TD2-01: Budapest GPS → Europe/Budapest, lat_lng_derived."""
        tz, src = resolve_training_timezone(
            None,
            lat=_BUDAPEST[0], lng=_BUDAPEST[1],
            accuracy_m=20, captured_at=_fresh(), now=_NOW,
        )
        assert tz  == "Europe/Budapest"
        assert src == "lat_lng_derived"

    def test_td2_02_sao_paulo_coords(self):
        """TD2-02: São Paulo GPS → America/Sao_Paulo, lat_lng_derived."""
        tz, src = resolve_training_timezone(
            None,
            lat=_SAO_PAULO[0], lng=_SAO_PAULO[1],
            accuracy_m=30, captured_at=_fresh(), now=_NOW,
        )
        assert tz  == "America/Sao_Paulo"
        assert src == "lat_lng_derived"

    def test_td2_03_london_coords(self):
        """TD2-03: London GPS → Europe/London, lat_lng_derived."""
        tz, src = resolve_training_timezone(
            None,
            lat=_LONDON[0], lng=_LONDON[1],
            accuracy_m=15, captured_at=_fresh(), now=_NOW,
        )
        assert tz  == "Europe/London"
        assert src == "lat_lng_derived"


# ── TD2-04..05: quality gates → fallback ─────────────────────────────────────

class TestLocationQualityFallback:

    def test_td2_04_accuracy_too_low(self):
        """TD2-04: accuracy=600m > 500m threshold → falls back to browser_iana."""
        tz, src = resolve_training_timezone(
            "Europe/Budapest",
            lat=_BUDAPEST[0], lng=_BUDAPEST[1],
            accuracy_m=600, captured_at=_fresh(), now=_NOW,
        )
        assert src == "browser_iana"
        assert tz  == "Europe/Budapest"

    def test_td2_05_stale_location(self):
        """TD2-05: 7-minute-old GPS fix → stale, falls back to browser_iana."""
        tz, src = resolve_training_timezone(
            "America/Sao_Paulo",
            lat=_BUDAPEST[0], lng=_BUDAPEST[1],
            accuracy_m=20, captured_at=_fresh(minutes_ago=7), now=_NOW,
        )
        assert src == "browser_iana"
        assert tz  == "America/Sao_Paulo"

    def test_td2_11_exactly_at_boundary(self):
        """TD2-11: captured_at exactly 300s ago → still fresh (≤ 300s = fresh)."""
        captured = _NOW - timedelta(seconds=300)
        tz, src = resolve_training_timezone(
            None,
            lat=_BUDAPEST[0], lng=_BUDAPEST[1],
            accuracy_m=20, captured_at=captured, now=_NOW,
        )
        assert src == "lat_lng_derived"

    def test_td2_12_accuracy_none_is_acceptable(self):
        """TD2-12: accuracy_m=None (unknown) treated as acceptable, uses TZF."""
        tz, src = resolve_training_timezone(
            None,
            lat=_BUDAPEST[0], lng=_BUDAPEST[1],
            accuracy_m=None, captured_at=_fresh(), now=_NOW,
        )
        assert src == "lat_lng_derived"
        assert tz  == "Europe/Budapest"


# ── TD2-06..08: invalid / missing coords ─────────────────────────────────────

class TestInvalidCoords:

    def test_td2_06_invalid_coords(self):
        """TD2-06: lat=999, lng=999 → TZF returns None → fallback browser_iana."""
        tz, src = resolve_training_timezone(
            "Europe/Budapest",
            lat=999.0, lng=999.0,
            accuracy_m=10, captured_at=_fresh(), now=_NOW,
        )
        # TZF returns None for ocean/invalid → fallback
        assert src == "browser_iana"
        assert tz  == "Europe/Budapest"

    def test_td2_07_lat_lng_none_falls_back(self):
        """TD2-07: lat=None, lng=None → skips TZF step → browser_iana."""
        tz, src = resolve_training_timezone(
            "Europe/Budapest",
            lat=None, lng=None,
            accuracy_m=10, captured_at=_fresh(), now=_NOW,
        )
        assert src == "browser_iana"
        assert tz  == "Europe/Budapest"

    def test_td2_08_no_lat_no_browser_tz(self):
        """TD2-08: lat=None + browser_timezone=None → utc_fallback."""
        tz, src = resolve_training_timezone(
            None,
            lat=None, lng=None,
        )
        assert tz  == "UTC"
        assert src == "utc_fallback"


# ── TD2-09: GPS priority over browser tz ─────────────────────────────────────

class TestGpsPriority:

    def test_td2_09_gps_wins_over_browser_tz(self):
        """TD2-09: fresh Budapest GPS beats contradicting browser_timezone São Paulo."""
        tz, src = resolve_training_timezone(
            "America/Sao_Paulo",   # browser says São Paulo
            lat=_BUDAPEST[0], lng=_BUDAPEST[1],  # GPS says Budapest
            accuracy_m=20, captured_at=_fresh(), now=_NOW,
        )
        assert tz  == "Europe/Budapest"   # GPS wins
        assert src == "lat_lng_derived"


# ── TD2-10: Phase 1 regression ────────────────────────────────────────────────

class TestPhase1Regression:

    def test_td2_10_browser_only_still_browser_iana(self):
        """TD2-10: Phase 1 call (browser tz only, no lat/lng) unchanged."""
        tz, src = resolve_training_timezone("Europe/Budapest")
        assert tz  == "Europe/Budapest"
        assert src == "browser_iana"

    def test_td2_10b_browser_only_sao_paulo(self):
        tz, src = resolve_training_timezone("America/Sao_Paulo")
        assert tz  == "America/Sao_Paulo"
        assert src == "browser_iana"

    def test_td2_10c_none_browser_utc_fallback(self):
        tz, src = resolve_training_timezone(None)
        assert tz  == "UTC"
        assert src == "utc_fallback"


# ── TD2-13: TZF failure → graceful fallback ───────────────────────────────────

class TestTzfFailureFallback:

    def test_td2_13_tzf_import_error_falls_back(self, monkeypatch):
        """TD2-13: If timezonefinder raises any exception, fall back to browser_iana."""
        import app.services.training_day as td_module

        def _broken_derive(lat, lng):
            raise RuntimeError("simulated TZF failure")

        monkeypatch.setattr(td_module, "_derive_tz_from_coords", _broken_derive)

        tz, src = resolve_training_timezone(
            "Europe/Budapest",
            lat=_BUDAPEST[0], lng=_BUDAPEST[1],
            accuracy_m=20, captured_at=_fresh(), now=_NOW,
        )
        assert src == "browser_iana"
        assert tz  == "Europe/Budapest"
